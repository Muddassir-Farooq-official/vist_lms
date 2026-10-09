"""SMS to students and guardians.

Queueing: other modules call queue_for_student(...) when something happens
(attendance fine, datesheet published, results published, fee overdue). Each
event can be switched off in Settings -> Institute Policy (SMS costs money),
and so can copies to the guardian. Messages are rows in sms_messages, so the
administration can see exactly what went out and retry failures.

Sending: send_pending() is called by the sender process (scripts/sms_sender.py,
every 30 seconds) and by the "Send now" button. It works with any provider that
takes a plain web request (most Pakistani bulk-SMS companies, Twilio-style
gateways). Configure in .env:

  SMS_PROVIDER_URL      e.g. https://api.example.pk/send?api_key={api_key}&to={to}&from={sender}&text={message}
  SMS_PROVIDER_METHOD   GET (default) or POST
  SMS_PROVIDER_BODY     for POST: form body template, e.g. to={to}&message={message}&sender={sender}
  SMS_API_KEY, SMS_SENDER_ID
  SMS_SUCCESS_MARKER    optional text the provider's reply must contain to count as sent
  SMS_NUMBER_FORMAT     92 (923001234567, default), +92 (+923001234567) or 0 (03001234567)

With no SMS_PROVIDER_URL every message is recorded as LOGGED and nothing is sent,
so the system can go live before the SMS contract is signed.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, quote_plus

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationAppError
from app.sms.models import SmsMessage, SmsStatus

MAX_ATTEMPTS = 3
MAX_BODY = 300
EVENT_SWITCH = {"ATTENDANCE_FINE": "sms_on_attendance_fine", "DATESHEET": "sms_on_datesheet",
                "RESULTS": "sms_on_results", "FEE_OVERDUE": "sms_on_fee_overdue", "ADMISSION": "sms_on_admission"}


def normalize_pk_mobile(raw: str | None) -> str | None:
    """A Pakistani mobile number as the provider wants it, or None when it is not a mobile number.
    Accepts 0300-1234567, 03001234567, 3001234567, 923001234567, +92 300 1234567, 0092300..."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0092"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("03"):
        core = digits[1:]
    elif len(digits) == 12 and digits.startswith("923"):
        core = digits[2:]
    elif len(digits) == 10 and digits.startswith("3"):
        core = digits
    else:
        return None
    fmt = settings.sms_number_format
    return {"+92": "+92" + core, "0": "0" + core}.get(fmt, "92" + core)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_BODY else text[: MAX_BODY - 1] + "…"


class SmsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------------- queueing
    async def queue_for_student(self, student_id: uuid.UUID, *, event: str, body: str, guardian_body: str | None = None,
                                once_within_days: int | None = None) -> int:
        """Queue to the student's mobile and (policy permitting) the primary guardian's. Returns how many were queued.
        once_within_days: skip if this event already went to this student that recently (reminders)."""
        switch = EVENT_SWITCH.get(event)
        if switch and not int(getattr(settings, switch, 1)):
            return 0
        from app.students.models import Student, StudentGuardian

        student = await self.session.get(Student, student_id)
        if student is None:
            return 0
        if once_within_days:
            since = datetime.now(timezone.utc) - timedelta(days=once_within_days)
            recent = (await self.session.execute(select(func.count()).select_from(SmsMessage).where(
                SmsMessage.student_id == student_id, SmsMessage.event == event, SmsMessage.created_at >= since))).scalar_one()
            if recent:
                return 0
        targets = [("STUDENT", student.contact_no, body)]
        if int(settings.sms_to_guardian):
            g = (await self.session.execute(select(StudentGuardian).where(StudentGuardian.student_id == student_id)
                                            .order_by(StudentGuardian.is_primary.desc()))).scalars().first()
            if g is not None and g.contact_no:
                targets.append(("GUARDIAN", g.contact_no, guardian_body or body))
        seen, n = set(), 0
        for who, number, text in targets:
            norm = normalize_pk_mobile(number)
            if norm in seen and norm is not None:
                continue                                  # student and guardian share a phone: one message
            seen.add(norm)
            self.session.add(SmsMessage(to_number=(number or "")[:20], recipient=who, body=_clip(text), event=event, student_id=student_id,
                                        status=SmsStatus.QUEUED if norm else SmsStatus.SKIPPED,
                                        last_error=None if norm else "Not a valid Pakistani mobile number"))
            n += 1 if norm else 0
        await self.session.flush()
        return n

    async def queue_to_number(self, number: str, *, event: str, body: str) -> int:
        """To someone who is not (yet) a student, e.g. an applicant. Uses the same on/off switch as the event."""
        switch = EVENT_SWITCH.get(event)
        if switch and not int(getattr(settings, switch, 1)):
            return 0
        norm = normalize_pk_mobile(number)
        self.session.add(SmsMessage(to_number=(number or "")[:20], recipient="APPLICANT", body=_clip(body), event=event,
                                    status=SmsStatus.QUEUED if norm else SmsStatus.SKIPPED,
                                    last_error=None if norm else "Not a valid Pakistani mobile number"))
        await self.session.flush()
        return 1 if norm else 0

    async def queue_test(self, number: str, body: str) -> SmsMessage:
        if normalize_pk_mobile(number) is None:
            raise ValidationAppError("Enter a Pakistani mobile number, e.g. 0300-1234567.")
        msg = SmsMessage(to_number=number[:20], recipient="TEST", body=_clip(body), event="TEST", status=SmsStatus.QUEUED)
        self.session.add(msg)
        await self.session.flush()
        return msg

    # ---------------------------------------------------------------- sending
    @staticmethod
    def _request(number: str, body: str) -> tuple[str, str, str | None]:
        enc = {"to": quote(number), "message": quote(body), "sender": quote(settings.sms_sender_id or ""), "api_key": quote(settings.sms_api_key or "")}
        url = settings.sms_provider_url
        for k, v in enc.items():
            url = url.replace("{" + k + "}", v)
        method = (settings.sms_provider_method or "GET").upper()
        data = None
        if method == "POST" and settings.sms_provider_body:
            data = settings.sms_provider_body
            for k, v in {"to": number, "message": body, "sender": settings.sms_sender_id or "", "api_key": settings.sms_api_key or ""}.items():
                data = data.replace("{" + k + "}", quote_plus(v))
        return method, url, data

    async def _deliver(self, client: httpx.AsyncClient, msg: SmsMessage) -> None:
        number = normalize_pk_mobile(msg.to_number)
        msg.attempts += 1
        try:
            method, url, data = self._request(number, msg.body)
            resp = await client.request(method, url, content=data,
                                        headers={"Content-Type": "application/x-www-form-urlencoded"} if data else None)
            text = resp.text[:2000]
            ok = 200 <= resp.status_code < 300 and (not settings.sms_success_marker or settings.sms_success_marker in text)
            if ok:
                msg.status, msg.sent_at, msg.last_error = SmsStatus.SENT, datetime.now(timezone.utc), None
                msg.provider_reference = text.strip()[:120] or None
                return
            msg.last_error = f"HTTP {resp.status_code}: {text.strip()[:250]}"
        except httpx.HTTPError as exc:
            msg.last_error = f"{type(exc).__name__}: {str(exc)[:250]}"
        if msg.attempts >= MAX_ATTEMPTS:
            msg.status = SmsStatus.FAILED

    async def send_pending(self, *, limit: int = 100, client: httpx.AsyncClient | None = None) -> dict:
        msgs = list((await self.session.execute(select(SmsMessage).where(SmsMessage.status == SmsStatus.QUEUED)
                                                .order_by(SmsMessage.created_at).limit(limit).with_for_update(skip_locked=True))).scalars())
        if not msgs:
            return {"processed": 0, "sent": 0, "failed": 0, "logged": 0}
        if not settings.sms_provider_url:
            for m in msgs:
                m.status, m.last_error = SmsStatus.LOGGED, "No SMS provider configured (SMS_PROVIDER_URL)"
            await self.session.flush()
            return {"processed": len(msgs), "sent": 0, "failed": 0, "logged": len(msgs)}
        own = client is None
        client = client or httpx.AsyncClient(timeout=15)
        try:
            for m in msgs:
                await self._deliver(client, m)
        finally:
            if own:
                await client.aclose()
        await self.session.flush()
        return {"processed": len(msgs), "sent": sum(m.status == SmsStatus.SENT for m in msgs),
                "failed": sum(m.status == SmsStatus.FAILED for m in msgs), "logged": 0}

    async def retry_failed(self) -> int:
        failed = list((await self.session.execute(select(SmsMessage).where(SmsMessage.status.in_([SmsStatus.FAILED, SmsStatus.LOGGED])))).scalars())
        for m in failed:
            m.status, m.attempts = SmsStatus.QUEUED, 0
        await self.session.flush()
        return len(failed)

    # ---------------------------------------------------------------- reminders
    async def queue_fee_overdue_reminders(self) -> int:
        """One reminder per student with any overdue instalment, at most once a week."""
        from app.fees.assignment_service import FeeStatusCalculator
        from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, PayerType

        rows = (await self.session.execute(select(FeeAssignment, FeeStructure).join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id)
                                           .where(FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.status == AssignmentStatus.ACTIVE))).all()
        overdue: dict = {}
        for a, s in rows:
            entries = list((await self.session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == a.id))).scalars())
            amount = sum(r["remaining"] for r in FeeStatusCalculator.installments(entries, None) if r["status"] == "OVERDUE")
            if amount > 0.005:
                overdue[a.payer_id] = overdue.get(a.payer_id, 0.0) + amount
        n = 0
        for sid, amount in overdue.items():
            n += await self.queue_for_student(
                sid, event="FEE_OVERDUE", once_within_days=7,
                body=f"{settings.app_name}: fee of Rs. {amount:,.0f} is overdue. Please pay at the bank or accounts office. Challan: My Fees in the portal.")
        return n

    # ---------------------------------------------------------------- what admin sees
    async def overview(self, *, status: SmsStatus | None = None, limit: int = 200) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        counts = {(k.value if hasattr(k, "value") else k): n for k, n in (await self.session.execute(
            select(SmsMessage.status, func.count()).where(SmsMessage.created_at >= since).group_by(SmsMessage.status))).all()}
        stmt = select(SmsMessage).order_by(SmsMessage.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(SmsMessage.status == status)
        items = [{"id": m.id, "created_at": m.created_at, "to_number": m.to_number, "recipient": m.recipient, "event": m.event,
                  "status": m.status.value, "attempts": m.attempts, "body": m.body, "last_error": m.last_error, "sent_at": m.sent_at}
                 for m in (await self.session.execute(stmt)).scalars()]
        return {"provider_configured": bool(settings.sms_provider_url), "last_7_days": counts, "messages": items}