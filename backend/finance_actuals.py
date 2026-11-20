"""Finance actuals sourced from operator records and OpenAI organization costs."""
import json
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


def summary(db):
    init(db)
    rows = db.execute("""SELECT to_char(day,'YYYY-MM') AS month,kind,source,currency,
        SUM(amount) AS amount FROM finance_actuals WHERE
        day >= (date_trunc('month', CURRENT_DATE) - INTERVAL '11 months')::date
        GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""").fetchall()
    recent = db.execute("""SELECT day,kind,source,currency,amount,reference,origin
        FROM finance_actuals ORDER BY day DESC,id LIMIT 30""").fetchall()
    return {"monthly": rows, "recent": recent,
            "openai_configured": bool(os.getenv("OPENAI_BILLING_ADMIN_KEY", "").strip())}


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
