"""Publishing Desk: account-scoped staff permission, CEO approval and actual Meta posting."""
import ipaddress
import json
import secrets
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from fastapi import HTTPException
from backend import social

def _clean_media(url):
    url=(url or "").strip()
    if not url:return None
    try:
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        if parsed.hostname in ("localhost","localhost.localdomain") or parsed.hostname.endswith(".local"):
            raise ValueError()
        try:
            ipaddress.ip_address(parsed.hostname)
            raise ValueError()
        except ValueError as exc:
            if str(exc):raise
        if len(url)>1800:raise ValueError()
    except Exception:
        raise HTTPException(400,"Media must be a publicly reachable HTTPS URL on a verified host")
    return url

def _authorization(db,staff_id,account_id):
    record=db.execute("""SELECT a.*,g.mode AS permission
        FROM social_accounts a
        JOIN social_publish_grants g ON g.account_id=a.id
        JOIN staff s ON s.id=g.staff_id
        WHERE a.id=%s AND g.staff_id=%s""",(account_id,staff_id)).fetchone()
    if not record or record["permission"] not in ("approval_required","direct_publish"):
        raise HTTPException(403,"CEO has not granted this staff member publish rights to this account")
    if record["platform"] not in social.SUPPORTED_PUBLISH:
        raise HTTPException(400,"Publishing not implemented for this platform")
    return record

def create(db,staff_id,account_id,caption,media_url=None):
    account=_authorization(db,staff_id,account_id)
    if not isinstance(caption,str) or not 1<=len(caption.strip())<=2200:
        raise HTTPException(400,"Post text must be 1–2200 characters")
    media=_clean_media(media_url)
    if account["platform"]=="instagram" and not media:
        raise HTTPException(400,"Instagram image publishing requires a public HTTPS image URL")
    state="Pending approval" if account["permission"]=="approval_required" else "Ready"
    ident="post_"+secrets.token_hex(13)
    db.execute("""INSERT INTO social_posts
        (id,staff_id,account_id,platform,account_name,caption,media_url,status,approval_mode)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ident,staff_id,account_id,account["platform"],account["display_name"],
         caption.strip(),media,state,account["permission"]))
    return {"id":ident,"status":state}

def update(db,ident,caption,media_url=None):
    post=db.execute("SELECT * FROM social_posts WHERE id=%s FOR UPDATE",(ident,)).fetchone()
    if not post:raise HTTPException(404,"Social draft not found")
    if post["status"] not in ("Pending approval","Ready"):
        raise HTTPException(409,"Only unpublished drafts can be edited")
    _authorization(db,post["staff_id"],post["account_id"])
    if not isinstance(caption,str) or not 1<=len(caption.strip())<=2200:
        raise HTTPException(400,"Post text must be 1–2200 characters")
    media=_clean_media(media_url)
    if post["platform"]=="instagram" and not media:
        raise HTTPException(400,"Instagram requires a public HTTPS image URL")
    db.execute("""UPDATE social_posts SET caption=%s,media_url=%s,
        status='Pending approval',updated_at=NOW() WHERE id=%s""",
        (caption.strip(),media,ident))
    return {"ok":True,"status":"Pending approval"}

def approve(db,ident):
    post=db.execute("SELECT * FROM social_posts WHERE id=%s FOR UPDATE",(ident,)).fetchone()
    if not post:raise HTTPException(404,"Social draft not found")
    _authorization(db,post["staff_id"],post["account_id"])
    if post["status"]!="Pending approval":
        raise HTTPException(409,"Draft does not require approval")
    db.execute("UPDATE social_posts SET status='Ready',updated_at=NOW() WHERE id=%s",(ident,))
    return {"ok":True}

def reject(db,ident):
    post=db.execute("SELECT * FROM social_posts WHERE id=%s FOR UPDATE",(ident,)).fetchone()
    if not post:raise HTTPException(404,"Social draft not found")
    if post["status"] not in ("Pending approval","Ready"):
        raise HTTPException(409,"Cannot reject a published or processing post")
    db.execute("UPDATE social_posts SET status='Rejected',updated_at=NOW() WHERE id=%s",(ident,))
    return {"ok":True}

def list_posts(db):
    social.schema(db)
    records=db.execute("""SELECT p.id,p.staff_id,s.name AS staff_name,p.account_id,
        p.platform,p.account_name,p.caption,p.media_url,p.status,
        p.approval_mode,p.platform_post_id,p.error,p.created_at,p.updated_at
        FROM social_posts p LEFT JOIN staff s ON s.id=p.staff_id
        ORDER BY p.created_at DESC LIMIT 80""").fetchall()
    return {"posts":[dict(x) for x in records]}

def claim(db,ident):
    post=db.execute("SELECT * FROM social_posts WHERE id=%s FOR UPDATE",(ident,)).fetchone()
    if not post:raise HTTPException(404,"Social draft not found")
    if post["status"]!="Ready":
        raise HTTPException(409,"Post is not ready, already published, or awaiting approval")
    account=_authorization(db,post["staff_id"],post["account_id"])
    if account["platform"]!=post["platform"]:raise HTTPException(409,"Account platform changed")
    if account["token_expires_at"] and account["token_expires_at"]<datetime.now(timezone.utc):
        raise HTTPException(409,"Social account token has expired; reconnect first")
    db.execute("UPDATE social_posts SET status='Publishing',updated_at=NOW() WHERE id=%s",(ident,))
    return dict(post),social.decrypt(account["access_token_cipher"])

def _form(url,data):
    request=urllib.request.Request(
        url,data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type":"application/x-www-form-urlencoded","Accept":"application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request,timeout=45) as response:
            result=json.load(response)
    except urllib.error.HTTPError as ex:
        raise HTTPException(502,f"Social publishing provider rejected this post (HTTP {ex.code})")
    except Exception:
        # A timeout could mean the remote post succeeded. Caller must NOT retry
        # blindly until the CEO checks the platform.
        raise HTTPException(502,"Publishing result uncertain; check the social account before retrying")
    if "error" in result:
        raise HTTPException(502,"Social provider returned an error")
    return result

def send(post,token):
    if post["platform"]=="facebook":
        result=_form(f"{social.GRAPH}/{urllib.parse.quote(post['platform_external_id'] if 'platform_external_id' in post else post['external_id'])}/feed",
                     {"message":post["caption"],"access_token":token})
        result_id=result.get("id")
        if not result_id:raise HTTPException(502,"Facebook did not confirm a post ID")
        return str(result_id)
    if post["platform"]=="instagram":
        result=_form(f"{social.GRAPH}/{urllib.parse.quote(post['external_id'])}/media",
                     {"caption":post["caption"],"image_url":post["media_url"],"access_token":token})
        creation=result.get("id")
        if not creation:raise HTTPException(502,"Instagram did not create a media container")
        published=_form(f"{social.GRAPH}/{urllib.parse.quote(post['external_id'])}/media_publish",
                        {"creation_id":creation,"access_token":token})
        result_id=published.get("id")
        if not result_id:raise HTTPException(502,"Instagram did not confirm media publication")
        return str(result_id)
    raise HTTPException(400,"Platform publishing is not implemented")

def publish_claimed(post,token):
    if not post.get("external_id"):
        raise HTTPException(409,"Missing platform account identifier")
    return send(post,token)

def finish(db,ident,external_id=None,error=None):
    if external_id:
        db.execute("""UPDATE social_posts SET status='Published',platform_post_id=%s,
            error=NULL,updated_at=NOW() WHERE id=%s AND status='Publishing'""",
            (external_id,ident))
    else:
        # Uncertain state requires operator reconciliation and is not retryable.
        db.execute("""UPDATE social_posts SET status='Needs verification',
            error=%s,updated_at=NOW() WHERE id=%s AND status='Publishing'""",
            (str(error or "Publishing outcome unknown")[:180],ident))
