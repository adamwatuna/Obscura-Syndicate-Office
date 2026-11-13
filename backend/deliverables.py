"""Centralized AI staff submissions and genuine download-only assets.

Every workforce execution is already persisted in workforce_runs. This module
organizes those existing records by status and staff; it never simulates media.
An image/video/document attachment becomes downloadable only when actual bytes
have been uploaded or generated.
"""
import io
import re
import secrets
import zipfile
from datetime import datetime
from pathlib import PurePath

from fastapi import HTTPException

MAX_ASSET_BYTES=20*1024*1024
MIME={
 "png":"image/png","jpg":"image/jpeg","jpeg":"image/jpeg","webp":"image/webp",
 "mp4":"video/mp4","mov":"video/quicktime","webm":"video/webm",
 "pptx":"application/vnd.openxmlformats-officedocument.presentationml.presentation",
 "pdf":"application/pdf",
 "docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
KIND={
 "png":"image","jpg":"image","jpeg":"image","webp":"image",
 "mp4":"video","mov":"video","webm":"video",
 "pptx":"presentation","pdf":"document","docx":"document",
}

def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS workforce_assets (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES workforce_runs(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        kind TEXT NOT NULL,
        media_type TEXT NOT NULL,
        content BYTEA NOT NULL,
        size_bytes BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    db.execute("CREATE INDEX IF NOT EXISTS workforce_assets_by_run ON workforce_assets(run_id,created_at)")


def safe_filename(filename):
    name=PurePath((filename or "submitted-file").replace("\\","/")).name
    name=re.sub(r"[\x00-\x1f\x7f\"\\]", "",name).strip()[:120]
    return name or "submitted-file"


def check_file(name,data):
    extension=name.rsplit(".",1)[-1].lower() if "." in name else ""
    if extension not in MIME:
        raise HTTPException(400,"Supported output formats: PNG, JPG, WEBP, PDF, DOCX, PPTX, MP4, MOV and WEBM")
    if not data or len(data)>MAX_ASSET_BYTES:
        raise HTTPException(413,"Output file must be non-empty and no larger than 20 MB")
    if extension=="png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400,"Image signature does not match PNG")
    if extension in ("jpg","jpeg") and not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400,"Image signature does not match JPEG")
    if extension=="webp" and not (data[:4]==b"RIFF" and data[8:12]==b"WEBP"):
        raise HTTPException(400,"Image signature does not match WEBP")
    if extension=="pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(400,"Document signature does not match PDF")
    if extension in ("mov","mp4") and data[4:8]!=b"ftyp":
        raise HTTPException(400,"Video signature does not match MP4/MOV")
    if extension=="webm" and not data.startswith(b"\x1a\x45\xdf\xa3"):
        raise HTTPException(400,"Video signature does not match WEBM")
    if extension in ("pptx","docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names=set(archive.namelist())
                expected="ppt/presentation.xml" if extension=="pptx" else "word/document.xml"
                if expected not in names or len(names)>4000:
                    raise HTTPException(400,"Office document format is invalid")
        except (zipfile.BadZipFile,OSError,ValueError):
            raise HTTPException(400,"Office document format is invalid")
    return extension


def save_asset(db,run_id,filename,data):
    init(db)
    run=db.execute("""SELECT id,status FROM workforce_runs
        WHERE id=%s""",(run_id,)).fetchone()
    if not run:
        raise HTTPException(404,"AI staff deliverable not found")
    if run["status"]=="Abandoned":
        raise HTTPException(409,"Abandoned deliverables cannot receive new assets")
    name=safe_filename(filename)
    ext=check_file(name,data)
    ident=secrets.token_hex(12)
    db.execute("""INSERT INTO workforce_assets
        (id,run_id,filename,kind,media_type,content,size_bytes)
        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (ident,run_id,name,KIND[ext],MIME[ext],data,len(data)))
    return {"id":ident,"run_id":run_id,"filename":name,
            "kind":KIND[ext],"size_bytes":len(data),"media_type":MIME[ext]}


def list_deliverables(db,limit=250):
    init(db)
    rows=db.execute("""SELECT r.id,r.task_id,r.task_title,r.staff_id,r.staff_name,
      r.status,r.created_at,r.reviewed_at,r.model,r.web_searched,
      LEFT(r.result,440) AS excerpt,
      COALESCE(s.role,'AI Staff') AS staff_role,
      COALESCE(s.department,'Unassigned') AS department,
      t.description AS task_description,t.status AS task_status,
      t.deleted_at IS NOT NULL AS archived_task,
      j.proposal_id AS meita_project_id
      FROM workforce_runs r
      LEFT JOIN staff s ON s.id=r.staff_id
      LEFT JOIN tasks t ON t.id=r.task_id
      LEFT JOIN LATERAL (
          SELECT proposal_id FROM coo_jobs
          WHERE workforce_run_id=r.id LIMIT 1
      ) j ON TRUE
      WHERE r.status<>'Abandoned'
      ORDER BY r.created_at DESC,r.id DESC LIMIT %s""",(limit,)).fetchall()
    ids=[r["id"] for r in rows]
    assets={}
    if ids:
        for a in db.execute("""SELECT id,run_id,filename,kind,media_type,size_bytes,created_at
            FROM workforce_assets WHERE run_id=ANY(%s) ORDER BY created_at""",(ids,)).fetchall():
            assets.setdefault(a["run_id"],[]).append(dict(a))
    return {"items":[{**dict(r),"assets":assets.get(r["id"],[])} for r in rows]}


def get_deliverable(db,run_id):
    init(db)
    row=db.execute("""SELECT r.*,s.role AS staff_role,s.department,
        t.description AS task_description
        FROM workforce_runs r
        LEFT JOIN staff s ON s.id=r.staff_id
        LEFT JOIN tasks t ON t.id=r.task_id
        WHERE r.id=%s AND r.status<>'Abandoned'""",(run_id,)).fetchone()
    if not row:raise HTTPException(404,"Deliverable not found")
    return dict(row)


def get_asset(db,run_id,asset_id):
    init(db)
    row=db.execute("""SELECT filename,kind,media_type,content,size_bytes
        FROM workforce_assets WHERE run_id=%s AND id=%s""",
        (run_id,asset_id)).fetchone()
    if not row:raise HTTPException(404,"Output file not found")
    return dict(row)


def presentation(report):
    """Real editable slides generated from an AI staff's stored written work."""
    from pptx import Presentation
    from pptx.util import Inches,Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    prs=Presentation()
    prs.slide_width=Inches(13.33);prs.slide_height=Inches(7.5)
    dark=RGBColor(26,46,68);muted=RGBColor(75,100,123)
    def textbox(slide,text,x,y,w,h,size=19,color=dark):
        shape=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
        frame=shape.text_frame;frame.word_wrap=True
        frame.clear();frame.paragraphs[0].text=text
        for p in frame.paragraphs:
            for run in p.runs:run.font.name="Aptos";run.font.size=Pt(size);run.font.color.rgb=color
    first=prs.slides.add_slide(prs.slide_layouts[6])
    textbox(first,"AI PRIVATE OFFICE / STAFF DELIVERY",.75,.6,11,.5,17,muted)
    textbox(first,report["task_title"],.75,1.7,11.5,2.0,31)
    textbox(first,(report["staff_role"] or "AI Staff")+" · "+report["staff_name"],.75,4.35,11.3,.6,19,muted)
    textbox(first,"Generated from the actual stored AI report; review all claims before publishing.",.75,6.3,11.6,.6,13,muted)
    content=(report.get("result") or "").strip()
    # Split on paragraph boundaries; bound slide text to avoid overflow.
    lines=content.splitlines() or [content]
    chunks=[];chunk=""
    for item in lines:
        if len(chunk)+len(item)+1>650 and chunk:
            chunks.append(chunk);chunk=""
        for start in range(0,len(item),650):
            part=item[start:start+650]
            if len(chunk)+len(part)+1>650 and chunk:
                chunks.append(chunk);chunk=""
            chunk+=part+"\n"
    if chunk:chunks.append(chunk)
    for i,chunk in enumerate(chunks[:32]):
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        textbox(slide,report["task_title"][:78],.65,.45,11.8,.7,23)
        textbox(slide,chunk,.7,1.55,11.9,4.9,16)
        textbox(slide,f"Page {i+1}/{min(32,len(chunks))} · Source: stored AI staff report",.7,6.85,11,.25,11,muted)
    out=io.BytesIO();prs.save(out);out.seek(0)
    return out
