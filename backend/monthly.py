"""Monthly director responsibilities: scheduled drafts, not autonomous AI actions.

All tasks persist in PostgreSQL. Assignments are generated exactly once per
calendar month in Asia/Kuala_Lumpur. Work must be started by CEO in Task Centre.
"""
import asyncio
import calendar
import logging
import secrets
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from backend import task_schedule
from pydantic import BaseModel, Field

logger=logging.getLogger("office.monthly")
MY_TZ=ZoneInfo("Asia/Kuala_Lumpur")
REGIONS=(
    "Klang Valley (Kuala Lumpur, Selangor & Putrajaya)",
    "Northern Malaysia (Penang, Kedah, Perlis & Perak)",
    "East Coast (Kelantan, Terengganu & Pahang)",
    "Southern Peninsula (Johor, Melaka & Negeri Sembilan)",
    "Sabah",
    "Sarawak",
    "Labuan",
)
# One regional focus shared by the Directors each month for coordinated analysis.
def period_now(now=None):
    day=(now or datetime.now(MY_TZ)).astimezone(MY_TZ)
    return f"{day.year:04d}-{day.month:02d}"

def region_for(period):
    year,month=parse_period(period)
    base=2026*12+11
    return REGIONS[((year*12+month)-base) % len(REGIONS)]

def parse_period(period):
    if not isinstance(period,str) or len(period)!=7 or period[4]!="-":
        raise HTTPException(400,"Month must be YYYY-MM")
    try:
        year=int(period[:4]);month=int(period[5:])
    except (ValueError,TypeError):
        raise HTTPException(400,"Month must be YYYY-MM")
    if not 2000<=year<=2100 or not 1<=month<=12:
        raise HTTPException(400,"Invalid reporting month")
    return year,month

class TemplateUpdate(BaseModel):
    title: str = Field(min_length=5,max_length=200)
    brief: str = Field(min_length=15,max_length=3000)
    due_day: int = Field(ge=1,le=28)
    enabled: bool

# Each Director receives a core reporting duty and a second action-oriented duty.
# Source-dependent work must clearly identify missing/unavailable evidence.
DEFAULTS=(
    ("intelligence-brief","Research & Intelligence","Regional Intelligence Bulletin — {region}",
     "Prepare a concise evidence-based intelligence briefing for {region}: regional economic indicators, "
     "market changes, sector developments, emerging opportunities and risks. State publication dates, "
     "distinguish verified facts from estimates, list verifiable source references and mark unavailable "
     "live data explicitly. Never invent fieldwork, interviews or current news.",18),
    ("intelligence-watch","Research & Intelligence","Competitor & Risk Watchlist — {region}",
     "Develop a watchlist of relevant sector/competitor developments and material risks for {region}. "
     "Document assumptions, gaps, recommended follow-up sources and confidence levels. Flag intelligence "
     "that needs human verification; do not assert live monitoring or private information access.",27),

    ("marketing-calendar","Marketing & Growth","Malaysia Regional Campaign Calendar — {region}",
     "Propose a localized 30-day marketing and content calendar for {region}, including audience segments, "
     "channel strategy, Malay/English and relevant cultural considerations, key messages, KPIs and creative "
     "briefs for Maryam. Prepare internal drafts only; no publishing or ad spending.",18),
    ("marketing-performance","Marketing & Growth","Campaign Performance & Growth Recommendations",
     "Prepare the month's marketing performance review using only authenticated connected metrics actually "
     "available. If metrics are missing, produce a clearly labeled data-request checklist, reporting "
     "template and improvement hypotheses; never invent reach, conversion or ROI figures.",27),

    ("creative-direction","Creative","Creative Direction & Brand Consistency Review — {region}",
     "Review approved available creative assets and brand materials for consistency. Prepare a monthly "
     "art-direction brief with region-sensitive visual storytelling, typography, image choices, tone and "
     "cultural considerations for {region}. If no assets are available, deliver a review checklist and "
     "concept proposals, not claims of completed inspections.",18),
    ("creative-production","Creative","Campaign Visuals & Video Storyboard Pack",
     "Develop internal creative concepts, storyboard directions, social content treatments, video shot lists "
     "and production briefs for the month's priority campaigns. Coordinate conceptually with Marketing; "
     "do not claim videos were rendered, designs exported or Premiere/CapCut operated.",27),

    ("finance-review","Finance & Treasury","Monthly Budget, Cash Flow & Variance Review",
     "Prepare an internal management finance pack: budget-versus-actual variance, cash flow outlook and "
     "material cost risks, strictly based on verified company records provided. If records are not "
     "accessible, deliver a data request and blank reconciliable reporting framework; never invent "
     "transactions or financial results. No payments or changes to ledgers.",18),
    ("finance-controls","Finance & Treasury","Expense Controls & Financial Risk Memo — {region}",
     "Assess financial control risks, potential regional operating cost considerations in {region}, "
     "approval thresholds and compliance questions. Distinguish generic guidance from verified current "
     "regulation and request qualified review when necessary.",27),

    ("operations-scorecard","Operations & Project Management","Monthly Operations & Delivery Scorecard",
     "Summarize verified current project/task statuses, overdue commitments, team capacity, blockers "
     "and dependency risks. Where task records or vendor logistics are missing, show data gaps and "
     "a practical reporting template instead of fictional progress.",18),
    ("operations-sop","Operations & Project Management","Regional Execution Plan & SOP Improvement — {region}",
     "Propose a monthly workflow optimization and operating checklist considering {region}: logistics, "
     "communication, local holidays where verified, supply constraints and handoff risks. Produce "
     "actionable internal SOP recommendations, not unauthorized external procurement.",27),

    ("technology-security","Technology & AI Systems","AI Office Reliability & Security Review",
     "Prepare a security and reliability review covering identity, database backups, integrations, "
     "dependency risks, API key handling and recovery planning. Identify which controls are verified "
     "from authorized records and which need technical validation. Never claim access to live "
     "monitoring, logs or infrastructure without actual connected tools.",18),
    ("technology-backlog","Technology & AI Systems","Automation & Platform Improvement Roadmap",
     "Prioritize one month of safe internal engineering improvements and technical debt based on "
     "available repository/system evidence, including tests, rollback plans, security risks, "
     "dependencies and CEO approvals required. No automatic deployments or credential use.",27),

    ("strategy-regional","Strategy & Business Development","Malaysia Regional Growth Opportunities — {region}",
     "Prepare an evidence-led opportunity map for {region}: relevant sectors, customers, commercial "
     "hypotheses, competitive landscape, entry constraints, uncertainties and financial information "
     "required. Distinguish sourced facts from speculative opportunities.",18),
    ("strategy-board","Strategy & Business Development","Executive Strategic Decision Brief",
     "Prepare a CEO-facing options memo summarizing the most material potential growth or partnership "
     "decisions, expected benefits, tradeoffs, risk scenarios, and information required for investment "
     "or approval. Do not claim partnerships or binding commercial decisions have been made.",27),

    ("knowledge-review","Knowledge & Learning Academy","Malaysian Source & Knowledge Quality Review — {region}",
     "Build a monthly internal knowledge quality review for {region}, listing authoritative data, "
     "company SOPs and relevant Malaysian sources actually supplied or accessible. Track publication "
     "dates, source gaps, contradictory information and needed updates. Do not pretend documents or "
     "videos have been ingested.",18),
    ("knowledge-training","Knowledge & Learning Academy","Director Learning & SOP Curriculum",
     "Propose role-based learning priorities and a practical SOP improvement syllabus for all nine "
     "Directors. Provide source suggestions, training outcomes, validity checks and access restrictions. "
     "Do not claim model training, web ingestion or completed coursework that was not performed.",27),

    ("risk-register","Governance, Risk & Compliance","Monthly Corporate & AI Risk Register",
     "Prepare a risk register covering privacy, cybersecurity, financial controls, internal delegation, "
     "operational reliability and reputation. Identify risk owners, evidence, likelihood/impact, "
     "mitigations and items requiring direct CEO escalation. No fabricated incidents.",18),
    ("risk-compliance","Governance, Risk & Compliance","Malaysia Compliance & Policy Change Watch — {region}",
     "Prepare a jurisdiction-aware compliance review for {region}, identifying federal/state "
     "requirements needing verification, PDPA, relevant tax/corporate/cyber frameworks and policy "
     "changes. Clearly label unverified legal claims, cite current official sources when actually "
     "accessible and recommend professional confirmation for legal conclusions.",27),
)

def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS monthly_templates (
        key TEXT PRIMARY KEY,
        department TEXT NOT NULL,
        title TEXT NOT NULL, brief TEXT NOT NULL,
        due_day SMALLINT NOT NULL CHECK(due_day BETWEEN 1 AND 28),
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    db.execute("""CREATE TABLE IF NOT EXISTS monthly_assignments (
        template_key TEXT NOT NULL REFERENCES monthly_templates(key),
        period TEXT NOT NULL,
        task_id TEXT NOT NULL UNIQUE,
        director_id TEXT NOT NULL,
        due_date DATE NOT NULL,
        brief TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(template_key,period))""")
    # Run once. Disabling or editing a template is preserved over future restarts.
    if not db.execute(
        "SELECT 1 FROM office_schema_migrations WHERE name='monthly_directors_v1'"
    ).fetchone():
        for key,dept,title,brief,due in DEFAULTS:
            db.execute("""INSERT INTO monthly_templates(key,department,title,brief,due_day)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (key) DO NOTHING""",
                (key,dept,title,brief,due))
        db.execute("""INSERT INTO office_schema_migrations(name)
            VALUES ('monthly_directors_v1') ON CONFLICT DO NOTHING""")

def generate_month(db,period=None):
    period=period or period_now()
    year,month=parse_period(period)
    focus=region_for(period)
    # Idempotent under restarts and concurrent workers.
    db.execute("SELECT pg_advisory_xact_lock(815024,202611)")
    rows=db.execute("""SELECT mt.key,mt.department,mt.title,mt.brief,mt.due_day,
        s.id AS director_id
        FROM monthly_templates mt
        JOIN staff s ON s.department=mt.department AND s.org_role='Director'
        WHERE mt.enabled=TRUE AND NOT EXISTS (
            SELECT 1 FROM monthly_assignments a
            WHERE a.template_key=mt.key AND a.period=%s)
        ORDER BY mt.department,mt.due_day""",(period,)).fetchall()
    new=[]
    for row in rows:
        title=row["title"].replace("{region}",focus)
        brief=row["brief"].replace("{region}",focus)
        due=date(year,month,min(int(row["due_day"]),calendar.monthrange(year,month)[1]))
        task_id=secrets.token_hex(12)
        db.execute("""INSERT INTO tasks
            (id,title,description,assignee,status,priority,due_date,recurrence,created)
            VALUES (%s,%s,%s,%s,'To do','Normal',%s,'Monthly',%s)""",
            (task_id,f"{period} · {title}",brief,row["director_id"],due,int(time.time())))
        db.execute("""INSERT INTO monthly_assignments
            (template_key,period,task_id,director_id,due_date,brief)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (row["key"],period,task_id,row["director_id"],due,brief))
        new.append(task_id)
    if new:
        db.execute("""INSERT INTO audit(action,detail,created)
            VALUES('monthly.generated',%s,%s)""",(f"{period}: {len(new)} director assignments",int(time.time())))
    return {"period":period,"region_focus":focus,"created":len(new)}

def detail_for_task(db,task_id):
    row=db.execute(
        "SELECT brief,due_date,period FROM monthly_assignments WHERE task_id=%s",
        (task_id,)).fetchone()
    if not row: return None
    return (row["brief"]+f"\nReporting period: {row['period']}."
            f" Due date (Malaysia): {row['due_date'].isoformat()}.")

def summary(db,period=None):
    period=period or period_now()
    parse_period(period)
    plans=db.execute("""SELECT a.period,a.task_id,a.director_id,a.due_date,
        a.brief,a.template_key,t.title,t.status,
        s.name AS director_name,s.department
        FROM monthly_assignments a
        LEFT JOIN tasks t ON t.id=a.task_id
        LEFT JOIN staff s ON s.id=a.director_id
        WHERE a.period=%s
        ORDER BY a.due_date,s.department,t.title""",(period,)).fetchall()
    templates=db.execute("""SELECT key,department,title,brief,due_day,enabled
        FROM monthly_templates ORDER BY department,due_day""").fetchall()
    records=[dict(r) for r in plans]
    return {"period":period,"region_focus":region_for(period),
            "completed":sum(1 for r in records if r["status"]=="Completed"),
            "total":len(records),
            "assignments":records,"templates":[dict(t) for t in templates]}

def update_template(db,key,body):
    result=db.execute("""UPDATE monthly_templates SET title=%s,brief=%s,
        due_day=%s,enabled=%s WHERE key=%s RETURNING key""",
        (body.title.strip(),body.brief.strip(),body.due_day,body.enabled,key)).fetchone()
    if not result: raise HTTPException(404,"Monthly template not found")
    return {"ok":True}

async def monthly_clock(open_db):
    """Wake around Malaysian midnight daily; on 1st, ensure new monthly work."""
    while True:
        now=datetime.now(MY_TZ)
        tomorrow=now.date()+timedelta(days=1)
        next_run=datetime(tomorrow.year,tomorrow.month,tomorrow.day,0,5,tzinfo=MY_TZ)
        await asyncio.sleep(max(30,(next_run-now).total_seconds()))
        try:
            with open_db() as db:
                generate_month(db)
                task_schedule.generate_due(db)
        except Exception:
            logger.exception("Scheduled director monthly task generation failed")
