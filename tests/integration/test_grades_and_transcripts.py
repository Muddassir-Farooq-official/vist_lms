"""Letter grades, GPA and the result card: the arithmetic, the grading scale, frozen grades, retakes, privacy and the PDF."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.academics.grade_scale import STANDARD_SCALE, GradeScaleService, gpa, grade_for
from app.academics.models import Enrollment, EnrollmentOutcome, EnrollmentResult, EnrollmentStatus, ResultSheet, ResultSheetStatus
from app.core.config import settings
from app.notifications.models import Notification
from tests.integration.test_attendance_and_grading import _login, _login_admin, _setup_offering_with_enrolled_students
from tests.integration.test_result_sheets import _class, _fill, _mark, _as_teacher

pytestmark = pytest.mark.asyncio
R = "/api/v1/results"
A = "/api/v1/academics"
SP = "StudentPass123!"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def standard():
    return [SimpleNamespace(letter=l, min_percent=m, grade_points=p, is_passing=ok) for l, m, p, ok in STANDARD_SCALE]


# ====================================================================== the arithmetic
def test_each_percentage_earns_the_right_letter_at_the_edges():
    s = standard()
    expect = [(100, "A", 4.0), (85, "A", 4.0), (84.99, "A-", 3.66), (80, "A-", 3.66), (79.99, "B+", 3.33), (75, "B+", 3.33), (74.99, "B", 3.0), (71, "B", 3.0), (70.99, "B-", 2.66),
              (68, "B-", 2.66), (64, "C+", 2.33), (61, "C", 2.0), (58, "C-", 1.66), (54, "D+", 1.33), (50, "D", 1.0)]
    for pct, letter, points in expect:
        assert grade_for(s, pct, True) == (letter, points), pct
    assert grade_for(s, 49.99, True) == ("D", 1.0)            # passed on a lower pass mark: never below the lowest passing grade
    assert grade_for(s, 30, False) == ("F", 0.0)
    assert grade_for(s, 95, False) == ("F", 0.0)              # failed for attendance: F whatever the marks


def test_gpa_is_weighted_by_credit_hours_and_rounds_half_up():
    assert gpa([(4.0, 3), (3.0, 3)]) == 3.5
    assert gpa([(4.0, 3), (2.0, 1)]) == 3.5                    # (12 + 2) / 4
    assert gpa([(4.0, 1), (2.0, 3)]) == 2.5                    # a 3-credit course counts three times a 1-credit one
    assert gpa([(3.66, 3), (2.33, 3)]) == 3.0                  # 2.995 rounds UP to 3.00
    assert gpa([(0.0, 3)]) == 0.0
    assert gpa([]) is None and gpa([(4.0, 0)]) is None


def test_a_proposed_scale_is_checked_in_plain_language():
    ok = [{"letter": l, "min_percent": m, "grade_points": p, "is_passing": ok} for l, m, p, ok in STANDARD_SCALE]
    assert GradeScaleService.validate(ok) == []
    simple = [{"letter": "A", "min_percent": 80, "grade_points": 4, "is_passing": True}, {"letter": "B", "min_percent": 60, "grade_points": 3, "is_passing": True},
              {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}]
    assert GradeScaleService.validate(simple) == []
    bad = lambda items: " ".join(GradeScaleService.validate(items))                                              # noqa: E731
    assert "start at 0%" in bad([{"letter": "A", "min_percent": 80, "grade_points": 4, "is_passing": True}, {"letter": "F", "min_percent": 10, "grade_points": 0, "is_passing": False}])
    assert "same name" in bad([{"letter": "A", "min_percent": 80, "grade_points": 4, "is_passing": True}, {"letter": "a", "min_percent": 40, "grade_points": 2, "is_passing": True}, {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}])
    assert "same percentage" in bad([{"letter": "A", "min_percent": 50, "grade_points": 4, "is_passing": True}, {"letter": "B", "min_percent": 50, "grade_points": 3, "is_passing": True}, {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}])
    assert "never earn fewer" in bad([{"letter": "A", "min_percent": 80, "grade_points": 2, "is_passing": True}, {"letter": "B", "min_percent": 60, "grade_points": 3, "is_passing": True}, {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}])
    assert "at least one grade as passing" in bad([{"letter": "A", "min_percent": 80, "grade_points": 4, "is_passing": True}, {"letter": "B", "min_percent": 0, "grade_points": 3, "is_passing": True}])
    assert "cannot sit above" in bad([{"letter": "A", "min_percent": 80, "grade_points": 4, "is_passing": False}, {"letter": "B", "min_percent": 40, "grade_points": 3, "is_passing": True}, {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}])
    assert "between 2 and 20" in bad([{"letter": "A", "min_percent": 0, "grade_points": 4, "is_passing": True}])


# ====================================================================== the scale, through the API
async def test_the_scale_starts_standard_and_only_the_administration_changes_it(app_client, admin_user, db_session):
    await _login_admin(app_client)
    entries = (await app_client.get(f"{R}/scale")).json()
    assert [(e["letter"], e["min_percent"], e["grade_points"]) for e in entries[:3]] == [("A", 85.0, 4.0), ("A-", 80.0, 3.66), ("B+", 75.0, 3.33)] and len(entries) == 11 and entries[-1]["is_passing"] is False
    bad = await app_client.put(f"{R}/scale", json={"entries": [{"letter": "A", "min_percent": 80, "grade_points": 4, "is_passing": True}, {"letter": "F", "min_percent": 20, "grade_points": 0, "is_passing": False}]})
    assert bad.status_code == 422 and "start at 0%" in bad.text
    new = {"entries": [{"letter": "A", "min_percent": 80, "grade_points": 4, "is_passing": True}, {"letter": "B", "min_percent": 65, "grade_points": 3, "is_passing": True},
                       {"letter": "C", "min_percent": 50, "grade_points": 2, "is_passing": True}, {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}]}
    ok = (await app_client.put(f"{R}/scale", json=new)).json()
    assert [e["letter"] for e in ok["entries"]] == ["A", "B", "C", "F"] and ok["warning"] is None
    odd = {"entries": [{"letter": "P", "min_percent": 40, "grade_points": 2, "is_passing": True}, {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}]}
    assert "pass mark" in (await app_client.put(f"{R}/scale", json=odd)).json()["warning"]              # warned, not blocked
    log = (await db_session.execute(select(__import__("app.audit.models", fromlist=["AuditLog"]).AuditLog).where(__import__("app.audit.models", fromlist=["AuditLog"]).AuditLog.action == "grade_scale.replaced"))).scalars().all()
    assert len(log) >= 2                                                                              # every change is on record
    from tests.integration.test_announcements import _first_login, _staff
    from tests.integration.test_teacher_content_and_bulk_timetable import _teacher_session
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="GS1", n_students=1)
    _tid, teacher = await _teacher_session(app_client, db_session, "TGS1", "35202-9200001-1", offering_id)
    await _first_login(app_client, teacher, "FirstPass#12345") if False else None
    await _login(app_client, students[0][1], SP)
    assert (await app_client.get(f"{R}/scale")).status_code == 200                                    # students may read the scale
    assert (await app_client.put(f"{R}/scale", json=new)).status_code == 403                          # but not change it


# ====================================================================== grades are frozen when results are published
async def test_publishing_gives_each_student_a_letter_and_later_scale_changes_do_not_rewrite_it(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session, code="GF1", cnic_base=6000)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70), (3, 10), (15, 60)))            # 73.33%, 10.83%, 62.5%
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    review = (await app_client.get(f"{R}/sheets/{offering}")).json()
    assert [(r["grade"], r["grade_points"]) for r in sorted(review["rows"], key=lambda r: r["roll_number"])] == [("B", 3.0), ("F", 0.0), ("C", 2.0)]       # projected before publishing
    assert {d["grade"]: d["count"] for d in review["stats"]["distribution"]} == {"B": 1, "C": 1, "F": 1}
    await app_client.post(f"{R}/sheets/{offering}/approve"); await app_client.post(f"{R}/sheets/{offering}/publish")
    for (_sid, username, _eid), letter in zip(students, ["B", "F", "C"]):
        await _login(app_client, username, SP)
        t = (await app_client.get(f"{R}/me/transcript")).json()
        c = t["terms"][0]["courses"][0]
        assert c["letter_grade"] == letter and c["credit_hours"] == 3 and c["quality_points"] == round(c["grade_points"] * 3, 2)
    note = (await db_session.execute(select(Notification).where(Notification.title.like("Result published for%")))).scalars().first()
    assert "(grade " in note.message
    # the administration changes the scale afterwards: the published grades stay as they were
    await _login_admin(app_client)
    await app_client.put(f"{R}/scale", json={"entries": [{"letter": "A", "min_percent": 50, "grade_points": 4, "is_passing": True}, {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}]})
    await _login(app_client, students[0][1], SP)
    assert (await app_client.get(f"{R}/me/transcript")).json()["terms"][0]["courses"][0]["letter_grade"] == "B"
    after = (await app_client.get(f"{R}/sheets/{offering}")) if False else None


async def test_a_correction_after_publication_updates_the_frozen_grade(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session, code="GC1", n=1, cnic_base=6100)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((3, 10),))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/approve"); await app_client.post(f"{R}/sheets/{offering}/publish")
    await _login(app_client, students[0][1], SP)
    assert (await app_client.get(f"{R}/me/transcript")).json()["summary"]["cgpa"] == 0.0
    await _login_admin(app_client)
    assert (await _mark(app_client, students[0][2], c2, 90, correction_reason="Script was marked against the wrong key")).status_code == 201     # (3+90)/120 = 77.5% -> B+
    await _login(app_client, students[0][1], SP)
    t = (await app_client.get(f"{R}/me/transcript")).json()
    assert t["terms"][0]["courses"][0]["letter_grade"] == "B+" and t["summary"]["cgpa"] == 3.33
    assert any("B+" in n.message for n in (await db_session.execute(select(Notification).where(Notification.title.like("Your result for%was corrected%")))).scalars())


# ====================================================================== the transcript: terms, GPA, CGPA, retakes
async def test_the_transcript_works_out_term_gpa_cgpa_and_a_retake_replaces_the_failed_attempt(app_client, admin_user, db_session):
    offering1, students = await _setup_offering_with_enrolled_students(app_client, code="TX", n_students=1, cnic_base=6200)
    student_id, username, enrollment1 = students[0]
    course1 = (await db_session.execute(select(__import__("app.academics.models", fromlist=["CourseOffering"]).CourseOffering.course_id).where(
        __import__("app.academics.models", fromlist=["CourseOffering"]).CourseOffering.id == offering1))).scalar_one()
    course2 = (await app_client.post(f"{A}/courses", json={"code": "TX-C2", "title": "Second Course", "credit_hours": 4})).json()["id"]
    o2 = (await app_client.post(f"{A}/offerings", json={"course_id": course2, "term_label": "Spring 2026"})).json()["id"]
    o3 = (await app_client.post(f"{A}/offerings", json={"course_id": str(course1), "term_label": "Temporary label"})).json()["id"]
    from app.academics.models import CourseOffering
    for oid, label, start in ((offering1, "Fall 2025", date(2025, 9, 1)), (o2, "Spring 2026", date(2026, 2, 1)), (o3, "Fall 2026", date(2026, 9, 1))):
        off = await db_session.get(CourseOffering, oid)
        off.term_label, off.term_start_date = label, start
    now = datetime.now(timezone.utc)
    e1 = await db_session.get(Enrollment, enrollment1)
    e1.status, e1.outcome = EnrollmentStatus.COMPLETED, EnrollmentOutcome.FAILED
    e2 = Enrollment(student_id=student_id, course_offering_id=o2, status=EnrollmentStatus.COMPLETED, outcome=EnrollmentOutcome.PASSED, is_retake=False, enrolled_at=now)
    e3 = Enrollment(student_id=student_id, course_offering_id=o3, status=EnrollmentStatus.COMPLETED, outcome=EnrollmentOutcome.PASSED, is_retake=True, enrolled_at=now)
    db_session.add_all([e2, e3]); await db_session.flush()
    for oid in (offering1, o2, o3):
        db_session.add(ResultSheet(course_offering_id=oid, status=ResultSheetStatus.PUBLISHED))
    for enr, pct, letter, gp_, credits, out in ((e1, 40, "F", 0.0, 3, "FAILED"), (e2, 82, "A-", 3.66, 4, "PASSED"), (e3, 72, "B", 3.0, 3, "PASSED")):
        db_session.add(EnrollmentResult(enrollment_id=enr.id, percentage=pct, total_obtained=pct, total_max=100, letter_grade=letter, grade_points=gp_, credit_hours=credits, outcome=out, computed_at=now))
    await db_session.commit()

    await _login(app_client, username, SP)
    t = (await app_client.get(f"{R}/me/transcript")).json()
    assert [x["label"] for x in t["terms"]] == ["Fall 2025", "Spring 2026", "Fall 2026"]                                   # in the order they happened
    t1, t2, t3 = t["terms"]
    assert (t1["gpa"], t1["cgpa_after"], t1["credits_attempted"], t1["credits_earned"]) == (0.0, 0.0, 3, 0)               # failed the first course
    assert (t2["gpa"], t2["cgpa_after"], t2["credits_earned_cumulative"]) == (3.66, 2.09, 4)                                # (0*3 + 3.66*4) / 7
    assert (t3["gpa"], t3["cgpa_after"], t3["credits_earned_cumulative"]) == (3.0, 3.38, 7)                                 # the retake REPLACES the F: (3.00*3 + 3.66*4) / 7
    c1, c3 = t1["courses"][0], t3["courses"][0]
    assert (c1["attempt"], c1["replaced"]) == (1, True) and (c3["attempt"], c3["replaced"]) == (2, False) and c3["quality_points"] == 9.0
    s = t["summary"]
    assert (s["cgpa"], s["credits_attempted"], s["credits_earned"], s["courses_to_clear"], s["terms"]) == (3.38, 7, 7, 0, 3)
    assert t["student"]["roll_number"] and t["student"]["program_code"] == "TX" and len(t["scale"]) == 11


async def test_only_published_results_appear_and_only_on_the_students_own_card(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session, code="GP1", n=2, cnic_base=6300)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70), (15, 60)))
    await _login(app_client, students[0][1], SP)
    assert (await app_client.get(f"{R}/me/transcript")).json()["terms"] == []                                  # still a draft: nothing on the card
    await _as_teacher(app_client, teacher)
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/approve")
    await _login(app_client, students[0][1], SP)
    assert (await app_client.get(f"{R}/me/transcript")).json()["terms"] == []                                  # approved is not published
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/publish")
    await _login(app_client, students[0][1], SP)
    mine = (await app_client.get(f"{R}/me/transcript")).json()
    assert len(mine["terms"]) == 1 and mine["student"]["name"] and mine["terms"][0]["courses"][0]["enrollment_id"] == students[0][2]
    other = (await app_client.get(f"{R}/students/{students[1][0]}/transcript"))
    assert other.status_code == 403                                                                            # not someone else's
    await _as_teacher(app_client, teacher)
    assert (await app_client.get(f"{R}/me/transcript")).status_code == 403                                     # staff have no result card
    assert (await app_client.get(f"{R}/students/{students[0][0]}/transcript")).status_code == 403
    await _login_admin(app_client)
    assert (await app_client.get(f"{R}/students/{students[1][0]}/transcript")).json()["student"]["roll_number"]
    assert (await app_client.get(f"{R}/students/00000000-0000-0000-0000-000000000000/transcript")).status_code == 404
    app_client.cookies.clear()
    assert (await app_client.get(f"{R}/me/transcript")).status_code == 401


async def test_results_published_before_grades_existed_get_their_grade_when_the_card_is_opened(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session, code="GL1", n=1, cnic_base=6400)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70),))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/approve"); await app_client.post(f"{R}/sheets/{offering}/publish")
    await db_session.execute(delete(EnrollmentResult)); await db_session.commit()                              # as if published before this feature
    await _login(app_client, students[0][1], SP)
    c = (await app_client.get(f"{R}/me/transcript")).json()["terms"][0]["courses"][0]
    assert c["letter_grade"] == "B" and c["outcome"] == "PASSED"
    assert (await db_session.execute(select(EnrollmentResult))).scalars().all() != []                          # and it is now stored for good


# ====================================================================== the PDF
async def test_the_result_card_downloads_as_a_pdf(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session, code="GD1", n=1, cnic_base=6500)
    await _login(app_client, students[0][1], SP)
    empty = await app_client.get(f"{R}/me/transcript.pdf")
    assert empty.status_code == 200 and empty.content[:5] == b"%PDF-"                                          # even with nothing published yet
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70),))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/approve"); await app_client.post(f"{R}/sheets/{offering}/publish")
    await _login(app_client, students[0][1], SP)
    r = await app_client.get(f"{R}/me/transcript.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-" and len(r.content) > len(empty.content)
    assert r.headers["content-disposition"].startswith("attachment") and "result-card-" in r.headers["content-disposition"] and r.headers["x-content-type-options"] == "nosniff"
    await _login_admin(app_client)
    assert (await app_client.get(f"{R}/students/{students[0][0]}/transcript.pdf")).content[:5] == b"%PDF-"
    await _as_teacher(app_client, teacher)
    assert (await app_client.get(f"{R}/students/{students[0][0]}/transcript.pdf")).status_code == 403


async def test_the_result_card_pdf_contains_the_right_words(app_client, admin_user, db_session):
    pypdf = pytest.importorskip("pypdf")
    import io

    offering, students, (c1, c2), teacher = await _class(app_client, db_session, code="GD2", n=1, cnic_base=6600)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70),))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/approve"); await app_client.post(f"{R}/sheets/{offering}/publish")
    await _login(app_client, students[0][1], SP)
    r = await app_client.get(f"{R}/me/transcript.pdf")
    text = "".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(r.content)).pages)
    for needle in ("RESULT CARD", "GD2-C1", "CGPA", "Credit hours earned", "Grading scale", "A- 80%+ = 3.66", "Computer-generated"):
        assert needle in text, needle