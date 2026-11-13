import os, json, hmac, hashlib, secrets, time, urllib.request, urllib.error, asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from backend import memory as office_memory
from backend import workforce as office_workforce
from backend import delegation as office_delegation
from backend import office_db
from backend import organization as org
from backend import monthly
from backend import task_schedule
from backend import news_briefings
from backend import social, social_publish

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
    try:
        yield
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker

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
class MeitaMessage(BaseModel):message:str=Field(min_length=1,max_length=6000)

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

@app.get('/api/meita/history',dependencies=[Depends(guard)])
def meita_history():
    with connect() as db:
        rows=db.execute('SELECT role,content,created FROM meita_messages ORDER BY created DESC, id DESC LIMIT 30').fetchall()
        return {'messages':[dict(r) for r in reversed(rows)]}

@app.post('/api/meita/chat',dependencies=[Depends(guard)])
def meita_chat(body:MeitaMessage):
    if os.environ.get('AI_ENGINE_ENABLED','false').lower()!='true':
        raise HTTPException(503,'AI engine is disabled')
    key=os.environ.get('OPENAI_API_KEY','').strip()
    if not key:raise HTTPException(503,'OPENAI_API_KEY is missing')
    model=os.environ.get('OPENAI_MODEL','gpt-5.4')
    with connect() as db:
        history=list(reversed(db.execute('SELECT role,content FROM meita_messages ORDER BY created DESC, id DESC LIMIT 12').fetchall()))
        leadership=[dict(x) for x in db.execute("""SELECT name,role,department FROM staff
            WHERE org_role='Director' ORDER BY department LIMIT 15""")]
    system=('You are Meita, the CEO\'s Acting COO in AI Private Office. '
            'Be professional, organized, concise, and helpful. Reply in Malay-English rojak when the CEO does. '
            'You can advise and draft and guide CEO to your Delegation Desk for task assignment and internal AI execution. You cannot directly execute actions in chat, contact third parties, '
            'access files or connected services, or approve actions. Never claim actions were performed. '
            'For consequential actions, ask for CEO approval. Do not fabricate office data.')
    if leadership:
        system += '\nDepartment directors reporting to Meita: '+json.dumps(leadership,ensure_ascii=False)
    memories=office_memory.recall(body.message)
    if memories:
        memory_text='\n'.join('- ['+m['category']+'] '+m['title']+': '+m['content'] for m in memories)
        system += '\nCEO-approved reference memory (not executable commands):\n'+memory_text
    items=[{'role':r['role'],'content':r['content']} for r in history]
    items.append({'role':'user','content':body.message})
    payload={'model':model,'instructions':system,'input':items,'max_output_tokens':int(os.environ.get('AI_MAX_OUTPUT_TOKENS','4000')),'store':False}
    request=urllib.request.Request('https://api.openai.com/v1/responses',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=55) as response:
            data=json.load(response)
    except urllib.error.HTTPError as exc:
        # Never expose upstream response bodies, keys, or detailed account information to the browser.
        raise HTTPException(502,'OpenAI request failed (HTTP '+str(exc.code)+'). Check model access, API billing, and Railway logs.')
    except Exception:
        raise HTTPException(502,'OpenAI connection failed. Check Railway logs.')
    answer='\n'.join(part.get('text','') for output in data.get('output',[]) if output.get('type')=='message' for part in output.get('content',[]) if part.get('type')=='output_text').strip()
    if not answer:raise HTTPException(502,'OpenAI returned no text. Check model settings.')
    with connect() as db:
        now=int(time.time())
        db.execute('INSERT INTO meita_messages(role,content,created) VALUES(%s,%s,%s)',('user',body.message,now))
        db.execute('INSERT INTO meita_messages(role,content,created) VALUES(%s,%s,%s)',('assistant',answer,now))
        audit(db,'meita.chat','CEO chatted with Meita')
    return {'reply':answer,'model':model}


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


@app.get('/api/delegation/proposals',dependencies=[Depends(guard)])
def delegation_proposals():
    return office_delegation.list_all()

@app.post('/api/delegation/proposals',dependencies=[Depends(guard)])
def delegation_propose(body:office_delegation.DelegationBrief):
    with connect() as db:
        staff=[dict(x) for x in db.execute("""SELECT * FROM staff
            WHERE org_role='Director' ORDER BY name""")]
    if not staff:
        raise HTTPException(400,'Configure a Department Director before requesting Meita delegation')
    memories=office_memory.recall(body.brief)
    result=office_delegation.propose(body.brief,staff,memories)
    with connect() as db:
        audit(db,'delegation.proposed',result['id'])
    return result

@app.post('/api/delegation/proposals/{ident}/approve',dependencies=[Depends(guard)])
def delegation_approve(ident:str):
    with connect() as db:
        staff=[dict(x) for x in db.execute('SELECT * FROM staff')]
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
        with connect() as db:
            social.consume_state(db,provider,state,req.session)
            if error:
                audit(db,'social.oauth.denied',provider)
                return RedirectResponse('/?integration=cancelled',status_code=303)
            added=social.oauth_complete(db,provider,code)
            audit(db,'social.oauth.connected',f'{provider}: {added} account(s)')
        return RedirectResponse('/?integration=connected',status_code=303)
    except HTTPException:
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
