"""Google Drive links: accept only genuine Drive / Docs links, and warn when one looks private."""
from __future__ import annotations

import asyncio
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError

_ID = r"[A-Za-z0-9_-]{10,}"
_PATHS = (
    re.compile(rf"^/file/d/{_ID}"),                                         # drive.google.com/file/d/<id>/view
    re.compile(rf"^/(?:drive/(?:u/\d+/)?)?folders/{_ID}"),                 # folders
    re.compile(rf"^/(?:document|spreadsheets|presentation|forms|drawings)/d/(?:e/)?{_ID}"),   # docs.google.com
)
_QUERY_ID_PATHS = ("/open", "/uc", "/folderview", "/drive/folders")

HELP = ("Paste a Google Drive or Google Docs link, for example https://drive.google.com/file/d/…/view. "
        "In Google Drive: Share → General access → \u201cAnyone with the link\u201d → Viewer → Copy link.")


def normalise(raw: str) -> str:
    """Returns the cleaned link, or raises a plain-language error."""
    url = (raw or "").strip()
    if not url:
        raise BusinessRuleViolationError("Paste your Google Drive link. " + HELP)
    if len(url) > 500:
        raise BusinessRuleViolationError("That link is too long.")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise BusinessRuleViolationError("The link must start with https://. " + HELP)
    if parsed.username or parsed.password:
        raise BusinessRuleViolationError("That link is not a normal Google Drive link. " + HELP)
    host = (parsed.hostname or "").lower()
    if host not in ("drive.google.com", "docs.google.com"):
        raise BusinessRuleViolationError("Only Google Drive and Google Docs links are accepted. " + HELP)
    ok = any(p.match(parsed.path) for p in _PATHS)
    if not ok and parsed.path.rstrip("/") in _QUERY_ID_PATHS:
        ok = any(re.fullmatch(_ID, v) for v in parse_qs(parsed.query).get("id", []))
    if not ok:
        raise BusinessRuleViolationError("That does not look like a link to a file or folder in Google Drive. " + HELP)
    return parsed._replace(fragment="").geturl()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):            # noqa: ARG002 - we only want to SEE where it would go
        return None


def _probe(url: str) -> str:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0 (VIST-LMS link check)"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=4) as response:
            return "public" if response.status == 200 else "unknown"
    except urllib.error.HTTPError as err:
        location = (err.headers.get("Location") or "").lower()
        # A private file sends an anonymous visitor to Google's sign-in page.
        if err.code in (301, 302, 303, 307, 308) and "accounts.google.com" in location:
            return "private"
        return "unknown"
    except Exception:                                        # no internet, timeout, DNS...: never block the student because of it
        return "unknown"


async def check_public_access(url: str) -> str:
    """'public' (anyone with the link can open it), 'private' (asks visitors to sign in) or 'unknown'
    (could not tell: treated as fine). Best effort only."""
    if not settings.assignment_drive_access_check:
        return "unknown"
    return await asyncio.to_thread(_probe, url)