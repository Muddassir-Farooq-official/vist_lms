"""Watches every request for the signs of an attack and records them (it never blocks or changes a request).

- attack-looking input in the address or in a JSON / form body (passwords and codes are never read);
- refused actions (403) and forged sign-in tokens, flagged by the error handler in app/main.py;
- the wrong-password limit (429 on sign-in), and alerts for repeated wrong passwords;
- scans for well-known weak spots (/.env, /wp-admin, *.php ...) that end in "not found".
The work happens after the answer has been sent, so nobody waits for it.
"""
from __future__ import annotations

from urllib.parse import unquote_plus

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_BODY = 64 * 1024
_SKIP = ("/static/", "/health", "/favicon", "/branding/")


class SecurityWatch:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(_SKIP):
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        ctype = headers.get("content-type", "").lower()
        tee = scope["method"] in ("POST", "PUT", "PATCH") and ("json" in ctype or "x-www-form-urlencoded" in ctype)
        body: list[bytes] = []
        seen = {"size": 0, "status": 0}

        async def receive_tee():
            message = await receive()
            if message.get("type") == "http.request" and seen["size"] < MAX_BODY:
                chunk = message.get("body", b"")[: MAX_BODY - seen["size"]]
                body.append(chunk)
                seen["size"] += len(chunk)
            return message

        # Every browser that opens the sign-in page or signs in gets a random id (no personal data) so that one browser
        # used for several people's accounts can be noticed (app/security/service.py: track_device).
        device = _cookie(headers, DEVICE_COOKIE)
        new_device = None
        if not device and (scope["path"] == LOGIN_PATH or (scope["method"] == "GET" and scope["path"] == "/")):
            import secrets

            new_device = device = secrets.token_urlsafe(18)
        scope["state"]["device_id"] = device

        async def send_watch(message):
            if message.get("type") == "http.response.start":
                seen["status"] = message.get("status", 0)
                if new_device:
                    secure = "; Secure" if settings.is_production else ""
                    message.setdefault("headers", [])
                    message["headers"] = list(message["headers"]) + [(b"set-cookie", (f"{DEVICE_COOKIE}={new_device}; Max-Age={2 * 365 * 86400}; "
                                                                                     f"Path=/; HttpOnly; SameSite=Lax{secure}").encode())]
            await send(message)

        await self.app(scope, receive_tee if tee else receive, send_watch)
        try:
            await self._after(scope, headers, ctype, b"".join(body), seen["status"])
        except Exception as exc:  # noqa: BLE001 - recording must never break a request
            logger.warning("security_watch_failed", error=str(exc))

    async def _after(self, scope, headers: dict, ctype: str, body: bytes, status: int) -> None:
        from app.security import service

        path = scope["path"]
        state = scope.get("state") or {}
        events: list[tuple[str, str | None]] = []
        flagged = state.get("security_event")
        if flagged:
            events.append(flagged)

        query = scope.get("query_string", b"").decode("latin-1")
        texts = [unquote_plus(path), query] + service.body_texts(body, ctype)
        hit = service.scan(texts)
        if hit:
            events.append(("SUSPICIOUS_INPUT", f"{hit[0]}: {hit[1]}"))
        if status == 404 and service.looks_like_probe(path):
            events.append(("PROBE", "Looked for " + path[:200]))
        login = path == LOGIN_PATH
        login_username = None
        if login and status in (200, 401, 429):
            login_username = _login_username(body, ctype)
            if status == 429:
                events.append(("SIGNIN_LIMIT", f"Too many wrong passwords for \"{login_username or '?'}\""))
        wrong = _wrong_portal(scope, headers, status)
        if wrong:
            events.append(("WRONG_PORTAL", wrong))
        if not events and not (login and status in (200, 401)):
            return

        from starlette.requests import Request

        from app.core.net import client_ip

        ip = client_ip(Request(scope))
        user_id, username = _who(headers)
        username = username or login_username
        agen, session = await _session(scope)
        try:
            for kind, detail in events:
                if kind == "ACCESS_DENIED" and not await _first_denial(user_id or ip, scope["method"], path):
                    continue                     # the same refusal again within ten minutes: one row is enough
                await service.record(session, kind, ip=ip, user_id=user_id, username=username, user_agent=headers.get("user-agent"),
                                     method=scope["method"], path=(path + ("?" + query if query else ""))[:255], detail=detail)
            if login and status in (200, 401):
                await service.track_device(session, device_id=state.get("device_id") or "", username=login_username, success=status == 200,
                                           ip=ip, user_agent=headers.get("user-agent"), path=path)
            if login and status == 401:
                await service.failed_signin_check(session, username=login_username, ip=ip)
        finally:
            await agen.aclose()


DEVICE_COOKIE = "vist_device"
LOGIN_PATH = f"{settings.api_v1_prefix}/auth/login"
# Portals a student or a parent may open. Anything else under /dashboard/ is someone else's screens.
OWN_PORTALS = {"STUDENT": ("student",), "PARENT": ("parent",)}


def _cookie(headers: dict, name: str) -> str | None:
    from http.cookies import SimpleCookie

    if not headers.get("cookie"):
        return None
    try:
        jar = SimpleCookie()
        jar.load(headers["cookie"])
        value = jar[name].value if name in jar else None
    except Exception:  # noqa: BLE001
        return None
    if name == DEVICE_COOKIE:                       # our own random id: anything else in that cookie is ignored
        return value if value and len(value) <= 40 and value.replace("-", "").replace("_", "").isalnum() else None
    return value or None


def _wrong_portal(scope, headers: dict, status: int) -> str | None:
    """A student or parent opening a page of another portal (administration, teachers, accounts ...)."""
    path = scope["path"]
    if scope["method"] != "GET" or not path.startswith("/dashboard/") or status >= 400:
        return None
    parts = path.split("/")
    portal = parts[2] if len(parts) > 2 else ""
    roles = _roles(headers)
    if not roles or any(r not in OWN_PORTALS for r in roles):
        return None                                       # staff move between screens as their work needs; the APIs decide
    allowed = {p for r in roles for p in OWN_PORTALS[r]}
    if portal in allowed or not portal:
        return None
    return f"A {'student' if 'STUDENT' in roles else 'parent'} account opened {path[:120]} (the {portal} screens)."


def _roles(headers: dict) -> list[str]:
    from app.core.security import decode_access_token

    token = _cookie(headers, "access_token") if headers.get("cookie") else None
    if not token:
        auth = headers.get("authorization", "")
        token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else None
    if not token:
        return []
    try:
        return list(decode_access_token(token).get("roles") or [])
    except Exception:  # noqa: BLE001
        return []


def _login_username(body: bytes, ctype: str) -> str | None:
    import json

    try:
        if "json" in ctype:
            value = json.loads(body.decode("utf-8", errors="replace")).get("username")
        else:
            from urllib.parse import parse_qs

            value = (parse_qs(body.decode("utf-8", errors="replace")).get("username") or [None])[0]
        return str(value)[:64] if value else None
    except Exception:  # noqa: BLE001
        return None


def _who(headers: dict) -> tuple[str | None, str | None]:
    """The signed-in person, from a VALID access token only (a forged one names nobody)."""
    from http.cookies import SimpleCookie

    from app.core.security import decode_access_token

    token = None
    if headers.get("cookie"):
        try:
            jar = SimpleCookie()
            jar.load(headers["cookie"])
            token = jar["access_token"].value if "access_token" in jar else None
        except Exception:  # noqa: BLE001
            token = None
    auth = headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
    if not token:
        return None, None
    try:
        payload = decode_access_token(token)
        return str(payload.get("sub")), payload.get("username")
    except Exception:  # noqa: BLE001
        return None, None


async def _first_denial(who: str | None, method: str, path: str) -> bool:
    try:
        from app.core.redis import get_redis

        return bool(await get_redis().set(f"secdeny:{who}:{method}:{path}"[:250], "1", nx=True, ex=600))
    except Exception:  # noqa: BLE001
        return True


async def _session(scope):
    """A database session the same way routes get one (tests swap get_db for their own)."""
    from app.core import database as db_module

    app = scope.get("app")
    dep = (getattr(app, "dependency_overrides", None) or {}).get(db_module.get_db, db_module.get_db)
    agen = dep()
    session = await agen.__anext__()
    return agen, session