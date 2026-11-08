"""Students to watch (teacher early warning) and the anonymous course & teacher feedback survey."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.academics.models import AssessmentType, ClassSession, CourseOffering, Enrollment, OfferingAssessment, StudentMark
from app.assignments.models import Assignment
from app.attendance.models import AttendanceRecord, AttendanceStatus
from app.core.clock import institute_today
from app.core.config import settings
from app.feedback import service as fb
from app.feedback.models import FeedbackAnswer, FeedbackComment, FeedbackSubmission
from tests.integration.test_attendance_and_grading import _create_n_sessions, _login, _login_admin, _setup_offering_with_enrolled_students
from tests.integration.test_teacher_content_and_bulk_timetable import _sign_in_teacher, _teacher_session

pytestmark = pytest.mark.asyncio
PW = "StudentPass123!"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _end_term_in(db, offering_id, days):
    await db.execute(update(CourseOffering).where(CourseOffering.id == uuid.UUID(offering_id)).values(term_end_date=institute_today() + timedelta(days=days)))
    await db.commit()


# ------------------------------------------------------------------ students to watch
async def test_teacher_sees_who_needs_attention_and_why(app_client, admin_user, db_session, monkeypatch):
    off, studs = await _setup_offering_with_enrolled_students(app_client, code="WL1", n_students=3, cnic_base=6100)
    await _create_n_sessions(app_client, off, 4)
    _tid, teacher = await _teacher_session(app_client, db_session, "WLT1", "35202-9920001-1", off)
    await _end_term_in(db_session, off, 30)
    oid = uuid.UUID(off)
    (a_id, _, a_enr), (b_id, _, b_enr), (c_id, _, _c_enr) = studs

    # A: attended 2 of 4 classes (50%)
    sessions = list((await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id == oid).order_by(ClassSession.session_date))).scalars())
    now = datetime.now(timezone.utc)
    for i, s in enumerate(sessions[:4]):
        for sid, present in ((a_id, i < 2), (b_id, True), (c_id, True)):
            db_session.add(AttendanceRecord(class_session_id=s.id, student_id=uuid.UUID(sid),
                                            status=AttendanceStatus.PRESENT if present else AttendanceStatus.ABSENT, marked_at=now))
    # B: 8 of 20 marks so far (40%); C: 18 of 20
    t = AssessmentType(name="WL Quiz", max_marks=20)
    db_session.add(t)
    await db_session.flush()
    oa = OfferingAssessment(course_offering_id=oid, assessment_type_id=t.id)
    db_session.add(oa)
    await db_session.flush()
    db_session.add(StudentMark(enrollment_id=uuid.UUID(b_enr), offering_assessment_id=oa.id, obtained_marks=8, entered_at=now))
    db_session.add(StudentMark(enrollment_id=uuid.UUID(_c_enr), offering_assessment_id=oa.id, obtained_marks=18, entered_at=now))
    # A also missed 2 assignments (B handed one in late? no: B did not get any)
    await db_session.execute(update(Enrollment).where(Enrollment.course_offering_id == oid).values(enrolled_at=now - timedelta(days=20)))
    for title in ("Essay", "Lab"):
        db_session.add(Assignment(course_offering_id=oid, title=title, instructions="x" * 20, deadline=now - timedelta(days=2)))
    await db_session.commit()
    from app.assignments.models import AssignmentSubmission
    for asg in (await db_session.execute(select(Assignment).where(Assignment.course_offering_id == oid))).scalars():
        for sid in (b_id, c_id):
            db_session.add(AssignmentSubmission(assignment_id=asg.id, student_id=uuid.UUID(sid), drive_url="https://drive.google.com/x", submitted_at=now - timedelta(days=3)))
    await db_session.commit()

    # C: fee overdue 45 days (the defaulters report is the source)
    from app.reports.finance_service import FinanceReportService

    async def fake_defaulters(self, **kw):
        return {"students": [{"student_id": uuid.UUID(c_id), "days_overdue": 45}, {"student_id": uuid.UUID(b_id), "days_overdue": 10}]}
    monkeypatch.setattr(FinanceReportService, "defaulters", fake_defaulters)

    await _sign_in_teacher(app_client, teacher)
    r = await app_client.get("/api/v1/academics/offerings/me/watchlist")
    assert r.status_code == 200, r.text
    body = r.json()
    by = {s["student_id"]: {x["kind"] for x in s["reasons"]} for s in body["students"]}
    assert by[a_id] == {"attendance", "assignments"}
    assert by[b_id] == {"marks"}                          # fee only 10 days late: not listed for that
    assert by[c_id] == {"fees"}
    assert body["students"][0]["student_id"] == a_id      # most reasons first
    fee_text = next(x["text"] for s in body["students"] if s["student_id"] == c_id for x in s["reasons"])
    assert "Rs" not in fee_text                           # no amounts for teachers
    assert body["courses"][0]["count"] == 3 and len(body["rules"]) == 4

    # a finished term drops off the list; other people cannot read it
    await _end_term_in(db_session, off, -1)
    assert (await app_client.get("/api/v1/academics/offerings/me/watchlist")).json()["students"] == []
    await _login(app_client, studs[0][1], PW)
    assert (await app_client.get("/api/v1/academics/offerings/me/watchlist")).status_code == 403


async def test_watch_page_is_in_the_teacher_menu(app_client):
    page = await app_client.get("/dashboard/faculty/watch", headers={"accept": "text/html"})
    assert page.status_code == 200 and "Students to watch" in page.text and "faculty-watch.js" in page.text


# ------------------------------------------------------------------ feedback survey
async def _survey_class(app_client, db, code, n=4, cnic_base=6200):
    off, studs = await _setup_offering_with_enrolled_students(app_client, code=code, n_students=n, cnic_base=cnic_base)
    _tid, teacher = await _teacher_session(app_client, db, code + "T", f"35202-99{cnic_base}1-1", off)
    return off, studs, teacher


async def _answer(app_client, username, off, score=4, comment=None):
    await _login(app_client, username, PW)
    qs = (await app_client.get("/api/v1/feedback/me")).json()["questions"]
    return await app_client.post(f"/api/v1/feedback/me/{off}", json={"scores": {q["id"]: score for q in qs}, "comment": comment})


async def test_students_answer_once_in_the_last_two_weeks_and_answers_are_anonymous(app_client, admin_user, db_session):
    off, studs, _teacher = await _survey_class(app_client, db_session, "FB1")
    await _end_term_in(db_session, off, 20)                         # not open yet
    await _login(app_client, studs[0][1], PW)
    me = (await app_client.get("/api/v1/feedback/me")).json()
    assert me["open"] == [] and me["upcoming"][0]["offering_id"] == off and me["questions"] == []
    closed = await app_client.post(f"/api/v1/feedback/me/{off}", json={"scores": {"x": 3}})
    assert closed.status_code in (409, 422, 400) and "not open" in closed.text

    await _end_term_in(db_session, off, 5)                          # inside the last 14 days
    me = (await app_client.get("/api/v1/feedback/me")).json()
    assert me["open"][0]["offering_id"] == off and len(me["questions"]) == len(fb.DEFAULT_QUESTIONS) and len(me["scale"]) == 5
    qs = me["questions"]
    partial = await app_client.post(f"/api/v1/feedback/me/{off}", json={"scores": {qs[0]["id"]: 5}})
    assert partial.status_code == 422 and "every question" in partial.text
    bad = await app_client.post(f"/api/v1/feedback/me/{off}", json={"scores": {q["id"]: 9 for q in qs}})
    assert bad.status_code == 422

    ok = await _answer(app_client, studs[0][1], off, 5, "  Very clear lectures.  ")
    assert ok.status_code == 204, ok.text
    again = await _answer(app_client, studs[0][1], off, 1)
    assert again.status_code in (409, 422, 400) and "already" in again.text
    me = (await app_client.get("/api/v1/feedback/me")).json()
    assert me["open"] == [] and me["done"][0]["offering_id"] == off

    # stored without the student or a time
    assert {c.name for c in FeedbackAnswer.__table__.columns} == {"id", "course_offering_id", "question_id", "score"}
    assert {c.name for c in FeedbackComment.__table__.columns} == {"id", "course_offering_id", "text"}
    assert (await db_session.execute(select(FeedbackComment.text))).scalar_one() == "Very clear lectures."
    assert (await db_session.execute(select(func.count()).select_from(FeedbackAnswer))).scalar_one() == len(qs)
    assert (await db_session.execute(select(func.count()).select_from(FeedbackSubmission))).scalar_one() == 1

    # someone not enrolled cannot answer
    other_off, other_studs = await _setup_offering_with_enrolled_students(app_client, code="FB1X", n_students=1, cnic_base=6290)
    await _login(app_client, other_studs[0][1], PW)
    assert (await app_client.post(f"/api/v1/feedback/me/{off}", json={"scores": {q["id"]: 3 for q in qs}})).status_code == 403


async def test_teacher_sees_summary_only_after_results_and_with_enough_answers(app_client, admin_user, db_session):
    off, studs, teacher = await _survey_class(app_client, db_session, "FB2", n=4, cnic_base=6300)
    await _end_term_in(db_session, off, 3)
    for (_, u, _), score in zip(studs[:2], (5, 3)):
        assert (await _answer(app_client, u, off, score, f"comment {score}")).status_code == 204

    await _sign_in_teacher(app_client, teacher)
    rows = (await app_client.get("/api/v1/feedback/teaching")).json()
    assert rows[0]["responses"] == 2 and rows[0]["summary"] is None and "published" in rows[0]["hidden_reason"]

    from app.academics.models import ResultSheet, ResultSheetStatus
    db_session.add(ResultSheet(course_offering_id=uuid.UUID(off), status=ResultSheetStatus.PUBLISHED))   # results are out
    await db_session.commit()
    await _login(app_client, teacher, "TeacherNew12345!")
    rows = (await app_client.get("/api/v1/feedback/teaching")).json()
    assert rows[0]["summary"]["enough"] is False and "Fewer than 3" in rows[0]["hidden_reason"]   # 2 answers could reveal who

    assert (await _answer(app_client, studs[2][1], off, 4)).status_code == 204
    await _login(app_client, teacher, "TeacherNew12345!")
    s = (await app_client.get("/api/v1/feedback/teaching")).json()[0]["summary"]
    assert s["enough"] is True and s["responses"] == 3 and s["overall"] == 4.0
    q0 = s["questions"][0]
    assert q0["counts"] == [0, 0, 1, 1, 1] and q0["average"] == 4.0 and q0["agree_pct"] == 67
    assert s["comments"] == ["comment 3", "comment 5"]                          # sorted, never in the order given

    await _login(app_client, studs[3][1], PW)
    assert (await app_client.get("/api/v1/feedback/teaching")).status_code == 403
    assert (await app_client.get("/api/v1/feedback/overview")).status_code == 403


async def test_oversight_sees_rates_any_time_and_scores_after_the_term(app_client, admin_user, db_session):
    off, studs, _t = await _survey_class(app_client, db_session, "FB3", n=3, cnic_base=6400)
    await _end_term_in(db_session, off, 2)
    for _, u, _e in studs:
        assert (await _answer(app_client, u, off, 2, "too fast")).status_code == 204
    await _login_admin(app_client)
    o = (await app_client.get("/api/v1/feedback/overview")).json()
    row = next(c for c in o["courses"] if c["offering_id"] == off)
    assert row["responses"] == 3 and row["enrolled"] == 3 and row["rate"] == 100 and row["is_open"] is True
    assert row["overall"] is None                                                # term not over yet
    d = (await app_client.get(f"/api/v1/feedback/overview/{off}")).json()
    assert d["summary"] is None and "term has ended" in d["hidden_reason"]

    await _end_term_in(db_session, off, -1)
    o = (await app_client.get("/api/v1/feedback/overview", params={"term_label": "Fall 2026"})).json()
    row = next(c for c in o["courses"] if c["offering_id"] == off)
    assert row["overall"] == 2.0 and row["enough"] is True
    d = (await app_client.get(f"/api/v1/feedback/overview/{off}")).json()
    assert d["summary"]["comments"] == ["too fast"] * 3


async def test_admin_edits_questions_and_old_answers_keep_their_wording(app_client, admin_user, db_session):
    await _login_admin(app_client)
    first = (await app_client.get("/api/v1/feedback/questions")).json()["questions"]
    assert first == fb.DEFAULT_QUESTIONS
    new = [first[0], "The classroom and equipment were good enough.", first[1]]
    r = await app_client.put("/api/v1/feedback/questions", json={"questions": new + ["  ", first[0]]})
    assert r.status_code == 200 and r.json()["questions"] == new
    assert (await app_client.put("/api/v1/feedback/questions", json={"questions": [" "]})).status_code == 422
    from app.feedback.models import FeedbackQuestion
    total = (await db_session.execute(select(func.count()).select_from(FeedbackQuestion))).scalar_one()
    assert total == len(fb.DEFAULT_QUESTIONS) + 1                                 # the dropped ones are kept, switched off


async def test_reminders_go_to_students_who_have_not_answered_once_a_day(app_client, admin_user, db_session, monkeypatch):
    off, studs, _t = await _survey_class(app_client, db_session, "FB4", n=2, cnic_base=6500)
    await _end_term_in(db_session, off, fb.WINDOW_DAYS - 1)                         # opens today
    assert (await _answer(app_client, studs[0][1], off)).status_code == 204
    from app.core import clock
    monkeypatch.setattr(clock, "institute_now", lambda: datetime.combine(institute_today(), datetime.min.time()).replace(hour=10, tzinfo=timezone.utc))
    from app.notifications.models import Notification
    before = (await db_session.execute(select(func.count()).select_from(Notification))).scalar_one()
    sent = await fb.remind(db_session)
    await db_session.commit()
    assert sent == 1
    note = (await db_session.execute(select(Notification).order_by(Notification.created_at.desc()))).scalars().first()
    assert note.title == "Course feedback is open" and note.link == "/dashboard/student/feedback" and "FB4-C1" in note.message
    assert await fb.remind(db_session) == 0                                         # once a day
    assert (await db_session.execute(select(func.count()).select_from(Notification))).scalar_one() == before + 1


async def test_survey_pages_render(app_client):
    for url, text in (("/dashboard/student/feedback", "anonymous"), ("/dashboard/faculty/feedback", "Student feedback"),
                      ("/dashboard/admin/feedback", "Questions"), ("/dashboard/principal/feedback", "Course feedback"),
                      ("/dashboard/hod/feedback", "Course feedback")):
        r = await app_client.get(url, headers={"accept": "text/html"})
        assert r.status_code == 200 and text in r.text, url
    assert "Questions" not in (await app_client.get("/dashboard/principal/feedback", headers={"accept": "text/html"})).text.split("content-main")[1]
    assert (await app_client.get("/dashboard/student-x/feedback", headers={"accept": "text/html"})).status_code == 404
