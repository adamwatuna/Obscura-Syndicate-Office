"""CEO-managed long-term memories in Railway PostgreSQL."""
import os, re, secrets
import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException
from pydantic import BaseModel, Field

CATEGORIES = {'CEO instruction', 'CEO preference', 'Company knowledge', 'Task decision'}

class MemoryInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=3000)
    category: str = 'CEO instruction'

def validate(body):
    if body.category not in CATEGORIES: raise HTTPException(400, 'Invalid category')
    return body

def connect():
    url=os.getenv('DATABASE_URL','').strip()
    if not url: raise HTTPException(503,'DATABASE_URL is not configured')
    try: return psycopg.connect(url, row_factory=dict_row, connect_timeout=8)
    except psycopg.Error: raise HTTPException(503,'Memory database is unavailable')

from backend import schema_state

@schema_state.bootstrap_only
def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS meita_memories (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
        category TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW())""")

def all_memories():
    try:
        with connect() as db:
            setup(db)
            return {'memories': db.execute('SELECT * FROM meita_memories ORDER BY updated_at DESC LIMIT 250').fetchall()}
    except psycopg.Error: raise HTTPException(503,'Unable to retrieve memories')

def add(body):
    validate(body)
    ident=secrets.token_hex(12)
    try:
        with connect() as db:
            setup(db)
            db.execute('INSERT INTO meita_memories(id,title,content,category) VALUES (%s,%s,%s,%s)',
                       (ident,body.title.strip(),body.content.strip(),body.category))
        return {'id': ident}
    except psycopg.Error: raise HTTPException(503,'Unable to save memory')

def update(ident,body):
    validate(body)
    try:
        with connect() as db:
            setup(db)
            row=db.execute('UPDATE meita_memories SET title=%s,content=%s,category=%s,updated_at=NOW() WHERE id=%s RETURNING id',
                           (body.title.strip(),body.content.strip(),body.category,ident)).fetchone()
            if row is None: raise HTTPException(404,'Memory not found')
        return {'ok':True}
    except psycopg.Error: raise HTTPException(503,'Unable to update memory')

def delete(ident):
    try:
        with connect() as db:
            setup(db)
            row=db.execute('DELETE FROM meita_memories WHERE id=%s RETURNING id',(ident,)).fetchone()
            if row is None: raise HTTPException(404,'Memory not found')
        return {'ok':True}
    except psycopg.Error: raise HTTPException(503,'Unable to delete memory')

def recall(message,limit=6):
    try:
        with connect() as db:
            setup(db)
            rows=db.execute('SELECT title,content,category FROM meita_memories ORDER BY updated_at DESC LIMIT 250').fetchall()
    except psycopg.Error:
        raise HTTPException(503,'Memory database unavailable; chat paused to avoid missing CEO instructions')
    terms=set(re.findall(r'\w{3,}',message.lower()))
    ranked=[]
    for row in rows:
        title=row['title'].lower(); content=row['content'].lower()
        score=sum(3 for t in terms if t in title)+sum(1 for t in terms if t in content)
        if row['category'] in ('CEO instruction','CEO preference'):score+=2
        if score: ranked.append((score,row))
    ranked.sort(key=lambda x:x[0],reverse=True)
    return [row for _,row in ranked[:limit]]
