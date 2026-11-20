"""Finance actuals sourced from operator records and OpenAI organization costs."""
import csv
import io
import json
from bisect import bisect_right
from functools import lru_cache
from time import monotonic
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field


class Entry(BaseModel):
    day: date
    kind: Literal["overhead", "expense", "income"]
    source: str = Field(min_length=1, max_length=120, pattern=r"\S")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal = Field(gt=0, le=100_000_000_000, decimal_places=2)
    reference: str = Field(default="", max_length=250)


def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS finance_actuals (
        id TEXT PRIMARY KEY, day DATE NOT NULL, kind TEXT NOT NULL,
        source TEXT NOT NULL, currency TEXT NOT NULL, amount NUMERIC NOT NULL,
        reference TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL)""")


def record(db, entry):
    init(db)
    ident = secrets.token_hex(12)
    db.execute("""INSERT INTO finance_actuals
        (id,day,kind,source,currency,amount,reference,origin)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'manual')""",
        (ident, entry.day, entry.kind, entry.source.strip(), entry.currency,
         entry.amount, entry.reference.strip()))
    return {"id": ident}


@lru_cache(maxsize=2)
def usd_rates(cache_period):
    """Cache the public BNM noon midpoint feed for one hour per process."""
    url = "https://storage.data.gov.my/finsector/exr/daily_1200.csv"
    with urllib.request.urlopen(url, timeout=6) as stream:
        raw = stream.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Unexpected BNM dataset size")
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    rates = {}
    for row in rows:
        if row.get("rate_type") != "middle" or not row.get("usd"):
            continue
        rate = Decimal(row["usd"])
        if rate.is_finite() and rate > 0:
            rates[date.fromisoformat(row["date"])] = rate
    if not rates:
        raise ValueError("No BNM USD rates")
    return sorted(rates), rates


def myr_view(daily):
    if any(row["currency"] == "USD" for row in daily):
        try:
            dates, rates = usd_rates(int(monotonic() // 3600))
        except (urllib.error.URLError, TimeoutError, ValueError, ArithmeticError):
            dates, rates = [], {}
    else:
        dates, rates = [], {}
    monthly = {}
    missing = set()
    applied = {}
    for row in daily:
        day, currency = row["day"], row["currency"]
        amount = row["amount"]
        if currency != "MYR":
            ix = bisect_right(dates, day) - 1
            if currency != "USD" or ix < 0 or (day - dates[ix]).days > 7:
                missing.add((day, currency))
                continue
            rate_day = dates[ix]
            rate = rates[rate_day]
            applied[day] = {"day": day, "rate_day": rate_day, "rate": rate}
            amount *= rate
        key = (day.strftime("%Y-%m"), row["kind"], row["source"])
        monthly[key] = monthly.get(key, Decimal(0)) + amount
    return {"monthly": [{"month": month, "kind": kind, "source": source,
                         "currency": "MYR", "amount": amount}
                        for (month, kind, source), amount in sorted(monthly.items())],
            "rates": [applied[day] for day in sorted(applied, reverse=True)],
            "omitted_days": len(missing)}


def summary(db, *, convert=False):
    init(db)
    rows = db.execute("""SELECT to_char(day,'YYYY-MM') AS month,kind,source,currency,
        SUM(amount) AS amount FROM finance_actuals WHERE
        day >= (date_trunc('month', CURRENT_DATE) - INTERVAL '11 months')::date
        GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""").fetchall()
    recent = db.execute("""SELECT day,kind,source,currency,amount,reference,origin
        FROM finance_actuals ORDER BY day DESC,id LIMIT 30""").fetchall()
    result = {"monthly": rows, "recent": recent,
              "openai_configured": bool(os.getenv("OPENAI_BILLING_ADMIN_KEY", "").strip())}
    if convert:
        daily = db.execute("""SELECT day,kind,source,currency,SUM(amount) AS amount
            FROM finance_actuals WHERE
            day >= (date_trunc('month', CURRENT_DATE) - INTERVAL '11 months')::date
            GROUP BY 1,2,3,4""").fetchall()
        result["myr"] = myr_view(daily)
    return result


def openai_costs():
    key = os.getenv("OPENAI_BILLING_ADMIN_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Configure the OpenAI billing Admin API key in Railway")
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=90)
    params = {"start_time": int(start.timestamp()), "end_time": int(end.timestamp()),
              "bucket_width": "1d", "limit": 90}
    rows = {}
    for _ in range(10):
        url = "https://api.openai.com/v1/organization/costs?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(request, timeout=12) as stream:
                page = json.load(stream, parse_float=Decimal)
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise HTTPException(502, "OpenAI billing sync failed. Check organization and Admin API key.") from None
        for bucket in page["data"]:
            day = datetime.fromtimestamp(bucket["start_time"], timezone.utc).date()
            for result in bucket["results"]:
                money = result["amount"]
                currency = money["currency"].upper()
                if not currency.isascii() or not currency.isalpha() or len(currency) != 3:
                    raise HTTPException(502, "OpenAI billing returned an invalid currency")
                pair = (day, currency)
                rows[pair] = rows.get(pair, Decimal(0)) + Decimal(str(money["value"]))
        if not page.get("has_more"):
            return start.date(), end.date(), rows
        if not page.get("next_page"):
            break
        params["page"] = page["next_page"]
    raise HTTPException(502, "OpenAI billing returned incomplete results")


def replace_openai(db, start, end, rows):
    init(db)
    db.execute("SELECT pg_advisory_xact_lock(92513, 5108)")
    db.execute("DELETE FROM finance_actuals WHERE origin='openai_api' AND day >= %s AND day < %s",
               (start, end))
    for (day, currency), amount in rows.items():
        if amount:
            db.execute("""INSERT INTO finance_actuals
                (id,day,kind,source,currency,amount,reference,origin)
                VALUES (%s,%s,'overhead','OpenAI API',%s,%s,'Organization costs','openai_api')""",
                (f"openai:{day}:{currency}", day, currency, amount))
    return {"days_with_costs": len({day for day, _ in rows})}
