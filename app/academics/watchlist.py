"""Students to watch: the early-warning list a teacher sees for the courses they teach this term.

A student is listed for a course when any of these is true (each is a separate reason, so the teacher sees why):
  * attendance in that course is below the minimum to pass (75%: the first fine band starts there)
  * marks entered so far in that course add up to less than half of the marks given so far
  * 2 or more assignments of that course were missed (deadline passed, nothing handed in, or sent back and not redone)
  * a fee has been overdue for 30 days or more (shown without the amount: the teacher only needs to know)

Only students still ENROLLED, only courses whose term has not ended. Nothing here changes data: it is read when asked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import institute_today

ATTENDANCE_MIN = 75.0
MARKS_MIN = 50.0
MISSED_MIN = 2
FEE_DAYS = 30
MIN_CLASSES_FOR_ATTENDANCE = 3       # too few classes held to judge yet


async def current_offerings(session: AsyncSession, teacher_id: uuid.UUID) -> list:
    from app.academics.models import CourseOffering

    today = institute_today()
    rows = (await session.execute(select(CourseOffering).where(CourseOffering.assigned_teacher_id == teacher_id))).scalars().all()
    return [o for o in rows if o.term_end_date is None or o.term_end_date >= today]


async def _marks_so_far(session: AsyncSession, offering_ids: list) -> dict:
    """(student_id, offering_id) -> (obtained, max) over the assessments that already have a mark for that student."""
    from app.academics.models import AssessmentType, Enrollment, OfferingAssessment, StudentMark

    rows = (await session.execute(
        select(Enrollment.student_id, Enrollment.course_offering_id, func.sum(StudentMark.obtained_marks), func.sum(AssessmentType.max_marks))
        .join(StudentMark, StudentMark.enrollment_id == Enrollment.id)
        .join(OfferingAssessment, OfferingAssessment.id == StudentMark.offering_assessment_id)
        .join(AssessmentType, AssessmentType.id == OfferingAssessment.assessment_type_id)
        .where(Enrollment.course_offering_id.in_(offering_ids))
        .group_by(Enrollment.student_id, Enrollment.course_offering_id))).all()
    return {(s, o): (float(got or 0), float(mx or 0)) for s, o, got, mx in rows}


async def _missed(session: AsyncSession, offering_ids: list) -> dict:
    """(student_id, offering_id) -> titles of assignments missed."""
    from app.academics.models import Enrollment, EnrollmentStatus
    from app.assignments.models import Assignment, AssignmentSubmission, SubmissionStatus

    now = datetime.now(timezone.utc)
    rows = (await session.execute(
        select(Enrollment.student_id, Assignment.course_offering_id, Assignment.title, AssignmentSubmission.status)
        .join(Assignment, and_(Assignment.course_offering_id == Enrollment.course_offering_id, Assignment.deadline < now,
                               Assignment.deadline > Enrollment.enrolled_at))
        .outerjoin(AssignmentSubmission, and_(AssignmentSubmission.assignment_id == Assignment.id,
                                              AssignmentSubmission.student_id == Enrollment.student_id))
        .where(Enrollment.course_offering_id.in_(offering_ids), Enrollment.status == EnrollmentStatus.ENROLLED)
        .order_by(Assignment.deadline))).all()
    out: dict = {}
    for sid, oid, title, status in rows:
        if status is None or status == SubmissionStatus.REJECTED:
            out.setdefault((sid, oid), []).append(title)
    return out


async def _fee_overdue(session: AsyncSession, student_ids: set) -> set:
    if not student_ids:
        return set()
    from app.reports.finance_service import FinanceReportService

    data = await FinanceReportService(session).defaulters(student_ids=student_ids)
    return {p["student_id"] for p in data["students"] if p["student_id"] in student_ids and p["days_overdue"] >= FEE_DAYS}


def _pct(a: float, b: float) -> float:
    return round(100 * a / b, 1) if b else 0.0


async def for_teacher(session: AsyncSession, teacher_id: uuid.UUID, offering_id: uuid.UUID | None = None) -> dict:
    from app.academics.models import Course, Enrollment, EnrollmentStatus
    from app.attendance.shortfall import percentages
    from app.students.models import Student

    offerings = await current_offerings(session, teacher_id)
    if offering_id is not None:
        offerings = [o for o in offerings if o.id == offering_id]
    if not offerings:
        return {"courses": [], "students": [], "total_students": 0, "rules": rules()}
    ids = [o.id for o in offerings]
    courses = {c.id: c for c in (await session.execute(select(Course).where(Course.id.in_({o.course_id for o in offerings})))).scalars()}
    course_of = {o.id: courses.get(o.course_id) for o in offerings}

    enrolled = (await session.execute(
        select(Enrollment.student_id, Enrollment.course_offering_id, Student.first_name, Student.last_name, Student.roll_number)
        .join(Student, Student.id == Enrollment.student_id)
        .where(Enrollment.course_offering_id.in_(ids), Enrollment.status == EnrollmentStatus.ENROLLED))).all()
    attendance = {}
    for oid in ids:
        for r in await percentages(session, offering_id=oid):
            attendance[(r["student_id"], oid)] = r
    marks = await _marks_so_far(session, ids)
    missed = await _missed(session, ids)
    fees = await _fee_overdue(session, {r.student_id for r in enrolled})

    students = []
    counts: dict = {oid: 0 for oid in ids}
    for r in enrolled:
        key = (r.student_id, r.course_offering_id)
        reasons = []
        a = attendance.get(key)
        if a and a["taken"] >= MIN_CLASSES_FOR_ATTENDANCE and a["percentage"] < ATTENDANCE_MIN:
            reasons.append({"kind": "attendance", "text": f"Attendance {a['percentage']:.0f}% ({a['present']} of {a['taken']} classes)",
                            "value": a["percentage"]})
        got, mx = marks.get(key, (0.0, 0.0))
        if mx > 0 and _pct(got, mx) < MARKS_MIN:
            reasons.append({"kind": "marks", "text": f"Marks so far {_pct(got, mx):.0f}% ({got:g} of {mx:g})", "value": _pct(got, mx)})
        miss = missed.get(key, [])
        if len(miss) >= MISSED_MIN:
            reasons.append({"kind": "assignments", "text": f"{len(miss)} assignments missed: " + ", ".join(miss[:3]) + ("…" if len(miss) > 3 else ""),
                            "value": len(miss)})
        if r.student_id in fees:
            reasons.append({"kind": "fees", "text": f"Fee overdue for {FEE_DAYS}+ days (the accounts office follows up)", "value": None})
        if not reasons:
            continue
        counts[r.course_offering_id] += 1
        c = course_of.get(r.course_offering_id)
        students.append({"student_id": r.student_id, "name": f"{r.first_name} {r.last_name}".strip(), "roll_number": r.roll_number,
                         "offering_id": r.course_offering_id, "course_code": c.code if c else "", "course_title": c.title if c else "",
                         "reasons": reasons})
    students.sort(key=lambda s: (-len(s["reasons"]), s["course_code"], s["name"]))
    course_rows = []
    for o in offerings:
        c = course_of.get(o.id)
        course_rows.append({"offering_id": o.id, "course_code": c.code if c else "", "course_title": c.title if c else "",
                            "section_label": o.section_label, "term_label": o.term_label, "count": counts[o.id],
                            "enrolled": sum(1 for r in enrolled if r.course_offering_id == o.id)})
    return {"courses": course_rows, "students": students, "total_students": len({r.student_id for r in enrolled}), "rules": rules()}


def rules() -> list[str]:
    return [f"Attendance below {ATTENDANCE_MIN:g}% (after at least {MIN_CLASSES_FOR_ATTENDANCE} classes)",
            f"Less than {MARKS_MIN:g}% of the marks given so far",
            f"{MISSED_MIN} or more assignments missed",
            f"A fee overdue for {FEE_DAYS} days or more"]


async def for_students(session: AsyncSession, student_ids: set | None = None) -> dict:
    """The same signs, per student across all their current courses (no fee sign: parents already see their fees).
    {student_id: [{"course_code", "course_title", "kind", "text"}]} — only students with at least one sign."""
    from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus
    from app.attendance.shortfall import percentages

    today = institute_today()
    q = (select(Enrollment.student_id, CourseOffering, Course).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
         .join(Course, Course.id == CourseOffering.course_id)
         .where(Enrollment.status == EnrollmentStatus.ENROLLED,
                (CourseOffering.term_end_date.is_(None)) | (CourseOffering.term_end_date >= today)))
    if student_ids is not None:
        q = q.where(Enrollment.student_id.in_(student_ids or {uuid.uuid4()}))
    rows = (await session.execute(q)).all()
    offerings = {o.id: (o, c) for _, o, c in rows}
    if not offerings:
        return {}
    ids = list(offerings)
    attendance = {}
    for oid in ids:
        for r in await percentages(session, offering_id=oid):
            attendance[(r["student_id"], oid)] = r
    marks = await _marks_so_far(session, ids)
    missed = await _missed(session, ids)
    out: dict = {}
    for sid, o, c in rows:
        key = (sid, o.id)
        a = attendance.get(key)
        if a and a["taken"] >= MIN_CLASSES_FOR_ATTENDANCE and a["percentage"] < ATTENDANCE_MIN:
            out.setdefault(sid, []).append({"course_code": c.code, "course_title": c.title, "kind": "attendance", "value": round(a["percentage"])})
        got, mx = marks.get(key, (0.0, 0.0))
        if mx > 0 and _pct(got, mx) < MARKS_MIN:
            out.setdefault(sid, []).append({"course_code": c.code, "course_title": c.title, "kind": "marks", "value": round(_pct(got, mx))})
        miss = missed.get(key, [])
        if len(miss) >= MISSED_MIN:
            out.setdefault(sid, []).append({"course_code": c.code, "course_title": c.title, "kind": "assignments", "value": len(miss)})
    for v in out.values():
        v.sort(key=lambda r: (r["course_code"], r["kind"]))
    return out
