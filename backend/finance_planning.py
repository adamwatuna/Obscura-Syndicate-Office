"""Finance phase 1: planning-only scenarios. No bank, ledger or payment integration.

Budget amounts are CEO-entered ASSUMPTIONS, never verified account balances.
"""
import secrets
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator
from fastapi import HTTPException
from psycopg.types.json import Jsonb

MAX_AMOUNT=Decimal("100000000000.00")

class BudgetLine(BaseModel):
    category: str=Field(min_length=1,max_length=100)
    amount: Decimal=Field(ge=0,le=MAX_AMOUNT)

    @field_validator("amount")
    @classmethod
    def finite_money(cls,value):
        if not value.is_finite():
            raise ValueError("Amounts must be finite")
        return value

class BudgetDraft(BaseModel):
    title: str=Field(min_length=4,max_length=180)
    period: str=Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    scenario: str=Field(default="Base case")
    currency: str=Field(default="MYR",pattern=r"^[A-Z]{3}$")
    projected_revenue: Decimal=Field(ge=0,le=MAX_AMOUNT)
    costs: list[BudgetLine]=Field(min_length=1,max_length=35)
    assumptions: str=Field(default="",max_length=4000)

    @field_validator("scenario")
    @classmethod
    def scenario_allowed(cls,value):
        if value not in ("Base case","Conservative","Optimistic"):
            raise ValueError("Choose a supported planning scenario")
        return value

    @field_validator("projected_revenue")
    @classmethod
    def finite_revenue(cls,value):
        if not value.is_finite():
            raise ValueError("Revenue must be finite")
        return value


from backend import schema_state

@schema_state.bootstrap_only
def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS finance_budget_drafts (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        period TEXT NOT NULL,
        scenario TEXT NOT NULL,
        currency TEXT NOT NULL DEFAULT 'MYR',
        projected_revenue NUMERIC(16,2) NOT NULL,
        costs JSONB NOT NULL,
        assumptions TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Draft for CEO review',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")


def totals(body):
    costs=sum((item.amount for item in body.costs),Decimal("0.00"))
    revenue=body.projected_revenue
    return {"projected_revenue":str(revenue.quantize(Decimal(".01"))),
            "projected_costs":str(costs.quantize(Decimal(".01"))),
            "projected_surplus":str((revenue-costs).quantize(Decimal(".01"))),
            "currency":body.currency,
            "data_status":"ASSUMPTIONS / PROJECTIONS ONLY — not bank actuals"}


def save(db,body):
    init(db)
    identifier=secrets.token_hex(12)
    result=totals(body)
    db.execute("""INSERT INTO finance_budget_drafts
       (id,title,period,scenario,currency,projected_revenue,costs,assumptions)
       VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",(
       identifier,body.title.strip(),body.period,body.scenario,
       body.currency,body.projected_revenue,
       Jsonb([{"category":x.category,"amount":str(x.amount)} for x in body.costs]),
       body.assumptions.strip()))
    return {"id":identifier,"status":"Draft for CEO review",**result}


def list_drafts(db):
    init(db)
    return [dict(row) for row in db.execute("""SELECT id,title,period,scenario,
      currency,projected_revenue,costs,assumptions,status,created_at
      FROM finance_budget_drafts ORDER BY created_at DESC LIMIT 100""").fetchall()]
