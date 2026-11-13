"""CEO-only TOTP MFA with encrypted secrets, single-use recovery codes and session revocation.

Uses RFC 6238 TOTP (SHA1, 30-second step, 6 digits). No SMS, email or
third-party login dependency. Existing password-only sessions are invalidated
when the CEO enables or disables MFA.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

ISSUER="AI Private Office"
MAX_SETUP_ATTEMPTS=5
MAX_ACTION_ATTEMPTS=5

def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ceo_security (
        id INTEGER PRIMARY KEY CHECK(id=1),
        totp_secret_encrypted TEXT,
        pending_secret_encrypted TEXT,
        pending_created TIMESTAMPTZ,
        pending_attempts INTEGER NOT NULL DEFAULT 0,
        recovery_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,
        session_version INTEGER NOT NULL DEFAULT 0,
        last_totp_step BIGINT NOT NULL DEFAULT -1,
        action_failures INTEGER NOT NULL DEFAULT 0,
        action_locked_until TIMESTAMPTZ)""")
    db.execute("INSERT INTO ceo_security(id) VALUES(1) ON CONFLICT(id) DO NOTHING")


def _key():
    master=os.environ["SESSION_SECRET"]
    digest=hashlib.sha256(("ai-private-office-totp-v1|"+master).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(raw):
    return _key().encrypt(raw.encode()).decode()


def _decrypt(blob):
    try:
        return _key().decrypt(blob.encode()).decode()
    except (InvalidToken,ValueError) as exc:
        raise HTTPException(503,"MFA security key mismatch. Restore the original SESSION_SECRET.") from exc


def _totp(secret,step):
    key=base64.b32decode(secret,casefold=True)
    digest=hmac.new(key,struct.pack(">Q",step),hashlib.sha1).digest()
    offset=digest[-1]&15
    integer=struct.unpack(">I",digest[offset:offset+4])[0]&0x7fffffff
    return f"{integer%1000000:06d}"


def current_code(secret,at=None):
    """For local/CI tests only; never expose the code to browser endpoints."""
    return _totp(secret,int((time.time() if at is None else at)//30))


def _matching_step(secret,code,now=None,last=-1):
    if not isinstance(code,str) or len(code)!=6 or not code.isdigit():
        return None
    step=int((time.time() if now is None else now)//30)
    for delta in (-1,0,1):
        candidate=step+delta
        if candidate>last and hmac.compare_digest(_totp(secret,candidate),code):
            return candidate
    return None


def _hash_recovery(code):
    secret=os.environ["SESSION_SECRET"].encode()
    return hmac.new(secret,("recovery:v1:"+code.replace("-","").upper()).encode(),hashlib.sha256).hexdigest()


def _consume_code(db,row,code):
    """Within a FOR UPDATE transaction; never consume recovery codes twice."""
    value=(code or "").strip().replace(" ","")
    if len(value)==6 and value.isdigit():
        step=_matching_step(_decrypt(row["totp_secret_encrypted"]),value,last=row["last_totp_step"])
        if step is not None:
            db.execute("UPDATE ceo_security SET last_totp_step=%s WHERE id=1",(step,))
            return True
    normalized=value.replace("-","").upper()
    if len(normalized)==12 and normalized.isalnum():
        hashed=_hash_recovery(normalized)
        matches=list(row["recovery_hashes"])
        for index,entry in enumerate(matches):
            if hmac.compare_digest(entry,hashed):
                matches.pop(index)
                db.execute("UPDATE ceo_security SET recovery_hashes=%s WHERE id=1",
                           (json.dumps(matches),))
                return True
    return False


def enabled(db):
    # Schema is initialized once at office bootstrap, not during each request.
    row=db.execute("""SELECT totp_secret_encrypted IS NOT NULL AS enabled,
        jsonb_array_length(recovery_hashes) AS recovery_remaining,
        session_version FROM ceo_security WHERE id=1""").fetchone()
    return dict(row)


def valid_session(db,session):
    if session.get("authorized") is not True:return False
    security=enabled(db)
    return session.get("session_version")==security["session_version"]


def password_valid(provided):
    stored=os.environ["CEO_PASSWORD"]
    return hmac.compare_digest(str(provided or "").encode(),stored.encode())


def verify_login(db,code):
    init(db)
    row=db.execute("SELECT * FROM ceo_security WHERE id=1 FOR UPDATE").fetchone()
    if row["totp_secret_encrypted"] and not _consume_code(db,row,code):
        return None
    return row["session_version"]


def start_setup(db,password):
    if not password_valid(password):
        raise HTTPException(403,"CEO password is incorrect")
    init(db)
    row=db.execute("SELECT * FROM ceo_security WHERE id=1 FOR UPDATE").fetchone()
    if row["totp_secret_encrypted"]:
        raise HTTPException(409,"Multi-factor authentication is already enabled")
    secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    db.execute("""UPDATE ceo_security SET pending_secret_encrypted=%s,
        pending_created=NOW(),pending_attempts=0 WHERE id=1""",(_encrypt(secret),))
    label=quote(ISSUER+":CEO",safe="")
    issuer=quote(ISSUER,safe="")
    url=f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    return {"secret":secret,"otpauth_url":url,
            "instructions":"Add this secret to an authenticator app and enter its current six-digit code within ten minutes. Do not share it."}


def confirm_setup(db,code):
    init(db)
    row=db.execute("SELECT * FROM ceo_security WHERE id=1 FOR UPDATE").fetchone()
    if row["totp_secret_encrypted"]:
        raise HTTPException(409,"Multi-factor authentication is already enabled")
    if not row["pending_secret_encrypted"] or not row["pending_created"]:
        raise HTTPException(409,"Start MFA setup before verifying")
    if row["pending_created"]<datetime.now(timezone.utc)-timedelta(minutes=10):
        db.execute("""UPDATE ceo_security SET pending_secret_encrypted=NULL,
            pending_created=NULL,pending_attempts=0 WHERE id=1""")
        return {"ok":False,"error":"Setup expired. Start again."}
    if row["pending_attempts"]>=MAX_SETUP_ATTEMPTS:
        raise HTTPException(429,"Too many MFA setup attempts. Start over.")
    secret=_decrypt(row["pending_secret_encrypted"])
    step=_matching_step(secret,(code or "").strip())
    if step is None:
        db.execute("""UPDATE ceo_security SET pending_attempts=pending_attempts+1
            WHERE id=1""")
        return {"ok":False,"error":"Incorrect authenticator code. Please check the current code."}
    codes=[secrets.token_hex(6).upper() for _ in range(10)]
    hashes=[_hash_recovery(c) for c in codes]
    db.execute("""UPDATE ceo_security SET totp_secret_encrypted=pending_secret_encrypted,
        pending_secret_encrypted=NULL,pending_created=NULL,pending_attempts=0,
        recovery_hashes=%s,last_totp_step=%s,session_version=session_version+1
        WHERE id=1""",(json.dumps(hashes),step))
    version=db.execute("SELECT session_version FROM ceo_security WHERE id=1").fetchone()["session_version"]
    return {"ok":True,"enabled":True,"recovery_codes":codes,"session_version":version}


def disable(db,password,code):
    if not password_valid(password):
        raise HTTPException(403,"CEO password is incorrect")
    init(db)
    row=db.execute("SELECT * FROM ceo_security WHERE id=1 FOR UPDATE").fetchone()
    if not row["totp_secret_encrypted"]:
        raise HTTPException(409,"MFA is already disabled")
    if row["action_locked_until"] and row["action_locked_until"]>datetime.now(timezone.utc):
        raise HTTPException(429,"Too many attempts. Try again in 15 minutes.")
    if not _consume_code(db,row,code):
        failures=row["action_failures"]+1
        db.execute("""UPDATE ceo_security SET action_failures=%s,
            action_locked_until=CASE WHEN %s >= %s THEN NOW()+INTERVAL '15 minutes'
                                     ELSE NULL END WHERE id=1""",
                   (failures,failures,MAX_ACTION_ATTEMPTS))
        return {"ok":False,"error":"Incorrect authenticator or recovery code"}
    db.execute("""UPDATE ceo_security SET totp_secret_encrypted=NULL,
        pending_secret_encrypted=NULL,pending_created=NULL,pending_attempts=0,
        recovery_hashes='[]'::jsonb,last_totp_step=-1,session_version=session_version+1,
        action_failures=0,action_locked_until=NULL WHERE id=1""")
    version=db.execute("SELECT session_version FROM ceo_security WHERE id=1").fetchone()["session_version"]
    return {"ok":True,"enabled":False,"session_version":version}
