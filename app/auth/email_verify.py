"""Prove an email address with a 6-digit code before using the portal.

When an administrator turns on "Every account must verify an email address" (Settings -> Email), an account without a
verified address can do nothing but this (and sign out) until it verifies. The same screen changes the address later.

- A code is emailed straight away (not through the queue) so the person is not kept waiting.
- It works for 10 minutes, once, and at most 5 wrong tries; asking again cancels the earlier code.
- One code a minute, 5 an hour per account, 20 an hour per network address.
- The address must not belong to another account. The code itself is never stored or logged, only a keyed hash.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.audit.service import AuditService
from app.auth.models import User
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, RateLimitedError, ValidationAppError
from app.core.redis import get_redis
from app.notifications.email_models import EmailOutbox, EmailVerificationCode

CODE_MINUTES = 10
MAX_TRIES = 5
RESEND_SECONDS = 60
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


def _hash(code_id: uuid.UUID, code: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"email:{code_id}:{code}".encode(), hashlib.sha256).hexdigest()


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    name, domain = email.split("@", 1)
    return (name[:2] + "***" if len(name) > 2 else name[:1] + "***") + "@" + domain


class EmailVerificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def status(self, user_id: uuid.UUID) -> dict:
        from app.core.dependencies import email_gate_on
        from app.notifications.email_service import load_config, recipient_email

        u = await self.session.get(User, user_id)
        cfg = await load_config(self.session)
        verified = bool(u.email and u.email_verified_at)
        suggested = u.email or await recipient_email(self.session, user_id, ignore_switch=True)
        last = (await self.session.execute(select(EmailVerificationCode).where(EmailVerificationCode.user_id == user_id,
                                                                              EmailVerificationCode.used_at.is_(None),
                                                                              EmailVerificationCode.expires_at > datetime.now(timezone.utc))
                                           .order_by(EmailVerificationCode.created_at.desc()))).scalars().first()
        return {"email": u.email, "verified": verified, "verified_at": u.email_verified_at, "suggested": suggested,
                "required": (not verified) and await email_gate_on(self.session), "can_send": cfg.ready,
                "code_sent_to": last.email if last else None}

    async def _limit(self, key: str, most: int, seconds: int, message: str) -> None:
        redis = get_redis()
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, seconds)
        if n > most:
            raise RateLimitedError(message)

    async def send_code(self, user_id: uuid.UUID, email: str, *, ip: str | None) -> dict:
        from app.core.side_log import committed_now
        from app.notifications.email_service import load_config, plain_error, render, send_one_now

        email = (email or "").strip().lower()
        if not EMAIL.match(email):
            raise ValidationAppError("Enter a valid email address, like name@gmail.com.")
        u = await self.session.get(User, user_id)
        if u.email and u.email_verified_at and u.email.lower() == email:
            raise BusinessRuleViolationError("This address is already verified on your account.")
        taken = (await self.session.execute(select(User.id).where(func.lower(User.email) == email, User.id != user_id))).first()
        if taken:
            raise BusinessRuleViolationError("This email address is used by another account. Use your own address.")
        cfg = await load_config(self.session, fresh=True)
        if not cfg.ready:
            raise BusinessRuleViolationError("The institute has not set up email yet. Please contact the office.")
        now = datetime.now(timezone.utc)
        last = (await self.session.execute(select(EmailVerificationCode.created_at).where(EmailVerificationCode.user_id == user_id)
                                           .order_by(EmailVerificationCode.created_at.desc()).limit(1))).scalar_one_or_none()
        if last is not None and (now - last).total_seconds() < RESEND_SECONDS:
            wait = RESEND_SECONDS - int((now - last).total_seconds())
            raise RateLimitedError(f"A code was just sent. You can ask for another in {wait} seconds.")
        await self._limit(f"emailverify:ip:{ip or 'unknown'}", 20, 3600, "Too many codes from this network. Please try again in an hour.")
        await self._limit(f"emailverify:user:{user_id}", 5, 3600, "Too many codes for this account. Please try again in an hour.")

        code = f"{secrets.randbelow(10**6):06d}"
        institute = settings.institute_name or settings.app_name or "VIST"
        subject, text, body = render(f"Your verification code: {code}",
                                     f"Enter this code in {institute} to confirm this email address belongs to you:\n\n{code}\n\n"
                                     f"It works for {CODE_MINUTES} minutes. If you did not ask for it, ignore this email. Never share this code.",
                                     None, footer=f"Sent because someone signed in to {institute} as {u.username} and entered this address.")
        error = None
        try:
            await run_in_threadpool(send_one_now, cfg, email, subject, text, body)
        except Exception as exc:
            error = plain_error(exc)
        record = dict(user_id=user_id, to_email=email, subject="Your verification code (hidden)", body_text="(the code is not kept)", kind="OTP",
                      status="SENT" if error is None else "FAILED", attempts=1, last_error=error, sent_at=now if error is None else None)
        if error is not None:
            async with committed_now(self.session) as side:          # the office can see why it failed (Settings -> Email)
                side.add(EmailOutbox(**record))
            raise BusinessRuleViolationError("The email could not be sent right now. Check the address, or try again in a few minutes.")
        self.session.add(EmailOutbox(**record))
        await self.session.execute(update(EmailVerificationCode).where(EmailVerificationCode.user_id == user_id,
                                                                       EmailVerificationCode.used_at.is_(None)).values(used_at=now))
        code_id = uuid.uuid4()
        self.session.add(EmailVerificationCode(id=code_id, user_id=user_id, email=email, code_hash=_hash(code_id, code),
                                               expires_at=now + timedelta(minutes=CODE_MINUTES), created_at=now))
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=user_id, action="auth.email_code_sent", entity_type="user", entity_id=user_id, ip=ip,
                                             metadata={"to": mask_email(email)})
        return {"sent_to": email, "minutes": CODE_MINUTES}

    async def verify(self, user_id: uuid.UUID, code: str, *, ip: str | None) -> dict:
        from app.core.dependencies import forget_session
        from app.core.side_log import committed_now

        now = datetime.now(timezone.utc)
        row = (await self.session.execute(select(EmailVerificationCode).where(EmailVerificationCode.user_id == user_id,
                                                                             EmailVerificationCode.used_at.is_(None))
                                          .order_by(EmailVerificationCode.created_at.desc()))).scalars().first()
        if row is None or row.expires_at <= now:
            raise BusinessRuleViolationError("This code has expired. Ask for a new code.")
        code = re.sub(r"\D", "", code or "")
        redis = get_redis()
        tries_key = f"emailverify:tries:{row.id}"
        if not hmac.compare_digest(_hash(row.id, code), row.code_hash):
            tries = int(await redis.incr(tries_key))
            if tries == 1:
                await redis.expire(tries_key, CODE_MINUTES * 60 + 60)
            # The error rolls the request back, so the count and the "code finished" mark are written in their own
            # committed session (no lock is held on the row by this request, so this never waits).
            async with committed_now(self.session) as side:
                r = await side.get(EmailVerificationCode, row.id)
                if r is not None:
                    r.attempts = max(r.attempts, tries)
                    if tries >= MAX_TRIES and r.used_at is None:
                        r.used_at = now
                await AuditService(side).log(actor_user_id=user_id, action="auth.email_code_wrong", entity_type="user", entity_id=user_id, ip=ip,
                                             metadata={"try": tries})
            if tries >= MAX_TRIES:
                raise BusinessRuleViolationError("Too many wrong tries. Ask for a new code.")
            left = MAX_TRIES - tries
            raise BusinessRuleViolationError(f"That code is not right. {left} tr{'ies' if left != 1 else 'y'} left.")
        if int(await redis.get(tries_key) or 0) >= MAX_TRIES:
            raise BusinessRuleViolationError("Too many wrong tries. Ask for a new code.")
        taken = (await self.session.execute(select(User.id).where(func.lower(User.email) == row.email, User.id != user_id))).first()
        if taken:
            raise BusinessRuleViolationError("This email address is now used by another account. Use your own address.")
        u = await self.session.get(User, user_id)
        before = {"email": u.email, "verified": bool(u.email_verified_at)}
        row.used_at = now
        u.email, u.email_verified_at = row.email, now
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=user_id, action="auth.email_verified", entity_type="user", entity_id=user_id, ip=ip,
                                             before=before, after={"email": row.email, "verified": True})
        forget_session(user_id)
        return {"verified": True, "email": row.email}