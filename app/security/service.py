"""Recording security events, spotting attack-looking input, and alerting the administration.

Everything here is best-effort: a failure to record or alert is logged and swallowed, never turned into an error for
the person whose request triggered it.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.security.models import SecurityEvent

logger = get_logger(__name__)

KINDS = {
    "ACCESS_DENIED": ("Tried something their role does not allow", "WARN"),
    "SUSPICIOUS_INPUT": ("Sent attack-looking input (SQL injection, script injection or path tricks)", "HIGH"),
    "BAD_TOKEN": ("Used a forged or tampered sign-in token", "HIGH"),
    "SIGNIN_LIMIT": ("Stopped by the wrong-password limit", "WARN"),
    "PROBE": ("Scanned for well-known weak spots (e.g. /.env, /wp-admin)", "INFO"),
}

# At most this many rows per kind and address in ten minutes: a scanner hammering the site cannot fill the database.
ROWS_PER_10_MIN = 20
ALERT_EVERY_MINUTES = 30

# ------------------------------------------------------------------ what attack-looking input looks like
_PATTERNS = [
    ("SQL injection", re.compile(
        r"\bunion\b[\s\S]{0,40}\bselect\b|'\s*(or|and)\s+['\"\w]+\s*(=|like)\s*['\"\w]|;\s*(drop|truncate|alter|delete|insert|update)\s+\w"
        r"|\b(pg_sleep|sleep|benchmark|waitfor\s+delay)\s*\(|\binformation_schema\b|\bpg_catalog\b|'\s*--|\bxp_cmdshell\b", re.I)),
    ("Script injection (XSS)", re.compile(
        r"<\s*script\b|javascript\s*:|<[^>]*\son[a-z]+\s*=|<\s*(iframe|object|embed|svg|img)\b[^>]*>|document\.cookie|\balert\s*\(\s*(document|1|\x27|\")", re.I)),
    ("Path traversal", re.compile(r"(\.\.[/\\]){2,}|/etc/passwd|\\windows\\win\.ini|%00", re.I)),
    ("Command injection", re.compile(r"[;|`]\s*(cat|wget|curl|bash|sh|nc|powershell)\s|\$\((cat|id|whoami)", re.I)),
]
_PROBE = re.compile(
    r"^/(\.env|\.git|\.aws|\.ssh|\.htaccess|\.DS_Store|wp-admin|wp-login|wp-content|wp-includes|xmlrpc\.php|phpmyadmin|pma|myadmin|"
    r"admin\.php|config\.php|server-status|cgi-bin|vendor/phpunit|actuator|boaform|HNAP1|owa|autodiscover|solr|jenkins|console)"
    r"|\.(php|asp|aspx|jsp|cgi)$", re.I)
_SECRET_KEYS = ("password", "token", "code", "otp", "secret")


def looks_like_probe(path: str) -> bool:
    return bool(_PROBE.search(path or ""))


def _values(obj, out: list[str], depth: int = 0) -> None:
    if depth > 6 or len(out) > 400:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(s in str(k).lower() for s in _SECRET_KEYS):
                continue                                       # never look at (or store) passwords and codes
            _values(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _values(v, out, depth + 1)
    elif isinstance(obj, str):
        out.append(obj)


def body_texts(body: bytes, content_type: str) -> list[str]:
    """The text values in a JSON or form body (secrets left out)."""
    if not body:
        return []
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    if "json" in content_type:
        try:
            _values(json.loads(text), out)
        except ValueError:
            out.append(text)
    elif "x-www-form-urlencoded" in content_type:
        for part in text.split("&"):
            k, _, v = part.partition("=")
            if not any(s in unquote_plus(k).lower() for s in _SECRET_KEYS):
                out.append(unquote_plus(v))
    return out


def scan(texts: list[str]) -> tuple[str, str] | None:
    """(what it looks like, the matching snippet) for the first attack-looking value, or None."""
    for raw in texts:
        if not raw or len(raw) < 4:
            continue
        candidates = {raw, unquote_plus(raw), unquote_plus(unquote_plus(raw))}
        for value in candidates:
            for label, pattern in _PATTERNS:
                m = pattern.search(value)
                if m:
                    start = max(m.start() - 30, 0)
                    return label, value[start:m.end() + 50].replace("\n", " ")[:160]
    return None


# ------------------------------------------------------------------ recording
async def _redis_count(key: str, seconds: int) -> int:
    try:
        from app.core.redis import get_redis

        r = get_redis()
        n = await r.incr(key)
        if n == 1:
            await r.expire(key, seconds)
        return int(n)
    except Exception:  # noqa: BLE001
        return 1


async def record(session: AsyncSession, kind: str, *, ip: str | None, user_id=None, username: str | None = None,
                 user_agent: str | None = None, method: str | None = None, path: str | None = None, detail: str | None = None) -> None:
    """Write one event in its own committed session (it must survive the request's rollback), then check the alert rules."""
    from app.core.side_log import committed_now

    if kind not in KINDS:
        return
    if await _redis_count(f"secev:{kind}:{ip or '-'}", 600) > ROWS_PER_10_MIN:
        return
    try:
        uid = uuid.UUID(str(user_id)) if user_id else None
    except ValueError:
        uid = None
    from sqlalchemy.exc import IntegrityError

    async def _write(link):
        async with committed_now(session) as side:
            side.add(SecurityEvent(kind=kind, severity=KINDS[kind][1], user_id=link, username=(username or None) and username[:64],
                                   ip_address=ip if ip and ip != "unknown" else None, user_agent=(user_agent or "")[:255] or None,
                                   method=(method or "")[:10] or None, path=(path or "")[:255] or None, detail=(detail or "")[:500] or None))

    try:
        try:
            await _write(uid)
        except IntegrityError:
            await _write(None)            # the account row is not committed yet: keep the event, with the username only
        async with committed_now(session) as side:
            await check_alerts(side, kind, ip=ip, user_id=uid, username=username)
    except Exception as exc:  # noqa: BLE001
        logger.warning("security_event_not_recorded", kind=kind, error=str(exc))


async def failed_signin_check(session: AsyncSession, *, username: str | None, ip: str | None) -> None:
    """After a wrong password: alert when one account or one address has failed many times in a short while."""
    from app.core.side_log import committed_now

    try:
        async with committed_now(session) as side:
            await _check_failed_signins(side, username=username, ip=ip)
    except Exception as exc:  # noqa: BLE001
        logger.warning("security_signin_check_failed", error=str(exc))


# ------------------------------------------------------------------ alerting
FAILED_PER_ACCOUNT = 10        # wrong passwords for one account within 15 minutes
FAILED_PER_ADDRESS = 20        # wrong passwords from one address (any accounts) within 15 minutes
DENIED_PER_PERSON = 5          # refused actions by one signed-in person within 10 minutes
SUSPICIOUS_PER_ADDRESS = 3     # attack-looking requests from one address within 10 minutes
BAD_TOKEN_PER_ADDRESS = 3


async def _check_failed_signins(session: AsyncSession, *, username: str | None, ip: str | None) -> None:
    from app.auth.models import LoginAttempt

    since = datetime.now(timezone.utc) - timedelta(minutes=15)
    base = select(func.count()).select_from(LoginAttempt).where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= since)
    if username:
        n = (await session.execute(base.where(LoginAttempt.username_attempted == username))).scalar_one()
        if n >= FAILED_PER_ACCOUNT:
            await alert(session, f"signin:{username.lower()}", "Security alert: many wrong passwords for one account",
                        f"{n} wrong passwords for the account \"{username}\" in the last 15 minutes"
                        + (f" (latest from {ip})" if ip else "") + ". Someone may be guessing this password. "
                        "If it is not the owner, consider resetting the password and checking where the attempts come from.")
    if ip:
        n = (await session.execute(base.where(LoginAttempt.ip_address == ip))).scalar_one()
        if n >= FAILED_PER_ADDRESS:
            await alert(session, f"signin-ip:{ip}", "Security alert: many wrong passwords from one address",
                        f"{n} wrong passwords from the address {ip} in the last 15 minutes, across one or more accounts. "
                        "This looks like password guessing.")


async def check_alerts(session: AsyncSession, kind: str, *, ip: str | None, user_id, username: str | None) -> None:
    since = datetime.now(timezone.utc) - timedelta(minutes=10)
    base = select(func.count()).select_from(SecurityEvent).where(SecurityEvent.kind == kind, SecurityEvent.created_at >= since)
    if kind == "ACCESS_DENIED" and user_id:
        n = (await session.execute(base.where(SecurityEvent.user_id == user_id))).scalar_one()
        if n >= DENIED_PER_PERSON:
            await alert(session, f"denied:{user_id}", "Security alert: someone keeps trying things they are not allowed to",
                        f"The account \"{username or user_id}\" was refused {n} times in the last 10 minutes"
                        + (f" (from {ip})" if ip else "") + ". It may be someone exploring other people's records or admin screens.")
    elif kind in ("SUSPICIOUS_INPUT", "BAD_TOKEN") and ip:
        n = (await session.execute(base.where(SecurityEvent.ip_address == ip))).scalar_one()
        limit = SUSPICIOUS_PER_ADDRESS if kind == "SUSPICIOUS_INPUT" else BAD_TOKEN_PER_ADDRESS
        if n >= limit:
            what = "attack-looking input (SQL or script injection)" if kind == "SUSPICIOUS_INPUT" else "forged sign-in tokens"
            await alert(session, f"{kind.lower()}:{ip}", "Security alert: attack attempts from one address",
                        f"{n} requests with {what} from the address {ip} in the last 10 minutes"
                        + (f", signed in as \"{username}\"" if username else "") + ". They were handled safely, but someone is testing the system.")


async def alert(session: AsyncSession, key: str, title: str, message: str) -> bool:
    """Tell every administrator (in the portal) and the administration email addresses. At most once per half hour per key."""
    try:
        from app.core.redis import get_redis

        if not await get_redis().set(f"secalert:{key}", "1", nx=True, ex=ALERT_EVERY_MINUTES * 60):
            return False
    except Exception:  # noqa: BLE001
        pass
    from app.auth.models import Role, User, UserRole
    from app.notifications.email_models import EmailOutbox
    from app.notifications.email_service import load_config, parse_addresses, render
    from app.notifications.models import Notification, NotificationCategory

    link = "/dashboard/admin/activity#security"
    admins = (await session.execute(select(User.id, User.email, User.email_verified_at).join(UserRole, UserRole.user_id == User.id)
                                    .join(Role, Role.id == UserRole.role_id).where(Role.code == "ADMIN", User.is_active.is_(True)))).all()
    for uid, _email, _verified in admins:
        session.add(Notification(user_id=uid, category=NotificationCategory.GENERAL, title=title[:200], message=message[:1000], link=link))
    cfg = await load_config(session)
    if cfg.ready:
        addresses = {a.lower() for a in (cfg.admin_emails or [])}
        addresses |= {e.lower() for _u, e, v in admins if e and v}
        footer = "You get this because you are an administrator, or this address is listed under Administration email addresses in Settings → Email."
        subject, text, body = render(title, message + "\n\nOpen Activity log → Security to see every attempt and where it came from.", link, footer=footer)
        for address in sorted(addresses):
            session.add(EmailOutbox(to_email=address, subject=subject, body_text=text, body_html=body, kind="SECURITY"))
    logger.warning("security_alert", key=key, title=title)
    return True


# ------------------------------------------------------------------ reading (Activity log → Security)
async def _names(session: AsyncSession, user_ids: set) -> dict:
    """Name and code for staff and students; the username for anyone else (an administrator account, a parent)."""
    from app.auth.models import User
    from app.employees.models import Employee
    from app.students.models import Student

    ids = [u for u in user_ids if u]
    if not ids:
        return {}
    out = {u: n for u, n in (await session.execute(select(User.id, User.username).where(User.id.in_(ids)))).all()}
    for uid, f, l, code in (await session.execute(select(Student.user_id, Student.first_name, Student.last_name, Student.roll_number).where(Student.user_id.in_(ids)))).all():
        out[uid] = f"{f} {l} (student {code})" if code else f"{f} {l} (student)"
    for uid, f, l, code in (await session.execute(select(Employee.user_id, Employee.first_name, Employee.last_name, Employee.employee_code).where(Employee.user_id.in_(ids)))).all():
        out[uid] = f"{f} {l} ({code})"
    return out


async def summary(session: AsyncSession, *, days: int) -> dict:
    from app.auth.models import LoginAttempt

    since = datetime.now(timezone.utc) - timedelta(days=days)
    counts = dict((await session.execute(select(SecurityEvent.kind, func.count()).where(SecurityEvent.created_at >= since)
                                         .group_by(SecurityEvent.kind))).all())
    failed = (await session.execute(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.success.is_(False), LoginAttempt.created_at >= since))).scalar_one()
    # Addresses behind the most trouble: failed sign-ins plus every recorded event.
    ev = (await session.execute(select(func.host(SecurityEvent.ip_address), func.count(), func.max(SecurityEvent.created_at))
                                .where(SecurityEvent.created_at >= since, SecurityEvent.ip_address.is_not(None), SecurityEvent.kind != "PROBE")
                                .group_by(func.host(SecurityEvent.ip_address)))).all()
    fl = (await session.execute(select(func.host(LoginAttempt.ip_address), func.count(), func.max(LoginAttempt.created_at))
                                .where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= since, LoginAttempt.ip_address.is_not(None))
                                .group_by(func.host(LoginAttempt.ip_address)))).all()
    ips: dict[str, dict] = {}
    for ip, n, last in ev:
        ips.setdefault(ip, {"ip": ip, "events": 0, "failed_signins": 0, "last_seen": last})["events"] = n
    for ip, n, last in fl:
        row = ips.setdefault(ip, {"ip": ip, "events": 0, "failed_signins": 0, "last_seen": last})
        row["failed_signins"] = n
        row["last_seen"] = max(row["last_seen"], last)
    top_ips = sorted(ips.values(), key=lambda r: (r["events"] * 3 + r["failed_signins"]), reverse=True)[:10]
    for r in top_ips:
        r["last_seen"] = r["last_seen"].isoformat()
    accounts = (await session.execute(select(LoginAttempt.username_attempted, func.count(), func.count(func.distinct(LoginAttempt.ip_address)),
                                             func.max(LoginAttempt.created_at))
                                      .where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= since)
                                      .group_by(LoginAttempt.username_attempted).order_by(func.count().desc()).limit(10))).all()
    return {"days": days, "failed_signins": failed,
            "counts": {k: int(counts.get(k, 0)) for k in KINDS},
            "top_ips": top_ips,
            "top_accounts": [{"username": u, "failed": n, "addresses": a, "last_seen": last.isoformat()} for u, n, a, last in accounts]}


async def events(session: AsyncSession, *, days: int, kind: str | None, ip: str | None, q: str | None, limit: int = 200) -> list[dict]:
    from app.auth.models import LoginAttempt

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list[dict] = []
    if kind in (None, "", "FAILED_SIGNIN"):
        stmt = select(LoginAttempt).where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= since)
        if ip:
            stmt = stmt.where(func.host(LoginAttempt.ip_address) == ip)
        if q:
            stmt = stmt.where(LoginAttempt.username_attempted.ilike(f"%{q}%"))
        for a in (await session.execute(stmt.order_by(LoginAttempt.created_at.desc()).limit(limit))).scalars():
            rows.append({"at": a.created_at.isoformat(), "kind": "FAILED_SIGNIN", "severity": "WARN", "user_id": str(a.user_id) if a.user_id else None,
                         "username": a.username_attempted, "ip": str(a.ip_address) if a.ip_address else None, "user_agent": a.user_agent,
                         "method": "POST", "path": "/api/v1/auth/login",
                         "detail": "Wrong password" if a.user_id else "Wrong password (no such account)"})
    if kind != "FAILED_SIGNIN":
        stmt = select(SecurityEvent).where(SecurityEvent.created_at >= since)
        if kind:
            stmt = stmt.where(SecurityEvent.kind == kind)
        if ip:
            stmt = stmt.where(func.host(SecurityEvent.ip_address) == ip)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((SecurityEvent.username.ilike(like)) | (SecurityEvent.path.ilike(like)) | (SecurityEvent.detail.ilike(like)))
        for e in (await session.execute(stmt.order_by(SecurityEvent.created_at.desc()).limit(limit))).scalars():
            rows.append({"at": e.created_at.isoformat(), "kind": e.kind, "severity": e.severity, "user_id": str(e.user_id) if e.user_id else None,
                         "username": e.username, "ip": str(e.ip_address) if e.ip_address else None, "user_agent": e.user_agent,
                         "method": e.method, "path": e.path, "detail": e.detail})
    rows.sort(key=lambda r: r["at"], reverse=True)
    rows = rows[:limit]
    names = await _names(session, {uuid.UUID(r["user_id"]) for r in rows if r["user_id"]})
    for r in rows:
        r["person"] = names.get(uuid.UUID(r["user_id"])) if r["user_id"] else None
    return rows