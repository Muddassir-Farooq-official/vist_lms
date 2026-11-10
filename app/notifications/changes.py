"""A plain notification to the person whenever the office changes something about them.

Every change the office makes is already written to the audit log (AuditService.log). This module turns the ones that
affect a person into a message for that person, in one place, so no screen can forget to tell them:

    staff      working hours (for everyone, their job role or just them), attendance corrections, holidays, salary,
               bonus, job role, status, portal access, password reset, record details, pay items, security deduction
    students   record details, status, password reset, photo, enrolment (added, removed, section moved, result outcome),
               marks entered or corrected, leave from classes, hostel room, fee changes (late fee, due dates for everyone,
               payments marked paid or reversed, fees ended or written off, credit moved)
    classes    a class added, moved (day / time / room) or removed from the timetable: its students and its teacher

Not here: changes whose own screen already sends a message (fee due date, scholarships, leave, warnings ...), and
anything the person did themselves. It never stops the change it is reporting: any problem here is logged and skipped.
"""
from __future__ import annotations

import contextlib
import contextvars
import uuid
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)
_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("vist_change_notices_quiet", default=False)
DAYS = {"MONDAY": "Mon", "TUESDAY": "Tue", "WEDNESDAY": "Wed", "THURSDAY": "Thu", "FRIDAY": "Fri", "SATURDAY": "Sat", "SUNDAY": "Sun"}
STUDENT_LINKS = {"timetable": "/dashboard/student/timetable", "results": "/dashboard/student/results", "fees": "/dashboard/student/fees",
                 "courses": "/dashboard/student/courses", "profile": "/dashboard/student/profile", "attendance": "/dashboard/student/attendance"}
TEACHER_TIMETABLE = "/dashboard/faculty/timetable"


@contextlib.contextmanager
def quiet():
    """For flows that already send their own, better message (e.g. a warning's decision changes a student's status)."""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


def silently(fn):
    """Decorator for bulk actions (scheduling a whole term ...): no per-change messages, which would flood everyone."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        with quiet():
            return await fn(*args, **kwargs)
    return wrapper


class Note:
    __slots__ = ("user_ids", "title", "message", "link", "category")

    def __init__(self, user_ids, title: str, message: str, link: str | None = None, category: str = "GENERAL"):
        self.user_ids, self.title, self.message, self.link, self.category = list(user_ids), title, message, link, category


# ------------------------------------------------------------------ who is affected
async def _emp_user(s: AsyncSession, employee_id) -> list:
    from app.employees.models import Employee

    if not employee_id:
        return []
    uid = (await s.execute(select(Employee.user_id).where(Employee.id == employee_id))).scalar_one_or_none()
    return [uid] if uid else []


async def _student_user(s: AsyncSession, student_id) -> list:
    from app.students.models import Student

    if not student_id:
        return []
    uid = (await s.execute(select(Student.user_id).where(Student.id == student_id))).scalar_one_or_none()
    return [uid] if uid else []


async def _payer_users(s: AsyncSession, assignment_id) -> list:
    from app.fees.models import FeeAssignment, PayerType

    a = await s.get(FeeAssignment, assignment_id) if assignment_id else None
    if a is None:
        return []
    return await (_student_user(s, a.payer_id) if a.payer_type == PayerType.STUDENT else _emp_user(s, a.payer_id))


async def _entry_assignment(s: AsyncSession, entry_id):
    from app.fees.models import FeeLedgerEntry

    e = await s.get(FeeLedgerEntry, entry_id) if entry_id else None
    return e.fee_assignment_id if e else None


async def _offering_people(s: AsyncSession, offering_id) -> tuple[list, list, str]:
    """(student user ids, teacher user ids, 'CS-201 A') for a course section."""
    from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus
    from app.employees.models import Employee
    from app.students.models import Student

    if not offering_id:
        return [], [], ""
    row = (await s.execute(select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id)
                           .where(CourseOffering.id == uuid.UUID(str(offering_id))))).first()
    if row is None:
        return [], [], ""
    o, c = row
    students = (await s.execute(select(Student.user_id).join(Enrollment, Enrollment.student_id == Student.id).where(
        Enrollment.course_offering_id == o.id, Enrollment.status == EnrollmentStatus.ENROLLED, Student.user_id.is_not(None)))).scalars().all()
    teacher = []
    if o.assigned_teacher_id:
        uid = (await s.execute(select(Employee.user_id).where(Employee.id == o.assigned_teacher_id))).scalar_one_or_none()
        teacher = [uid] if uid else []
    return list(students), teacher, f"{c.code} {o.section_label or ''}".strip()


async def _enrollment(s: AsyncSession, enrollment_id):
    from app.academics.models import Course, CourseOffering, Enrollment

    row = (await s.execute(select(Enrollment, Course).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                           .join(Course, Course.id == CourseOffering.course_id).where(Enrollment.id == enrollment_id))).first()
    return row if row else (None, None)


def _fields(before: dict | None, after: dict | None) -> str:
    """'phone, address' from the keys whose values changed (never the values: they may be private)."""
    before, after = before or {}, after or {}
    skip = {"updated_at", "created_at", "id", "user_id"}
    keys = [k for k in after if k not in skip and str(before.get(k)) != str(after.get(k))]
    names = [k.replace("_id", "").replace("_", " ") for k in keys][:6]
    return ", ".join(names)


def _hm(v) -> str:
    return str(v or "")[:5]


def _timing_text(after: dict) -> str:
    start, end = _hm(after.get("start_time") or after.get("arrive")), _hm(after.get("end_time") or after.get("leave"))
    days = after.get("days") or after.get("work_days")
    text = f"{start}–{end}" if start and end else ""
    if days:
        text += (" · " if text else "") + str(days).replace("[", "").replace("]", "").replace("'", "")
    return text


# ------------------------------------------------------------------ the rules: action -> what to tell whom
async def _timing(s, *, entity_id, metadata, **_):
    from app.employees.models import Employee, EmployeeStatus

    scope = (metadata or {}).get("scope")
    q = select(Employee.user_id).where(Employee.user_id.is_not(None), Employee.status != EmployeeStatus.TERMINATED)
    if scope == "JOB_ROLE":
        q = q.where(Employee.job_role_id == entity_id)
    elif scope == "EMPLOYEE":
        q = q.where(Employee.id == entity_id)
    users = (await s.execute(q)).scalars().all()
    detail = _timing_text((metadata or {}).get("after") or {})
    who = {"DEFAULT": "for all staff", "JOB_ROLE": "for your job role", "EMPLOYEE": "for you"}.get(scope, "")
    return Note(users, "Your working hours have changed", f"The office has changed the working hours {who}" + (f": {detail}." if detail else ".")
                + " Check your attendance page.")


async def _timing_removed(s, *, entity_id, metadata, **_):
    if (metadata or {}).get("scope") != "EMPLOYEE":
        return await _timing(s, entity_id=entity_id, metadata={"scope": "JOB_ROLE"} | (metadata or {}))
    return Note(await _emp_user(s, entity_id), "Your working hours have changed", "Your own working hours were removed: the hours for your job role "
                "(or the institute's normal hours) apply again.")


async def _staff_day(s, *, entity_id, metadata, action, **_):
    from app.timekeeping.models import StaffAttendance

    emp_id = entity_id
    if action == "timekeeping.day_corrected":
        row = await s.get(StaffAttendance, entity_id)
        emp_id = row.employee_id if row else None
    day = (metadata or {}).get("day") or ""
    text = {"timekeeping.day_corrected": f"Your attendance for {day} was corrected by the office.",
            "timekeeping.day_cleared": f"Your attendance for {day} was cleared by the office.",
            "timekeeping.mark_undone": "A sign-in or sign-out of yours today was undone by the office."}[action]
    return Note(await _emp_user(s, emp_id), "Your attendance was changed", text)


async def _leave_excused(s, *, entity_id, metadata, **_):
    m = metadata or {}
    return Note(await _emp_user(s, entity_id), "Days marked as leave", f"{m.get('days', '')} day(s) from {m.get('from')} to {m.get('to')} are marked as leave"
                + (f": {m.get('reason')}" if m.get("reason") else "."))


async def _holiday(s, *, metadata, **_):
    from app.employees.models import Employee, EmployeeStatus

    users = (await s.execute(select(Employee.user_id).where(Employee.user_id.is_not(None), Employee.status != EmployeeStatus.TERMINATED))).scalars().all()
    m = metadata or {}
    return Note(users, "Holiday", f"{m.get('day')} is a holiday for staff" + (f": {m.get('reason')}." if m.get("reason") else "."))


def _employee(title: str, message: Callable[[dict, dict, dict], str]):
    async def rule(s, *, entity_id, before, after, metadata, **_):
        return Note(await _emp_user(s, entity_id), title, message(before or {}, after or {}, metadata or {}))
    return rule


def _money(v) -> str:
    try:
        return f"Rs. {float(v):,.0f}"
    except (TypeError, ValueError):
        return ""


async def _pay_item(s, *, entity_id, action, **_):
    from app.payroll.models import EmployeePayItem

    i = await s.get(EmployeePayItem, entity_id)
    if i is None:
        return None
    word = "added to" if action == "payroll.pay_item_added" else "changed in"
    return Note(await _emp_user(s, i.employee_id), "Your pay was changed", f"An allowance or deduction was {word} your monthly pay. See My pay & attendance.",
                category="PAYROLL")


def _student(title: str, message: Callable[[dict, dict, dict], str], link: str | None = None, category: str = "GENERAL"):
    async def rule(s, *, entity_id, before, after, metadata, **_):
        return Note(await _student_user(s, entity_id), title, message(before or {}, after or {}, metadata or {}), link, category)
    return rule


STATUS_WORDS = {"ACTIVE": "Active", "SUSPENDED": "Suspended", "DROPPED": "Dropped", "GRADUATED": "Graduated", "FROZEN": "Frozen",
                "PENDING_VERIFICATION": "Waiting for verification", "ON_LEAVE": "On leave", "TERMINATED": "Employment ended", "INACTIVE": "Inactive"}


async def _enrol(s, *, entity_id, action, before, after, **_):
    e, c = await _enrollment(s, entity_id)
    if e is None:
        return None
    users = await _student_user(s, e.student_id)
    name = f"{c.code} {c.title}"
    if action in ("enrollment.created", "enrollment.re_enrolled"):
        return Note(users, "Added to a course", f"You have been enrolled in {name}.", STUDENT_LINKS["courses"])
    if action == "enrollment.withdrawn":
        return Note(users, "Removed from a course", f"You are no longer enrolled in {name}.", STUDENT_LINKS["courses"])
    if action == "enrollment.section_moved":
        return Note(users, "Your section has changed", f"{name}: moved from section {(before or {}).get('section') or '—'} to "
                    f"{(after or {}).get('section') or '—'}. Check your timetable.", STUDENT_LINKS["timetable"])
    if action == "enrollment.outcome_set":
        return Note(users, "Course result updated", f"{name}: your result is now {(after or {}).get('outcome', '').replace('_', ' ').lower()}.",
                    STUDENT_LINKS["results"], "RESULT")
    return None


async def _mark(s, *, after, action, before, **_):
    after = after or {}
    e, c = await _enrollment(s, uuid.UUID(after["enrollment_id"])) if after.get("enrollment_id") else (None, None)
    if e is None:
        return None
    what = after.get("assessment_type", "an assessment")
    got = after.get("obtained_marks")
    if action == "student_mark.corrected":
        text = f"{c.code}: your {what} marks were corrected from {(before or {}).get('obtained_marks')} to {got}."
    else:
        text = f"{c.code}: your {what} marks are in ({got})."
    return Note(await _student_user(s, e.student_id), "Marks updated", text, STUDENT_LINKS["results"], "RESULT")


async def _fee_entry(s, *, entity_id, action, before, after, metadata, **_):
    users = await _payer_users(s, await _entry_assignment(s, entity_id))
    if action == "fee.late_fee_posted":
        return Note(users, "Late fee added", f"A late fee of {_money((after or {}).get('amount_posted'))} was added because a payment is overdue.",
                    STUDENT_LINKS["fees"], "FEE")
    return Note(users, "A payment was reversed", f"The payment with receipt {(before or {}).get('receipt')} ({_money((before or {}).get('amount'))}) was "
                f"reversed by the office: {(metadata or {}).get('reason', '')}", STUDENT_LINKS["fees"], "FEE")


async def _fee_assignment(s, *, entity_id, action, before, after, metadata, **_):
    users = await _payer_users(s, entity_id)
    m = metadata or {}
    if action == "fee_assignment.status_changed":
        text = f"One of your fees is now {(after or {}).get('status', '').replace('_', ' ').lower()}."
    elif action == "fee.future_installments_cancelled":
        text = f"Instalments not yet due ({_money(m.get('amount'))}) were cancelled" + (f": {m.get('why')}." if m.get("why") else ".")
    elif action == "fee.credit_moved":
        text = f"{_money(m.get('amount'))} of your credit was moved to another of your fees."
    else:      # scholarship.withdrawn
        text = "A scholarship or discount on one of your fees was withdrawn. See your fees for the new amounts."
    return Note(users, "Your fees were changed", text, STUDENT_LINKS["fees"], "FEE")


async def _challan_paid(s, *, entity_id, metadata, **_):
    from app.fees.payment_models import PaymentChallan

    ch = await s.get(PaymentChallan, entity_id)
    if ch is None:
        return None
    return Note(await _payer_users(s, ch.fee_assignment_id), "Payment recorded", f"Challan {ch.consumer_number} ({_money(ch.amount)}) was marked paid by the office.",
                STUDENT_LINKS["fees"], "FEE")


async def _due_dates_for_all(s, *, entity_id, after, **_):
    from app.fees.models import FeeAssignment, FeeStructure, PayerType
    from app.students.models import Student

    st = await s.get(FeeStructure, entity_id)
    if st is None or not (after or {}).get("changed"):
        return None
    users = (await s.execute(select(Student.user_id).join(FeeAssignment, (FeeAssignment.payer_id == Student.id) & (FeeAssignment.payer_type == PayerType.STUDENT))
                             .where(FeeAssignment.fee_structure_id == st.id, Student.user_id.is_not(None)))).scalars().all()
    return Note(users, "Fee due date changed", f"{st.name}: instalment {(after or {}).get('installment_number')} is now due on {(after or {}).get('due_date')}.",
                STUDENT_LINKS["fees"], "FEE")


async def _hostel(s, *, entity_id, action, metadata, **_):
    m = metadata or {}
    if action == "hostel.checked_in":
        text = f"You have been given hostel room {m.get('room')} from {m.get('from')} (monthly fee {_money(m.get('monthly_fee'))})."
    else:
        text = f"Your hostel stay ends on {m.get('to')}" + (f": {m.get('reason')}." if m.get("reason") else ".")
    return Note(await _student_user(s, entity_id), "Hostel", text)


async def _leave_from_classes(s, *, entity_id, metadata, **_):
    m = metadata or {}
    return Note(await _student_user(s, entity_id), "Leave from classes", f"You are on leave from {m.get('from')} to {m.get('to')} "
                f"({m.get('classes', 0)} classes marked as leave).", STUDENT_LINKS["attendance"])


async def _slot(s, *, action, before, after, **_):
    after, before = after or {}, before or {}
    oid = after.get("course_offering_id")
    if not oid:
        return None
    students, teacher, label = await _offering_people(s, oid)
    when = f"{DAYS.get(str(after.get('day_of_week')), after.get('day_of_week', ''))} {_hm(after.get('start_time'))}–{_hm(after.get('end_time'))}"
    room = f", room {after.get('room')}" if after.get("room") else ""
    if action == "timetable_slot.changed":
        was = f"{DAYS.get(str(before.get('day_of_week')), before.get('day_of_week', ''))} {_hm(before.get('start_time'))}"
        text = f"{label}: the class on {was} moves to {when}{room}" + (f" from {after.get('from_date')}." if after.get("from_date") else ".")
    elif action == "timetable_slot.created":
        text = f"{label}: a new class on {when}{room}."
    else:
        text = f"{label}: the class on {when} is removed from the timetable."
    return [Note(students, "Timetable change", text, STUDENT_LINKS["timetable"]), Note(teacher, "Timetable change", text, TEACHER_TIMETABLE)]


async def _offering(s, *, entity_id, before, after, **_):
    before, after = before or {}, after or {}
    if str(before.get("assigned_teacher_id")) == str(after.get("assigned_teacher_id")) and before.get("section_label") == after.get("section_label"):
        return None                       # only capacity / open-for-enrolment changed: nothing for the students
    students, teacher, label = await _offering_people(s, entity_id)
    teacher_changed = str(before.get("assigned_teacher_id")) != str(after.get("assigned_teacher_id"))
    text = f"{label}: the teacher has changed. Check your timetable." if teacher_changed else f"{label}: the section details have changed. Check your timetable."
    notes = [Note(students, "Class change", text, STUDENT_LINKS["timetable"])]
    if teacher_changed:
        notes.append(Note(teacher, "Class assigned to you", f"{label} is now yours to teach. See My Timetable.", TEACHER_TIMETABLE))
    return notes


RULES: dict[str, Callable[..., Awaitable["Note | list[Note] | None"]]] = {
    # staff
    "timekeeping.timing_set": _timing, "timekeeping.timing_removed": _timing_removed,
    "timekeeping.day_corrected": _staff_day, "timekeeping.day_cleared": _staff_day, "timekeeping.mark_undone": _staff_day,
    "timekeeping.leave_excused": _leave_excused, "timekeeping.holiday": _holiday,
    "employee.updated": _employee("Your staff record was updated", lambda b, a, m: f"The office updated your record ({_fields(b, a) or 'details'}).".replace(" ()", "")),
    "employee.job_role_changed": _employee("Your job role has changed", lambda b, a, m: "Your job role was changed by the office. Your menus and working hours may be different."),
    "employee.salary_set": _employee("Your salary has changed", lambda b, a, m: f"Your base salary is now {_money(a.get('base_salary'))}"
                                     + (f" from {m.get('effective_from')}." if m.get("effective_from") else ".")),
    "employee.salary_incremented": _employee("Salary increment", lambda b, a, m: f"Your base salary goes from {_money(b.get('base_salary'))} to {_money(a.get('base_salary'))}."),
    "employee.bonus_awarded": _employee("Bonus", lambda b, a, m: f"You have been given a bonus of {_money(a.get('amount'))}" + (f": {a.get('reason')}." if a.get("reason") else ".")),
    "employee.status_changed": _employee("Your status has changed", lambda b, a, m: f"Your status is now: {STATUS_WORDS.get(a.get('status'), a.get('status'))}."),
    "employee.password_reset": _employee("Your password was reset", lambda b, a, m: "The office reset your password. If you did not ask for this, tell the administration."),
    "employee.access_roles_changed": _employee("Your portal access has changed", lambda b, a, m: "The parts of the portal you can use were changed. Sign in again to see them."),
    "payroll.pay_item_added": _pay_item, "payroll.pay_item_updated": _pay_item,
    "payroll.employee_setting": _employee("Your pay settings changed", lambda b, a, m: "Your security deduction settings were changed by the office."),
    # students
    "student.updated": _student("Your record was updated", lambda b, a, m: f"The office updated your record ({_fields(b, a) or 'details'}).".replace(" ()", ""), STUDENT_LINKS["profile"]),
    "student.identity_corrected": _student("Your record was corrected", lambda b, a, m: "The office corrected your name or identity details.", STUDENT_LINKS["profile"]),
    "student.status_changed": _student("Your status has changed", lambda b, a, m: f"Your status is now: {STATUS_WORDS.get(a.get('status'), a.get('status'))}."),
    "student.password_reset": _student("Your password was reset", lambda b, a, m: "The office reset your password. If you did not ask for this, tell the administration."),
    "student.photo_replaced": _student("Your photo was changed", lambda b, a, m: "The office replaced the photo on your record.", STUDENT_LINKS["profile"]),
    "student.reactivated_after_attendance_fine": _student("Your portal is open again", lambda b, a, m: "Your attendance fine is settled and your portal is open again."),
    "enrollment.created": _enrol, "enrollment.re_enrolled": _enrol, "enrollment.withdrawn": _enrol, "enrollment.section_moved": _enrol, "enrollment.outcome_set": _enrol,
    "student_mark.entered": _mark, "student_mark.corrected": _mark,
    "attendance.leave_granted": _leave_from_classes,
    "hostel.checked_in": _hostel, "hostel.checked_out": _hostel,
    "fee.late_fee_posted": _fee_entry, "fee.payment_reversed": _fee_entry,
    "fee_assignment.status_changed": _fee_assignment, "fee.future_installments_cancelled": _fee_assignment, "fee.credit_moved": _fee_assignment,
    "scholarship.withdrawn": _fee_assignment,
    "fee.payment_marked_paid_by_staff": _challan_paid, "fee.due_date_changed_for_all": _due_dates_for_all,
    # classes
    "timetable_slot.created": _slot, "timetable_slot.changed": _slot, "timetable_slot.deleted": _slot,
    "course_offering.updated": _offering,
}


async def after_audit(session: AsyncSession, *, action: str, entity_id, actor_user_id, before, after, metadata) -> int:
    """Called by AuditService.log for every entry. Cheap when the action has no rule. Never raises."""
    rule = RULES.get(action)
    if rule is None or _quiet.get():
        return 0
    try:
        async with session.begin_nested():
            result = await rule(session, action=action, entity_id=entity_id, before=before, after=after, metadata=metadata)
            notes = [n for n in (result if isinstance(result, list) else [result]) if n is not None]
            from app.notifications.models import NotificationCategory
            from app.notifications.service import create_notification

            sent, told = 0, set()
            for note in notes:
                for uid in note.user_ids:
                    if uid is None or uid == actor_user_id or uid in told:   # once each; never about one's own change
                        continue
                    told.add(uid)
                    await create_notification(session, user_id=uid, category=NotificationCategory(note.category), title=note.title[:200],
                                              message=note.message[:1000], link=note.link)
                    sent += 1
            return sent
    except Exception as exc:  # noqa: BLE001 - a message problem must never undo the change itself
        logger.warning("change_notice_failed", action=action, error=str(exc)[:300])
        return 0
