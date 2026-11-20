"""Canva operations assigned to the CEO-approved Creative roles."""
import base64
import io
import json
import re
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from backend import deliverables, social

APP = "https://api.canva.com/rest/v1"
PRODUCERS = {"art director", "graphic designer", "brand identity designer"}


def allowed(staff, operation):
    department = staff.get("department")
    role = " ".join((staff.get("role") or "").casefold().split()).removeprefix("ai ")
    if operation == "produce":
        return (department == "Creative" and role in PRODUCERS | {"ui/ux designer"}
                or department == "Marketing & Growth"
                and role in {"social media strategist", "digital marketing specialist", "content strategist"})
    if operation == "video":
        return department == "Creative" and role in {"video editor", "motion graphics designer"}
    if operation == "review" and department == "Creative":
        return (role == "creative quality assurance specialist"
                or role == "creative director" and (staff.get("name") or "").casefold() == "maryam")
    return False

def access_token(db):
    accounts = db.execute("""SELECT * FROM social_accounts WHERE provider='canva'
        ORDER BY connected_at DESC LIMIT 2 FOR UPDATE""").fetchall()
    if len(accounts) != 1:
        raise HTTPException(409, "Connect exactly one Canva workspace in Integration Centre")
    account = accounts[0]
    expires = account["token_expires_at"]
    if expires and expires > datetime.now(timezone.utc) + timedelta(minutes=5):
        return social.decrypt(account["access_token_cipher"])
    client, secret, _, _ = social._configuration("canva")
    basic = base64.b64encode(f"{client}:{secret}".encode()).decode()
    data = social._http_json(APP + "/oauth/token", {
        "grant_type": "refresh_token",
        "refresh_token": social.decrypt(account["refresh_token_cipher"]),
    }, headers={"Authorization": f"Basic {basic}"})
    if not data.get("access_token") or not data.get("refresh_token"):
        raise HTTPException(502, "Canva token refresh failed. Reconnect Canva in Integration Centre.")
    db.execute("""UPDATE social_accounts SET access_token_cipher=%s,
        refresh_token_cipher=%s,token_expires_at=%s WHERE id=%s""", (
        social.encrypt(data["access_token"]), social.encrypt(data["refresh_token"]),
        social._expiry(data.get("expires_in")), account["id"]))
    return data["access_token"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, *, headers=None, data=None, limit=1_000_000):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=12) as response:
            result = response.read(limit + 1)
    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(502, "Canva request failed. Check Canva before retrying.") from None
    if len(result) > limit:
        raise HTTPException(502, "Canva response is too large for Task Centre")
    return result


def job(token, endpoint, payload, content_type, headers=None):
    auth = {"Authorization": f"Bearer {token}", "Content-Type": content_type, **(headers or {})}
    result = json.loads(request(APP + endpoint, headers=auth, data=payload))["job"]
    job_id = urllib.parse.quote(result["id"], safe="")
    deadline = time.monotonic() + 35
    while result["status"] == "in_progress" and time.monotonic() < deadline:
        time.sleep(2)
        result = json.loads(request(APP + endpoint + "/" + job_id, headers=auth))["job"]
    if result["status"] != "success":
        raise HTTPException(502, "Canva job did not finish successfully. Check Canva before retrying.")
    return result


def export_asset(token, design_id, file_type):
    payload = json.dumps({"design_id": design_id, "format": {"type": file_type}}).encode()
    urls = job(token, "/exports", payload, "application/json").get("urls") or []
    if len(urls) != 1:
        raise HTTPException(502, "Canva did not return a single downloadable file")
    url = urllib.parse.urlsplit(urls[0])
    host = url.hostname or ""
    if (url.scheme != "https" or url.username or url.password or url.port not in (None, 443)
        or not (host == "canva.com" or host.endswith(".canva.com")
                or host.endswith(".canva.cn") or host == "canva-apps.com"
                or host.endswith(".canva-apps.com"))):
        raise HTTPException(502, "Canva did not return a supported download URL")
    data = request(urls[0], limit=deliverables.MAX_ASSET_BYTES)
    deliverables.check_file(f"canva-design.{file_type}", data)
    return data


def export_pdf(token, design_id):
    return export_asset(token, design_id, "pdf")


def review_id(brief):
    match = re.search(r"https://www\.canva\.com/design/([A-Za-z0-9_-]+)(?=[/\s?]|$)", brief)
    if not match:
        raise HTTPException(400, "Include the Canva design link for this task")
    return match.group(1)


def make_layout(title, answer):
    """Lay out the staff copy in an editable portrait presentation."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(8.27), Inches(11.69)
    color = RGBColor.from_string
    lines = []
    for line in answer.splitlines():
        clean = re.sub(r"^#+\s*", "", line).replace("**", "").strip()
        lines.extend(textwrap.wrap(clean, width=68) or [""])
    for start in range(0, len(lines), 25):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = color("FBF5ED")
        box = slide.shapes.add_textbox(Inches(.65), Inches(.55), Inches(6.97), Inches(1.2))
        p = box.text_frame.paragraphs[0]
        p.text = title[:105]
        p.font.size, p.font.bold, p.font.color.rgb = Pt(25), True, color("552038")
        frame = slide.shapes.add_textbox(Inches(.65), Inches(1.95), Inches(6.97), Inches(8.9)).text_frame
        frame.word_wrap = True
        for j, line in enumerate(lines[start:start + 25]):
            p = frame.add_paragraph() if j else frame.paragraphs[0]
            p.text = line
            p.font.size, p.font.color.rgb = Pt(15), color("222029")
            p.space_after = Pt(3)
    stream = io.BytesIO()
    deck.save(stream)
    return stream.getvalue()


def import_design(token, title, content):
    metadata = json.dumps({"title_base64": base64.b64encode(title.encode()).decode(),
                           "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"})
    result = job(token, "/imports", content, "application/octet-stream",
                 {"Import-Metadata": metadata})
    designs = (result.get("result") or {}).get("designs") or []
    if len(designs) != 1:
        raise HTTPException(502, "Canva import did not return one design")
    design_id = designs[0]["id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", design_id):
        raise HTTPException(502, "Canva returned an invalid design ID")
    link = f"https://www.canva.com/design/{design_id}/edit"
    return link, export_pdf(token, design_id)


def produce(token, title, answer):
    return import_design(token, title, make_layout(title, answer))
