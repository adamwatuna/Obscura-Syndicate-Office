"""Keep PostgreSQL schema DDL in serialized app bootstrap, never normal API requests.

The office starts by running office_db.bootstrap(), which creates and upgrades
all tables inside one transaction. PostgreSQL DDL such as ALTER TABLE takes
AccessExclusiveLocks: repeating it in concurrent requests caused a real
deadlock in Learning Academy and Meita History. Once bootstrap has committed,
schema initializers become no-ops until the next process startup.

A separate call to bootstrap() can deliberately re-run migrations, useful for
legacy import and CI tests; the context is local to that call/thread.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_ready = False
_migrating = ContextVar("office_schema_migrating", default=False)

@contextmanager
def migration():
    token = _migrating.set(True)
    try:
        yield
    finally:
        _migrating.reset(token)

def mark_ready():
    global _ready
    _ready = True

def bootstrap_only(fn):
    @wraps(fn)
    def decorated(db,*args,**kwargs):
        if _ready and not _migrating.get():
            return None
        return fn(db,*args,**kwargs)
    return decorated
