"""Read-only invoice intake from a CEO-selected Gmail label."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from backend import social


ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
LABEL = "Obscura Invoices"


def token(db):
    rows = db.execute("""SELECT * FROM social_accounts WHERE provider='gmail'
        LIMIT 2 FOR UPDATE""").fetchall()
    if len(rows) != 1:
        raise HTTPException(409, "Connect one Gmail mailbox in Integration Centre.")
    row = rows[0]
    if row["token_expires_at"] and row["token_expires_at"] > datetime.now(timezone.utc) + timedelta(minutes=2):
        return social.decrypt(row["access_token_cipher"])
    client, secret, _, _ = social._configuration("gmail")
    result = social._http_json("https://oauth2.googleapis.com/token", {
        "grant_type": "refresh_token", "client_id": client, "client_secret": secret,
        "refresh_token": social.decrypt(row["refresh_token_cipher"]),
    })
    access = result.get("access_token")
    if not access:
        raise HTTPException(502, "Gmail token refresh failed. Reconnect Gmail.")
    db.execute("""UPDATE social_accounts SET access_token_cipher=%s,
        refresh_token_cipher=COALESCE(%s,refresh_token_cipher),token_expires_at=%s WHERE id=%s""",
        (social.encrypt(access), social.encrypt(result["refresh_token"]) if result.get("refresh_token") else None,
         social._expiry(result.get("expires_in")), row["id"]))
    return access


def list_invoices(access):
    headers = {"Authorization": f"Bearer {access}"}
    labels = social._http_json(ROOT + "/labels", headers=headers).get("labels", [])
    label = next((entry["id"] for entry in labels if entry["name"] == LABEL), None)
    if not label:
        return {"label": LABEL, "label_exists": False, "messages": []}
    messages = social._http_json(social._query(ROOT + "/messages", {
        "labelIds": label, "maxResults": 20, "fields": "messages(id),nextPageToken",
    }), headers=headers)
    result = []
    for item in messages.get("messages", []):
        from urllib.parse import quote
        ident = quote(item["id"], safe="")
        msg = social._http_json(social._query(ROOT + "/messages/" + ident, {
            "format": "metadata", "fields": "id,labelIds,internalDate,snippet,payload(headers)",
        }), headers=headers)
        if label not in msg.get("labelIds", []):
            continue
        fields = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        day = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, ZoneInfo("Asia/Kuala_Lumpur")).date()
        result.append({"id": msg["id"], "subject": fields.get("subject", "")[:250],
                       "sender": fields.get("from", "")[:200], "day": day,
                       "snippet": msg.get("snippet", "")[:500]})
    return {"label": LABEL, "label_exists": True, "messages": result,
            "more": bool(messages.get("nextPageToken"))}
