"""Email: settings an administrator edits on the Settings page, a test email, and every in-app notification also sent
by email (like Google Classroom) to people who have an email address and have not switched it off.

Flow: create_notification() -> queue_for_notification() adds an email_outbox row in the same transaction (no network
call while serving a request). The background sender (scripts/sms_sender.py, or the web app itself when that service
is not running: app/core/background.py) calls send_pending() every 30-60 seconds. Failed sends are retried with
growing gaps, then marked FAILED.

Settings come from the email_settings row; until an administrator saves it, SMTP_* values in .env are used (if any).
The SMTP password is stored encrypted (app/core/secretbox.py) and never sent back to the browser.
"""
from __future__ import annotations

import html
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger
from app.core.secretbox import open_
from app.notifications.email_models import EmailOutbox, EmailSettings

logger = get_logger(__name__)
RETRY_MINUTES = [1, 5, 15, 60]          # gaps between attempts; after the last one the email is FAILED
SEND_TIMEOUT = 20
_cache: tuple[float, "EmailConfig"] | None = None
CACHE_SECONDS = 30


@dataclass
class EmailConfig:
    enabled: bool
    host: str | None
    port: int
    security: str            # starttls | ssl | none
    username: str | None
    password: str | None
    from_name: str | None
    from_email: str | None
    reply_to: str | None
    notify_by_email: bool
    admin_emails: list[str] = field(default_factory=list)             # administration: a copy of notifications
    admin_copy_categories: set[str] | None = None                     # None = every kind

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.host and self.from_email)


async def load_config(session: AsyncSession, *, fresh: bool = False) -> EmailConfig:
    global _cache
    if not fresh and _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    row = await session.get(EmailSettings, 1)
    if row is not None:
        cfg = EmailConfig(row.enabled, row.host, row.port, row.security, row.username, open_(row.password_enc), row.from_name, row.from_email,
                          row.reply_to, row.notify_by_email, parse_addresses(row.admin_emails),
                          {c.strip() for c in row.admin_copy_categories.split(",") if c.strip()} if row.admin_copy_categories else None)
    else:                                                                   # nothing saved yet: .env values, if any
        cfg = EmailConfig(bool(settings.smtp_host), settings.smtp_host, settings.smtp_port, "ssl" if settings.smtp_port == 465 else "starttls",
                          settings.smtp_user, settings.smtp_password, settings.institute_name, settings.institute_email or settings.smtp_user,
                          None, True)
    _cache = (time.monotonic(), cfg)
    return cfg


def parse_addresses(raw: str | None) -> list[str]:
    """Administration addresses typed one per line (commas / semicolons also work): valid ones, lower-case, no repeats."""
    import re

    out: list[str] = []
    for part in re.split(r"[\s,;]+", raw or ""):
        a = part.strip().lower()
        if a and re.match(r"^[^@\s]{1,64}@[^@\s]+\.[a-z]{2,}$", a) and a not in out:
            out.append(a)
    return out[:10]


def forget_cached_config() -> None:
    global _cache
    _cache = None


# ------------------------------------------------------------------ the message
def render(title: str, message: str, link: str | None, *, footer: str | None = None) -> tuple[str, str, str]:
    """(subject, text, html) for a notification email."""
    institute = settings.institute_name or "VIST"
    footer = footer or f"You get this email because you have an account at {institute}. To stop these emails, switch them off on My Profile."
    base = (settings.public_url or "").rstrip("/")
    url = (base + link) if (base and link and link.startswith("/")) else (base or None)
    subject = f"{title} — {institute}"[:250]
    text = f"{title}\n\n{message}\n\n" + (f"Open: {url}\n\n" if url else "") + footer
    button = (f'<p style="margin:24px 0"><a href="{html.escape(url)}" style="background:#f5b700;color:#1a1300;text-decoration:none;'
              f'font-weight:700;padding:12px 22px;border-radius:10px;display:inline-block">Open in {html.escape(institute)}</a></p>') if url else ""
    body = (f'<div style="background:#f3f6fb;padding:24px 12px;font-family:Segoe UI,Arial,sans-serif;color:#0f1d3d">'
            f'<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e1e7f2">'
            f'<div style="background:#0f1f4d;color:#ffffff;padding:16px 24px;font-weight:700;font-size:16px">{html.escape(institute)}</div>'
            f'<div style="padding:24px"><h1 style="font-size:20px;margin:0 0 12px">{html.escape(title)}</h1>'
            f'<p style="font-size:15px;line-height:1.55;margin:0;white-space:pre-line">{html.escape(message)}</p>{button}</div>'
            f'<div style="padding:14px 24px;border-top:1px solid #e1e7f2;font-size:12px;color:#5b6b8f">{html.escape(footer)}</div></div></div>')
    return subject, text, body


def _message(cfg: EmailConfig, to: str, subject: str, text: str, body_html: str | None) -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = formataddr((cfg.from_name or settings.institute_name or "", cfg.from_email or ""))
    m["To"] = to
    if cfg.reply_to:
        m["Reply-To"] = cfg.reply_to
    m["Message-ID"] = make_msgid(domain=(cfg.from_email or "vist.local").split("@")[-1])
    m.set_content(text)
    if body_html:
        m.add_alternative(body_html, subtype="html")
    return m


def _connect(cfg: EmailConfig) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if cfg.security == "ssl":
        s = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=SEND_TIMEOUT, context=ctx)
    else:
        s = smtplib.SMTP(cfg.host, cfg.port, timeout=SEND_TIMEOUT)
        s.ehlo()
        if cfg.security == "starttls":
            s.starttls(context=ctx)
            s.ehlo()
    if cfg.username:
        s.login(cfg.username, cfg.password or "")
    return s


def plain_error(exc: Exception) -> str:
    """A reason an administrator can act on."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "The username or password was refused by the mail server. For Gmail use an App Password, not your normal password."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the recipient address."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The mail server refused the From address. Use an address this account is allowed to send from."
    if isinstance(exc, (ConnectionRefusedError, OSError)) and "timed out" not in str(exc).lower():
        return f"Could not connect to the mail server ({exc}). Check the host and port, and that the server allows outgoing mail."
    if "timed out" in str(exc).lower():
        return "The mail server did not answer in time. Check the host, port and security setting (587 = STARTTLS, 465 = SSL)."
    if isinstance(exc, ssl.SSLError):
        return f"Secure connection failed ({exc}). Try the other security setting: 587 with STARTTLS, or 465 with SSL."
    return f"{type(exc).__name__}: {exc}"[:400]


def send_one_now(cfg: EmailConfig, to: str, subject: str, text: str, body_html: str | None) -> None:
    """Blocking: open a connection, send one email. Raises on failure (see plain_error)."""
    s = _connect(cfg)
    try:
        s.send_message(_message(cfg, to, subject, text, body_html))
    finally:
        try:
            s.quit()
        except Exception:
            pass


# ------------------------------------------------------------------ queueing
async def recipient_email(session: AsyncSession, user_id: uuid.UUID, *, ignore_switch: bool = False) -> str | None:
    """The address to email this person at: their login's email, else the one on their student / staff record.
    None when they have none or switched emails off."""
    from app.auth.models import User
    from app.employees.models import Employee
    from app.students.models import Student

    u = await session.get(User, user_id)
    if u is None or not u.is_active or not (u.email_notifications or ignore_switch):
        return None
    email = (u.email or "").strip()
    if not email:
        email = ((await session.execute(select(Student.email).where(Student.user_id == user_id))).scalar_one_or_none() or "").strip()
    if not email:
        email = ((await session.execute(select(Employee.email).where(Employee.user_id == user_id))).scalar_one_or_none() or "").strip()
    return email if "@" in email else None


async def queue_for_notification(session: AsyncSession, notification) -> EmailOutbox | None:
    cfg = await load_config(session)
    if not cfg.ready:
        return None
    row = None
    to = await recipient_email(session, notification.user_id) if cfg.notify_by_email else None
    if to:
        subject, text, body = render(notification.title, notification.message, notification.link)
        row = EmailOutbox(user_id=notification.user_id, notification_id=notification.id, to_email=to, subject=subject, body_text=text, body_html=body)
        session.add(row)
        await session.flush()
    await queue_admin_copies(session, notification, cfg, skip=to)
    return row


# ------------------------------------------------------------------ administration copies
ADMIN_COPY_WAIT_MINUTES = 2          # a copy waits this long so the same notice to many people becomes one email
ADMIN_COPY_MAX_NAMES = 40
ADMIN_COPY_FOOTER = "You get this copy because this address is listed under Administration email addresses in Settings → Email."


async def _person_label(session: AsyncSession, user_id: uuid.UUID) -> str:
    from app.auth.models import User
    from app.employees.models import Employee
    from app.students.models import Student

    s = (await session.execute(select(Student.first_name, Student.last_name, Student.roll_number).where(Student.user_id == user_id))).first()
    if s:
        return f"{s[0]} {s[1]} (student {s[2]})" if s[2] else f"{s[0]} {s[1]} (student)"
    e = (await session.execute(select(Employee.first_name, Employee.last_name, Employee.employee_code).where(Employee.user_id == user_id))).first()
    if e:
        return f"{e[0]} {e[1]} ({e[2]})"
    u = (await session.execute(select(User.username).where(User.id == user_id))).scalar_one_or_none()
    return f"{u} (parent / other account)" if u else "an account"


def _admin_copy_message(title: str, message: str, names: list[str], link: str | None) -> tuple[str, str, str]:
    shown = names[:ADMIN_COPY_MAX_NAMES]
    more = len(names) - len(shown)
    who = "\n".join("• " + n for n in shown) + (f"\n…and {more} more" if more > 0 else "")
    head = f"Sent to {len(names)} {'person' if len(names) == 1 else 'people'}:" if len(names) != 1 else "Sent to:"
    return render(f"Copy: {title}", f"{message}\n\n{head}\n{who}", link, footer=ADMIN_COPY_FOOTER)


async def queue_admin_copies(session: AsyncSession, notification, cfg: EmailConfig, *, skip: str | None = None) -> int:
    """A copy of this notification to every administration address (Settings → Email). The same notice going to many
    people within a couple of minutes is merged into ONE email that lists who got it."""
    import hashlib

    if not cfg.admin_emails:
        return 0
    category = getattr(notification.category, "value", notification.category)
    if cfg.admin_copy_categories is not None and category not in cfg.admin_copy_categories:
        return 0
    key = hashlib.sha256(f"{category}|{notification.title}|{notification.message}|{notification.link or ''}".encode()).hexdigest()
    who = await _person_label(session, notification.user_id)
    now = datetime.now(timezone.utc)
    queued = 0
    for address in cfg.admin_emails:
        if skip and address == skip.lower():
            continue                                                       # this administrator already gets the notice itself
        row = (await session.execute(select(EmailOutbox).where(
            EmailOutbox.group_key == key, EmailOutbox.to_email == address, EmailOutbox.status == "PENDING", EmailOutbox.kind == "ADMIN_COPY",
            EmailOutbox.attempts == 0, EmailOutbox.created_at >= now - timedelta(minutes=30))
            .order_by(EmailOutbox.created_at.desc()).limit(1).with_for_update(skip_locked=True))).scalars().first()
        if row is not None:
            names = [n for n in (row.copy_recipients or "").split("\n") if n]
            names.append(who)
            row.copy_recipients = "\n".join(names)
            row.subject, row.body_text, row.body_html = _admin_copy_message(notification.title, notification.message, names, notification.link)
        else:
            subject, text, body = _admin_copy_message(notification.title, notification.message, [who], notification.link)
            session.add(EmailOutbox(notification_id=notification.id, to_email=address, subject=subject, body_text=text, body_html=body,
                                    kind="ADMIN_COPY", group_key=key, copy_recipients=who,
                                    next_try_at=now + timedelta(minutes=ADMIN_COPY_WAIT_MINUTES)))
        queued += 1
    await session.flush()
    return queued


# ------------------------------------------------------------------ sending
async def send_pending(session: AsyncSession, *, limit: int = 100, time_budget: float | None = None) -> dict:
    """Send due emails (one SMTP connection for the batch). Commits nothing: the caller commits."""
    cfg = await load_config(session, fresh=True)
    now = datetime.now(timezone.utc)
    rows = list((await session.execute(select(EmailOutbox).where(EmailOutbox.status == "PENDING", EmailOutbox.next_try_at <= now)
                                       .order_by(EmailOutbox.created_at).limit(limit).with_for_update(skip_locked=True))).scalars())
    if not rows:
        return {"emails": 0, "sent": 0, "failed": 0}
    if not cfg.ready:
        return {"emails": len(rows), "sent": 0, "failed": 0, "waiting": "email is switched off or not set up"}

    def deliver() -> list[tuple[EmailOutbox, Exception | None]]:
        out: list = []
        started = time.monotonic()
        try:
            conn = _connect(cfg)
        except Exception as exc:                                            # nothing can go: every row gets this reason
            return [(r, exc) for r in rows]
        try:
            for r in rows:
                if time_budget is not None and out and time.monotonic() - started >= time_budget:
                    break
                try:
                    conn.send_message(_message(cfg, r.to_email, r.subject, r.body_text, r.body_html))
                    out.append((r, None))
                except Exception as exc:
                    out.append((r, exc))
        finally:
            try:
                conn.quit()
            except Exception:
                pass
        return out

    results = await run_in_threadpool(deliver)
    sent = failed = 0
    for r, exc in results:
        r.attempts += 1
        if exc is None:
            r.status, r.sent_at, r.last_error = "SENT", datetime.now(timezone.utc), None
            sent += 1
        else:
            r.last_error = plain_error(exc)[:500]
            if r.attempts > len(RETRY_MINUTES) or isinstance(exc, smtplib.SMTPRecipientsRefused):
                r.status = "FAILED"
                failed += 1
            else:
                r.next_try_at = datetime.now(timezone.utc) + timedelta(minutes=RETRY_MINUTES[r.attempts - 1])
    await session.flush()
    if failed or (results and results[0][1] is not None):
        logger.warning("email_send_problems", sent=sent, failed=failed, reason=results[0][1] and plain_error(results[0][1]))
    return {"emails": len(results), "sent": sent, "failed": failed}


class EmailService:
    """Kept for callers that send one email directly: queued like any other email."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session

    async def send(self, *, to_email: str, subject: str, body: str) -> None:
        if self.session is None:
            logger.info("email_not_queued_no_session", to=to_email, subject=subject)
            return
        self.session.add(EmailOutbox(to_email=to_email, subject=subject, body_text=body, kind="DIRECT"))
        await self.session.flush()