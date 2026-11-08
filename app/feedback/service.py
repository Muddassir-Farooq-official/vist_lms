"""Course & teacher feedback.

* Open for each course during the LAST 14 DAYS of its term (term_end_date). Courses without an end date are not surveyed.
* Each enrolled student answers once per course: every question on a 1-5 scale, plus an optional comment.
* Anonymous: answers and comments are stored without the student and without a time (see models.py).
* Teachers see their own summary only once that course's results are PUBLISHED, and only when at least
  MIN_RESPONSES students answered (fewer could reveal who wrote what).
* Principal / HOD / admin (ACADEMIC_OVERSIGHT) see response rates at any time, and scores and comments for every
  course once its term has ended (same minimum).
* Students are reminded in the app on the day the survey opens and 2 days before it closes, if they have not answered.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import institute_today
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.feedback.models import FeedbackAnswer, FeedbackComment, FeedbackQuestion, FeedbackSubmission

logger = get_logger(__name__)

WINDOW_DAYS = 14
MIN_RESPONSES = 3
MAX_COMMENT = 1000
SCALE = ["Strongly disagree", "Disagree", "Not sure", "Agree", "Strongly agree"]
DEFAULT_QUESTIONS = [
    "The teacher explained the subject clearly.",
    "The teacher came to class on time and used the full class time.",
    "The course covered what the course outline promised.",
    "The teacher was helpful when I had questions, in or after class.",
    "Assignments, quizzes and tests matched what was taught.",
    "Marks and comments on my work were fair and came back in good time.",
    "The teacher treated all students with respect.",
    "Overall, this was a good course.",
]


# ---------------------------------------------------------------- questions
async def questions(session: AsyncSession) -> list[FeedbackQuestion]:
    rows = list((await session.execute(select(FeedbackQuestion).where(FeedbackQuestion.is_active.is_(True)).order_by(FeedbackQuestion.position))).scalars())
    if rows:
        return rows
    if (await session.execute(select(func.count()).select_from(FeedbackQuestion))).scalar_one():
        return []                                      # an administrator switched every question off
    for i, text in enumerate(DEFAULT_QUESTIONS):
        session.add(FeedbackQuestion(position=i + 1, text=text, is_active=True))
    await session.flush()
    return await questions(session)


async def set_questions(session: AsyncSession, texts: list[str]) -> list[FeedbackQuestion]:
    clean = []
    for t in texts:
        t = " ".join((t or "").split())
        if t and t not in clean:
            clean.append(t)
    if not clean:
        raise ValidationAppError("Keep at least one question.")
    if any(len(t) > 300 for t in clean):
        raise ValidationAppError("Each question can be at most 300 characters.")
    current = {q.text: q for q in await questions(session)}
    for q in current.values():
        if q.text not in clean:
            q.is_active = False                        # kept for the answers already given
    for i, t in enumerate(clean):
        if t in current:
            current[t].position = i + 1
        else:
            session.add(FeedbackQuestion(position=i + 1, text=t, is_active=True))
    await session.flush()
    return await questions(session)


# ---------------------------------------------------------------- the window
def window(offering) -> tuple[date, date] | None:
    end = getattr(offering, "term_end_date", None)
    if end is None:
        return None
    return end - timedelta(days=WINDOW_DAYS - 1), end


def is_open(offering, today: date | None = None) -> bool:
    w = window(offering)
    today = today or institute_today()
    return w is not None and w[0] <= today <= w[1]


def term_over(offering, today: date | None = None) -> bool:
    end = getattr(offering, "term_end_date", None)
    return end is not None and (today or institute_today()) > end


# ---------------------------------------------------------------- students
async def _teacher_names(session: AsyncSession, teacher_ids: set) -> dict:
    from app.employees.models import Employee

    if not teacher_ids:
        return {}
    return {e.id: e.full_name for e in (await session.execute(select(Employee).where(Employee.id.in_(teacher_ids)))).scalars()}


async def for_student(session: AsyncSession, student_id: uuid.UUID) -> dict:
    from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus

    today = institute_today()
    rows = (await session.execute(
        select(CourseOffering, Course).join(Enrollment, Enrollment.course_offering_id == CourseOffering.id).join(Course, Course.id == CourseOffering.course_id)
        .where(Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED,
               CourseOffering.term_end_date.is_not(None), CourseOffering.term_end_date >= today))).all()
    done = set((await session.execute(select(FeedbackSubmission.course_offering_id).where(FeedbackSubmission.student_id == student_id))).scalars())
    names = await _teacher_names(session, {o.assigned_teacher_id for o, _ in rows if o.assigned_teacher_id})
    out = {"open": [], "upcoming": [], "done": []}
    for o, c in sorted(rows, key=lambda r: r[1].code):
        w = window(o)
        item = {"offering_id": o.id, "course_code": c.code, "course_title": c.title, "teacher": names.get(o.assigned_teacher_id),
                "opens_on": w[0], "closes_on": w[1]}
        if o.id in done:
            out["done"].append(item)
        elif is_open(o, today):
            out["open"].append(item)
        else:
            out["upcoming"].append(item)
    qs = await questions(session) if out["open"] else []
    return {**out, "questions": [{"id": q.id, "text": q.text} for q in qs], "scale": SCALE}


async def submit(session: AsyncSession, student_id: uuid.UUID, offering_id: uuid.UUID, scores: dict, comment: str | None) -> None:
    from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus

    offering = await session.get(CourseOffering, offering_id)
    if offering is None:
        raise ResourceNotFoundError("Course not found.")
    enrolled = (await session.execute(select(func.count()).select_from(Enrollment).where(
        Enrollment.student_id == student_id, Enrollment.course_offering_id == offering_id, Enrollment.status == EnrollmentStatus.ENROLLED))).scalar_one()
    if not enrolled:
        raise AuthorizationError("You can only give feedback on a course you are enrolled in.")
    if not is_open(offering):
        w = window(offering)
        raise BusinessRuleViolationError("Feedback for this course is not open." + (f" It is open from {w[0]:%d %b} to {w[1]:%d %b}." if w else ""))
    if await session.get(FeedbackSubmission, (student_id, offering_id)) is not None:
        raise BusinessRuleViolationError("You have already given feedback for this course. Thank you!")
    qs = await questions(session)
    wanted = {str(q.id): q for q in qs}
    given = {str(k): v for k, v in (scores or {}).items()}
    missing = [q for k, q in wanted.items() if k not in given]
    if missing:
        raise ValidationAppError(f"Please answer every question ({len(missing)} left).")
    for k, v in given.items():
        if k not in wanted:
            raise ValidationAppError("One of the questions has changed. Please reload the page and answer again.")
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 5:
            raise ValidationAppError("Each answer must be from 1 to 5.")
    text = (comment or "").strip()
    if len(text) > MAX_COMMENT:
        raise ValidationAppError(f"The comment can be at most {MAX_COMMENT} characters.")
    try:
        async with session.begin_nested():
            session.add(FeedbackSubmission(student_id=student_id, course_offering_id=offering_id, submitted_on=institute_today()))
            await session.flush()
    except IntegrityError:
        raise BusinessRuleViolationError("You have already given feedback for this course. Thank you!") from None
    for k, v in given.items():
        session.add(FeedbackAnswer(course_offering_id=offering_id, question_id=wanted[k].id, score=v))
    if text:
        session.add(FeedbackComment(course_offering_id=offering_id, text=text))
    await session.flush()


# ---------------------------------------------------------------- summaries
async def _enrolled_counts(session: AsyncSession, ids: list) -> dict:
    from app.academics.models import Enrollment, EnrollmentStatus

    if not ids:
        return {}
    return dict((await session.execute(select(Enrollment.course_offering_id, func.count()).where(
        Enrollment.course_offering_id.in_(ids), Enrollment.status.in_([EnrollmentStatus.ENROLLED, EnrollmentStatus.COMPLETED]))
        .group_by(Enrollment.course_offering_id))).all())


async def _response_counts(session: AsyncSession, ids: list) -> dict:
    if not ids:
        return {}
    return dict((await session.execute(select(FeedbackSubmission.course_offering_id, func.count()).where(
        FeedbackSubmission.course_offering_id.in_(ids)).group_by(FeedbackSubmission.course_offering_id))).all())


async def summary(session: AsyncSession, offering_id: uuid.UUID, *, with_comments: bool = True) -> dict:
    responses = (await _response_counts(session, [offering_id])).get(offering_id, 0)
    if responses < MIN_RESPONSES:
        return {"responses": responses, "enough": False, "questions": [], "overall": None, "comments": []}
    rows = (await session.execute(
        select(FeedbackQuestion.id, FeedbackQuestion.text, FeedbackQuestion.position, FeedbackAnswer.score, func.count())
        .join(FeedbackAnswer, FeedbackAnswer.question_id == FeedbackQuestion.id)
        .where(FeedbackAnswer.course_offering_id == offering_id)
        .group_by(FeedbackQuestion.id, FeedbackQuestion.text, FeedbackQuestion.position, FeedbackAnswer.score))).all()
    per: dict = {}
    for qid, text, pos, score, n in rows:
        q = per.setdefault(qid, {"id": qid, "text": text, "position": pos, "counts": [0, 0, 0, 0, 0]})
        q["counts"][int(score) - 1] += int(n)
    out_q, total, count = [], 0, 0
    for q in sorted(per.values(), key=lambda x: x["position"]):
        n = sum(q["counts"])
        s = sum((i + 1) * c for i, c in enumerate(q["counts"]))
        q["average"] = round(s / n, 2) if n else None
        q["agree_pct"] = round(100 * (q["counts"][3] + q["counts"][4]) / n) if n else None
        total, count = total + s, count + n
        out_q.append(q)
    comments = []
    if with_comments:
        comments = sorted(t for t in (await session.execute(select(FeedbackComment.text).where(FeedbackComment.course_offering_id == offering_id))).scalars())
    return {"responses": responses, "enough": True, "questions": out_q, "overall": round(total / count, 2) if count else None, "comments": comments}


async def _offering_rows(session: AsyncSession, offerings: list) -> list[dict]:
    from app.academics.models import Course

    ids = [o.id for o in offerings]
    courses = {c.id: c for c in (await session.execute(select(Course).where(Course.id.in_({o.course_id for o in offerings} or {uuid.uuid4()})))).scalars()}
    enrolled = await _enrolled_counts(session, ids)
    responses = await _response_counts(session, ids)
    names = await _teacher_names(session, {o.assigned_teacher_id for o in offerings if o.assigned_teacher_id})
    out = []
    for o in offerings:
        c = courses.get(o.course_id)
        w = window(o)
        e, r = enrolled.get(o.id, 0), responses.get(o.id, 0)
        out.append({"offering_id": o.id, "course_code": c.code if c else "", "course_title": c.title if c else "", "section_label": o.section_label,
                    "term_label": o.term_label, "teacher": names.get(o.assigned_teacher_id), "enrolled": e, "responses": r,
                    "rate": round(100 * r / e) if e else None, "opens_on": w[0] if w else None, "closes_on": w[1] if w else None,
                    "is_open": is_open(o), "term_over": term_over(o)})
    return out


async def for_teacher(session: AsyncSession, teacher_id: uuid.UUID) -> list[dict]:
    from app.academics.models import CourseOffering, ResultSheet, ResultSheetStatus

    offerings = list((await session.execute(select(CourseOffering).where(CourseOffering.assigned_teacher_id == teacher_id,
                                                                         CourseOffering.term_end_date.is_not(None)))).scalars())
    published = set((await session.execute(select(ResultSheet.course_offering_id).where(
        ResultSheet.course_offering_id.in_([o.id for o in offerings] or [uuid.uuid4()]), ResultSheet.status == ResultSheetStatus.PUBLISHED))).scalars())
    rows = await _offering_rows(session, offerings)
    for r in rows:
        if r["offering_id"] in published:
            r["summary"] = await summary(session, r["offering_id"])
            r["hidden_reason"] = None if r["summary"]["enough"] else f"Fewer than {MIN_RESPONSES} students answered, so it is not shown (it could reveal who wrote what)."
        else:
            r["summary"] = None
            r["hidden_reason"] = "Shown after this course's results are published."
    rows.sort(key=lambda r: (r["closes_on"] or date.min), reverse=True)
    return rows


async def oversight(session: AsyncSession, term_label: str | None = None) -> dict:
    from app.academics.models import CourseOffering

    terms = [t for t in (await session.execute(select(CourseOffering.term_label).where(CourseOffering.term_end_date.is_not(None))
                                                .group_by(CourseOffering.term_label).order_by(func.max(CourseOffering.term_end_date).desc()))).scalars()]
    term = term_label or (terms[0] if terms else None)
    offerings = list((await session.execute(select(CourseOffering).where(CourseOffering.term_label == term, CourseOffering.term_end_date.is_not(None))))
                     .scalars()) if term else []
    rows = await _offering_rows(session, offerings)
    for r in rows:
        if r["term_over"]:
            s = await summary(session, r["offering_id"], with_comments=False)
            r["overall"] = s["overall"]
            r["enough"] = s["enough"]
        else:
            r["overall"], r["enough"] = None, False
    rows.sort(key=lambda r: (r["teacher"] or "~", r["course_code"]))
    e, rsp = sum(r["enrolled"] for r in rows), sum(r["responses"] for r in rows)
    return {"terms": terms, "term": term, "courses": rows, "enrolled": e, "responses": rsp, "rate": round(100 * rsp / e) if e else None,
            "min_responses": MIN_RESPONSES}


async def oversight_detail(session: AsyncSession, offering_id: uuid.UUID) -> dict:
    from app.academics.models import CourseOffering

    o = await session.get(CourseOffering, offering_id)
    if o is None:
        raise ResourceNotFoundError("Course not found.")
    row = (await _offering_rows(session, [o]))[0]
    if not term_over(o):
        return {**row, "summary": None, "hidden_reason": "Scores are shown once the term has ended."}
    s = await summary(session, offering_id)
    return {**row, "summary": s, "hidden_reason": None if s["enough"] else f"Fewer than {MIN_RESPONSES} students answered."}


# ---------------------------------------------------------------- reminders
async def remind(session: AsyncSession) -> int:
    """From 9 AM, once a day: remind students who have not answered, on the day the survey opens and 2 days before it
    closes. Never raises."""
    from app.core.background import beat
    from app.core.clock import institute_now
    from app.notifications.email_models import ServiceHeartbeat

    try:
        now = institute_now()
        if now.hour < 9:
            return 0
        today = now.date()
        hb = await session.get(ServiceHeartbeat, "feedback-reminder")
        if hb is not None and (hb.detail or "").startswith(today.isoformat()):
            return 0
        sent = 0
        async with session.begin_nested():
            from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus
            from app.notifications.models import NotificationCategory
            from app.notifications.service import create_notification
            from app.students.models import Student

            opens = today + timedelta(days=WINDOW_DAYS - 1)          # term ends then: the survey opens today
            closing = today + timedelta(days=2)
            rows = (await session.execute(
                select(Student.user_id, Course.code, CourseOffering.term_end_date)
                .join(Enrollment, Enrollment.student_id == Student.id)
                .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                .join(Course, Course.id == CourseOffering.course_id)
                .outerjoin(FeedbackSubmission, (FeedbackSubmission.student_id == Student.id) & (FeedbackSubmission.course_offering_id == CourseOffering.id))
                .where(Enrollment.status == EnrollmentStatus.ENROLLED, Student.user_id.is_not(None), FeedbackSubmission.student_id.is_(None),
                       CourseOffering.term_end_date.in_([opens, closing])))).all()
            per: dict = {}
            for uid, code, end in rows:
                per.setdefault((uid, end == closing), []).append(code)
            for (uid, last_call), codes in per.items():
                codes = sorted(set(codes))
                await create_notification(
                    session, user_id=uid, category=NotificationCategory.GENERAL,
                    title=("Last days to give course feedback" if last_call else "Course feedback is open"),
                    message=(f"Tell us how your courses went: {', '.join(codes)}. It takes 2 minutes and is anonymous: teachers never see who answered."),
                    link="/dashboard/student/feedback")
                sent += 1
        await beat(session, "feedback-reminder", f"{today.isoformat()} sent {sent}")
        return sent
    except Exception as exc:  # noqa: BLE001 - a reminder problem must not stop the sender
        logger.warning("feedback_reminder_failed", error=str(exc)[:300])
        return 0
