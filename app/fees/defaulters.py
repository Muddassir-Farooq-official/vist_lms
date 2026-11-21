"""Fee defaulter follow-up: who is overdue (by batch term or session, days and amount), SMS reminders sent on purpose by
the office (previewed first, at most one fee SMS per student per day), promises to pay, and exemptions from the
admit-card fee hold.

The amounts come from the same calculation as the defaulters report and the admit-card hold
(app/reports/finance_service.py -> FeeStatusCalculator); nothing here changes a fee, a payment or the ledger.

  * a promise to pay by a date: until that date no fee reminder (automatic or sent from here) goes to the student or
    their family, and their admit card is not held for fees; after it, everything applies again by itself;
  * an exemption (administrators only): the admit card may be printed despite overdue fees, with a reason and
    optionally a last day; reminders still go. Kept on record when revoked (table admit_card_exemptions, kind FEES).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

FEE_EVENTS = ("FEE_OVERDUE", "FEE_DEFAULTER")
EVENT = "FEE_DEFAULTER"
MAX_PROMISE_DAYS = 90
MAX_SEND = 2000
DEFAULT_MESSAGE = ("{institute}: dear {name} ({roll}), fee of Rs. {amount} is overdue since {since}. "
                   "Please pay at the bank or accounts office. Challan: My Fees in the portal.")
PLACEHOLDERS = ("{institute}", "{name}", "{roll}", "{amount}", "{since}", "{days}")


# ---------------------------------------------------------------------- who belongs to a batch term / session
async def batch_student_ids(session: AsyncSession, *, term_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None) -> set[uuid.UUID] | None:
    """Students of one batch term, or of every term of a session: the term's program + semester (active students) and
    anyone enrolled in a subject of the term. None when neither is chosen (= everyone)."""
    from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus
    from app.academics.terms import AcademicSession, AcademicTerm
    from app.students.models import Student, StudentStatus

    if term_id is None and session_id is None:
        return None
    if term_id is not None:
        term = await session.get(AcademicTerm, term_id)
        if term is None:
            raise ResourceNotFoundError("Term not found.")
        terms = [term]
    else:
        if await session.get(AcademicSession, session_id) is None:
            raise ResourceNotFoundError("Session not found.")
        terms = list((await session.execute(select(AcademicTerm).where(AcademicTerm.session_id == session_id))).scalars())
    ids: set[uuid.UUID] = set()
    labels = [t.label.lower() for t in terms]
    for t in terms:
        if t.program_id and t.semester:
            ids |= set((await session.execute(select(Student.id).where(Student.program_id == t.program_id, Student.current_semester == t.semester,
                                                                       Student.status == StudentStatus.ACTIVE))).scalars().all())
    if labels:
        ids |= set((await session.execute(select(Enrollment.student_id).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                          .where(func.lower(CourseOffering.term_label).in_(labels), Enrollment.status != EnrollmentStatus.WITHDRAWN))).scalars().all())
    return ids


# ---------------------------------------------------------------------- promises and exemptions
async def active_promises(session: AsyncSession, student_ids, today: date | None = None) -> dict:
    """{student_id: FeePromise} for promises not cancelled whose date has not passed."""
    from app.fees.models import FeePromise

    today = today or institute_today()
    stmt = select(FeePromise).where(FeePromise.cancelled_at.is_(None), FeePromise.promised_date >= today)
    if student_ids is not None:
        ids = list(student_ids)
        if not ids:
            return {}
        stmt = stmt.where(FeePromise.student_id.in_(ids))
    out: dict = {}
    for p in (await session.execute(stmt.order_by(FeePromise.created_at))).scalars():
        out[p.student_id] = p                       # the latest one wins
    return out


async def promise_for(session: AsyncSession, student_id: uuid.UUID, today: date | None = None):
    return (await active_promises(session, [student_id], today)).get(student_id)


def promise_dict(p, student=None, users: dict | None = None) -> dict:
    users = users or {}
    out = {"id": p.id, "student_id": p.student_id, "promised_date": p.promised_date, "amount": float(p.amount) if p.amount is not None else None,
           "note": p.note, "created_at": p.created_at, "created_by": users.get(p.created_by), "cancelled_at": p.cancelled_at,
           "cancelled_by": users.get(p.cancelled_by)}
    if student is not None:
        out.update({"roll_number": student.roll_number, "name": f"{student.first_name} {student.last_name}"})
    return out


async def add_promise(session: AsyncSession, *, student_id: uuid.UUID, promised_date: date, amount: float | None, note: str | None, actor_id: uuid.UUID):
    """Record a promise; a promise already running for the student is replaced (cancelled, kept on record)."""
    from app.audit.service import AuditService
    from app.fees.models import FeePromise
    from app.students.models import Student

    today = institute_today()
    if promised_date < today:
        raise ValidationAppError("The promised date cannot be in the past.")
    if promised_date > today + timedelta(days=MAX_PROMISE_DAYS):
        raise ValidationAppError(f"A promise can be at most {MAX_PROMISE_DAYS} days ahead.")
    if amount is not None and amount <= 0:
        raise ValidationAppError("The amount must be more than 0 (or leave it empty).")
    if await session.get(Student, student_id) is None:
        raise ResourceNotFoundError("Student not found.")
    old = await promise_for(session, student_id, today)
    if old is not None:
        old.cancelled_at, old.cancelled_by = datetime.now(timezone.utc), actor_id
    row = FeePromise(student_id=student_id, promised_date=promised_date, amount=amount, note=(note or "").strip()[:300] or None,
                     created_by=actor_id)
    session.add(row)
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action="fee.promise_added", entity_type="student", entity_id=student_id,
                                    before={"promised_date": old.promised_date.isoformat()} if old else None,
                                    after={"promised_date": promised_date.isoformat(), "amount": amount, "note": row.note})
    return row


async def cancel_promise(session: AsyncSession, promise_id: uuid.UUID, *, actor_id: uuid.UUID):
    from app.audit.service import AuditService
    from app.fees.models import FeePromise

    row = await session.get(FeePromise, promise_id)
    if row is None:
        raise ResourceNotFoundError("Promise not found.")
    if row.cancelled_at is not None:
        raise BusinessRuleViolationError("It was already cancelled.")
    row.cancelled_at, row.cancelled_by = datetime.now(timezone.utc), actor_id
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action="fee.promise_cancelled", entity_type="student", entity_id=row.student_id,
                                    before={"promised_date": row.promised_date.isoformat()})
    return row


async def list_promises(session: AsyncSession, *, include_old: bool = False) -> list[dict]:
    from app.auth.models import User
    from app.fees.models import FeePromise
    from app.students.models import Student

    today = institute_today()
    rows = (await session.execute(select(FeePromise, Student).join(Student, Student.id == FeePromise.student_id)
                                  .order_by(FeePromise.created_at.desc()).limit(500))).all()
    uids = {u for p, _ in rows for u in (p.created_by, p.cancelled_by) if u}
    users = {u.id: u.username for u in (await session.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    out = []
    for p, s in rows:
        active = p.cancelled_at is None and p.promised_date >= today
        if active or include_old:
            out.append({**promise_dict(p, s, users), "active": active})
    return out


async def fee_hold_lifted(session: AsyncSession, student_id: uuid.UUID) -> str | None:
    """Why overdue fees do not hold this student's admit card right now (an exemption or a promise), or None."""
    from app.students.regdeadline import active_exemption

    today = institute_today()
    ex = await active_exemption(session, student_id, "FEES", today)
    if ex is not None:
        return "exempted" + (f" until {ex.until:%d %b %Y}" if ex.until else "")
    p = await promise_for(session, student_id, today)
    if p is not None:
        return f"promised to pay by {p.promised_date:%d %b %Y}"
    return None


# ---------------------------------------------------------------------- the list
def _tz():
    from zoneinfo import ZoneInfo

    from app.core.config import settings

    return ZoneInfo(settings.institute_timezone)


def day_start_utc() -> datetime:
    """Midnight today (institute time) in UTC: "today" for the one-fee-SMS-a-day rule."""
    return datetime.combine(institute_today(), time.min, tzinfo=_tz()).astimezone(timezone.utc)


async def _last_reminders(session: AsyncSession, ids) -> dict:
    from app.sms.models import SmsMessage

    ids = list(ids)
    if not ids:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=60)
    return dict((await session.execute(select(SmsMessage.student_id, func.max(SmsMessage.created_at)).where(
        SmsMessage.student_id.in_(ids), SmsMessage.event.in_(FEE_EVENTS), SmsMessage.created_at >= since).group_by(SmsMessage.student_id))).all())


async def defaulters(session: AsyncSession, *, term_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None,
                     program_id: uuid.UUID | None = None, semester: int | None = None, min_amount: float | None = None,
                     min_days: int | None = None, student_ids: set | None = None) -> dict:
    """Students with overdue fees (any amount by default), with their promise, exemption and last reminder."""
    from app.reports.finance_service import FinanceReportService
    from app.students.regdeadline import exempt_ids

    ids = await batch_student_ids(session, term_id=term_id, session_id=session_id)
    if student_ids is not None:
        ids = set(student_ids) if ids is None else ids & set(student_ids)
    r = await FinanceReportService(session).defaulters(program_id=program_id, semester=semester, min_amount=min_amount, student_ids=ids)
    rows = [p for p in r["students"] if not min_days or p["days_overdue"] >= min_days]
    today = r["as_of"]
    promises = await active_promises(session, [p["student_id"] for p in rows], today)
    exempt = await exempt_ids(session, "FEES", today)
    last = await _last_reminders(session, [p["student_id"] for p in rows])
    start = day_start_utc()
    for p in rows:
        pr = promises.get(p["student_id"])
        p["promise"] = {"id": pr.id, "promised_date": pr.promised_date, "amount": float(pr.amount) if pr.amount is not None else None,
                        "note": pr.note} if pr else None
        p["exempt"] = p["student_id"] in exempt
        p["last_reminder"] = last.get(p["student_id"])
        p["reminded_today"] = bool(p["last_reminder"] and p["last_reminder"] >= start)
    from app.reports.finance_service import BUCKETS

    totals = {b: round(sum(p[b] for p in rows), 2) for b in BUCKETS}
    totals["total"] = round(sum(p["overdue"] for p in rows), 2)
    return {"as_of": today, "students": rows, "totals": totals, "count": len(rows),
            "promised": sum(1 for p in rows if p["promise"]), "exempted": sum(1 for p in rows if p["exempt"])}


# ---------------------------------------------------------------------- reminders sent from the list
def render(template: str, p: dict) -> str:
    from app.core.config import settings

    text = template
    for key, value in (("{institute}", settings.app_name), ("{name}", p["name"]), ("{roll}", p.get("roll_number") or ""),
                       ("{amount}", f"{p['overdue']:,.0f}"), ("{since}", f"{p['oldest_due']:%d %b %Y}" if p.get("oldest_due") else ""),
                       ("{days}", str(p.get("days_overdue") or ""))):
        text = text.replace(key, value)
    return text


async def remind(session: AsyncSession, *, student_ids: list[uuid.UUID], to: str, message: str | None, dry_run: bool,
                 actor_id: uuid.UUID) -> dict:
    """SMS the chosen defaulters now. Skips anyone not overdue any more, with a promise running, already sent a fee SMS
    today, or without a valid mobile number. dry_run only shows who would get what."""
    from app.audit.service import AuditService
    from app.sms.models import SmsMessage, SmsStatus
    from app.sms.service import _clip, normalize_pk_mobile

    if to not in ("STUDENT", "PARENT", "BOTH"):
        raise ValidationAppError("Choose students, parents or both.")
    if not student_ids:
        raise ValidationAppError("Choose at least one student.")
    if len(student_ids) > MAX_SEND:
        raise ValidationAppError(f"At most {MAX_SEND} students at a time.")
    template = (message or "").strip() or DEFAULT_MESSAGE
    if len(template) > 400:
        raise ValidationAppError("The message is too long (400 characters at most, before the details are filled in).")
    data = await defaulters(session, student_ids=set(student_ids))
    by_id = {p["student_id"]: p for p in data["students"]}
    out_rows, n_messages, n_students = [], 0, 0
    reasons: dict[str, int] = {}

    def skip(sid, name, why):
        reasons[why] = reasons.get(why, 0) + 1
        out_rows.append({"student_id": sid, "name": name, "sent_to": [], "skipped": why})

    from app.students.models import Student

    names = {s.id: f"{s.first_name} {s.last_name}" for s in (await session.execute(select(Student).where(Student.id.in_(student_ids)))).scalars()}
    for sid in dict.fromkeys(student_ids):
        p = by_id.get(sid)
        if p is None:
            skip(sid, names.get(sid, "?"), "no overdue fee")
            continue
        if p["promise"]:
            skip(sid, p["name"], "promised to pay")
            continue
        if p["reminded_today"]:
            skip(sid, p["name"], "already sent a fee SMS today")
            continue
        targets = []
        if to in ("STUDENT", "BOTH") and normalize_pk_mobile(p["phone"]):
            targets.append(("STUDENT", p["phone"]))
        if to in ("PARENT", "BOTH") and normalize_pk_mobile(p["guardian_phone"]):
            if not targets or normalize_pk_mobile(p["guardian_phone"]) != normalize_pk_mobile(targets[0][1]):
                targets.append(("GUARDIAN", p["guardian_phone"]))
        if not targets:
            skip(sid, p["name"], "no valid mobile number")
            continue
        body = _clip(render(template, p))
        out_rows.append({"student_id": sid, "name": p["name"], "roll_number": p["roll_number"], "overdue": p["overdue"],
                         "sent_to": [w for w, _ in targets], "body": body, "skipped": None})
        n_students += 1
        n_messages += len(targets)
        if not dry_run:
            for who, number in targets:
                session.add(SmsMessage(to_number=(number or "")[:20], recipient=who, body=body, event=EVENT, student_id=sid,
                                       status=SmsStatus.QUEUED))
    result = {"dry_run": dry_run, "students": n_students, "messages": n_messages, "skipped": reasons, "rows": out_rows[:500],
              "sample": next((r["body"] for r in out_rows if r.get("body")), render(template, {"name": "Student Name", "roll_number": "R-001",
                                                                                              "overdue": 12500, "oldest_due": institute_today(), "days_overdue": 10}))}
    if not dry_run and n_messages:
        await session.flush()
        await AuditService(session).log(actor_user_id=actor_id, action="fee.defaulter_sms_sent", entity_type="student", entity_id=None,
                                        metadata={"students": n_students, "messages": n_messages, "to": to, "skipped": reasons})
    return result


def csv_rows(data: dict) -> tuple[list[str], list[list]]:
    from app.reports.finance_service import BUCKETS

    header = ["Roll number", "Name", "Father / guardian", "Program", "Semester", "Phone", "Guardian phone", "Oldest due", "Days overdue",
              *[b + " days" for b in BUCKETS], "Total overdue", "Late fee accrued", "Fines pending", "Promised to pay by", "Admit card exempted",
              "Last fee SMS"]
    rows = [[s["roll_number"], s["name"], s.get("father") or "", s["program"] or "", s["semester"] or "", s["phone"] or "", s["guardian_phone"] or "",
             s["oldest_due"], s["days_overdue"], *[s[b] for b in BUCKETS], s["overdue"], s["late_fee_accrued"], s["fines_pending"],
             s["promise"]["promised_date"] if s["promise"] else "", "yes" if s["exempt"] else "",
             s["last_reminder"].astimezone(_tz()).strftime("%Y-%m-%d %H:%M") if s["last_reminder"] else ""] for s in data["students"]]
    return header, rows
