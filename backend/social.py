"""Social account connections, encrypted OAuth tokens and scoped AI staff publication rights.

Social tokens NEVER leave the backend. CEO credentials alone do not confer
publishing rights: a connected account and explicit per-staff grant are required.
"""
import hashlib
import hmac
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

PROVIDERS = ("meta", "google", "tiktok")
PLATFORMS = ("facebook", "instagram", "youtube", "tiktok")
SUPPORTED_PUBLISH = ("facebook", "instagram")
PERMISSION_MODES = ("off", "approval_required", "direct_publish")
GRAPH = "https://graph.facebook.com/v27.0"
CLIENT_VARS = {
    "meta": ("SOCIAL_META_APP_ID", "SOCIAL_META_APP_SECRET"),
    "google": ("SOCIAL_GOOGLE_CLIENT_ID", "SOCIAL_GOOGLE_CLIENT_SECRET"),
    "tiktok": ("SOCIAL_TIKTOK_CLIENT_KEY", "SOCIAL_TIKTOK_CLIENT_SECRET"),
}
SCOPES = {
    "meta": "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish",
    "google": "https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/youtube.upload",
    "tiktok": "user.info.basic,video.publish",
}


def _configuration(provider):
    if provider not in PROVIDERS:
        raise HTTPException(400, "Unsupported social provider")
    client_name, secret_name=CLIENT_VARS[provider]
    client=os.getenv(client_name, "").strip()
    secret=os.getenv(secret_name, "").strip()
    base=os.getenv("SOCIAL_PUBLIC_BASE_URL","").strip().rstrip("/")
    crypto=os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY","").strip()
    if not client or not secret or not crypto or not base.startswith("https://"):
        raise HTTPException(503,"OAuth not configured for this provider. Set server-side app credentials, encryption key and public HTTPS URL.")
    try: Fernet(crypto.encode())
    except Exception: raise HTTPException(503,"SOCIAL_TOKEN_ENCRYPTION_KEY must be a valid Fernet key")
    parsed=urllib.parse.urlsplit(base)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("","/"):
        raise HTTPException(503,"SOCIAL_PUBLIC_BASE_URL must be an HTTPS origin only")
    return client,secret,base,crypto


def encrypt(token):
    key=os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY","").strip()
    if not key: raise HTTPException(503,"Token encryption key is not configured")
    try: return Fernet(key.encode()).encrypt(token.encode()).decode()
    except Exception: raise HTTPException(503,"Token encryption configuration is invalid")


def decrypt(value):
    key=os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY","").strip()
    if not key: raise HTTPException(503,"Token encryption key is not configured")
    try: return Fernet(key.encode()).decrypt(value.encode()).decode()
    except (ValueError,InvalidToken): raise HTTPException(503,"Cannot decrypt saved social account token")


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS social_oauth_states (
        state_hash TEXT PRIMARY KEY, session_hash TEXT NOT NULL,
        provider TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    db.execute("""CREATE TABLE IF NOT EXISTS social_accounts (
        id TEXT PRIMARY KEY, platform TEXT NOT NULL, provider TEXT NOT NULL,
        external_id TEXT NOT NULL, display_name TEXT NOT NULL,
        access_token_cipher TEXT NOT NULL,
        refresh_token_cipher TEXT,
        token_expires_at TIMESTAMPTZ,
        granted_scopes TEXT NOT NULL DEFAULT '',
        connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(platform,external_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS social_publish_grants (
        staff_id TEXT NOT NULL, account_id TEXT NOT NULL
            REFERENCES social_accounts(id) ON DELETE CASCADE,
        mode TEXT NOT NULL DEFAULT 'off',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(staff_id,account_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS social_posts (
        id TEXT PRIMARY KEY,staff_id TEXT NOT NULL,
        account_id TEXT NOT NULL,platform TEXT NOT NULL,
        account_name TEXT NOT NULL,caption TEXT NOT NULL,
        media_url TEXT,
        status TEXT NOT NULL,approval_mode TEXT NOT NULL,
        platform_post_id TEXT,error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    db.execute("CREATE INDEX IF NOT EXISTS social_posts_recency ON social_posts(created_at DESC)")


def _http_json(url, form=None, headers=None, timeout=18):
    encoded=urllib.parse.urlencode(form).encode() if form is not None else None
    req=urllib.request.Request(url,data=encoded,headers={
        "Accept":"application/json",
        **({"Content-Type":"application/x-www-form-urlencoded"} if form is not None else {}),
        **(headers or {})},method="POST" if form is not None else "GET")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Never echo provider responses; they may contain secrets or PII.
        raise HTTPException(502,f"Social provider request failed (HTTP {error.code})")
    except (urllib.error.URLError,TimeoutError,ValueError):
        raise HTTPException(502,"Social provider unavailable or invalid response")


def _query(url, params):
    return url+"?"+urllib.parse.urlencode(params)


def oauth_start(db,provider,session):
    client,secret,base,key=_configuration(provider)
    schema(db)
    if not session.get("authorized"):
        raise HTTPException(401,"CEO login required")
    # Session nonce is never sent to external providers.
    nonce=session.get("social_oauth_nonce") or secrets.token_urlsafe(24)
    session["social_oauth_nonce"]=nonce
    state=secrets.token_urlsafe(36)
    state_hash=hashlib.sha256(state.encode()).hexdigest()
    nonce_hash=hashlib.sha256(nonce.encode()).hexdigest()
    db.execute("DELETE FROM social_oauth_states WHERE created_at < NOW() - INTERVAL '15 minutes'")
    db.execute("""INSERT INTO social_oauth_states(state_hash,session_hash,provider)
        VALUES (%s,%s,%s)""",(state_hash,nonce_hash,provider))
    redirect=f"{base}/api/social/oauth/{provider}/callback"
    if provider=="meta":
        return _query("https://www.facebook.com/v27.0/dialog/oauth",{
            "client_id":client,"redirect_uri":redirect,"state":state,
            "response_type":"code","scope":SCOPES["meta"]})
    if provider=="google":
        return _query("https://accounts.google.com/o/oauth2/v2/auth",{
            "client_id":client,"redirect_uri":redirect,"state":state,
            "response_type":"code","scope":SCOPES["google"],
            "access_type":"offline","prompt":"consent"})
    return _query("https://www.tiktok.com/v2/auth/authorize/",{
        "client_key":client,"redirect_uri":redirect,"state":state,
        "response_type":"code","scope":SCOPES["tiktok"]})


def consume_state(db,provider,state,session):
    if not state or len(state)>200 or provider not in PROVIDERS or not session.get("authorized"):
        raise HTTPException(403,"Invalid or expired OAuth authorization")
    nonce=session.get("social_oauth_nonce")
    if not nonce: raise HTTPException(403,"OAuth session missing")
    hashed=hashlib.sha256(state.encode()).hexdigest()
    row=db.execute("""DELETE FROM social_oauth_states WHERE state_hash=%s
        AND provider=%s AND session_hash=%s
        AND created_at > NOW() - INTERVAL '10 minutes'
        RETURNING state_hash""",(hashed,provider,hashlib.sha256(nonce.encode()).hexdigest())).fetchone()
    if not row:raise HTTPException(403,"OAuth state mismatch or already used")
    return True


def _store_account(db,platform,provider,external_id,name,token,refresh=None,expires=None,scope=""):
    if platform not in PLATFORMS or not external_id or not token:
        raise HTTPException(502,"Provider did not return a usable social account")
    ident="soc_"+secrets.token_hex(14)
    db.execute("""INSERT INTO social_accounts
        (id,platform,provider,external_id,display_name,access_token_cipher,
         refresh_token_cipher,token_expires_at,granted_scopes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(platform,external_id) DO UPDATE SET
         provider=EXCLUDED.provider,display_name=EXCLUDED.display_name,
         access_token_cipher=EXCLUDED.access_token_cipher,
         refresh_token_cipher=COALESCE(EXCLUDED.refresh_token_cipher,
                                        social_accounts.refresh_token_cipher),
         token_expires_at=EXCLUDED.token_expires_at,
         granted_scopes=EXCLUDED.granted_scopes,connected_at=NOW()""",
        (ident,platform,provider,str(external_id),str(name)[:200],
         encrypt(token),encrypt(refresh) if refresh else None,expires,str(scope)[:1000]))


def _expiry(seconds):
    if seconds is None:return None
    try:
        from datetime import timedelta
        return datetime.now(timezone.utc)+timedelta(seconds=int(seconds))
    except (ValueError,TypeError,OverflowError):return None


def oauth_complete(db,provider,code):
    client,secret,base,_=_configuration(provider)
    redirect=f"{base}/api/social/oauth/{provider}/callback"
    if not code or len(code)>3000:raise HTTPException(400,"Missing OAuth authorization code")
    if provider=="meta":
        payload=_http_json(_query(GRAPH+"/oauth/access_token",{
            "client_id":client,"client_secret":secret,
            "redirect_uri":redirect,"code":code}))
        token=payload.get("access_token")
        if not token:raise HTTPException(502,"Meta did not return an access token")
        # Long-lived user token yields long-lived Page tokens.
        long=_http_json(_query(GRAPH+"/oauth/access_token",{
            "grant_type":"fb_exchange_token","client_id":client,
            "client_secret":secret,"fb_exchange_token":token}))
        token=long.get("access_token") or token
        proof=hmac.new(secret.encode(),token.encode(),hashlib.sha256).hexdigest()
        pages=_http_json(_query(GRAPH+"/me/accounts",{
            "fields":"id,name,access_token,instagram_business_account{id,username}",
            "limit":100,"access_token":token,"appsecret_proof":proof}))
        added=0
        for page in pages.get("data",[]):
            pid=str(page.get("id") or "")
            page_token=page.get("access_token")
            if not pid or not page_token:continue
            _store_account(db,"facebook",provider,pid,page.get("name") or "Facebook Page",
                           page_token,scope=SCOPES["meta"])
            added+=1
            ig=page.get("instagram_business_account") or {}
            if ig.get("id"):
                _store_account(db,"instagram",provider,str(ig["id"]),
                               ig.get("username") or "Instagram Professional",
                               page_token,scope=SCOPES["meta"])
                added+=1
        if not added:raise HTTPException(400,"No authorized Facebook Pages or linked Instagram Professional accounts found")
        return added
    if provider=="google":
        payload=_http_json("https://oauth2.googleapis.com/token",{
            "client_id":client,"client_secret":secret,
            "code":code,"redirect_uri":redirect,"grant_type":"authorization_code"})
        token=payload.get("access_token")
        if not token:raise HTTPException(502,"Google did not return an access token")
        channel=_http_json("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",headers={
            "Authorization":"Bearer "+token})
        items=channel.get("items",[])
        if not items:raise HTTPException(400,"No YouTube channel available on this authorized account")
        for item in items:
            _store_account(db,"youtube",provider,item["id"],
                           item.get("snippet",{}).get("title","YouTube Channel"),
                           token,refresh=payload.get("refresh_token"),
                           expires=_expiry(payload.get("expires_in")),
                           scope=payload.get("scope",SCOPES["google"]))
        return len(items)
    payload=_http_json("https://open.tiktokapis.com/v2/oauth/token/",{
        "client_key":client,"client_secret":secret,"code":code,
        "grant_type":"authorization_code","redirect_uri":redirect})
    token=payload.get("access_token")
    open_id=payload.get("open_id")
    if not token or not open_id:raise HTTPException(502,"TikTok did not return authorized account details")
    profile=_http_json("https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,username",
        headers={"Authorization":"Bearer "+token})
    user=(profile.get("data") or {}).get("user") or {}
    _store_account(db,"tiktok",provider,open_id,user.get("display_name") or user.get("username") or "TikTok Account",
                   token,refresh=payload.get("refresh_token"),
                   expires=_expiry(payload.get("expires_in")),
                   scope=payload.get("scope",SCOPES["tiktok"]))
    return 1


def account_summary(db):
    schema(db)
    records=db.execute("""SELECT id,platform,provider,external_id,display_name,
        granted_scopes,connected_at,token_expires_at
        FROM social_accounts ORDER BY platform,display_name""").fetchall()
    grants=db.execute("""SELECT g.staff_id,g.account_id,g.mode,s.name AS staff_name
        FROM social_publish_grants g JOIN staff s ON s.id=g.staff_id
        ORDER BY s.name""").fetchall()
    providers=[]
    base=os.getenv("SOCIAL_PUBLIC_BASE_URL","").strip()
    encryption_ready=False
    try:
        Fernet(os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY","").encode())
        encryption_ready=True
    except Exception:pass
    ready=base.startswith("https://") and encryption_ready
    for provider in PROVIDERS:
        names=CLIENT_VARS[provider]
        ok=ready and bool(os.getenv(names[0])) and bool(os.getenv(names[1]))
        providers.append({
            "id":provider,
            "name":{"meta":"Facebook & Instagram (Meta)","google":"YouTube (Google)",
                    "tiktok":"TikTok"}[provider],
            "configured":ok,
            "publishing_available":provider=="meta",
            "callback":base.rstrip("/")+f"/api/social/oauth/{provider}/callback" if base else "",
            "setup":"Developer app OAuth + HTTPS redirect + required platform approval"})
    return {"providers":providers,
            "accounts":[{**dict(a),"publishing_supported":a["platform"] in SUPPORTED_PUBLISH,
                         "token_status":"expired" if a["token_expires_at"] and a["token_expires_at"]<datetime.now(timezone.utc) else "not_expired_or_unverified"}
                        for a in records],
            "grants":[dict(g) for g in grants]}


def set_permission(db,staff_id,account_id,mode):
    schema(db)
    if mode not in PERMISSION_MODES:raise HTTPException(400,"Invalid publishing permission mode")
    staff=db.execute("SELECT id FROM staff WHERE id=%s",(staff_id,)).fetchone()
    account=db.execute("SELECT platform FROM social_accounts WHERE id=%s",(account_id,)).fetchone()
    if not staff or not account:raise HTTPException(404,"AI staff or connected account not found")
    if account["platform"] not in SUPPORTED_PUBLISH and mode!="off":
        raise HTTPException(400,"Publishing for this platform is not implemented yet; connect-only")
    db.execute("""INSERT INTO social_publish_grants(staff_id,account_id,mode)
        VALUES (%s,%s,%s) ON CONFLICT(staff_id,account_id)
        DO UPDATE SET mode=EXCLUDED.mode,updated_at=NOW()""",(staff_id,account_id,mode))
    return {"ok":True,"mode":mode}


def disconnect(db,account_id):
    account=db.execute("SELECT platform,display_name FROM social_accounts WHERE id=%s FOR UPDATE",
                       (account_id,)).fetchone()
    if not account:raise HTTPException(404,"Social account not found")
    db.execute("""UPDATE social_posts SET status='Cancelled',updated_at=NOW()
        WHERE account_id=%s AND status IN ('Pending approval','Ready')""",(account_id,))
    db.execute("DELETE FROM social_accounts WHERE id=%s",(account_id,))
    return {"ok":True,"platform":account["platform"],"display_name":account["display_name"]}
