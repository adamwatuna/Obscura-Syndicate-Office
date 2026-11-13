"""CEO-only Executive HQ feed of source-backed staff news briefings.

Entries are actual stored workforce runs for explicit News Update tasks;
no fabricated feed items, no generic task status masquerading as news.
"""
from backend import workforce

def list_for_ceo(db,limit=12):
    workforce.init(db)
    records=db.execute("""SELECT r.id,r.task_id,r.task_title,r.staff_name,
        r.result,r.status,r.created_at,r.reviewed_at,r.searched_at,
        r.web_searched,r.sources,t.priority
        FROM workforce_runs r
        JOIN tasks t ON t.id=r.task_id
        WHERE t.category='News Update'
          AND t.deleted_at IS NULL
          AND r.web_searched=TRUE
          AND r.status IN ('For approval','Approved')
        ORDER BY r.created_at DESC, r.id DESC LIMIT %s""",(limit,)).fetchall()
    return {"briefings":[dict(row) for row in records],
            "requires_ceo_approval":True,
            "source":"AI staff deliverables from CEO-assigned News Update tasks"}
