"""One card per student for the home pages: today's classes and attendance, fees to pay (with the challan to pay now),
the next exam and the latest result. And the phone notices for families: a child marked absent in a class, and a
course result published (as notifications, which the push service sends to phones that allowed it).

Reads only; the notices never change a record."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


async def card(session: AsyncSession, student) -> dict:
    from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering, Enrollment, EnrollmentResult, EnrollmentStatus
    from app.attendance.models import AttendanceRecord
    from app.core.clock import institute_today
    from app.exams.models import ExamPaper, ExamSchedule, ScheduleStatus
    from app.exams.service import ExamScheduleService
    from app.fees.account_service import AccountService
    from app.fees.models import PayerType
    from app.programs.models import Program

    today = institute_today()
    prog = await session.get(Program, student.program_id) if student.program_id else None
    # today's classes, with this student's mark when taken
    mine = select(Enrollment.course_offering_id).where(Enrollment.student_id == student.id, Enrollment.status == EnrollmentStatus.ENROLLED)
    rows = (await session.execute(select(ClassSession, Course.code, Course.title).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
                                  .join(Course, Course.id == CourseOffering.course_id).where(
        ClassSession.course_offering_id.in_(mine), ClassSession.session_date == today, ClassSession.status != ClassSessionStatus.CANCELLED)
        .order_by(ClassSession.start_time))).all()
    marks = {r.class_session_id: r.status.value for r in (await session.execute(select(AttendanceRecord).where(
        AttendanceRecord.student_id == student.id, AttendanceRecord.class_session_id.in_([s.id for s, _c, _t in rows] or [uuid.uuid4()])))).scalars()}
    classes = [{"course_code": code, "course_title": title, "start": s.start_time.strftime("%H:%M"), "end": s.end_time.strftime("%H:%M"), "room": s.room,
                "attendance": marks.get(s.id)} for s, code, title in rows]
    # fees: what is due, and the challan to pay now (overdue first, then the next due)
    acc = await AccountService(session).build(PayerType.STUDENT, student.id, staff_view=False)
    pay = None
    open_rows = [(r, a) for a in acc["assignments"] if a["status"] == "ACTIVE" for r in a["installments"] if r["remaining"] > 0.005]
    open_rows.sort(key=lambda x: (x[0]["status"] != "OVERDUE", str(x[0]["due_date"] or "9999")))
    for r, a in open_rows:
        c = r.get("challan")
        if c and c["status"] == "PENDING":
            pay = {"challan_id": c["id"], "consumer_number": c["consumer_number"], "amount": c["amount"], "due_date": c["due_date"], "fee": a["fee_name"],
                   "overdue": r["status"] == "OVERDUE"}
            break
    s = acc["summary"]
    # the next exam paper
    own = await ExamScheduleService(session)._my_offerings(student.id)
    nxt = None
    if own:
        row = (await session.execute(select(ExamPaper, ExamSchedule, Course).join(ExamSchedule, ExamSchedule.id == ExamPaper.schedule_id)
                                     .join(CourseOffering, CourseOffering.id == ExamPaper.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
                                     .where(ExamSchedule.status == ScheduleStatus.PUBLISHED, ExamPaper.course_offering_id.in_(list(own)), ExamPaper.exam_date >= today)
                                     .order_by(ExamPaper.exam_date, ExamPaper.start_time).limit(1))).first()
        if row:
            p, sch, c = row
            nxt = {"course_code": c.code, "course_title": c.title, "exam": sch.name, "date": p.exam_date, "start": p.start_time.strftime("%H:%M"), "room": p.room,
                   "days": (p.exam_date - today).days}
    # the latest result
    last = (await session.execute(select(EnrollmentResult, Course).join(Enrollment, Enrollment.id == EnrollmentResult.enrollment_id)
                                  .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
                                  .where(Enrollment.student_id == student.id).order_by(EnrollmentResult.updated_at.desc()).limit(1))).first()
    result = None
    if last:
        r, c = last
        result = {"course_code": c.code, "course_title": c.title, "grade": r.letter_grade, "percentage": float(r.percentage), "outcome": r.outcome,
                  "supplementary": r.supplementary, "date": r.updated_at}
    return {"id": student.id, "name": f"{student.first_name} {student.last_name}", "roll_number": student.roll_number,
            "program": prog.code if prog else None, "semester": student.current_semester,
            "today": {"date": today, "classes": classes, "absent": sum(1 for c in classes if c["attendance"] == "ABSENT"),
                      "present": sum(1 for c in classes if c["attendance"] == "PRESENT")},
            "fees": {"to_pay": s["total_to_pay"], "overdue": s["overdue"], "next_due": s["next_due"], "pay": pay},
            "next_exam": nxt, "latest_result": result, "transport": await _transport(session, student.id)}


async def _transport(session: AsyncSession, student_id) -> dict | None:
    try:
        async with session.begin_nested():
            from app.transport.service import TransportService

            return await TransportService(session).student_seat(student_id)
    except Exception:                       # noqa: BLE001 - the card shows without it
        return None


async def parent_ids(session: AsyncSession, student_id: uuid.UUID) -> list[uuid.UUID]:
    from app.parents.models import ParentStudentLink

    return list((await session.execute(select(ParentStudentLink.user_id).where(ParentStudentLink.student_id == student_id))).scalars())


# ---------------------------------------------------------------------- notices to phones
async def result_notices(session: AsyncSession, offering_id: uuid.UUID, *, course_code: str, course_title: str, internal_only: bool) -> int:
    """A course's result was published: tell each student and their parents (a notification, pushed to phones). Never raises."""
    from app.core.config import settings

    if not int(getattr(settings, "notify_family_results", 1)):
        return 0
    try:
        from app.academics.models import Enrollment, EnrollmentStatus
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.students.models import Student

        n = 0
        async with session.begin_nested():
            rows = (await session.execute(select(Student).join(Enrollment, Enrollment.student_id == Student.id).where(
                Enrollment.course_offering_id == offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN))).scalars().all()
            what = "Internal marks" if internal_only else "Result"
            for s in rows:
                if s.user_id:
                    await create_notification(session, user_id=s.user_id, category=NotificationCategory.RESULT, title=f"{what} published: {course_code}",
                                              message=f"{course_code} {course_title[:60]}: your {what.lower()} can be seen now.", link="/dashboard/student/results")
                    n += 1
                for pid in await parent_ids(session, s.id):
                    await create_notification(session, user_id=pid, category=NotificationCategory.RESULT, title=f"{what} published: {s.first_name} — {course_code}",
                                              message=f"{s.first_name} {s.last_name}'s {what.lower()} for {course_code} {course_title[:50]} can be seen now.",
                                              link="/dashboard/parent")
                    n += 1
        return n
    except Exception as exc:  # noqa: BLE001 - a notice must never stop publishing
        logger.warning("result_notices_failed", error=str(exc)[:300])
        return 0


async def absence_round(session: AsyncSession, now: datetime | None = None) -> int:
    """Run by the sender: students marked ABSENT in a class today -> one notice per class to the student and their parents
    (pushed to phones). Each class is told once. Never raises."""
    from app.core.config import settings

    if not int(getattr(settings, "notify_family_absence", 1)):
        return 0
    try:
        async with session.begin_nested():
            return await _absences(session, now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("absence_notices_failed", error=str(exc)[:300])
        return 0


async def _absences(session: AsyncSession, now: datetime | None) -> int:
    from app.academics.models import ClassSession, Course, CourseOffering
    from app.attendance.models import AttendanceRecord, AttendanceStatus
    from app.core.clock import institute_today
    from app.notifications.models import Notification, NotificationCategory
    from app.notifications.service import create_notification
    from app.students.models import Student

    today = institute_today()
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=12)
    rows = (await session.execute(select(AttendanceRecord, ClassSession, Course, Student).join(ClassSession, ClassSession.id == AttendanceRecord.class_session_id)
                                  .join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
                                  .join(Student, Student.id == AttendanceRecord.student_id).where(
        AttendanceRecord.status == AttendanceStatus.ABSENT, ClassSession.session_date == today, AttendanceRecord.marked_at >= since).limit(2000))).all()
    if not rows:
        return 0
    day_start = (now or datetime.now(timezone.utc)) - timedelta(hours=20)      # told already today
    n = 0
    for rec, cs, course, s in rows:
        link = f"/dashboard/student/attendance#s{cs.id}"
        targets = ([s.user_id] if s.user_id else []) + await parent_ids(session, s.id)
        for uid in targets:
            done = (await session.execute(select(func.count()).select_from(Notification).where(
                Notification.user_id == uid, Notification.link.in_([link, f"/dashboard/parent#s{cs.id}-{s.id}"]), Notification.created_at >= day_start))).scalar_one()
            if done:
                continue
            is_parent = uid != s.user_id
            await create_notification(
                session, user_id=uid, category=NotificationCategory.GENERAL,
                title=(f"{s.first_name} was absent: {course.code}" if is_parent else f"Marked absent: {course.code}"),
                message=(f"{s.first_name} {s.last_name} was marked absent in {course.code} {course.title[:50]} today at {cs.start_time:%H:%M}."
                         if is_parent else f"You were marked absent in {course.code} {course.title[:50]} today at {cs.start_time:%H:%M}. "
                                           "If this is wrong, ask your teacher."),
                link=(f"/dashboard/parent#s{cs.id}-{s.id}" if is_parent else link))
            n += 1
    return n
