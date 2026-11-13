import os, json, hmac, hashlib, secrets, sqlite3, time
from contextlib import contextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

ROOT=Path(__file__).resolve().parent.parent
DB=os.environ.get('DATABASE_PATH',str(ROOT/'office.sqlite3'))
PASSWORD=os.environ.get('CEO_PASSWORD')
SECRET=os.environ.get('SESSION_SECRET')
if not PASSWORD or not SECRET or len(SECRET)<32:
    raise RuntimeError('Set CEO_PASSWORD and SESSION_SECRET (at least 32 characters) before starting')
app=FastAPI(title='AI Private Office',docs_url=None,redoc_url=None)
app.add_middleware(SessionMiddleware,secret_key=SECRET,https_only=os.environ.get('COOKIE_SECURE','true').lower()=='true',same_site='lax',max_age=28800)
@contextmanager
def connect():
    c=sqlite3.connect(DB,timeout=15)
    c.row_factory=sqlite3.Row
    try:
        yield c
        c.commit()
    finally:c.close()
with connect() as db:
    db.execute('CREATE TABLE IF NOT EXISTS staff (id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,department TEXT NOT NULL,expertise TEXT NOT NULL DEFAULT "",tools TEXT NOT NULL DEFAULT "",autonomy TEXT NOT NULL DEFAULT "Approval required",status TEXT NOT NULL DEFAULT "Available")')
    db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY,title TEXT NOT NULL,assignee TEXT NOT NULL DEFAULT "",status TEXT NOT NULL DEFAULT "To do",created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS lessons (id TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,assigned TEXT NOT NULL DEFAULT "",status TEXT NOT NULL DEFAULT "Pending review",created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT NOT NULL,detail TEXT NOT NULL,created INTEGER NOT NULL)')
class Login(BaseModel):password:str
class Staff(BaseModel):name:str=Field(min_length=1,max_length=100);role:str=Field(min_length=1,max_length=120);department:str=Field(min_length=1,max_length=100);expertise:str='';tools:str='';autonomy:str='Approval required'
class Task(BaseModel):title:str=Field(min_length=1,max_length=250);assignee:str=''
class Lesson(BaseModel):title:str=Field(min_length=1,max_length=200);kind:str;source:str=Field(min_length=1,max_length=3000);assigned:str=''
class Status(BaseModel):status:str

def guard(req:Request):
    if req.session.get('authorized') is not True:raise HTTPException(401,'Login required')
def audit(db,action,detail):db.execute('INSERT INTO audit(action,detail,created) VALUES(?,?,?)',(action,detail,int(time.time())))
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
        return {k:[dict(x) for x in db.execute('SELECT * FROM '+k+(' ORDER BY created DESC' if k in ('tasks','lessons','audit') else ' ORDER BY name'))] for k in ('staff','tasks','lessons','audit')}
@app.post('/api/staff',dependencies=[Depends(guard)])
def create_staff(body:Staff):
    ident=secrets.token_hex(12)
    with connect() as db:
        db.execute('INSERT INTO staff VALUES(?,?,?,?,?,?,?,?)',(ident,body.name,body.role,body.department,body.expertise,body.tools,body.autonomy,'Available'))
        audit(db,'staff.hired',body.name)
    return {'id':ident}
@app.post('/api/tasks',dependencies=[Depends(guard)])
def create_task(body:Task):
    ident=secrets.token_hex(12)
    with connect() as db:
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?)',(ident,body.title,body.assignee,'To do',int(time.time())))
        audit(db,'task.created',body.title)
    return {'id':ident}
@app.patch('/api/tasks/{ident}',dependencies=[Depends(guard)])
def update_task(ident:str,body:Status):
    if body.status not in ('To do','In progress','For approval','Completed'):raise HTTPException(400,'Invalid status')
    with connect() as db:
        result=db.execute('UPDATE tasks SET status=? WHERE id=?',(body.status,ident))
        if not result.rowcount:raise HTTPException(404,'Task not found')
        audit(db,'task.status',f'{ident}: {body.status}')
    return {'ok':True}
@app.post('/api/lessons',dependencies=[Depends(guard)])
def create_lesson(body:Lesson):
    if body.kind not in ('YouTube link','Website link','Note'):raise HTTPException(400,'Invalid lesson type')
    if body.kind!='Note' and not body.source.startswith(('https://','http://')):raise HTTPException(400,'A valid URL is required')
    ident=secrets.token_hex(12)
    with connect() as db:
        db.execute('INSERT INTO lessons VALUES(?,?,?,?,?,?,?)',(ident,body.title,body.kind,body.source,body.assigned,'Pending review',int(time.time())))
        audit(db,'academy.added',body.title)
    return {'id':ident}
@app.get('/api/capabilities',dependencies=[Depends(guard)])
def capabilities():
    return {'working':['Secure password login','Staff configuration and hiring','Task management','Knowledge source registry','Audit log','Desk status from task records'], 'planned':['MFA','AI execution','YouTube transcript ingestion','File processing','OAuth integrations','Accounting engine','Autonomous workflows','Multi-user permissions']}
@app.get('/')
def home():return FileResponse(ROOT/'frontend'/'index.html')
