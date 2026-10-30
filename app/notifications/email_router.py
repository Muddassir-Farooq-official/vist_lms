"""Settings → Email: the mail server the institute sends from, a test email, and the outbox (administrator)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.audit.service import AuditService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import ValidationAppError
from app.core.secretbox import open_, seal
from app.notifications.email_models import EmailOutbox, EmailSettings
from app.notifications.email_service import forget_cached_config, load_config, plain_error, render, send_one_now

router = APIRouter(prefix="/settings/email", tags=["email"])
me_router = APIRouter(prefix="/me/email-notifications", tags=["email"])
admin = require_permission(Permissions.POLICY_MANAGE)
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


class EmailSettingsIn(BaseModel):
    enabled: bool
    host: str | None = Field(default=None, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    security: str = Field(default="starttls", pattern="^(starttls|ssl|none)$")
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=500)       # None = keep the saved one; "" = remove it
    from_name: str | None = Field(default=None, max_length=120)
    from_email: str | None = Field(default=None, max_length=255)
    reply_to: str | None = Field(default=None, max_length=255)
    notify_by_email: bool = True
    require_verified_email: bool = False
    admin_emails: str | None = Field(default=None, max_length=3000)              # one per line
    admin_copy_categories: list[str] | None = None                               # None / empty = every kind


def _categories() -> list[dict]:
    from app.notifications.models import NotificationCategory

    labels = {"FEE": "Fees", "FINANCE": "Finance", "PAYROLL": "Payroll / salary", "RESULT": "Results", "ASSIGNMENT": "Assignments",
              "ANNOUNCEMENT": "Announcements", "APPLICATION": "Applications / approvals", "TASK": "Tasks", "COMPLAINT": "Complaints",
              "FINE": "Fines", "LIBRARY": "Library", "GENERAL": "Everything else"}
    return [{"code": c.value, "label": labels.get(c.value, c.value.title())} for c in NotificationCategory]


async def _verified_counts(db) -> dict:
    from app.auth.models import User

    active = (await db.execute(select(func.count()).select_from(User).where(User.is_active.is_(True)))).scalar_one()
    verified = (await db.execute(select(func.count()).select_from(User).where(User.is_active.is_(True), User.email.is_not(None),
                                                                              User.email_verified_at.is_not(None)))).scalar_one()
    return {"active_accounts": active, "verified_accounts": verified}


def _out(row: EmailSettings | None, cfg) -> dict:
    return {"saved": row is not None, "enabled": cfg.enabled, "host": cfg.host, "port": cfg.port, "security": cfg.security, "username": cfg.username,
            "has_password": bool(row.password_enc) if row else bool(cfg.password),
            "password_unreadable": bool(row and row.password_enc and open_(row.password_enc) is None),
            "from_name": cfg.from_name, "from_email": cfg.from_email, "reply_to": cfg.reply_to, "notify_by_email": cfg.notify_by_email,
            "last_test_at": row.last_test_at if row else None, "last_test_ok": row.last_test_ok if row else None,
            "last_test_error": row.last_test_error if row else None, "updated_at": row.updated_at if row else None,
            "require_verified_email": bool(row and row.require_verified_email), "admin_emails": cfg.admin_emails,
            "admin_copy_categories": sorted(cfg.admin_copy_categories) if cfg.admin_copy_categories is not None else None,
            "categories": _categories()}


@router.get("", summary="The mail server settings (the password is never shown)")
async def get_settings(current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return {**_out(await db.get(EmailSettings, 1), await load_config(db, fresh=True)), **await _verified_counts(db)}


@router.put("", summary="Save the mail server settings")
async def save_settings(payload: EmailSettingsIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    host = (payload.host or "").strip() or None
    from_email = (payload.from_email or "").strip() or None
    reply_to = (payload.reply_to or "").strip() or None
    for label, v in (("From address", from_email), ("Reply-to address", reply_to)):
        if v and not EMAIL.match(v):
            raise ValidationAppError(f"{label}: enter a valid email address.")
    if payload.enabled and not (host and from_email):
        raise ValidationAppError("To turn email on, give the mail server (host) and the From address.")
    from app.auth.models import User
    from app.notifications.email_service import parse_addresses

    raw_admin = (payload.admin_emails or "").strip()
    admin_list = parse_addresses(raw_admin)
    if raw_admin:
        bad = [p for p in re.split(r"[\s,;]+", raw_admin) if p and p.lower() not in admin_list]
        if bad:
            raise ValidationAppError(f"Administration email addresses: \"{bad[0][:60]}\" is not a valid email address (at most 10 addresses).")
    valid_cats = {c["code"] for c in _categories()}
    cats = [c for c in (payload.admin_copy_categories or []) if c in valid_cats]
    row = await db.get(EmailSettings, 1)
    if row is None:
        row = EmailSettings(id=1)
        db.add(row)
    if payload.require_verified_email and not (row and row.require_verified_email):
        # Turning it on: email must work, and the person turning it on must not lock themselves out.
        if not (payload.enabled and host and from_email):
            raise ValidationAppError("Turn email on (with the mail server and From address) before requiring everyone to verify their email.")
        if row.last_test_ok is not True:
            raise ValidationAppError("Send a test email that arrives first (below), so you know the codes will reach people.")
        mine = await db.get(User, current_user.id)
        if not (mine and mine.email and mine.email_verified_at):
            raise ValidationAppError("Verify your own email first (menu → My email), so this cannot lock you out.")
    before = {"enabled": row.enabled, "host": row.host, "port": row.port, "security": row.security, "username": row.username,
              "from_email": row.from_email, "notify_by_email": row.notify_by_email, "require_verified_email": row.require_verified_email,
              "admin_emails": row.admin_emails, "admin_copy_categories": row.admin_copy_categories}
    row.enabled, row.host, row.port, row.security = payload.enabled, host, payload.port, payload.security
    row.username = (payload.username or "").strip() or None
    row.from_name, row.from_email, row.reply_to = (payload.from_name or "").strip() or None, from_email, reply_to
    row.notify_by_email = payload.notify_by_email
    row.require_verified_email = payload.require_verified_email
    row.admin_emails = "\n".join(admin_list) or None
    row.admin_copy_categories = ",".join(cats) if cats and len(cats) < len(valid_cats) else None
    password_changed = payload.password is not None
    if password_changed:
        row.password_enc = seal(payload.password) if payload.password else None
    row.updated_by, row.updated_at = current_user.id, datetime.now(timezone.utc)
    await db.flush()
    forget_cached_config()
    await AuditService(db).log(actor_user_id=current_user.id, action="settings.email_saved", entity_type="email_settings", entity_id=None,
                               before=before, after={**{k: getattr(row, k) for k in before}, "password_changed": password_changed})
    return {**_out(row, await load_config(db, fresh=True)), **await _verified_counts(db)}


class TestIn(BaseModel):
    to: str = Field(min_length=3, max_length=255)


@router.post("/test", summary="Send a test email now with the saved settings (shows the mail server's answer)")
async def send_test(payload: TestIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    to = payload.to.strip()
    if not EMAIL.match(to):
        raise ValidationAppError("Enter the email address to send the test to.")
    cfg = await load_config(db, fresh=True)
    if not (cfg.host and cfg.from_email):
        raise ValidationAppError("Save the mail server (host) and the From address first.")
    subject, text, body = render("Test email", "Your email settings work. Notifications will reach people by email from now on "
                                 "(when email is turned on).", "/dashboard/admin/settings")
    error = None
    try:
        await run_in_threadpool(send_one_now, cfg, to, subject, text, body)
    except Exception as exc:
        error = plain_error(exc)
    row = await db.get(EmailSettings, 1)
    if row is not None:
        row.last_test_at, row.last_test_ok, row.last_test_error = datetime.now(timezone.utc), error is None, error
    db.add(EmailOutbox(user_id=current_user.id, to_email=to, subject=subject, body_text=text, body_html=body, kind="TEST",
                       status="SENT" if error is None else "FAILED", attempts=1, last_error=error,
                       sent_at=datetime.now(timezone.utc) if error is None else None))
    await AuditService(db).log(actor_user_id=current_user.id, action="settings.email_test", entity_type="email_settings", entity_id=None,
                               metadata={"to": to, "ok": error is None, "error": error})
    return {"ok": error is None, "error": error, "to": to}


@router.get("/outbox", summary="Recent emails: waiting, sent and failed")
async def outbox(status: str | None = None, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    stmt = select(EmailOutbox).order_by(EmailOutbox.created_at.desc()).limit(50)
    if status:
        stmt = stmt.where(EmailOutbox.status == status.upper())
    rows = (await db.execute(stmt)).scalars().all()
    day = datetime.now(timezone.utc) - timedelta(days=1)
    counts = dict((await db.execute(select(EmailOutbox.status, func.count()).where(EmailOutbox.created_at >= day).group_by(EmailOutbox.status))).all())
    waiting = (await db.execute(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.status == "PENDING"))).scalar_one()
    return {"last_24h": {"sent": counts.get("SENT", 0), "failed": counts.get("FAILED", 0)}, "waiting": waiting,
            "items": [{"id": r.id, "to": r.to_email, "subject": r.subject, "status": r.status, "kind": r.kind, "attempts": r.attempts,
                       "error": r.last_error, "created_at": r.created_at, "sent_at": r.sent_at} for r in rows]}


@router.post("/outbox/retry", summary="Try every failed email again")
async def retry_failed(current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    rows = (await db.execute(select(EmailOutbox).where(EmailOutbox.status == "FAILED", EmailOutbox.kind != "TEST"))).scalars().all()
    for r in rows:
        r.status, r.attempts, r.next_try_at = "PENDING", 0, datetime.now(timezone.utc)
    return {"queued_again": len(rows)}


# ------------------------------------------------------------------ the person's own switch
class MyEmailIn(BaseModel):
    email_notifications: bool | None = None
    email: str | None = Field(default=None, max_length=255)      # parents (no student / staff record) set their own address here


@me_router.get("", summary="Whether my notifications are also emailed to me, and to which address")
async def my_email(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.auth.models import User
    from app.notifications.email_service import recipient_email

    u = await db.get(User, current_user.id)
    cfg = await load_config(db)
    address = await recipient_email(db, current_user.id, ignore_switch=True)
    return {"email_notifications": u.email_notifications, "address": address, "institute_sends_email": cfg.ready and cfg.notify_by_email}


@me_router.put("", summary="Switch my notification emails on or off; parents can also set their email address")
async def set_my_email(payload: MyEmailIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.auth.models import User
    from app.employees.models import Employee
    from app.students.models import Student

    u = await db.get(User, current_user.id)
    after: dict = {}
    if payload.email is not None:
        has_record = (await db.execute(select(Student.id).where(Student.user_id == u.id))).first() or \
            (await db.execute(select(Employee.id).where(Employee.user_id == u.id))).first()
        if has_record:
            raise ValidationAppError("Change your email under Update my details on My Profile (the office approves it).")
        email = payload.email.strip().lower()
        if email and not EMAIL.match(email):
            raise ValidationAppError("Enter a valid email address.")
        if (email or None) != (u.email or None):
            u.email_verified_at = None                                 # a new address must be verified again
        u.email = email or None
        after["email"] = u.email
    if payload.email_notifications is not None:
        u.email_notifications = payload.email_notifications
        after["email_notifications"] = payload.email_notifications
    await db.flush()
    await AuditService(db).log(actor_user_id=current_user.id, action="notifications.email_switched", entity_type="user", entity_id=u.id, after=after)
    return await my_email(current_user, db)


# ------------------------------------------------------------------ background services status (admin dashboard)
status_router = APIRouter(prefix="/system", tags=["system"])


@status_router.get("/status", summary="Import worker, SMS / email sender, and anything waiting too long (administrator)")
async def system_status(current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    from app.core.background import status

    return await status(db)