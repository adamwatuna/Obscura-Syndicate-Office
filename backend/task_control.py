"""CEO task control: abandon work and soft-remove entries without deleting audit/output.

A running AI provider request cannot be revoked once sent; cancellation is applied
when it returns. Queued jobs are cancelled immediately and never claimed.
"""
from fastapi import HTTPException


def setup(db):
    from backend import meita_autowork, organization
    meita_autowork.init(db)
    organization.init_plans(db)
    db.execute("ALTER TABLE coo_jobs ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")
    db.execute("ALTER TABLE director_jobs ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")


def _lock_task(db,task_id):
    # Queue claims lock the job before changing the task; use the same order.
    db.execute("SELECT id FROM coo_jobs WHERE task_id=%s FOR UPDATE",(task_id,)).fetchall()
    db.execute("SELECT id FROM director_jobs WHERE task_id=%s FOR UPDATE",(task_id,)).fetchall()
    task=db.execute("SELECT id,title,status,deleted_at FROM tasks WHERE id=%s FOR UPDATE",
                    (task_id,)).fetchone()
    if not task or task["deleted_at"] is not None:
        raise HTTPException(404,"Task not found")
    return task


def _abandon_locked(db,task_id):
    """Mark jobs atomically: running requests get a cancellation pending marker."""
    db.execute("""UPDATE coo_jobs SET
        status=CASE WHEN status='Running' THEN 'Cancellation requested'
                    ELSE 'Cancelled' END,updated_at=NOW()
        WHERE task_id=%s AND status NOT IN ('Cancelled','Completed')""",(task_id,))
    db.execute("""UPDATE director_jobs SET
        status=CASE WHEN status='Running' THEN 'Cancellation requested'
                    ELSE 'Cancelled' END,updated_at=NOW()
        WHERE task_id=%s AND status NOT IN ('Cancelled','Completed')""",(task_id,))
    db.execute("""UPDATE workforce_runs SET status='Abandoned'
        WHERE task_id=%s AND status='For approval'""",(task_id,))
    db.execute("UPDATE tasks SET status='Abandoned',ai_run_started=TRUE WHERE id=%s",
               (task_id,))
    waiting=db.execute("""SELECT
      EXISTS(SELECT 1 FROM coo_jobs WHERE task_id=%s AND status='Cancellation requested')
      OR EXISTS(SELECT 1 FROM director_jobs WHERE task_id=%s AND status='Cancellation requested')
      AS pending""",(task_id,task_id)).fetchone()
    return bool(waiting["pending"])


def abandon(db,task_id):
    setup(db)
    task=_lock_task(db,task_id)
    if task["status"]=="Completed":
        raise HTTPException(409,"Completed tasks can be removed from the workspace, not abandoned")
    if task["status"]=="Abandoned":
        pending=db.execute("""SELECT 1 FROM coo_jobs
           WHERE task_id=%s AND status='Cancellation requested' LIMIT 1""",(task_id,)).fetchone()
        return {"ok":True,"status":"Abandoned","cancellation_pending":bool(pending)}
    pending=_abandon_locked(db,task_id)
    return {"ok":True,"status":"Abandoned","cancellation_pending":pending}


def archive(db,task_id):
    setup(db)
    task=_lock_task(db,task_id)
    pending=False
    if task["status"] not in ("Completed","Abandoned"):
        pending=_abandon_locked(db,task_id)
    else:
        pending=bool(db.execute("""SELECT 1 FROM coo_jobs
            WHERE task_id=%s AND status='Cancellation requested' LIMIT 1""",
            (task_id,)).fetchone())
    db.execute("UPDATE tasks SET deleted_at=NOW() WHERE id=%s",(task_id,))
    db.execute("UPDATE coo_jobs SET archived_at=NOW(),updated_at=NOW() WHERE task_id=%s",
               (task_id,))
    db.execute("UPDATE director_jobs SET archived_at=NOW(),updated_at=NOW() WHERE task_id=%s",
               (task_id,))
    # Stop future generated occurrences only when removing the series origin.
    series=db.execute("""SELECT s.id FROM task_series s
        JOIN tasks t ON t.series_id=s.id
        WHERE t.id=%s AND s.origin_task_id=t.id""",(task_id,)).fetchone()
    if series:
        db.execute("UPDATE task_series SET enabled=FALSE WHERE id=%s",(series["id"],))
    return {"ok":True,"removed_task_id":task_id,
            "recurrence_stopped":bool(series),"cancellation_pending":pending}


def job_action(db,job_id,action):
    setup(db)
    row=db.execute("SELECT id,task_id,status,archived_at FROM coo_jobs WHERE id=%s",
                   (job_id,)).fetchone()
    if not row or row["archived_at"] is not None:
        raise HTTPException(404,"Meita assignment not found")
    if row["task_id"]:
        return abandon(db,row["task_id"]) if action=="abandon" else archive(db,row["task_id"])
    job=db.execute("SELECT id,status FROM coo_jobs WHERE id=%s FOR UPDATE",
                   (job_id,)).fetchone()
    if action=="abandon" and job["status"]=="Completed":
        raise HTTPException(409,"Completed assignments can only be removed from view")
    if action=="abandon":
        db.execute("""UPDATE coo_jobs SET status=CASE
            WHEN status='Running' THEN 'Cancellation requested'
            ELSE 'Cancelled' END,updated_at=NOW() WHERE id=%s""",(job_id,))
        return {"ok":True,"status":"Cancelled"}
    db.execute("""UPDATE coo_jobs SET
        status=CASE WHEN status='Running' THEN 'Cancellation requested'
                    WHEN status='Completed' THEN status ELSE 'Cancelled' END,
        archived_at=NOW(),updated_at=NOW() WHERE id=%s""",(job_id,))
    return {"ok":True,"status":"Removed"}
