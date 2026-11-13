import os, json, hmac, hashlib, secrets, time, urllib.request, urllib.error, asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse, Response
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from starlette.middleware.sessions import SessionMiddleware
from backend import memory as office_memory
from backend import workforce as office_workforce
from backend import delegation as office_delegation
from backend import office_db
from backend import organization as org
from backend import monthly
from backend import task_schedule
from backend import news_briefings
from backend import meita_team
from backend import meita_chat_delegate
from backend import meita_autowork, meita_decks
from backend import social, social_publish
from backend import references as office_references

ROOT=Path(__file__).resolve().parent.parent
PASSWORD=os.environ.get('CEO_PASSWORD')
SECRET=os.environ.get('SESSION_SECRET')
if not PASSWORD or not SECRET or len(SECRET)<32:
    raise RuntimeError('Set CEO_PASSWORD and SESSION_SECRET (at least 32 characters) before starting')
# Database initialization is atomic and imports any accessible legacy SQLite in read-only mode.
office_db.bootstrap()
connect=office_db.connect

@asynccontextmanager
async def lifespan(app):
    # The database bootstrap has already generated any missing current-month tasks.
    # This clock keeps assignments recurring even if the CEO does not open the UI.
    ticker=asyncio.create_task(monthly.monthly_clock(connect))
    assistant_worker=asyncio.create_task(meita_autowork.autonomous_worker(connect))
    try:
        yield
    finally:
        ticker.cancel()
        assistant_worker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker
        with suppress(asyncio.CancelledError):
            await assistant_worker

app=FastAPI(title='AI Private Office',docs_url=None,redoc_url=None,lifespan=lifespan)
app.add_middleware(SessionMiddleware,secret_key=SECRET,https_only=os.environ.get('COOKIE_SECURE','true').lower()=='true',same_site='lax',max_age=28800)

class Login(BaseModel):password:str
class SocialGrant(BaseModel):
    staff_id: str = Field(min_length=1,max_length=120)
    account_id: str = Field(min_length=1,max_length=120)
    mode: str
class SocialDraft(BaseModel):
    staff_id: str = Field(min_length=1,max_length=120)
    account_id: str = Field(min_length=1,max_length=120)
    caption: str = Field(min_length=1,max_length=2200)
    media_url: str | None = None
class SocialRunDraft(BaseModel):
    run_id: str = Field(min_length=1,max_length=120)
    account_id: str = Field(min_length=1,max_length=120)
    media_url: str | None = None
class SocialDraftEdit(BaseModel):
    caption: str = Field(min_length=1,max_length=2200)
    media_url: str | None = None

class Staff(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=120)
    department: str = Field(min_length=1, max_length=100)
    expertise: str = Field(default='', max_length=16000)
    tools: str = Field(default='', max_length=2000)
    autonomy: str = Field(default='Approval required', max_length=100)
    org_role: str | None = None
    manager_id: str | None = None
    delegation_permission: str | None = None
class Task(BaseModel):
    title: str = Field(min_length=1,max_length=250)
    description: str = Field(default='',max_length=3000)
    category: str = 'General'
    assignee: str = ''
    priority: str = 'Normal'
    due_date: str | None = None
    recurrence: str = 'None'
class Lesson(BaseModel):title:str=Field(min_length=1,max_length=200);kind:str;source:str=Field(min_length=1,max_length=3000);assigned:str=''
class Status(BaseModel):status:str
class MeitaMessage(BaseModel):
    message: str = Field(min_length=1,max_length=6000)
    reference_ids: list[str] = Field(default_factory=list,max_length=6)

class ReferenceUrl(BaseModel):
    url: str = Field(min_length=10,max_length=1800)

def guard(req:Request):
    if req.session.get('authorized') is not True:raise HTTPException(401,'Login required')
def audit(db,action,detail):db.execute('INSERT INTO audit(action,detail,created) VALUES(%s,%s,%s)',(action,detail,int(time.time())))
@app.post('/api/login')
def login(body:Login,req:Request):
    if not hmac.compare_digest(body.password.encode(),PASSWORD.encode()):
        time.sleep(.35)
        raise HTTPException(401,'Invalid credentials')
    req.session.clear();req.session['authorized']=True
    return {'ok':True}
@app.post('/api/logout')
def logout(req:Request):req.session.clear();return {'ok':True}
@app.get('/api/me')
def me(req:Request):return {'authenticated':req.session.get('authorized') is True}
@app.get('/api/office',dependencies=[Depends(guard)])
def office():
    with connect() as db:
        monthly.generate_month(db)
        task_schedule.generate_due(db)
        result = {'staff': [dict(x) for x in db.execute(
            'SELECT id,name,role,department,status,org_role,manager_id,delegation_permission,portrait_slot FROM staff ORDER BY department,name')]}
        result['tasks']=[dict(x) for x in db.execute("""SELECT t.*,
            EXISTS(SELECT 1 FROM monthly_assignments m WHERE m.task_id=t.id) AS is_monthly,
            EXISTS(SELECT 1 FROM task_series ts WHERE ts.origin_task_id=t.id
                AND ts.id=t.series_id) AS is_series_origin
            FROM tasks t WHERE t.deleted_at IS NULL
            ORDER BY created DESC,id DESC LIMIT 1000""")]
        for table in ('lessons','audit'):
            result[table]=[dict(x) for x in db.execute(
                'SELECT * FROM '+table+' ORDER BY created DESC,id DESC')]
        return result
@app.get('/api/organization',dependencies=[Depends(guard)])
def get_organization():
    with connect() as db:
        return org.summary(db)

@app.post('/api/staff',dependencies=[Depends(guard)])
def create_staff(body:Staff):
    if not all((body.name.strip(),body.role.strip(),body.department.strip())):
        raise HTTPException(400,'Name, title and department are required')
    ident=secrets.token_hex(12)
    with connect() as db:
        hierarchy=org.validate_staff(db,body)
        db.execute("""INSERT INTO staff
            (id,name,role,department,expertise,tools,autonomy,status,
             org_role,manager_id,delegation_permission)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'Available',%s,%s,%s)""",
            (ident,body.name.strip(),body.role.strip(),hierarchy['department'],
             body.expertise,body.tools,body.autonomy,hierarchy['org_role'],
             hierarchy['manager_id'],hierarchy['delegation_permission']))
        org.after_staff_saved(db,ident)
        audit(db,'staff.hired',f'{ident}: {body.name.strip()} ({hierarchy["org_role"]})')
    return {'id':ident}

@app.get('/api/staff/{ident}/configuration',dependencies=[Depends(guard)])
def get_staff_configuration(ident:str):
    # Private expertise/instructions available only after CEO session validation.
    with connect() as db:
        row=db.execute('SELECT '+org.STAFF_COLUMNS+' FROM staff WHERE id=%s',
                       (ident,)).fetchone()
        if row is None:raise HTTPException(404,'Staff not found')
        return {'staff':dict(row)}

@app.put('/api/staff/{ident}',dependencies=[Depends(guard)])
def update_staff(ident:str,body:Staff):
    if not all((body.name.strip(),body.role.strip(),body.department.strip())):
        raise HTTPException(400,'Name, title and department are required')
    with connect() as db:
        existing=db.execute('SELECT * FROM staff WHERE id=%s FOR UPDATE',(ident,)).fetchone()
        if not existing:raise HTTPException(404,'Staff not found')
        old=dict(existing)
        hierarchy=org.validate_staff(db,body,ident,old)
        db.execute("""UPDATE staff SET name=%s,role=%s,department=%s,
            expertise=%s,tools=%s,autonomy=%s,org_role=%s,
            manager_id=%s,delegation_permission=%s WHERE id=%s""",
            (body.name.strip(),body.role.strip(),hierarchy['department'],
             body.expertise,body.tools,body.autonomy,hierarchy['org_role'],
             hierarchy['manager_id'],hierarchy['delegation_permission'],ident))
        org.after_staff_saved(db,ident,old)
        audit(db,'staff.updated',f'{ident}: {old["name"]} -> {body.name.strip()}')
    return {'ok':True}

@app.delete('/api/staff/{ident}',dependencies=[Depends(guard)])
def delete_staff(ident:str):
    with connect() as db:
        existing=db.execute('SELECT * FROM staff WHERE id=%s FOR UPDATE',(ident,)).fetchone()
        if existing is None:raise HTTPException(404,'Staff not found')
        reports=db.execute('UPDATE staff SET manager_id=NULL WHERE manager_id=%s',(ident,)).rowcount
        org.cancel_director_plans(db,ident)
        org.cancel_staff_jobs(db,ident)
        unassigned=db.execute("UPDATE tasks SET assignee='' WHERE assignee=%s",
                              (ident,)).rowcount
        job_count=0
        if db.execute("SELECT to_regclass('public.coo_jobs') AS tbl").fetchone()['tbl'] is not None:
            job_count=db.execute("""UPDATE coo_jobs SET status='Cancelled',
                updated_at=NOW() WHERE staff->>'id'=%s
                AND status IN ('Approved','Retry available')""",(ident,)).rowcount
        db.execute('DELETE FROM staff WHERE id=%s',(ident,))
        audit(db,'staff.deleted',f'{ident}: {existing["name"]}; unassigned_tasks={unassigned}; direct_reports_unassigned={reports}')
    return {'ok':True,'unassigned_tasks':unassigned,'cancelled_jobs':job_count,
            'direct_reports_unassigned':reports}

@app.post('/api/tasks',dependencies=[Depends(guard)])
def create_task(body:Task):
    with connect() as db:
        result=task_schedule.create(db,body)
        audit(db,'task.created',f'{result["id"]}: {body.title}')
    return result

@app.put('/api/tasks/{ident}',dependencies=[Depends(guard)])
def edit_task(ident:str,body:Task):
    with connect() as db:
        result=task_schedule.update(db,ident,body)
        audit(db,'task.updated',f'{ident}: {body.title}')
    return result

@app.delete('/api/tasks/{ident}',dependencies=[Depends(guard)])
def remove_task(ident:str):
    with connect() as db:
        result=task_schedule.remove(db,ident)
        audit(db,'task.removed',f'{ident}: recurrence_stopped={result["recurrence_stopped"]}')
    return result

@app.patch('/api/tasks/{ident}',dependencies=[Depends(guard)])
def update_task(ident:str,body:Status):
    if body.status not in ('To do','In progress','For approval','Completed'):raise HTTPException(400,'Invalid status')
    with connect() as db:
        result=db.execute('UPDATE tasks SET status=%s WHERE id=%s AND deleted_at IS NULL',(body.status,ident))
        if not result.rowcount:raise HTTPException(404,'Task not found')
        audit(db,'task.status',f'{ident}: {body.status}')
    return {'ok':True}
@app.post('/api/lessons',dependencies=[Depends(guard)])
def create_lesson(body:Lesson):
    if body.kind not in ('YouTube link','Website link','Note'):raise HTTPException(400,'Invalid lesson type')
    if body.kind!='Note' and not body.source.startswith(('https://','http://')):raise HTTPException(400,'A valid URL is required')
    ident=secrets.token_hex(12)
    with connect() as db:
        db.execute('INSERT INTO lessons VALUES(%s,%s,%s,%s,%s,%s,%s)',(ident,body.title,body.kind,body.source,body.assigned,'Pending review',int(time.time())))
        audit(db,'academy.added',body.title)
    return {'id':ident}
@app.get('/api/capabilities',dependencies=[Depends(guard)])
def capabilities():
    return {'working':['Password-protected CEO session','Director-led staff hierarchy','Scoped internal AI delegation','AI-generated internal task drafts','CEO review','PostgreSQL records and memory','Knowledge source registry','Audit log'], 'planned':['MFA and multi-user roles','YouTube transcript ingestion','File processing','External-app OAuth integrations','Accounting engine','Independent staff identity and external action permissions']}
@app.get('/')
def home():return FileResponse(ROOT/'frontend'/'index.html')


@app.post('/api/meita/references/upload',dependencies=[Depends(guard)])
async def upload_meita_reference(file:UploadFile=File(...)):
    data=await file.read(office_references.MAX_BYTES+1)
    await file.close()
    with connect() as db:
        return office_references.save_file(db,file.filename or 'reference',data)

@app.post('/api/meita/references/link',dependencies=[Depends(guard)])
def link_meita_reference(body:ReferenceUrl):
    with connect() as db:
        return office_references.save_url(db,body.url)

@app.get('/api/meita/references',dependencies=[Depends(guard)])
def list_meita_references():
    with connect() as db:
        return {'references':office_references.metadata(db)}

@app.get('/api/meita/references/{ident}/content',dependencies=[Depends(guard)])
def get_meita_reference_content(ident:str,preview:bool=False):
    with connect() as db:
        row=db.execute("""SELECT filename,mime_type,content,preview,source_url
            FROM meita_references WHERE id=%s""",(ident,)).fetchone()
    if not row:raise HTTPException(404,'Reference not found')
    data=bytes(row['preview'] if preview and row['preview'] else row['content'] or b'')
    if not data:raise HTTPException(404,'This reference has no downloadable file')
    mime='image/jpeg' if preview and row['preview'] else row['mime_type']
    return Response(content=data,media_type=mime,headers={
        'Cache-Control':'private, no-store',
        'Content-Disposition':'inline' if preview else 'attachment; filename="reference"',
        'X-Content-Type-Options':'nosniff'})

@app.get('/api/meita/live-activity',dependencies=[Depends(guard)])
def meita_live_activity():
    with connect() as db:
        meita_autowork.init(db)
        rows=db.execute("""SELECT j.id,j.task_id,j.title,j.status,j.started_at,j.updated_at,
            j.staff->>'id' AS staff_id,j.staff->>'name' AS staff_name,
            j.proposal_id,p.title AS project_title,t.status AS task_status
            FROM coo_jobs j JOIN coo_proposals p ON p.id=j.proposal_id
            LEFT JOIN tasks t ON t.id=j.task_id
            ORDER BY j.created_at DESC LIMIT 250""").fetchall()
    return {'jobs':[dict(r) for r in rows]}

@app.get('/api/meita/history',dependencies=[Depends(guard)])
def meita_history():
    with connect() as db:
        office_delegation.init(db)
        rows=db.execute("""SELECT m.id,m.role,m.content,m.created,
            p.id AS proposal_id,p.status AS proposal_status,
            p.title AS proposal_title,p.plan AS proposal_plan,
            p.auto_execute AS proposal_automatic,m.reference_ids AS reference_ids,
            FROM meita_messages m
            LEFT JOIN coo_proposals p ON p.chat_message_id=m.id
            ORDER BY m.created DESC,m.id DESC LIMIT 30""").fetchall()
    messages=[]
    for r in reversed(rows):
        message={k:r[k] for k in ("id","role","content","created","reference_ids")}
        if r["proposal_id"]:
            message["proposal"]={"id":r["proposal_id"],
                "status":r["proposal_status"],
                "title":r["proposal_title"],
                "plan":r["proposal_plan"],
                "automatic":r["proposal_automatic"]}
        messages.append(message)
    return {"messages":messages}

@app.post('/api/meita/chat',dependencies=[Depends(guard)])
def meita_chat(body:MeitaMessage):
    with connect() as db:
        office_delegation.init(db)
        refs=office_references.validate_ids(db,body.reference_ids)
        reference_context=office_references.summaries(db,body.reference_ids,max_chars=5500)
        reference_images=office_references.images(db,body.reference_ids)
        history=list(reversed(db.execute("""SELECT role,content FROM meita_messages
            ORDER BY created DESC,id DESC LIMIT 12""").fetchall()))
        leadership=[dict(x) for x in db.execute("""SELECT name,role,department
            FROM staff WHERE org_role='Director' ORDER BY department LIMIT 15""")]
        team_context=meita_team.latest_summary(db)
        config=meita_team.settings(db)
        recent=[{"title":p["title"],"brief":p["brief"][:900],
                 "status":p["status"]}
                for p in db.execute("""SELECT title,brief,status FROM coo_proposals
                    ORDER BY created_at DESC LIMIT 3""").fetchall()]
    memories=office_memory.recall(body.message)
    routed=meita_chat_delegate.respond(
        body.message,history,leadership,config,team_context,memories,recent,
        reference_context=reference_context,reference_images=reference_images)
    proposal_plan=None
    if routed["intent"]=="delegate":
        with connect() as db:
            current_settings=meita_team.settings(db)
            eligible={p["id"] for p in meita_team.available_staff(
                db,current_settings["delegation_scope"])}
            staff=[dict(p) for p in db.execute("""SELECT * FROM staff
                WHERE status='Available' ORDER BY department,name""")
                if p["id"] in eligible]
        if not staff:
            raise HTTPException(400,
                "Meita has no eligible available AI staff for this assignment")
        proposal_plan=office_delegation.prepare_plan(
            routed["delegation_brief"],staff,memories,
            max_jobs=current_settings["max_delegation_jobs"],
            reference_context=reference_context,reference_images=reference_images)
    with connect() as db:
        now=int(time.time())
        db.execute("""INSERT INTO meita_messages(role,content,created,reference_ids)
            VALUES ('user',%s,%s,%s)""",(body.message,now,Jsonb(body.reference_ids)))
        message=db.execute("""INSERT INTO meita_messages(role,content,created)
            VALUES ('assistant',%s,%s) RETURNING id""",
            (routed["reply"],now)).fetchone()
        proposal=None
        if proposal_plan:
            config=meita_team.settings(db)
            eligible={p["id"] for p in meita_team.available_staff(
                db,config["delegation_scope"])}
            latest=[dict(row) for row in db.execute(
                "SELECT * FROM staff WHERE status='Available'")
                if row["id"] in eligible]
            latest_ids={p["id"] for p in latest}
            if any(j["staff_id"] not in latest_ids for j in proposal_plan["jobs"]):
                raise HTTPException(409,
                    "Team configuration changed; please repeat the instruction")
            proposal=office_delegation.save_proposal(
                db,routed["delegation_brief"],proposal_plan,
                chat_message_id=message["id"],reference_ids=body.reference_ids)
            if config["auto_execute_internal"]:
                if os.getenv("MEITA_AUTO_WORKER_ENABLED","true").lower()!="true":
                    raise HTTPException(503,"Meita internal auto-worker is disabled")
                assigned=meita_autowork.auto_assign_chat(db,proposal["id"],latest)
                proposal.update({"status":"Approved","automatic":True,
                                 "assigned_tasks":assigned["jobs"]})
                routed["reply"]=(
                    f"Baik CEO. Saya dah agihkan {len(assigned['jobs'])} tugasan "
                    "kepada AI team dan masukkan ke execution queue. "
                    "Semak Task History untuk progress dan Deliverables untuk hasil.")
                db.execute("""UPDATE meita_messages SET content=%s WHERE id=%s""",
                           (routed["reply"],message["id"]))
                audit(db,"meita.chat.auto_assigned",proposal["id"])
            else:
                proposal["automatic"]=False
                audit(db,"meita.chat.delegation_proposed",proposal["id"])
        else:
            audit(db,"meita.chat","CEO chatted with Meita")
    return {"reply":routed["reply"],"model":routed["model"],
            "proposal":proposal}

@app.get('/api/meita/task-history',dependencies=[Depends(guard)])
def meita_task_history():
    with connect() as db:
        return meita_autowork.history(db)

@app.get('/api/meita/deliverables/{ident}',dependencies=[Depends(guard)])
def meita_delivery_slides(ident:str):
    with connect() as db:
        return meita_decks.from_db(db,ident)

@app.get('/api/meita/deliverables/{ident}/powerpoint',
         dependencies=[Depends(guard)])
def download_meita_powerpoint(ident:str):
    with connect() as db:
        deck=meita_decks.from_db(db,ident)
    stream=meita_decks.as_pptx(deck)
    safe="".join(c for c in deck["title"] if c.isalnum() or c in " -_")
    filename=(safe.strip() or "Meita-Executive-Delivery")[:65].replace(" ","-")
    return StreamingResponse(stream,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition":f'attachment; filename="{filename}.pptx"',
                 "Cache-Control":"no-store"})

@app.post('/api/meita/jobs/{ident}/retry',dependencies=[Depends(guard)])
def meita_retry_internal_job(ident:str):
    with connect() as db:
        meita_autowork.init(db)
        job=db.execute("""SELECT j.id,j.status,j.task_id,j.staff
            FROM coo_jobs j WHERE j.id=%s FOR UPDATE""",(ident,)).fetchone()
        if not job:raise HTTPException(404,"Meita task not found")
        if job["status"]!="Retry available":
            raise HTTPException(409,"Only confirmed failed AI jobs can be retried")
        current=db.execute("""SELECT 1 FROM staff WHERE id=%s
            AND status='Available'""",(job["staff"]["id"],)).fetchone()
        if not current:raise HTTPException(409,"Assigned AI staff is unavailable")
        db.execute("""UPDATE coo_jobs SET status='Queued',updated_at=NOW()
            WHERE id=%s""",(ident,))
        audit(db,'meita.autorun.retry_requested',ident)
    return {"ok":True,"status":"Queued"}

@app.get('/api/memory',dependencies=[Depends(guard)])
def list_memory():
    return office_memory.all_memories()

@app.post('/api/memory',dependencies=[Depends(guard)])
def add_memory(body:office_memory.MemoryInput):
    result=office_memory.add(body)
    with connect() as db: audit(db,'memory.created',body.title)
    return result

@app.put('/api/memory/{ident}',dependencies=[Depends(guard)])
def edit_memory(ident:str,body:office_memory.MemoryInput):
    result=office_memory.update(ident,body)
    with connect() as db: audit(db,'memory.updated',ident)
    return result

@app.delete('/api/memory/{ident}',dependencies=[Depends(guard)])
def delete_memory(ident:str):
    result=office_memory.delete(ident)
    with connect() as db: audit(db,'memory.deleted',ident)
    return result


@app.get('/api/workforce/runs',dependencies=[Depends(guard)])
def workforce_runs():
    return office_workforce.get_runs()

@app.post('/api/workforce/run/{task_id}',dependencies=[Depends(guard)])
def workforce_run(task_id:str):
    with connect() as db:
        record=db.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(task_id,)).fetchone()
        if record is None or record['deleted_at'] is not None:
            raise HTTPException(404,'Task not found')
        if record['ai_run_started'] or record['status'] in ('For approval','Completed'):
            raise HTTPException(409,'AI task already started or completed')
        if not record['assignee']:
            raise HTTPException(400,'Assign an AI staff member before running this task')
        if not record['description'].strip():
            raise HTTPException(400,'Add task description before running AI')
        staff=db.execute('SELECT * FROM staff WHERE id=%s',(record['assignee'],)).fetchone()
        if staff is None:raise HTTPException(404,'Assigned staff not found')
        task=dict(record);staff=dict(staff)
        old_status=task['status']
        task['brief']=task['description']
        monthly_note=monthly.detail_for_task(db,task_id)
        if monthly_note:
            task['brief']+='\nMonthly assignment context: '+monthly_note[-160:]
        if task.get('due_date'):
            task['brief']+='\nDue date (Malaysia): '+task['due_date'].isoformat()
        task['brief']+='\nPriority: '+task['priority']
        db.execute("UPDATE tasks SET ai_run_started=TRUE,status='In progress' WHERE id=%s",(task_id,))
    try:
        result=office_workforce.execute(task,staff)
    except Exception:
        # API/network failures should not lock an unexecuted task forever.
        with connect() as db:
            db.execute("""UPDATE tasks SET ai_run_started=FALSE,status=%s
                WHERE id=%s AND status='In progress'""",(old_status,task_id))
        raise
    with connect() as db:
        db.execute("UPDATE tasks SET status='For approval' WHERE id=%s",(task_id,))
        audit(db,'workforce.deliverable',task_id)
    return result

@app.post('/api/workforce/runs/{ident}/review',dependencies=[Depends(guard)])
def workforce_review(ident:str,body:Status):
    result=office_workforce.review(ident,body.status)
    with connect() as db: audit(db,'workforce.review',ident+': '+body.status)
    return result


@app.get('/api/meita/configuration',dependencies=[Depends(guard)])
def meita_configuration():
    with connect() as db:
        return {'settings':meita_team.settings(db),
                'team':[ {k:p[k] for k in ("id","name","role","department","org_role","status")}
                    for p in meita_team.available_staff(db,"all_staff")]}

@app.put('/api/meita/configuration',dependencies=[Depends(guard)])
def update_meita_configuration(body:meita_team.MeitaSettings):
    with connect() as db:
        result=meita_team.save_settings(db,body)
        audit(db,'meita.configuration.updated',
              f'delegation_scope={body.delegation_scope} discussion_scope={body.discussion_scope}')
    return result

@app.get('/api/meita/team/meetings',dependencies=[Depends(guard)])
def meita_team_meetings():
    with connect() as db:
        return meita_team.meeting_index(db)

@app.post('/api/meita/team/meetings',dependencies=[Depends(guard)])
def meita_create_meeting(body:meita_team.MeetingBrief):
    with connect() as db:
        outcome=meita_team.create_meeting(db,body.topic)
        audit(db,'meita.team.meeting_created',outcome['id'])
    return outcome

@app.get('/api/meita/team/meetings/{ident}',dependencies=[Depends(guard)])
def meita_meeting_details(ident:str):
    with connect() as db:
        return meita_team.meeting_details(db,ident)

@app.post('/api/meita/team/meetings/{ident}/discuss',dependencies=[Depends(guard)])
def meita_discuss_with_team(ident:str,body:meita_team.MeetingFollowup):
    return meita_team.run_round(connect,ident,body.instruction)

@app.get('/api/meita/team/meetings/{ident}/delegation-brief',
         dependencies=[Depends(guard)])
def meita_meeting_to_delegation(ident:str):
    with connect() as db:
        return {'brief':meita_team.proposal_brief_from_meeting(db,ident)}

@app.get('/api/delegation/proposals',dependencies=[Depends(guard)])
def delegation_proposals():
    return office_delegation.list_all()

@app.post('/api/delegation/proposals',dependencies=[Depends(guard)])
def delegation_propose(body:office_delegation.DelegationBrief):
    with connect() as db:
        config=meita_team.settings(db)
        selected=meita_team.available_staff(db,config['delegation_scope'])
        staff=[dict(x) for x in db.execute("""SELECT * FROM staff ORDER BY department,name""")]
        allowed={person['id'] for person in selected}
        staff=[person for person in staff if person['id'] in allowed]
    if not staff:
        raise HTTPException(400,'No eligible AI staff in Meita delegation configuration')
    memories=office_memory.recall(body.brief)
    result=office_delegation.propose(
        body.brief,staff,memories,max_jobs=config['max_delegation_jobs'])
    with connect() as db:
        audit(db,'delegation.proposed',result['id'])
    return result

@app.post('/api/delegation/proposals/{ident}/approve',dependencies=[Depends(guard)])
def delegation_approve(ident:str):
    with connect() as db:
        config=meita_team.settings(db)
        allowed={p["id"] for p in meita_team.available_staff(
            db,config["delegation_scope"])}
        staff=[dict(x) for x in db.execute("SELECT * FROM staff WHERE status='Available'")
               if x["id"] in allowed]
    result=office_delegation.decide(ident,True,staff)
    with connect() as db:
        audit(db,'delegation.approved',ident)
    return result

@app.post('/api/delegation/proposals/{ident}/reject',dependencies=[Depends(guard)])
def delegation_reject(ident:str):
    result=office_delegation.decide(ident,False,[])
    with connect() as db:
        audit(db,'delegation.rejected',ident)
    return result

@app.post('/api/delegation/jobs/{ident}/run',dependencies=[Depends(guard)])
def delegation_run(ident:str):
    result=office_delegation.run(ident)
    with connect() as db:
        audit(db,'delegation.executed',ident)
    return result


@app.get('/api/database/status',dependencies=[Depends(guard)])
def database_status():
    return office_db.status()


@app.get('/api/directors/plans',dependencies=[Depends(guard)])
def department_plans():
    with connect() as db:
        return org.list_plans(db)

@app.post('/api/directors/{ident}/plans',dependencies=[Depends(guard)])
def department_plan(ident:str,body:org.DirectorBrief):
    # A CEO request starts planning. Director scope is always checked server-side.
    with connect() as db:
        director,staff=org.propose(db,ident,body.brief)
    plan=org._openai_plan(director,staff,body.brief)
    with connect() as db:
        result=org.save_proposal(db,ident,body.brief,plan)
        audit(db,'director.plan.proposed',f'{ident}: {result["id"]} - {result["status"]}')
    return result

@app.post('/api/directors/plans/{ident}/approve',dependencies=[Depends(guard)])
def approve_department_plan(ident:str):
    with connect() as db:
        result=org.decide(db,ident,True)
        audit(db,'director.plan.approved',ident)
    return result

@app.post('/api/directors/plans/{ident}/reject',dependencies=[Depends(guard)])
def reject_department_plan(ident:str):
    with connect() as db:
        result=org.decide(db,ident,False)
        audit(db,'director.plan.rejected',ident)
    return result

@app.post('/api/directors/jobs/{ident}/run',dependencies=[Depends(guard)])
def run_department_job(ident:str):
    with connect() as db:
        task,staff=org.claim_job(db,ident)
    try:
        result=office_workforce.execute(task,staff)
    except Exception:
        with connect() as db:org.finish_job(db,ident)
        raise
    with connect() as db:
        org.finish_job(db,ident,result['id'])
        audit(db,'director.job.delivered',f'{ident}: {result["id"]}')
    return result


@app.get('/api/monthly',dependencies=[Depends(guard)])
def monthly_overview(period:str|None=None):
    with connect() as db:
        if not period or period==monthly.period_now():
            monthly.generate_month(db)
        return monthly.summary(db,period)

@app.post('/api/monthly/generate',dependencies=[Depends(guard)])
def monthly_generate_current():
    with connect() as db:
        outcome=monthly.generate_month(db)
        if outcome['created']:
            audit(db,'monthly.generated.manual',outcome['period'])
        return outcome

@app.put('/api/monthly/templates/{key}',dependencies=[Depends(guard)])
def monthly_edit_template(key:str,body:monthly.TemplateUpdate):
    with connect() as db:
        result=monthly.update_template(db,key,body)
        audit(db,'monthly.template.updated',key)
        return result


@app.get('/api/news-briefings',dependencies=[Depends(guard)])
def executive_news_briefings():
    """Authenticated HQ: actual web-sourced staff news deliverables for CEO review."""
    with connect() as db:
        return news_briefings.list_for_ceo(db)


@app.get('/api/social/status',dependencies=[Depends(guard)])
def social_status():
    with connect() as db:
        data=social.account_summary(db)
        data.update(social_publish.list_posts(db))
        return data

@app.post('/api/social/oauth/{provider}/start',dependencies=[Depends(guard)])
def social_oauth_start(provider:str,req:Request):
    with connect() as db:
        authorize=social.oauth_start(db,provider,req.session)
        audit(db,'social.oauth.started',provider)
    return {'authorization_url':authorize}

@app.get('/api/social/oauth/{provider}/callback')
def social_oauth_callback(provider:str,req:Request,
                          state:str|None=None,code:str|None=None,error:str|None=None):
    # Provider callback is a browser navigation; maintain CEO session + one-use state.
    try:
        # Consume and COMMIT the one-use state before provider network calls.
        # If token/channel discovery fails later, state must not roll back and
        # be reusable with the same authorization code.
        with connect() as db:
            social.consume_state(db,provider,state,req.session)
        if error:
            with connect() as db:
                audit(db,'social.oauth.denied',provider)
            return RedirectResponse('/?integration=cancelled',status_code=303)
        with connect() as db:
            added=social.oauth_complete(db,provider,code)
            audit(db,'social.oauth.connected',f'{provider}: {added} account(s)')
        return RedirectResponse('/?integration=connected',status_code=303)
    except HTTPException as exc:
        # Report only fixed, human-readable diagnostic categories; never put
        # authorization codes, provider payloads or secrets in URL parameters.
        reason=str(exc.detail)
        if provider=='google' and reason in (
            'google_token_exchange','youtube_channel_access','youtube_no_channel'
        ):
            return RedirectResponse(
                '/?integration=failed&reason='+reason,status_code=303)
        return RedirectResponse('/?integration=failed',status_code=303)
    except Exception:
        return RedirectResponse('/?integration=failed',status_code=303)

@app.post('/api/social/grants',dependencies=[Depends(guard)])
def social_grant(body:SocialGrant):
    with connect() as db:
        result=social.set_permission(db,body.staff_id,body.account_id,body.mode)
        audit(db,'social.permission.changed',
              f'{body.staff_id} => {body.account_id}: {body.mode}')
    return result

@app.post('/api/social/accounts/{account_id}/refresh-instagram',dependencies=[Depends(guard)])
def refresh_instagram_account(account_id:str):
    with connect() as db:
        result=social.refresh_instagram(db,account_id)
        audit(db,'social.instagram.token_refreshed',account_id)
    return result

@app.delete('/api/social/accounts/{account_id}',dependencies=[Depends(guard)])
def social_disconnect(account_id:str):
    with connect() as db:
        result=social.disconnect(db,account_id)
        audit(db,'social.account.disconnected',account_id)
    return result

@app.post('/api/social/posts',dependencies=[Depends(guard)])
def social_create_draft(body:SocialDraft):
    with connect() as db:
        result=social_publish.create(db,body.staff_id,body.account_id,
                                     body.caption,body.media_url)
        audit(db,'social.draft.created',f'{result["id"]} by {body.staff_id}')
    return result

@app.get('/api/social/approved-runs',dependencies=[Depends(guard)])
def social_approved_runs():
    with connect() as db:
        office_workforce.init(db)
        rows=db.execute("""SELECT r.id,r.staff_id,r.staff_name,r.task_title,
            LEFT(r.result,220) AS excerpt,r.created_at
            FROM workforce_runs r JOIN staff s ON s.id=r.staff_id
            WHERE r.status='Approved'
            ORDER BY r.created_at DESC LIMIT 70""").fetchall()
    return {'runs':[dict(x) for x in rows]}

@app.post('/api/social/posts/from-run',dependencies=[Depends(guard)])
def social_create_from_run(body:SocialRunDraft):
    with connect() as db:
        office_workforce.init(db)
        run=db.execute("""SELECT id,staff_id,result FROM workforce_runs
            WHERE id=%s AND status='Approved'""",(body.run_id,)).fetchone()
        if not run:raise HTTPException(404,"CEO-approved AI deliverable not found")
        caption=run['result'].strip()
        if len(caption)>2200:
            raise HTTPException(400,"AI deliverable too long for a social post; prepare a concise caption")
        result=social_publish.create(db,run['staff_id'],body.account_id,
                                     caption,body.media_url)
        audit(db,'social.draft.from_ai',
              f'{result["id"]} from run {body.run_id}')
    return result

@app.put('/api/social/posts/{ident}',dependencies=[Depends(guard)])
def social_edit_draft(ident:str,body:SocialDraftEdit):
    with connect() as db:
        result=social_publish.update(db,ident,body.caption,body.media_url)
        audit(db,'social.draft.edited',ident)
    return result

@app.post('/api/social/posts/{ident}/approve',dependencies=[Depends(guard)])
def social_approve_post(ident:str):
    with connect() as db:
        result=social_publish.approve(db,ident)
        audit(db,'social.post.approved',ident)
    return result

@app.post('/api/social/posts/{ident}/reject',dependencies=[Depends(guard)])
def social_reject_post(ident:str):
    with connect() as db:
        result=social_publish.reject(db,ident)
        audit(db,'social.post.rejected',ident)
    return result

@app.post('/api/social/posts/{ident}/publish',dependencies=[Depends(guard)])
def social_publish_post(ident:str):
    # Persist one-use claim before contacting provider to prevent double posting.
    with connect() as db:
        post,token=social_publish.claim(db,ident)
        audit(db,'social.post.publishing',ident)
    try:
        platform_id=social_publish.publish_claimed(post,token)
    except Exception as exc:
        with connect() as db:
            social_publish.finish(db,ident,error='Provider response unconfirmed; check account manually')
            audit(db,'social.post.needs_verification',ident)
        if isinstance(exc,HTTPException):raise
        raise HTTPException(502,"Social publishing outcome unconfirmed; verify at provider")
    with connect() as db:
        social_publish.finish(db,ident,external_id=platform_id)
        audit(db,'social.post.published',f'{ident}: {post["platform"]}')
    return {'ok':True,'status':'Published','platform_post_id':platform_id}
