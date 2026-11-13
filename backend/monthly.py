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
LEGACY_REGIONS=(
    "Klang Valley (Kuala Lumpur, Selangor & Putrajaya)",
    "Northern Malaysia (Penang, Kedah, Perlis & Perak)",
    "East Coast (Kelantan, Terengganu & Pahang)",
    "Southern Peninsula (Johor, Melaka & Negeri Sembilan)",
    "Sabah",
    "Sarawak",
    "Labuan",
)
# Historic values used only to identify untouched automatically generated tasks.
def period_now(now=None):
    day=(now or datetime.now(MY_TZ)).astimezone(MY_TZ)
    return f"{day.year:04d}-{day.month:02d}"

def _legacy_region_for(period):
    year,month=parse_period(period)
    base=2026*12+11
    return LEGACY_REGIONS[((year*12+month)-base) % len(LEGACY_REGIONS)]

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
LEGACY_DEFAULTS=(
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
# Region is no longer assigned automatically. CEO can specify any area in an
# individual Task Description when location matters to the assignment.
NEUTRAL_TEMPLATES = {
    "intelligence-brief": (
        "Monthly Intelligence Bulletin",
        "Prepare an evidence-based business intelligence briefing covering relevant "
        "economic indicators, market changes, sector developments, emerging opportunities "
        "and risks within the CEO's approved scope. If a specific Malaysian region or "
        "state is given in this task description, focus the analysis accordingly. "
        "State publication dates, separate verified facts from estimates, and identify "
        "unavailable live data. Never invent fieldwork, interviews or current news."),
    "intelligence-watch": (
        "Competitor & Risk Watchlist",
        "Develop a competitor and sector risk watchlist for the approved business scope. "
        "Identify developments, research gaps, recommended verification sources and "
        "confidence levels. Address a specific region only when the CEO requests it "
        "in the task description. Do not claim live monitoring or private data access."),
    "marketing-calendar": (
        "Monthly Marketing Campaign Calendar",
        "Propose a 30-day marketing and content calendar with audience segments, "
        "channels, key messages, KPIs and creative briefs for Maryam. Follow any "
        "market or regional requirements explicitly given by the CEO in the task description. "
        "Prepare internal drafts only; no publishing or ad spending."),
    "creative-direction": (
        "Creative Direction & Brand Consistency Review",
        "Review accessible approved creative assets for brand consistency and propose "
        "a practical monthly art-direction brief, covering storytelling, typography, "
        "visual choices and tone. Include regional or cultural considerations only "
        "when the CEO identifies that scope. If no assets are available, deliver a "
        "review checklist and concepts, not claims of completed inspections."),
    "finance-controls": (
        "Expense Controls & Financial Risk Memo",
        "Assess documented financial control risks, approval thresholds, relevant "
        "operating cost drivers and compliance questions. Apply region-specific "
        "cost or regulatory comparisons only if specified in the task description. "
        "Distinguish generic guidance from verified current rules and request "
        "qualified professional review where necessary."),
    "operations-sop": (
        "Operations Execution Plan & SOP Improvement",
        "Propose a monthly workflow optimization and internal operating checklist "
        "covering communication, handoffs, logistics, supplier dependencies and "
        "delivery risks. Include state holidays or regional logistics only if the "
        "CEO's task description requests a geographic scope. Do not initiate procurement."),
    "strategy-regional": (
        "Business Growth Opportunities Review",
        "Prepare an evidence-led commercial opportunity map for the CEO's business "
        "priorities, including markets, customers, competitive factors, entry constraints, "
        "risks, and data needed for financial assessment. Investigate specific states "
        "or regions only when requested by the CEO. Distinguish sourced facts from hypotheses."),
    "knowledge-review": (
        "Source & Knowledge Quality Review",
        "Review relevant authoritative sources and internal SOPs actually accessible "
        "to the Knowledge Academy, recording publication dates, evidence gaps, "
        "contradictions and updates needed. Include a Malaysian regional scope only "
        "when the CEO sets one in the task description. Do not claim document or "
        "video ingestion that has not occurred."),
    "risk-compliance": (
        "Corporate Compliance & Policy Change Watch",
        "Prepare a compliance review identifying relevant requirements needing "
        "verification, including applicable privacy, corporate, tax and cybersecurity "
        "frameworks. Add state-specific jurisdiction questions only when directed "
        "by the CEO in the task description. Identify unverified claims and seek "
        "professional confirmation for legal conclusions."),
}
DEFAULTS = tuple(
    (key, dept, *(NEUTRAL_TEMPLATES.get(key, (title, brief))), due)
    for key,dept,title,brief,due in LEGACY_DEFAULTS
)

def remove_automatic_region(db):
    """One-time, non-destructive update of untouched system-generated records.

    Preserve CEO-edited templates, tasks, AI-run outputs and historical months.
    Only original descriptions/titles are changed, and only for unrun tasks
    in the current or future reporting months.
    """
    key="optional_regional_scope_v1"
    if db.execute("SELECT 1 FROM office_schema_migrations WHERE name=%s",
                  (key,)).fetchone():
        return {"templates":0,"assignments":0,"applied":False}
    changed_templates=0
    changed_tasks=0
    this_period=period_now()
    runs_exist=bool(db.execute("SELECT to_regclass('public.workforce_runs') AS t").fetchone()["t"])
    no_runs_clause=("AND NOT EXISTS (SELECT 1 FROM workforce_runs w WHERE w.task_id=tasks.id)"
                    if runs_exist else "")
    updated={row[0]:row for row in DEFAULTS}
    for old_key,dept,old_title,old_brief,due in LEGACY_DEFAULTS:
        new=updated[old_key]
        if new[2:4]==(old_title,old_brief):
            continue
        # Preserve customized templates; change only exact auto-seeded defaults.
        result=db.execute("""UPDATE monthly_templates SET title=%s,brief=%s
            WHERE key=%s AND title=%s AND brief=%s""",
            (new[2],new[3],old_key,old_title,old_brief))
        changed_templates+=result.rowcount
        records=db.execute("""SELECT a.period,a.task_id
            FROM monthly_assignments a
            WHERE a.template_key=%s AND a.period >= %s""",
            (old_key,this_period)).fetchall()
        for row in records:
            period=row["period"]
            old_focus=_legacy_region_for(period)
            prior_title=f"{period} · {old_title.replace('{region}',old_focus)}"
            prior_brief=old_brief.replace("{region}",old_focus)
            revised_title=f"{period} · {new[2]}"
            updated_task=db.execute("""UPDATE tasks SET title=%s,description=%s
                WHERE id=%s AND title=%s AND description=%s
                  AND status='To do' AND ai_run_started=FALSE AND deleted_at IS NULL
                  """+no_runs_clause,
                (revised_title,new[3],row["task_id"],prior_title,prior_brief))
            if updated_task.rowcount:
                db.execute("""UPDATE monthly_assignments SET brief=%s
                    WHERE task_id=%s AND brief=%s""",
                    (new[3],row["task_id"],prior_brief))
                changed_tasks+=updated_task.rowcount
    db.execute("INSERT INTO office_schema_migrations(name) VALUES (%s)",(key,))
    return {"templates":changed_templates,"assignments":changed_tasks,"applied":True}

from backend import schema_state

@schema_state.bootstrap_only
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
        title=row["title"]
        brief=row["brief"]
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
    return {"period":period,"created":len(new)}

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
        JOIN tasks t ON t.id=a.task_id AND t.deleted_at IS NULL
        LEFT JOIN staff s ON s.id=a.director_id
        WHERE a.period=%s
        ORDER BY a.due_date,s.department,t.title""",(period,)).fetchall()
    templates=db.execute("""SELECT key,department,title,brief,due_day,enabled
        FROM monthly_templates ORDER BY department,due_day""").fetchall()
    records=[dict(r) for r in plans]
    return {"period":period,
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
