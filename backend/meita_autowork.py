"""Meita Acting COO: database-backed internal assignment queue.

The CEO's explicit instruction in Meita chat can authorize bounded INTERNAL
delegation and execution. External publishing, messages, financial transactions,
deployment, social permissions and approval of third-party actions are excluded.
"""
import asyncio
import logging
import os
import secrets
import time
from contextlib import suppress

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from backend import workforce
from backend import memory as office_memory

logger=logging.getLogger("office.meita.autowork")

def init(db):
    # Legacy existing delegation tables remain available for old work.
    from backend import delegation
    delegation.init(db)
    db.execute("ALTER TABLE coo_proposals ADD COLUMN IF NOT EXISTS auto_execute BOOLEAN NOT NULL DEFAULT FALSE")
    db.execute("ALTER TABLE coo_jobs ADD COLUMN IF NOT EXISTS task_id TEXT UNIQUE")
    db.execute("ALTER TABLE coo_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ")
    db.execute("CREATE INDEX IF NOT EXISTS coo_auto_queue ON coo_jobs(status,created_at)")

def assign(db,proposal_id,staff,automatic=False):
    """Atomically assign real Task Centre rows and internal COO job records.

    Staff scope and availability are validated against actual current DB rows,
    not merely staff IDs produced by the AI. Never creates duplicate assignments.
    """
    init(db)
    proposal=db.execute("SELECT * FROM coo_proposals WHERE id=%s FOR UPDATE",
                        (proposal_id,)).fetchone()
    if not proposal:
        raise HTTPException(404,"Meita delegation plan not found")
    if proposal["status"]!="Pending approval":
        raise HTTPException(409,"This delegation plan was already processed")
    roster={p["id"]:p for p in staff}
    jobs=proposal["plan"].get("jobs",[])
    if not jobs or len(jobs)>9:
        raise HTTPException(409,"Meita plan is empty or exceeds the 9-task limit")
    for item in jobs:
        person=roster.get(item.get("staff_id"))
        if not person or person.get("status")!="Available":
            raise HTTPException(409,"A proposed AI staff member is not currently eligible")
        if not item.get("title") or not item.get("brief"):
            raise HTTPException(409,"Incomplete internal AI task")
    created=[]
    for item in jobs:
        person=roster[item["staff_id"]]
        task_id=secrets.token_hex(12)
        job_id=secrets.token_hex(12)
        title=item["title"].strip()[:250]
        brief=item["brief"].strip()[:3000]
        # Delegated tasks are locked against a concurrent manual Task Centre
        # Run AI. Execution is driven only by the corresponding COO job.
        task_status="In progress" if automatic else "To do"
        db.execute("""INSERT INTO tasks
            (id,title,description,category,assignee,priority,
             status,recurrence,created,ai_run_started)
            VALUES (%s,%s,%s,'General',%s,'Normal',%s,'None',%s,TRUE)""",
            (task_id,title,brief,person["id"],task_status,int(time.time())))
        snapshot={key:person.get(key) or "" for key in
                  ("id","name","role","department","expertise")}
        db.execute("""INSERT INTO coo_jobs
            (id,proposal_id,title,brief,staff,status,task_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (job_id,proposal_id,title,brief,Jsonb(snapshot),
             "Queued" if automatic else "Approved",task_id))
        created.append({"job_id":job_id,"task_id":task_id,
                        "staff_id":person["id"],"staff_name":person["name"],
                        "title":title,"status":"Queued" if automatic else "Approved"})
    db.execute("""UPDATE coo_proposals
        SET status='Approved',auto_execute=%s,reviewed_at=NOW()
        WHERE id=%s""",(automatic,proposal_id))
    return {"status":"Approved","automatic":automatic,"jobs":created}

def auto_assign_chat(db,proposal_id,available):
    """CEO's explicit assign request is sufficient to authorize internal work."""
    return assign(db,proposal_id,available,automatic=True)

def claim(db):
    init(db)
    row=db.execute("""WITH next_job AS (
          SELECT id FROM coo_jobs WHERE status='Queued'
          ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE coo_jobs AS j
        SET status='Running',started_at=NOW(),updated_at=NOW()
        FROM next_job WHERE j.id=next_job.id
        RETURNING j.*""").fetchone()
    if not row:return None
    if row["task_id"]:
        db.execute("""UPDATE tasks SET status='In progress',ai_run_started=TRUE
            WHERE id=%s AND deleted_at IS NULL""",(row["task_id"],))
    return dict(row)

def _memory_from_result(db,job,answer):
    """Store traceable AI-work notes, never unlabelled factual assertions."""
    office_memory.setup(db)
    ident="meita-delivery-"+job["id"]
    excerpt=" ".join(str(answer or "").strip().split())[:1500]
    title=("AI delivery · "+job["title"])[:120]
    content=(f"AI-generated internal draft; not independently fact-checked. "
             f"Staff: {job['staff'].get('name','AI staff')}. "
             f"Task Centre ID: {job.get('task_id') or 'legacy'}. "
             f"Report excerpt: {excerpt}")[:3000]
    db.execute("""INSERT INTO meita_memories (id,title,content,category)
        VALUES (%s,%s,%s,'Company knowledge')
        ON CONFLICT(id) DO UPDATE SET content=EXCLUDED.content,updated_at=NOW()""",
        (ident,title,content))

def _finish_success(db,job,run_id,answer):
    db.execute("""UPDATE coo_jobs SET status='Delivered',
        workforce_run_id=%s,updated_at=NOW()
        WHERE id=%s AND status='Running'""",(run_id,job["id"]))
    if job.get("task_id"):
        db.execute("""UPDATE tasks SET status='For approval',
            ai_run_started=TRUE WHERE id=%s""",(job["task_id"],))
    _memory_from_result(db,job,answer)
    db.execute("""INSERT INTO audit(action,detail,created)
        VALUES('meita.autorun.delivered',%s,%s)""",
        (job["id"]+" · "+job["title"][:100],int(time.time())))

def process_one(connect):
    """Handle exactly one pending AI job, independent from the HTTP request."""
    if os.getenv("AI_ENGINE_ENABLED","false").lower()!="true" or not os.getenv("OPENAI_API_KEY","").strip():
        return None
    with connect() as db:
        job=claim(db)
    if job is None:return None
    try:
        with connect() as db:
            person=db.execute("""SELECT * FROM staff WHERE id=%s
                AND status='Available'""",(job["staff"]["id"],)).fetchone()
        if not person:
            raise RuntimeError("Assigned AI staff is no longer available")
        task={"id":job["task_id"] or job["id"],
              "title":job["title"],"brief":job["brief"],"category":"General"}
        result=workforce.execute(task,dict(person))
    except Exception as error:
        logger.warning("Meita internal AI task failed: %s",type(error).__name__)
        with connect() as db:
            db.execute("""UPDATE coo_jobs
                SET status='Retry available',updated_at=NOW()
                WHERE id=%s AND status='Running'""",(job["id"],))
            db.execute("""INSERT INTO audit(action,detail,created)
                VALUES('meita.autorun.failed',%s,%s)""",
                (job["id"]+" · "+type(error).__name__,int(time.time())))
        return {"job_id":job["id"],"status":"Retry available"}
    with connect() as db:
        _finish_success(db,job,result["id"],result["result"])
    return {"job_id":job["id"],"status":"Delivered"}

def recover_interrupted(connect):
    """Never silently repeat an AI request with an uncertain upstream result."""
    with connect() as db:
        init(db)
        workforce.init(db)
        stalled=db.execute("""SELECT * FROM coo_jobs
            WHERE status='Running' AND updated_at < NOW()-INTERVAL '12 minutes'
            FOR UPDATE SKIP LOCKED""").fetchall()
        for job in stalled:
            existing=db.execute("""SELECT id,result FROM workforce_runs
                WHERE task_id=%s ORDER BY created_at DESC LIMIT 1""",
                (job["task_id"] or job["id"],)).fetchone()
            if existing:
                _finish_success(db,dict(job),existing["id"],existing["result"])
            else:
                db.execute("""UPDATE coo_jobs SET status='Needs verification',
                    updated_at=NOW() WHERE id=%s""",(job["id"],))
                db.execute("""INSERT INTO audit(action,detail,created)
                    VALUES ('meita.autorun.interrupted',%s,%s)""",
                    (job["id"],int(time.time())))

async def autonomous_worker(connect):
    """Database-backed queue survives app restarts; multi-worker safe claims."""
    if os.getenv("MEITA_AUTO_WORKER_ENABLED","true").lower()!="true":
        return
    while True:
        try:
            await asyncio.to_thread(recover_interrupted,connect)
            result=await asyncio.to_thread(process_one,connect)
            if result is None:
                await asyncio.sleep(8)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Meita worker cycle failed")
            await asyncio.sleep(15)

def history(db,limit=60):
    init(db)
    records=db.execute("""SELECT p.id,p.title,p.brief,p.status,p.auto_execute,
        p.created_at,p.chat_message_id,
        j.id AS job_id,j.task_id,j.title AS job_title,j.brief AS job_brief,
        j.status AS job_status,j.workforce_run_id,
        j.staff->>'name' AS staff_name,
        j.staff->>'department' AS department,
        r.result AS result_preview
        FROM coo_proposals p LEFT JOIN coo_jobs j ON j.proposal_id=p.id
        LEFT JOIN workforce_runs r ON r.id=j.workforce_run_id
        ORDER BY p.created_at DESC,p.id,j.created_at,j.id
        LIMIT %s""",(limit*9,)).fetchall()
    groups={}
    for row in records:
        pid=row["id"]
        if pid not in groups:
            groups[pid]={"id":pid,"title":row["title"],"brief":row["brief"],
                 "status":row["status"],"automatic":row["auto_execute"],
                 "created_at":row["created_at"],"chat_message_id":row["chat_message_id"],
                 "jobs":[]}
        if row["job_id"]:
            groups[pid]["jobs"].append({
                "id":row["job_id"],"task_id":row["task_id"],
                "title":row["job_title"],"description":row["job_brief"],
                "staff_name":row["staff_name"],"department":row["department"],
                "status":row["job_status"],
                "has_output":bool(row["workforce_run_id"])})
    return {"requests":list(groups.values())[:limit]}

def output_bundle(db,proposal_id):
    init(db)
    workforce.init(db)
    proposal=db.execute("""SELECT id,title,brief,status,auto_execute,
        created_at FROM coo_proposals WHERE id=%s""",(proposal_id,)).fetchone()
    if not proposal:raise HTTPException(404,"Meita assignment not found")
    rows=db.execute("""SELECT j.id,j.title,j.brief,j.status,j.task_id,j.staff,
        j.workforce_run_id,r.result,r.sources,r.created_at AS delivered_at
        FROM coo_jobs j LEFT JOIN workforce_runs r ON r.id=j.workforce_run_id
        WHERE j.proposal_id=%s ORDER BY j.created_at,j.id""",
        (proposal_id,)).fetchall()
    return {"proposal":dict(proposal),"jobs":[dict(row) for row in rows]}
