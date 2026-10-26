"""Forgotten password: a 6-digit code by SMS to the mobile number on the person's record.

- Students: their own mobile. Staff: their own mobile. Parents: the guardian mobile on their child's record.
- The answer is always the same ("if your number is on record, a code is on its way"), so the screen never
  tells a stranger which usernames exist or whose number is missing.
- A code works for 10 minutes, once, and at most 5 wrong tries; asking again cancels the earlier code.
  At most 3 codes per username per hour and 10 per address per hour.
- Resetting signs the account out everywhere and is recorded in the audit log.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.models import PasswordResetToken, User
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, RateLimitedError
from app.core.redis import get_redis
from app.core.security import hash_password, verify_password

CODE_MINUTES = 10
MAX_TRIES = 5
GENERIC = ("If that username is ours and a mobile number is on its record, a 6-digit code is on its way by SMS. "
           "It works for 10 minutes. No SMS? Ask the office for a new one-time password.")


def _hash(token_id: uuid.UUID, code: str) -> str:
    """Bound to this one request's row id, so two requests never produce the same stored value."""
    return hmac.new(settings.jwt_secret.encode(), f"{token_id}:{code}".encode(), hashlib.sha256).hexdigest()


def mask(number: str | None) -> str:
    digits = "".join(c for c in (number or "") if c.isdigit())
    return f"****{digits[-3:]}" if len(digits) >= 7 else ""


class PasswordResetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _mobile_for(self, user: User) -> str | None:
        from app.employees.models import Employee
        from app.students.models import Student, StudentGuardian

        s = (await self.session.execute(select(Student.contact_no).where(Student.user_id == user.id))).scalar_one_or_none()
        if s:
            return s
        e = (await self.session.execute(select(Employee.contact_no).where(Employee.user_id == user.id))).scalar_one_or_none()
        if e:
            return e
        uname = (user.username or "").replace("-", "")
        if len(uname) == 13 and uname.isdigit():                      # a parent: username is the guardian CNIC
            cnic = f"{uname[:5]}-{uname[5:12]}-{uname[12]}"
            rows = (await self.session.execute(select(StudentGuardian.contact_no).where(StudentGuardian.cnic == cnic,
                                                                                         StudentGuardian.contact_no.is_not(None)))).scalars().all()
            if rows:
                return rows[0]
        return None

    async def _limit(self, key: str, most: int, seconds: int) -> None:
        redis = get_redis()
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, seconds)
        if n > most:
            raise RateLimitedError("Too many reset requests. Please wait an hour, or ask the office for a new one-time password.")

    async def request(self, username: str, *, ip: str | None) -> dict:
        username = (username or "").strip()
        if not username:
            raise BusinessRuleViolationError("Enter your username (roll number, employee code or CNIC).")
        await self._limit(f"pwreset:ip:{ip or 'unknown'}", 10, 3600)
        await self._limit(f"pwreset:user:{username.lower()}", 3, 3600)
        user = (await self.session.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None or not user.is_active:
            return {"message": GENERIC}
        number = await self._mobile_for(user)
        if not number:
            await self.audit.log(actor_user_id=None, action="auth.password_reset_no_mobile", entity_type="user", entity_id=user.id, ip=ip)
            return {"message": GENERIC}
        now = datetime.now(timezone.utc)
        await self.session.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
                                   .values(used_at=now))
        code = f"{secrets.randbelow(10**6):06d}"
        token_id = uuid.uuid4()
        self.session.add(PasswordResetToken(id=token_id, user_id=user.id, token_hash=_hash(token_id, code), expires_at=now + timedelta(minutes=CODE_MINUTES),
                                            created_at=now))
        await get_redis().delete(f"pwreset:tries:{user.id}")
        from app.sms.service import SmsService

        await SmsService(self.session).queue_to_number(number, event="PASSWORD_RESET",
                                                       body=f"{settings.app_name}: your password reset code is {code}. It works for {CODE_MINUTES} minutes. Never share it with anyone.")
        await self.session.flush()
        await self.audit.log(actor_user_id=None, action="auth.password_reset_requested", entity_type="user", entity_id=user.id, ip=ip,
                             metadata={"sent_to": mask(number)})
        return {"message": GENERIC}

    async def perform(self, username: str, code: str, new_password: str, *, ip: str | None) -> None:
        wrong = BusinessRuleViolationError("That code is wrong or has expired. Ask for a new code.")
        user = (await self.session.execute(select(User).where(User.username == (username or "").strip()))).scalar_one_or_none()
        if user is None or not user.is_active:
            raise wrong
        now = datetime.now(timezone.utc)
        token = (await self.session.execute(select(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None),
                                                                            PasswordResetToken.expires_at > now)
                                            .order_by(PasswordResetToken.created_at.desc()))).scalars().first()
        if token is None:
            raise wrong
        redis = get_redis()
        tries = await redis.incr(f"pwreset:tries:{user.id}")
        if tries == 1:
            await redis.expire(f"pwreset:tries:{user.id}", CODE_MINUTES * 60)
        if tries > MAX_TRIES or not hmac.compare_digest(_hash(token.id, (code or "").strip()), token.token_hash):
            # The error below rolls the request back, so the "code finished" mark and the audit row are written in
            # their own committed session; otherwise five wrong tries were silently undone (audit SEC-03).
            from app.core.side_log import committed_now

            async with committed_now(self.session) as side:
                if tries >= MAX_TRIES:
                    t = await side.get(PasswordResetToken, token.id)
                    if t is not None and t.used_at is None:
                        t.used_at = now                                # five wrong tries: this code is finished
                await AuditService(side).log(actor_user_id=None, action="auth.password_reset_wrong_code", entity_type="user", entity_id=user.id, ip=ip,
                                             metadata={"try": tries})
            raise wrong
        if len(new_password or "") < 10:
            raise BusinessRuleViolationError("Choose a password of at least 10 characters.")
        if verify_password(new_password, user.password_hash):
            raise BusinessRuleViolationError("Choose a password different from your current one.")
        if new_password.strip().lower() in {"vertex123", (user.username or "").lower()}:
            raise BusinessRuleViolationError("That password is too easy to guess. Choose something only you know.")
        token.used_at = now
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        user.temp_password = None
        from app.auth.repository import AuthRepository

        await AuthRepository(self.session).revoke_all_for_user(user.id)
        await redis.delete(f"pwreset:tries:{user.id}", f"ratelimit:login:global:{user.username}")
        await self.audit.log(actor_user_id=user.id, action="auth.password_reset_by_sms", entity_type="user", entity_id=user.id, ip=ip)