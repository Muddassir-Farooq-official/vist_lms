"""University Reg No deadlines (affiliated programs).

Each batch term (or every batch term of a session at once) can have a last day for its students' university registration
numbers (`academic_terms.reg_deadline`). Only students of programs whose exams the affiliating university holds count.

  * before the deadline: the administration and exam office are reminded once a week (Mondays), then every day in the
    last 7 days; each student without a Reg No gets a notification on the same days and an SMS (once a week, at most
    every 3 days in the last week; Settings -> Rules -> SMS can switch it off);
  * after the deadline (while the term is open): the student's admit card is held (`regno_hold`), unless the
    administration exempted that student with a reason; reminders go on once a week;
  * the internal award list warns about every student without a Reg No (`reg_check`).

Nothing here changes a Reg No or any result; it only reminds and holds admit cards.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

logger = structlog.get_logger(__name__)
MARK = "regno-reminders"
REMINDER_HOUR = 10          # like the overdue-fee reminders (scripts/sms_sender.py)
LAST_DAYS = 7
KINDS = ("REG_NO", "FEES")


def _fmt(d: date) -> str:
    return d.strftime("%d %b %Y")


async def affiliated_program_ids(session: AsyncSession) -> set[uuid.UUID]:
    from app.programs.models import Program

    return set((await session.execute(select(Program.id).where(Program.exam_body == "AFFILIATED"))).scalars().all())


async def term_student_ids(session: AsyncSession, term, affiliated: set[uuid.UUID] | None = None) -> set[uuid.UUID]:
    """Active students of affiliated programs in this batch term: its program + semester, and anyone enrolled in a subject
    of the term."""
    from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus
    from app.students.models import Student, StudentStatus

    if affiliated is None:
        affiliated = await affiliated_program_ids(session)
    if not affiliated:
        return set()
    ids: set[uuid.UUID] = set()
    if term.program_id and term.semester and term.program_id in affiliated:
        ids |= set((await session.execute(select(Student.id).where(Student.program_id == term.program_id, Student.current_semester == term.semester,
                                                                   Student.status == StudentStatus.ACTIVE))).scalars().all())
    ids |= set((await session.execute(select(Student.id).join(Enrollment, Enrollment.student_id == Student.id)
                                      .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                      .where(func.lower(CourseOffering.term_label) == term.label.lower(), Enrollment.status != EnrollmentStatus.WITHDRAWN,
                                             Student.status == StudentStatus.ACTIVE, Student.program_id.in_(affiliated)))).scalars().all())
    return ids


async def _missing(session: AsyncSession, ids: set[uuid.UUID]) -> list:
    from app.students.models import Student

    if not ids:
        return []
    return list((await session.execute(select(Student).where(Student.id.in_(ids), Student.reg_number.is_(None))
                                       .order_by(Student.roll_number))).scalars().all())


async def _live_terms(session: AsyncSession, today: date) -> list:
    """Open terms with a deadline that have not ended."""
    from app.academics.terms import AcademicTerm, TermStatus

    rows = (await session.execute(select(AcademicTerm).where(AcademicTerm.reg_deadline.is_not(None), AcademicTerm.status == TermStatus.OPEN)
                                  .order_by(AcademicTerm.reg_deadline, AcademicTerm.label))).scalars().all()
    return [t for t in rows if t.end_date is None or t.end_date >= today]


# ---------------------------------------------------------------------- exemptions (shared with the fee hold)
async def active_exemption(session: AsyncSession, student_id: uuid.UUID, kind: str, today: date | None = None):
    from app.core.clock import institute_today
    from app.exams.models import AdmitCardExemption

    today = today or institute_today()
    rows = (await session.execute(select(AdmitCardExemption).where(AdmitCardExemption.student_id == student_id, AdmitCardExemption.kind == kind,
                                                                   AdmitCardExemption.revoked_at.is_(None))
                                  .order_by(AdmitCardExemption.created_at.desc()))).scalars().all()
    return next((r for r in rows if r.until is None or r.until >= today), None)


async def exempt_ids(session: AsyncSession, kind: str, today: date) -> set[uuid.UUID]:
    from app.exams.models import AdmitCardExemption

    rows = (await session.execute(select(AdmitCardExemption.student_id, AdmitCardExemption.until).where(
        AdmitCardExemption.kind == kind, AdmitCardExemption.revoked_at.is_(None)))).all()
    return {sid for sid, until in rows if until is None or until >= today}


def exemption_dict(e, student=None, users: dict | None = None) -> dict:
    users = users or {}
    out = {"id": e.id, "student_id": e.student_id, "kind": e.kind, "reason": e.reason, "until": e.until, "created_at": e.created_at,
           "created_by": users.get(e.created_by), "revoked_at": e.revoked_at, "revoked_by": users.get(e.revoked_by)}
    if student is not None:
        out.update({"roll_number": student.roll_number, "name": f"{student.first_name} {student.last_name}"})
    return out


async def add_exemption(session: AsyncSession, *, student_id: uuid.UUID, kind: str, reason: str, until: date | None, actor_id: uuid.UUID):
    from app.audit.service import AuditService
    from app.core.clock import institute_today
    from app.exams.models import AdmitCardExemption
    from app.students.models import Student

    if kind not in KINDS:
        raise ValidationAppError("Choose REG_NO or FEES.")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationAppError("Write the reason (at least 3 characters).")
    if until is not None and until < institute_today():
        raise ValidationAppError("The last day cannot be in the past.")
    student = await session.get(Student, student_id)
    if student is None:
        raise ResourceNotFoundError("Student not found.")
    if await active_exemption(session, student_id, kind) is not None:
        raise BusinessRuleViolationError(f"{student.first_name} {student.last_name} is already exempted; revoke that one first to change it.")
    row = AdmitCardExemption(student_id=student_id, kind=kind, reason=reason[:500], until=until, created_by=actor_id)
    session.add(row)
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action="admit_card.exemption_added", entity_type="student", entity_id=student_id,
                                    after={"kind": kind, "reason": row.reason, "until": until.isoformat() if until else None})
    return row


async def revoke_exemption(session: AsyncSession, exemption_id: uuid.UUID, *, actor_id: uuid.UUID, kind: str | None = None):
    from app.audit.service import AuditService
    from app.exams.models import AdmitCardExemption

    row = await session.get(AdmitCardExemption, exemption_id)
    if row is None or (kind and row.kind != kind):
        raise ResourceNotFoundError("Exemption not found.")
    if row.revoked_at is not None:
        raise BusinessRuleViolationError("It was already revoked.")
    row.revoked_at = datetime.now(timezone.utc)
    row.revoked_by = actor_id
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action="admit_card.exemption_revoked", entity_type="student", entity_id=row.student_id,
                                    before={"kind": row.kind, "reason": row.reason})
    return row


async def list_exemptions(session: AsyncSession, kind: str, *, include_old: bool = False) -> list[dict]:
    from app.auth.models import User
    from app.core.clock import institute_today
    from app.exams.models import AdmitCardExemption
    from app.students.models import Student

    today = institute_today()
    rows = (await session.execute(select(AdmitCardExemption, Student).join(Student, Student.id == AdmitCardExemption.student_id)
                                  .where(AdmitCardExemption.kind == kind).order_by(AdmitCardExemption.created_at.desc()).limit(500))).all()
    uids = {u for e, _ in rows for u in (e.created_by, e.revoked_by) if u}
    users = {u.id: u.username for u in (await session.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    out = []
    for e, s in rows:
        active = e.revoked_at is None and (e.until is None or e.until >= today)
        if active or include_old:
            out.append({**exemption_dict(e, s, users), "active": active})
    return out


# ---------------------------------------------------------------------- deadlines
async def overview(session: AsyncSession) -> dict:
    """Every batch term with a deadline (open ones), with how many of its students still have no Reg No."""
    from app.academics.terms import AcademicSession, AcademicTerm, TermStatus
    from app.core.clock import institute_today

    today = institute_today()
    affiliated = await affiliated_program_ids(session)
    exempt = await exempt_ids(session, "REG_NO", today)
    terms = (await session.execute(select(AcademicTerm).where(AcademicTerm.status == TermStatus.OPEN).order_by(AcademicTerm.label))).scalars().all()
    sessions = {s.id: s.name for s in (await session.execute(select(AcademicSession))).scalars()}
    rows = []
    for t in terms:
        ids = await term_student_ids(session, t, affiliated) if t.reg_deadline else set()
        missing = await _missing(session, ids)
        rows.append({"term_id": t.id, "label": t.label, "session_id": t.session_id, "session": sessions.get(t.session_id),
                     "program_id": t.program_id, "affiliated": (t.program_id in affiliated) if t.program_id else None,
                     "reg_deadline": t.reg_deadline, "days_left": (t.reg_deadline - today).days if t.reg_deadline else None,
                     "students": len(ids), "missing": len(missing), "missing_exempt": sum(1 for s in missing if s.id in exempt),
                     "missing_roll_numbers": [s.roll_number for s in missing[:30]]})
    return {"today": today, "affiliated_programs": len(affiliated), "terms": rows,
            "sessions": [{"id": k, "name": v} for k, v in sorted(sessions.items(), key=lambda kv: kv[1])]}


async def set_deadline(session: AsyncSession, *, term_id: uuid.UUID | None, session_id: uuid.UUID | None, deadline: date | None,
                       actor_id: uuid.UUID) -> dict:
    """Set (or clear, with None) the deadline of one batch term, or of every open batch term of an affiliated program in a session."""
    from app.academics.terms import AcademicSession, AcademicTerm, TermStatus
    from app.audit.service import AuditService

    if (term_id is None) == (session_id is None):
        raise ValidationAppError("Choose one term or one session.")
    affiliated = await affiliated_program_ids(session)
    skipped: list[str] = []
    if term_id is not None:
        term = await session.get(AcademicTerm, term_id)
        if term is None:
            raise ResourceNotFoundError("Term not found.")
        if deadline is not None and term.program_id and term.program_id not in affiliated:
            raise BusinessRuleViolationError(f"{term.label} belongs to a program whose exams the institute holds itself: it needs no university Reg No.")
        if deadline is not None and term.status != TermStatus.OPEN:
            raise BusinessRuleViolationError(f"{term.label} is closed.")
        terms = [term]
    else:
        sess = await session.get(AcademicSession, session_id)
        if sess is None:
            raise ResourceNotFoundError("Session not found.")
        terms = []
        for t in (await session.execute(select(AcademicTerm).where(AcademicTerm.session_id == session_id).order_by(AcademicTerm.label))).scalars():
            if deadline is not None and (t.status != TermStatus.OPEN or (t.program_id and t.program_id not in affiliated)):
                skipped.append(t.label)
                continue
            terms.append(t)
        if not terms and deadline is not None:
            raise BusinessRuleViolationError(f"{sess.name} has no open term of an affiliated program.")
    changed = []
    for t in terms:
        if t.reg_deadline != deadline:
            before = t.reg_deadline
            t.reg_deadline = deadline
            changed.append(t.label)
            await AuditService(session).log(actor_user_id=actor_id, action="term.reg_deadline_set", entity_type="academic_term", entity_id=t.id,
                                            before={"reg_deadline": before.isoformat() if before else None},
                                            after={"reg_deadline": deadline.isoformat() if deadline else None})
    await session.flush()
    return {"changed": changed, "skipped": skipped, "reg_deadline": deadline}


# ---------------------------------------------------------------------- the admit-card hold
async def regno_hold(session: AsyncSession, student_id: uuid.UUID, today: date | None = None) -> str | None:
    """Why the admit card is held for a missing Reg No, or None."""
    from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus
    from app.core.clock import institute_today
    from app.students.models import Student

    today = today or institute_today()
    student = await session.get(Student, student_id, populate_existing=True)     # the Reg No may have just been entered
    if student is None or student.reg_number or student.program_id is None:
        return None
    terms = [t for t in await _live_terms(session, today) if t.reg_deadline < today]
    if not terms or student.program_id not in await affiliated_program_ids(session):
        return None
    labels = None
    for t in terms:
        hit = t.program_id == student.program_id and t.semester is not None and t.semester == student.current_semester
        if not hit:
            if labels is None:
                labels = {(x or "").lower() for x in (await session.execute(select(CourseOffering.term_label).join(
                    Enrollment, Enrollment.course_offering_id == CourseOffering.id).where(
                    Enrollment.student_id == student_id, Enrollment.status != EnrollmentStatus.WITHDRAWN))).scalars()}
            hit = t.label.lower() in labels
        if hit:
            if await active_exemption(session, student_id, "REG_NO", today) is not None:
                return None
            return f"university Reg No not given (the last day was {_fmt(t.reg_deadline)})"
    return None


async def reg_check(session: AsyncSession, offering_id: uuid.UUID) -> dict:
    """For the internal award list: how many of the subject's students have no Reg No, and the batch's deadline."""
    from app.academics.affiliation import is_affiliated, program_of_offering
    from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus
    from app.academics.terms import AcademicTerm
    from app.students.models import Student

    offering = await session.get(CourseOffering, offering_id)
    if offering is None:
        raise ResourceNotFoundError("Course offering not found.")
    program = await program_of_offering(session, offering)
    if not is_affiliated(program):
        return {"affiliated": False, "missing": 0, "students": [], "reg_deadline": None}
    rows = (await session.execute(select(Student).join(Enrollment, Enrollment.student_id == Student.id).where(
        Enrollment.course_offering_id == offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN, Student.reg_number.is_(None))
        .order_by(Student.roll_number))).scalars().all()
    term = (await session.execute(select(AcademicTerm).where(func.lower(AcademicTerm.label) == (offering.term_label or "").lower()))).scalars().first()
    deadline = term.reg_deadline if term else None
    if deadline is None and rows:                     # the subject's term has none: the batch term of these students
        from app.core.clock import institute_today

        sems = {s.current_semester for s in rows}
        found = [t.reg_deadline for t in await _live_terms(session, institute_today()) if t.program_id == program.id and t.semester in sems]
        deadline = min(found) if found else None
    return {"affiliated": True, "missing": len(rows), "reg_deadline": deadline,
            "students": [{"roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}"} for s in rows[:50]]}


# ---------------------------------------------------------------------- daily reminders (scripts/sms_sender.py)
def due_today(deadline: date, today: date) -> bool:
    left = (deadline - today).days
    if 0 <= left <= LAST_DAYS:
        return True
    return today.weekday() == 0                     # Mondays: weekly before the last week, and after the deadline


async def regno_reminder_round(session: AsyncSession, now: datetime | None = None) -> int:
    """Run by the sender every 30 seconds; does its work once a day from 10:00. Never raises."""
    try:
        async with session.begin_nested():
            return await _round(session, now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("regno_reminders_failed", error=str(exc)[:300])
        return 0


async def _staff(session: AsyncSession) -> list:
    from app.auth.models import Role, User, UserRole
    from app.auth.permissions import Roles

    q = (select(User).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
         .where(Role.code.in_([Roles.ADMIN, Roles.EXAM_CONTROLLER]), User.is_active.is_(True)).distinct())
    return list((await session.execute(q)).scalars().all())


async def _round(session: AsyncSession, now: datetime | None) -> int:
    from app.core.background import beat
    from app.core.clock import institute_now
    from app.core.config import settings
    from app.notifications.email_models import ServiceHeartbeat
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification
    from app.sms.service import SmsService

    now = now or institute_now()
    today = now.date()
    if now.hour < REMINDER_HOUR:
        return 0
    mark = await session.get(ServiceHeartbeat, MARK)
    if mark is not None and mark.detail == today.isoformat():
        return 0
    affiliated = await affiliated_program_ids(session)
    exempt = await exempt_ids(session, "REG_NO", today)
    lines, told = [], {}
    for t in await _live_terms(session, today):
        if not due_today(t.reg_deadline, today):
            continue
        missing = await _missing(session, await term_student_ids(session, t, affiliated))
        if not missing:
            continue
        left = (t.reg_deadline - today).days
        when = (f"last day {_fmt(t.reg_deadline)}, " + ("today" if left == 0 else f"{left} day(s) left")) if left >= 0 else \
            f"the last day was {_fmt(t.reg_deadline)}: their admit cards are held"
        lines.append(f"{t.label}: {len(missing)} student(s) ({when})")
        for s in missing:
            if s.id not in exempt and s.id not in told:
                told[s.id] = (s, t.reg_deadline, left)
    n = 0
    if lines:
        text = "Students without a university Reg No — " + "; ".join(lines[:8]) + (f" (+{len(lines) - 8} more)" if len(lines) > 8 else "") + \
            ". Fill them in on University Reg Nos."
        for u in await _staff(session):
            await create_notification(session, user_id=u.id, category=NotificationCategory.GENERAL, title="Reg Nos missing",
                                      message=text[:1000], link="/dashboard/admin/reg-numbers")
            n += 1
    sms = SmsService(session)
    for s, deadline, left in told.values():
        if left >= 0:
            msg = (f"Your university registration number (Reg No) is not on record. Please give it to the exam office by {_fmt(deadline)}; "
                   "after that your admit card will be held.")
        else:
            msg = "Your university registration number (Reg No) is not on record, so your admit card is held. Please give it to the exam office."
        if s.user_id:
            await create_notification(session, user_id=s.user_id, category=NotificationCategory.GENERAL, title="Reg No needed", message=msg)
            n += 1
        await sms.queue_for_student(s.id, event="REG_NO", body=f"{settings.app_name}: {msg}", to_guardian=False,
                                    once_within_days=3 if 0 <= left <= LAST_DAYS else 6)
    await beat(session, MARK, today.isoformat())
    return n
