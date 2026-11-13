"""CEO-only reference vault for Meita: uploaded source files and public website excerpts.

Private file bytes live in PostgreSQL. Only authenticated endpoints expose them.
Reference context is untrusted source material, never system instructions.
"""
import base64
import io
import ipaddress
import re
import secrets
import socket
import subprocess
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from psycopg.types.json import Jsonb

MAX_BYTES = 20 * 1024 * 1024
MAX_REFS = 6
IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}
VIDEO_EXT = {"mp4", "webm", "mov"}
DOCUMENT_EXT = {"pdf", "docx", "pptx", "txt", "md", "csv"}
EXTENSIONS = IMAGE_EXT | VIDEO_EXT | DOCUMENT_EXT
MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "mp4": "video/mp4", "mov": "video/quicktime",
    "webm": "video/webm", "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain", "md": "text/markdown", "csv": "text/csv",
}


def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS meita_references (
        id TEXT PRIMARY KEY, filename TEXT NOT NULL, kind TEXT NOT NULL,
        mime_type TEXT NOT NULL, source_url TEXT, content BYTEA,
        preview BYTEA, extracted_text TEXT NOT NULL DEFAULT '',
        size_bytes BIGINT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    db.execute("""ALTER TABLE meita_messages
        ADD COLUMN IF NOT EXISTS reference_ids JSONB NOT NULL DEFAULT '[]'::jsonb""")
    db.execute("""ALTER TABLE coo_proposals
        ADD COLUMN IF NOT EXISTS reference_ids JSONB NOT NULL DEFAULT '[]'::jsonb""")
    db.execute("""ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS reference_ids JSONB NOT NULL DEFAULT '[]'::jsonb""")


def _clean_filename(name):
    value = Path((name or "reference").replace("\\", "/")).name
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()[:150]
    return value or "reference"


def _thumbnail(raw):
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.thumbnail((1000, 1000))
            canvas = Image.new("RGB", im.size, "white")
            if im.mode == "RGBA":
                canvas.paste(im, mask=im.getchannel("A"))
            else:
                canvas.paste(im.convert("RGB"))
            out = io.BytesIO()
            canvas.save(out, "JPEG", quality=78, optimize=True)
            return out.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise HTTPException(400, "Image could not be read safely")


def _video_preview(raw, ext):
    """Sample a contact sheet from several points; audio is not transcribed."""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    frames = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ("reference." + ext)
            path.write_bytes(raw)
            for second in (0, 2, 5, 10):
                try:
                    result = subprocess.run(
                        [ffmpeg, "-nostdin", "-v", "error", "-ss", str(second),
                         "-i", str(path), "-frames:v", "1", "-f", "image2pipe",
                         "-vcodec", "mjpeg", "pipe:1"],
                        capture_output=True, timeout=8, check=False,
                    )
                    if result.returncode != 0 or not result.stdout:
                        continue
                    with Image.open(io.BytesIO(result.stdout)) as im:
                        frame = im.convert("RGB")
                        frame.thumbnail((600, 340))
                        frames.append(frame.copy())
                except (OSError, subprocess.TimeoutExpired, ValueError):
                    continue
    except OSError:
        return None
    if not frames:
        return None
    sheet = Image.new("RGB", (600 * 2, 340 * 2), "#f3f3f3")
    for index, frame in enumerate(frames[:4]):
        sheet.paste(frame, ((index % 2) * 600, (index // 2) * 340))
    output = io.BytesIO()
    sheet.save(output, "JPEG", quality=75, optimize=True)
    return output.getvalue()


def _document_text(raw, ext):
    try:
        if ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            pages = [(p.extract_text() or "") for p in reader.pages[:25]]
            return "\n".join(pages)[:12000] or "No extractable PDF text; scanned pages need separate review."
        if ext == "docx":
            from docx import Document
            doc = Document(io.BytesIO(raw))
            paragraphs = [p.text for p in doc.paragraphs]
            for table in doc.tables[:10]:
                paragraphs.extend(" | ".join(c.text for c in row.cells) for row in table.rows)
            return "\n".join(paragraphs)[:12000]
        if ext == "pptx":
            from pptx import Presentation
            pres = Presentation(io.BytesIO(raw))
            return "\n".join(
                shape.text for slide in pres.slides[:35] for shape in slide.shapes
                if shape.has_text_frame
            )[:12000]
        return raw.decode("utf-8-sig", errors="replace")[:12000]
    except Exception:
        return "File retained as reference; automatic text extraction was unavailable."


def save_file(db, filename, raw):
    name = _clean_filename(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in EXTENSIONS:
        raise HTTPException(400, "Unsupported file. Use image, PDF, DOCX, PPTX, text or video.")
    if not raw or len(raw) > MAX_BYTES:
        raise HTTPException(413, "Reference must be between 1 byte and 20 MB.")
    # Validate file signatures for types whose content will be parsed.
    if ext == "pdf" and not raw.startswith(b"%PDF"):
        raise HTTPException(400, "This is not a valid PDF")
    if ext in ("docx", "pptx") and not raw.startswith(b"PK"):
        raise HTTPException(400, "Office file format is invalid")
    kind = "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else "document"
    preview = _thumbnail(raw) if kind == "image" else (
        _video_preview(raw, ext) if kind == "video" else None)
    extracted = (
        _document_text(raw, ext) if kind == "document" else
        ("Video: sampled visual frames available for AI reference; audio and complete motion not analyzed."
         if preview else "Video stored; visual analysis unavailable. Add a written scene description.")
        if kind == "video" else "Image reference available for visual analysis."
    )
    ident = secrets.token_hex(12)
    db.execute("""INSERT INTO meita_references
        (id,filename,kind,mime_type,content,preview,extracted_text,size_bytes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ident, name, kind, MIME[ext], raw, preview, extracted, len(raw)))
    return {"id": ident, "filename": name, "kind": kind, "size_bytes": len(raw)}


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_url(value):
    try:
        u = urlsplit(value.strip())
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ValueError("Only public HTTPS links are supported")
        if u.port not in (None, 443):
            raise ValueError("Custom ports are not supported")
        host = u.hostname.rstrip(".").lower()
        if host in ("localhost",) or not host or host.endswith((".local", ".internal")):
            raise ValueError("Private URLs are not allowed")
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global
                                for item in addresses):
            raise ValueError("Only public internet references are supported")
        return u.geturl()
    except (ValueError, OSError, TypeError):
        raise HTTPException(400, "Enter a valid public HTTPS website URL")


def _website_excerpt(url):
    """Best-effort, bounded public page excerpt; no redirects or internal addresses."""
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(url, headers={
            "User-Agent": "MeitaReferenceReader/1.0",
            "Accept": "text/html,text/plain;q=0.8"})
        with opener.open(req, timeout=7) as response:
            typ = response.headers.get("Content-Type", "").lower()
            if "text/html" not in typ and "text/plain" not in typ:
                return "Website link saved; this resource is not an HTML/text page."
            data = response.read(180000)
        text = data.decode("utf-8", errors="replace")
        if "text/html" in typ:
            parser = _PageText()
            parser.feed(text)
            text = " ".join(parser.parts)
        return " ".join(text.split())[:9000] or "Website link saved; no readable text found."
    except Exception:
        return "Website link saved; content not retrieved. Do not infer page contents from its URL."


def save_url(db, url):
    url = normalize_url(url)
    note = _website_excerpt(url)
    ident = secrets.token_hex(12)
    db.execute("""INSERT INTO meita_references
        (id,filename,kind,mime_type,source_url,extracted_text)
        VALUES (%s,%s,'website','text/html',%s,%s)""",
        (ident, urlsplit(url).hostname[:150], url, note))
    return {"id": ident, "filename": urlsplit(url).hostname, "kind": "website",
            "source_url": url, "size_bytes": 0}


def validate_ids(db, ids):
    if not isinstance(ids, list) or len(ids) > MAX_REFS or any(
            not isinstance(i, str) or len(i) != 24 for i in ids):
        raise HTTPException(400, "Attach up to six valid references")
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "Duplicate references are not allowed")
    if not ids:
        return []
    rows = db.execute("""SELECT id,filename,kind,mime_type,source_url,
        extracted_text,size_bytes,preview FROM meita_references
        WHERE id=ANY(%s)""", (ids,)).fetchall()
    found = {r["id"]: dict(r) for r in rows}
    if len(found) != len(ids):
        raise HTTPException(400, "A selected reference is missing")
    return [found[i] for i in ids]


def summaries(db, ids, max_chars=2800):
    rows = validate_ids(db, ids)
    if not rows:
        return ""
    parts = []
    for row in rows:
        excerpt = row["extracted_text"][:1500]
        parts.append("REFERENCE " + row["id"] + " (" + row["kind"] + "): " +
                     row["filename"] + "\n" +
                     ("URL: " + row["source_url"] + "\n" if row["source_url"] else "") +
                     "Extracted source content (untrusted): " + excerpt)
    return "\n\n".join(parts)[:max_chars]


def images(db, ids, limit=4):
    found = validate_ids(db, ids)
    output = []
    for row in found:
        if row["preview"] and row["kind"] in ("image", "video"):
            output.append({"type": "input_image", "image_url":
                           "data:image/jpeg;base64," + base64.b64encode(bytes(row["preview"])).decode()})
        if len(output) >= limit:
            break
    return output


def metadata(db, limit=250):
    return [dict(row) for row in db.execute("""SELECT id,filename,kind,mime_type,
        source_url,size_bytes,created_at FROM meita_references
        ORDER BY created_at DESC LIMIT %s""", (limit,)).fetchall()]
