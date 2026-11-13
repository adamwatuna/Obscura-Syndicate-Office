import os, json, hmac, hashlib, secrets, time, urllib.request, urllib.error
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from backend import memory as office_memory
from backend import workforce as office_workforce
from backend import delegation as office_delegation
from backend import office_db

ROOT=Path(__file__).resolve().parent.parent
PASSWORD=os.environ.get('CEO_PASSWORD')
SECRET=os.environ.get('SESSION_SECRET')
if not PASSWORD or not SECRET or len(SECRET)<32:
    raise RuntimeError('Set CEO_PASSWORD and SESSION_SECRET (at least 32 characters) before starting')
# Database initialization is atomic and imports any accessible legacy SQLite in read-only mode.
office_db.bootstrap()
connect=office_db.connect
app=FastAPI(title='AI Private Office',docs_url=None,redoc_url=None)
app.add_middleware(SessionMiddleware,secret_key=SECRET,https_only=os.environ.get('COOKIE_SECURE','true').lower()=='true',same_site='lax',max_age=28800)

class Login(BaseModel):password:str
class Staff(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=120)
    department: str = Field(min_length=1, max_length=100)
    expertise: str = Field(default='', max_length=16000)
    tools: str = Field(default='', max_length=2000)
    autonomy: str = Field(default='Approval required', max_length=100)
class Task(BaseModel):title:str=Field(min_length=1,max_length=250);assignee:str=''
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
        result = {'staff': [dict(x) for x in db.execute(
            'SELECT id,name,role,department,status FROM staff ORDER BY name')]}
        for table in ('tasks', 'lessons', 'audit'):
            result[table] = [dict(x) for x in db.execute(
                'SELECT * FROM '+table+' ORDER BY created DESC, id DESC')]
        return result
@app.post('/api/staff',dependencies=[Depends(guard)])
def create_staff(body:Staff):
    ident=secrets.token_hex(12)
    with connect() as db:
        db.execute('INSERT INTO staff VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(ident,body.name,body.role,body.department,body.expertise,body.tools,body.autonomy,'Available'))
        audit(db,'staff.hired',body.name)
    return {'id':ident}

@app.get('/api/staff/{ident}/configuration',dependencies=[Depends(guard)])
def get_staff_configuration(ident:str):
    # The regular office listing must never expose private staff configuration.
    with connect() as db:
        row = db.execute('SELECT id,name,role,department,expertise,tools,autonomy,status FROM staff WHERE id=%s',(ident,)).fetchone()
        if row is None: raise HTTPException(404,'Staff not found')
        return {'staff':dict(row)}

@app.put('/api/staff/{ident}',dependencies=[Depends(guard)])
def update_staff(ident:str, body:Staff):
    if not body.name.strip() or not body.role.strip() or not body.department.strip():
        raise HTTPException(400,'Name, job title and department are required')
    with connect() as db:
        existing=db.execute('SELECT name FROM staff WHERE id=%s FOR UPDATE',(ident,)).fetchone()
        if not existing: raise HTTPException(404,'Staff not found')
        db.execute("""UPDATE staff SET name=%s, role=%s, department=%s,
            expertise=%s, tools=%s, autonomy=%s WHERE id=%s""",
            (body.name.strip(),body.role.strip(),body.department.strip(),
             body.expertise,body.tools,body.autonomy,ident))
        audit(db,'staff.updated',f'{ident}: {existing["name"]} -> {body.name.strip()}')
    return {'ok':True}

@app.delete('/api/staff/{ident}',dependencies=[Depends(guard)])
def delete_staff(ident:str):
    # Preserve existing task records; clear their assignee instead of deleting tasks.
    with connect() as db:
        existing=db.execute('SELECT name FROM staff WHERE id=%s FOR UPDATE',(ident,)).fetchone()
        if existing is None: raise HTTPException(404,'Staff not found')
        unassigned=db.execute("UPDATE tasks SET assignee='' WHERE assignee=%s",(ident,)).rowcount
        # Keep historical delegation and AI result snapshots for CEO audit.
        # Unstarted delegation jobs for this staff cannot be executed afterwards.
        job_count=0
        if db.execute("SELECT to_regclass('public.coo_jobs') AS tbl").fetchone()['tbl'] is not None:
            job_count=db.execute("""UPDATE coo_jobs SET status='Cancelled',
                updated_at=NOW() WHERE staff->>'id'=%s
                AND status IN ('Approved','Retry available')""",(ident,)).rowcount
        db.execute('DELETE FROM staff WHERE id=%s',(ident,))
        audit(db,'staff.deleted',f'{ident}: {existing["name"]}; unassigned_tasks={unassigned}; cancelled_jobs={job_count}')
    return {'ok':True,'unassigned_tasks':unassigned,'cancelled_jobs':job_count}
@app.post('/api/tasks',dependencies=[Depends(guard)])
def create_task(body:Task):
    ident=secrets.token_hex(12)
    with connect() as db:
        db.execute('INSERT INTO tasks VALUES(%s,%s,%s,%s,%s)',(ident,body.title,body.assignee,'To do',int(time.time())))
        audit(db,'task.created',body.title)
    return {'id':ident}
@app.patch('/api/tasks/{ident}',dependencies=[Depends(guard)])
def update_task(ident:str,body:Status):
    if body.status not in ('To do','In progress','For approval','Completed'):raise HTTPException(400,'Invalid status')
    with connect() as db:
        result=db.execute('UPDATE tasks SET status=%s WHERE id=%s',(body.status,ident))
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
    return {'working':['Secure password login','Staff configuration and hiring','Task management','Knowledge source registry','Audit log','Desk status from task records'], 'planned':['MFA','AI execution','YouTube transcript ingestion','File processing','OAuth integrations','Accounting engine','Autonomous workflows','Multi-user permissions']}
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
    system=('You are Meita, the CEO\'s Acting COO in AI Private Office. '
            'Be professional, organized, concise, and helpful. Reply in Malay-English rojak when the CEO does. '
            'You can advise and draft and guide CEO to your Delegation Desk for task assignment and internal AI execution. You cannot directly execute actions in chat, contact third parties, '
            'access files or connected services, or approve actions. Never claim actions were performed. '
            'For consequential actions, ask for CEO approval. Do not fabricate office data.')
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
        task=db.execute('SELECT * FROM tasks WHERE id=%s',(task_id,)).fetchone()
        if task is None: raise HTTPException(404,'Task not found')
        if not task['assignee']: raise HTTPException(400,'Assign a staff member before running this task')
        staff=db.execute('SELECT * FROM staff WHERE id=%s',(task['assignee'],)).fetchone()
        if staff is None: raise HTTPException(404,'Assigned staff not found')
        task=dict(task); staff=dict(staff)
    result=office_workforce.execute(task,staff)
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
        staff=[dict(x) for x in db.execute('SELECT * FROM staff ORDER BY name')]
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
