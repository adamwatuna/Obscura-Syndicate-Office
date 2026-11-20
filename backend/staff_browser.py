"""Read-only public page screenshots for staff tasks."""
import base64
import ipaddress
import json
import os
import re
import urllib.request
from urllib.parse import urlsplit

_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_PUBLIC_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}")


def public_url_from_brief(brief):
    for match in re.finditer(r"https://[^\s<>\]\x22]+", brief or ""):
        url = match.group().rstrip(".,);")
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if (len(url) > 2000 or parsed.username or parsed.password or parsed.port
                    or parsed.query or parsed.fragment or not _PUBLIC_HOST.fullmatch(host)
                    or host.endswith((".local", ".internal", ".localhost", ".onion", ".test"))):
                continue
            try:
                ipaddress.ip_address(host)
            except ValueError:
                return url
        except ValueError:
            continue
    return None


def screenshot(url):
    account = os.getenv("CLOUDFLARE_BROWSER_ACCOUNT_ID", "").strip()
    token = os.getenv("CLOUDFLARE_BROWSER_API_TOKEN", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account) or not token:
        return None
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/browser-run/screenshot",
        data=json.dumps({"url": url, "viewport": {"width": 1280, "height": 900},
                         "gotoOptions": {"waitUntil": "domcontentloaded", "timeout": 9000}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            image = response.read(_MAX_IMAGE_BYTES + 1)
    except (OSError, ValueError):
        return None
    if len(image) > _MAX_IMAGE_BYTES or not image.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return image


def image_input(image):
    encoded = base64.b64encode(image).decode("ascii")
    return {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"}
