"""CEO-owned task metadata and recurring work schedules in Malaysia time.

No recurring task triggers an AI call. Schedules only create future task records.
"""
import calendar
import secrets
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException

TZ=ZoneInfo("Asia/Kuala_Lumpur")
PRIORITIES=("Urgent","High","Normal","Low")
FREQUENCIES=("None","Daily","Weekly","Monthly")

def local_today():
    return datetime.now(TZ).date()

def parse_due(value,required=False):
    if not value:
        if required: raise HTTPException(400,"Set a due date for recurring tasks")
        return None
    try: return date.fromisoformat(str(value))
    except (TypeError,ValueError): raise HTTPException(400,"Due date must be YYYY-MM-DD")

def validate(body):
    if not body.title.strip() or len(body.title)>250:
        raise HTTPException(400,"Task title is required")
    if len(body.description)>3000:
        raise HTTPException(400,"Description cannot exceed 3000 characters")
    if body.priority not in PRIORITIES:
        raise HTTPException(400,"Invalid task priority")
    if body.recurrence not in FREQUENCIES:
        raise HTTPException(400,"Invalid recurring schedule")
    due=parse_due(body.due_date,body.recurrence!="None")
    return due

def setup(db):
    db.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''")
    db.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'Normal'")
    db.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS due_date DATE")
    db.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence TEXT NOT NULL DEFAULT 'None'")
    db.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS series_id TEXT")
    db.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS occurrence_date DATE")
    db.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS ai_run_started BOOLEAN NOT NULL DEFAULT FALSE")
    db.execute("""CREATE TABLE IF NOT EXISTS task_series (
        id TEXT PRIMARY KEY, origin_task_id TEXT NOT NULL,
        title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
        assignee TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'Normal',
        frequency TEXT NOT NULL,
        anchor_day SMALLINT NOT NULL,
        next_due_date DATE NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS unique_task_occurrence
        ON tasks(series_id,occurrence_date) WHERE series_id IS NOT NULL""")

def next_date(day,frequency,anchor_day):
    if frequency=="Daily":return day+timedelta(days=1)
    if frequency=="Weekly":return day+timedelta(days=7)
    if frequency=="Monthly":
        year=day.year+(1 if day.month==12 else 0)
        month=1 if day.month==12 else day.month+1
        return date(year,month,min(anchor_day,calendar.monthrange(year,month)[1]))
    raise HTTPException(400,"Unsupported recurrence")

def _assignee(db,assignee):
    if not assignee:return ""
    if not db.execute("SELECT 1 FROM staff WHERE id=%s",(assignee,)).fetchone():
        raise HTTPException(400,"Assigned AI staff does not exist")
    return assignee

def create(db,body):
    due=validate(body);assignee=_assignee(db,body.assignee)
    task_id=secrets.token_hex(12)
    frequency=body.recurrence
    series_id=secrets.token_hex(12) if frequency!="None" else None
    db.execute("""INSERT INTO tasks
        (id,title,description,assignee,priority,due_date,recurrence,
         series_id,occurrence_date,status,created)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'To do',%s)""",
        (task_id,body.title.strip(),body.description.strip(),assignee,
         body.priority,due,frequency,series_id,due if series_id else None,int(time.time())))
    if series_id:
        db.execute("""INSERT INTO task_series
            (id,origin_task_id,title,description,assignee,priority,frequency,anchor_day,next_due_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (series_id,task_id,body.title.strip(),body.description.strip(),
             assignee,body.priority,frequency,due.day,
             next_date(due,frequency,due.day)))
    return {"id":task_id,"series_id":series_id}

def update(db,task_id,body):
    due=validate(body)
    row=db.execute("SELECT * FROM tasks WHERE id=%s FOR UPDATE",(task_id,)).fetchone()
    if not row:raise HTTPException(404,"Task not found")
    if row["status"] in ("For approval","Completed") or row["ai_run_started"]:
        raise HTTPException(409,"AI execution already started or task reviewed; editing is locked")
    if db.execute("SELECT to_regclass('public.workforce_runs') AS table_name").fetchone()["table_name"]:
        if db.execute("SELECT 1 FROM workforce_runs WHERE task_id=%s LIMIT 1",(task_id,)).fetchone():
            raise HTTPException(409,"AI has already generated output for this task")
    # Monthly workplans own their repeating schedules. Editing one assignment changes
    # only that month's task; the recurring template remains in Monthly Workplan.
    from_monthly=db.execute(
        "SELECT 1 FROM monthly_assignments WHERE task_id=%s",(task_id,)
    ).fetchone() if db.execute("SELECT to_regclass('public.monthly_assignments') AS t").fetchone()["t"] else None
    if from_monthly and body.recurrence!="Monthly":
        raise HTTPException(400,"Monthly Workplan recurrence is managed in Monthly Workplan")
    assignee=_assignee(db,body.assignee)
    existing_series=row.get("series_id")
    origin=False
    if existing_series:
        series=db.execute("SELECT * FROM task_series WHERE id=%s FOR UPDATE",
                          (existing_series,)).fetchone()
        origin=bool(series and series["origin_task_id"]==task_id)
        if not origin and body.recurrence!=row["recurrence"]:
            raise HTTPException(400,"Edit the original recurring task to change the schedule")
    new_series_id=existing_series
    if existing_series and origin and body.recurrence=="None":
        db.execute("UPDATE task_series SET enabled=FALSE WHERE id=%s",(existing_series,))
        new_series_id=None
    elif existing_series and origin:
        db.execute("""UPDATE task_series SET title=%s,description=%s,assignee=%s,
            priority=%s,frequency=%s,anchor_day=%s,next_due_date=%s,enabled=TRUE
            WHERE id=%s""",
            (body.title.strip(),body.description.strip(),assignee,body.priority,
             body.recurrence,due.day,next_date(due,body.recurrence,due.day),
             existing_series))
    elif not existing_series and body.recurrence!="None" and not from_monthly:
        new_series_id=secrets.token_hex(12)
        db.execute("""INSERT INTO task_series
            (id,origin_task_id,title,description,assignee,priority,frequency,anchor_day,next_due_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (new_series_id,task_id,body.title.strip(),body.description.strip(),
             assignee,body.priority,body.recurrence,due.day,
             next_date(due,body.recurrence,due.day)))
    db.execute("""UPDATE tasks SET title=%s,description=%s,assignee=%s,priority=%s,
        due_date=%s,recurrence=%s,series_id=%s,occurrence_date=%s
        WHERE id=%s""",
        (body.title.strip(),body.description.strip(),assignee,body.priority,
         due,body.recurrence,new_series_id,
         due if new_series_id else None,task_id))
    return {"ok":True}

def generate_due(db,today=None,limit=120):
    today=today or local_today()
    # Idempotent under concurrent processes, bootstraps and manual reads.
    db.execute("SELECT pg_advisory_xact_lock(815024,202612)")
    series=db.execute("""SELECT * FROM task_series WHERE enabled=TRUE
        AND next_due_date<=%s ORDER BY next_due_date LIMIT 120
        FOR UPDATE""",(today,)).fetchall()
    created=0
    for row in series:
        due=row["next_due_date"]
        iterations=0
        while due<=today and iterations<limit:
            iterations+=1
            new_id=secrets.token_hex(12)
            # Existing directors can be removed; create unassigned tasks if necessary.
            assignee=row["assignee"] if db.execute(
                "SELECT 1 FROM staff WHERE id=%s",(row["assignee"],)
            ).fetchone() else ""
            result=db.execute("""INSERT INTO tasks
                (id,title,description,assignee,priority,due_date,recurrence,
                 series_id,occurrence_date,status,created)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'To do',%s)
                ON CONFLICT (series_id,occurrence_date) DO NOTHING""",
                (new_id,row["title"],row["description"],assignee,row["priority"],
                 due,row["frequency"],row["id"],due,int(time.time())))
            created+=result.rowcount
            due=next_date(due,row["frequency"],row["anchor_day"])
        db.execute("UPDATE task_series SET next_due_date=%s WHERE id=%s",(due,row["id"]))
    return {"created":created}
