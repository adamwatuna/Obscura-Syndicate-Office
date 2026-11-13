"""Learning Academy: one indexed knowledge catalog shared by Meita and internal AI staff.

This is scoped retrieval at execution time, NOT model fine-tuning or permanent
training. Uploaded bytes remain in the existing authenticated Meita vault.
"""
import re
import secrets
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field
from backend import references as references

MAX_LIBRARY_ITEMS = 300
STOP = {
    "about", "after", "before", "content", "create", "creative", "deliver",
    "details", "draft", "from", "have", "ideas", "make", "need", "please",
    "prepare", "project", "reference", "references", "staff", "task", "team",
    "that", "their", "this", "using", "with", "work", "your", "yang", "untuk",
    "buat", "dengan", "dalam", "boleh", "saya", "awak", "kami", "meita",
    "kepada", "semua", "baru", "akan", "lagi", "bagi", "berikan", "satu",
    "hasilkan", "seperti", "lain", "idea", "vision", "based",
}


class LibraryReferenceEdit(BaseModel):
    title: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=3000)
    tags: str = Field(default="", max_length=240)
    scope: str = Field(default="all", max_length=140)
    archived: bool = False


def init(db):
    references.init(db)
    db.execute("ALTER TABLE meita_references ADD COLUMN IF NOT EXISTS academy_title TEXT NOT NULL DEFAULT ''")
    db.execute("ALTER TABLE meita_references ADD COLUMN IF NOT EXISTS academy_notes TEXT NOT NULL DEFAULT ''")
    db.execute("ALTER TABLE meita_references ADD COLUMN IF NOT EXISTS academy_tags TEXT NOT NULL DEFAULT ''")
    db.execute("ALTER TABLE meita_references ADD COLUMN IF NOT EXISTS academy_scope TEXT NOT NULL DEFAULT 'all'")
    db.execute("ALTER TABLE meita_references ADD COLUMN IF NOT EXISTS academy_archived BOOLEAN NOT NULL DEFAULT FALSE")
    db.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS source_message_id BIGINT")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS academy_vision_message_unique
        ON lessons(source_message_id) WHERE source_message_id IS NOT NULL""")


def _scope_valid(db, scope):
    if scope == "all":
        return True
    if scope.startswith("department:"):
        department = scope[len("department:"):]
        return bool(department) and bool(db.execute(
            "SELECT 1 FROM staff WHERE department=%s LIMIT 1",
            (department,)).fetchone())
    if scope.startswith("staff:"):
        staff_id = scope[len("staff:"):]
        return bool(staff_id) and bool(db.execute(
            "SELECT 1 FROM staff WHERE id=%s LIMIT 1", (staff_id,)).fetchone())
    return False


def update_reference(db, ident, body):
    init(db)
    if not _scope_valid(db, body.scope):
        raise HTTPException(400, "Choose an existing department or staff member")
    updated = db.execute("""UPDATE meita_references SET
        academy_title=%s,academy_notes=%s,academy_tags=%s,academy_scope=%s,
        academy_archived=%s WHERE id=%s RETURNING id""",
        (body.title.strip(),body.notes.strip(),body.tags.strip(),body.scope,
         body.archived,ident)).fetchone()
    if not updated:
        raise HTTPException(404,"Reference not found")
    return {"id":ident,"ok":True}


def save_vision_from_chat(db, message_id):
    """CEO explicitly promotes an earlier CEO chat instruction to the staff library."""
    init(db)
    row = db.execute("""SELECT id,content FROM meita_messages
        WHERE id=%s AND role='user'""",(message_id,)).fetchone()
    if not row:
        raise HTTPException(404,"CEO instruction not found")
    existing=db.execute("""SELECT id FROM lessons
        WHERE source_message_id=%s""",(message_id,)).fetchone()
    if existing:
        return {"id":existing["id"],"created":False}
    ident=secrets.token_hex(12)
    title="CEO vision · "+" ".join(row["content"].split())[:142]
    db.execute("""INSERT INTO lessons
        (id,title,kind,source,assigned,status,created,source_message_id)
        VALUES (%s,%s,'CEO Vision',%s,'All AI Staff','CEO guidance',%s,%s)
        ON CONFLICT DO NOTHING""",
        (ident,title[:200],row["content"],int(time.time()),message_id))
    saved=db.execute("""SELECT id FROM lessons
        WHERE source_message_id=%s""",(message_id,)).fetchone()
    return {"id":saved["id"],"created":saved["id"]==ident}


def _search_matches(query,item):
    q=query.casefold().strip()
    if not q:
        return True
    hay=" ".join(str(x or "") for x in (
        item.get("title"),item.get("filename"),item.get("notes"),
        item.get("tags"),item.get("source_preview"),item.get("source_url"),
    )).casefold()
    return all(piece in hay for piece in q.split()[:7])


def library(db, search="", kind="all", limit=250, include_archived=False):
    init(db)
    limit=min(max(1,int(limit)),MAX_LIBRARY_ITEMS)
    items=[]
    rows=db.execute("""SELECT r.id,r.filename,r.kind,r.mime_type,r.source_url,
        r.size_bytes,r.created_at,r.academy_title,r.academy_notes,
        r.academy_tags,r.academy_scope,r.academy_archived,
        (SELECT LEFT(m.content,260) FROM meita_messages m
         WHERE m.role='user' AND m.reference_ids @> jsonb_build_array(r.id)
         ORDER BY m.created DESC,m.id DESC LIMIT 1) AS linked_vision,
        (SELECT COUNT(*) FROM tasks t
         WHERE t.deleted_at IS NULL AND t.reference_ids @> jsonb_build_array(r.id))
         AS linked_tasks
        FROM meita_references r
        ORDER BY r.created_at DESC LIMIT %s""",(limit,)).fetchall()
    for row in rows:
        r=dict(row)
        if r["academy_archived"] and not include_archived:
            continue
        item={"id":r["id"],"type":"reference","kind":r["kind"],
              "title":r["academy_title"] or r["filename"],
              "filename":r["filename"],"mime_type":r["mime_type"],
              "source_url":r["source_url"],"size_bytes":r["size_bytes"],
              "notes":r["academy_notes"],"tags":r["academy_tags"],
              "scope":r["academy_scope"],"archived":r["academy_archived"],
              "linked_vision":r["linked_vision"],"linked_tasks":r["linked_tasks"],
              "created_at":r["created_at"]}
        if kind in ("all","reference",r["kind"]) and _search_matches(search,item):
            items.append(item)
    projects=db.execute("""SELECT p.id,p.title,p.brief,p.created_at,
        p.reference_ids,(SELECT COUNT(*) FROM coo_jobs j
        WHERE j.proposal_id=p.id) AS linked_tasks
        FROM coo_proposals p WHERE p.chat_message_id IS NOT NULL
        ORDER BY p.created_at DESC LIMIT %s""",(limit,)).fetchall()
    for p in projects:
        item={"id":p["id"],"type":"project","kind":"CEO Project Vision",
              "title":p["title"],"source_preview":p["brief"][:1000],
              "notes":"","tags":"","scope":"all",
              "reference_ids":p["reference_ids"],"linked_tasks":p["linked_tasks"],
              "created_at":p["created_at"]}
        if kind in ("all","project") and _search_matches(search,item):
            items.append(item)
    rows=db.execute("""SELECT id,title,kind,source,assigned,status,created,
        source_message_id FROM lessons ORDER BY created DESC LIMIT %s""",(limit,)).fetchall()
    for lesson in rows:
        r=dict(lesson)
        item={"id":r["id"],"type":"lesson","kind":r["kind"],
              "title":r["title"],"source_preview":r["source"][:1000],
              "notes":"","tags":"","scope":r["assigned"] or "all",
              "status":r["status"],"source_message_id":r["source_message_id"],
              "created_at":r["created"]}
        if kind in ("all","lesson",r["kind"]) and _search_matches(search,item):
            items.append(item)
    items.sort(key=lambda x:str(x.get("created_at") or ""),reverse=True)
    return {"items":items[:limit],"total":len(items)}


def _keywords(text):
    return {word for word in re.findall(r"[^\W_]{4,}",str(text or "").casefold())
            if word not in STOP and not word.isnumeric()}


def _score(query, title, tags, notes, body):
    terms=_keywords(query)
    if not terms:
        return 0
    title_terms=_keywords(title)
    tag_terms=_keywords(tags)
    note_terms=_keywords(notes)
    body_terms=_keywords(body[:7000])
    return sum(7 for t in terms if t in title_terms)+sum(
        6 for t in terms if t in tag_terms)+sum(
        3 for t in terms if t in note_terms)+sum(
        1 for t in terms if t in body_terms)


def _eligible(scope, staff):
    if staff is None or scope in ("all","All AI Staff",""):
        return True
    return (scope == "staff:"+staff.get("id","") or
            scope == "department:"+staff.get("department","") or
            scope in (staff.get("name"),staff.get("department")))


def relevant(db, query, staff=None, exclude_ids=(), limit=3, include_visual=True):
    """Deterministic relevance; never sends the whole library into every AI call."""
    init(db)
    rows=db.execute("""SELECT id,filename,kind,source_url,extracted_text,preview,
        academy_title,academy_notes,academy_tags,academy_scope
        FROM meita_references WHERE academy_archived=FALSE
        ORDER BY created_at DESC LIMIT 250""").fetchall()
    omit=set(exclude_ids or [])
    candidates=[]
    for row in rows:
        r=dict(row)
        if r["id"] in omit or not _eligible(r["academy_scope"],staff):
            continue
        score=_score(query,r["academy_title"] or r["filename"],
                     r["academy_tags"],r["academy_notes"],r["extracted_text"])
        if score>=5:
            candidates.append((score,r))
    candidates.sort(key=lambda item:item[0],reverse=True)
    selected=[r for _,r in candidates[:limit]]
    texts=[]
    for row in selected:
        text=("LIBRARY REFERENCE "+row["id"]+" ("+row["kind"]+") — "+
              (row["academy_title"] or row["filename"]))
        if row["academy_notes"]:
            text+="\nCEO guidance: "+row["academy_notes"][:850]
        if row["source_url"]:
            text+="\nSource URL: "+row["source_url"]
        text+="\nSource excerpt (untrusted): "+row["extracted_text"][:1200]
        texts.append(text)
    # Only saved textual notes and explicitly promoted visions are usable as
    # source content. Do not claim that YouTube or linked pages were ingested.
    lessons=db.execute("""SELECT title,kind,source,assigned FROM lessons
        WHERE kind IN ('Note','CEO Vision') ORDER BY created DESC LIMIT 150""").fetchall()
    ranked=[]
    for lesson in lessons:
        if not _eligible(lesson["assigned"] or "",staff):
            continue
        score=_score(query,lesson["title"],"",lesson["source"],lesson["source"])
        if score>=5:
            ranked.append((score,lesson))
    ranked.sort(key=lambda x:x[0],reverse=True)
    for _,row in ranked[:2]:
        texts.append("ACADEMY "+row["kind"]+" — "+row["title"]+
                     "\nCEO-provided guidance (untrusted): "+row["source"][:1100])
    return {"reference_ids":[r["id"] for r in selected],
            "context":"\n\n".join(texts)[:4500],
            "images":references.images(db,[r["id"] for r in selected],limit=2)
                     if include_visual and selected else []}
