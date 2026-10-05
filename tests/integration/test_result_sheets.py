"""Marks are a draft until they are submitted, approved and published. Students see nothing, and pass/fail is not decided, until then."""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.academics.models import StudentMark
from app.audit.models import AuditLog
from app.core.config import settings
from app.notifications.models import Notification, NotificationCategory
from tests.integration.test_attendance_and_grading import _get_or_create_assessment_type, _login, _login_admin, _setup_offering_with_enrolled_students
from tests.integration.test_teacher_content_and_bulk_timetable import _sign_in_teacher, _teacher_session

pytestmark = pytest.mark.asyncio
R = "/api/v1/results"
A = "/api/v1/academics"
SP = "StudentPass123!"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _class(app_client, db, code="RS1", n=3, with_teacher=True, cnic_base=2000):
    """A course with n students and two assessments (Quiz /20, Final /100). Returns offering, students, assessment ids, teacher login."""
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code=code, n_students=n, cnic_base=cnic_base)
    quiz, final = await _get_or_create_assessment_type(app_client, f"{code} Quiz", 20), await _get_or_create_assessment_type(app_client, f"{code} Final", 100)
    c1 = (await app_client.post(f"{A}/offerings/{offering_id}/assessments", json={"assessment_type_id": quiz})).json()["id"]
    c2 = (await app_client.post(f"{A}/offerings/{offering_id}/assessments", json={"assessment_type_id": final})).json()["id"]
    teacher = None
    if with_teacher:
        _tid, teacher = await _teacher_session(app_client, db, f"T{code}", f"35202-91{abs(hash(code)) % 90000 + 10000}-1", offering_id)
        await _sign_in_teacher(app_client, teacher)
    return offering_id, students, (c1, c2), teacher


async def _mark(client, enrollment_id, assessment_id, marks, **extra):
    return await client.post(f"{A}/enrollments/{enrollment_id}/marks/{assessment_id}", json={"obtained_marks": marks, **extra})


async def _fill(client, students, assessments, scores=((18, 70), (10, 20), (15, 60))):
    for (_sid, _user, enrollment_id), (s1, s2) in zip(students, scores):
        assert (await _mark(client, enrollment_id, assessments[0], s1)).status_code == 201
        assert (await _mark(client, enrollment_id, assessments[1], s2)).status_code == 201


async def _as_teacher(client, teacher):
    await _login(client, teacher, "TeacherNew12345!")


async def test_a_sheet_needs_every_mark_before_it_can_be_submitted_and_then_locks(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session)
    await _as_teacher(app_client, teacher)
    s = (await app_client.get(f"{R}/offerings/{offering}/sheet")).json()
    assert s["status"] == "DRAFT" and s["can_edit"] is True and s["can_submit"] is False and s["completeness"]["missing_students"] == 3
    await _mark(app_client, students[0][2], c1, 18)                                                                      # only one of six marks
    early = await app_client.post(f"{R}/offerings/{offering}/submit")
    assert early.status_code == 422 and "missing for 3 students" in early.text
    await _fill(app_client, students, (c1, c2))
    s = (await app_client.get(f"{R}/offerings/{offering}/sheet")).json()
    assert s["can_submit"] is True and s["completeness"]["complete_students"] == 3
    done = (await app_client.post(f"{R}/offerings/{offering}/submit")).json()
    assert done["status"] == "SUBMITTED" and done["can_edit"] is False and done["submitted_at"]
    # now locked, for the teacher and for the administration alike
    locked = await _mark(app_client, students[0][2], c1, 5)
    assert locked.status_code == 422 and "locked" in locked.text
    assert (await app_client.post(f"{R}/offerings/{offering}/submit")).status_code == 422                                 # cannot submit twice
    await _login_admin(app_client)
    extra = await _get_or_create_assessment_type(app_client, "Late addition", 10)
    assert (await app_client.post(f"{A}/offerings/{offering}/assessments", json={"assessment_type_id": extra})).status_code == 422     # the structure is locked too
    assert (await _mark(app_client, students[0][2], c1, 5)).status_code == 422                                           # even the administration must send it back first


async def test_a_sheet_cannot_be_submitted_without_assessments_or_by_someone_elses_teacher(app_client, admin_user, db_session):
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="RSE", n_students=1)
    _tid, teacher = await _teacher_session(app_client, db_session, "TRSE", "35202-9100001-1", offering_id)
    await _sign_in_teacher(app_client, teacher)
    empty = await app_client.post(f"{R}/offerings/{offering_id}/submit")
    assert empty.status_code == 422 and "at least one assessment" in empty.text
    other_offering, _s, _a, _t = await _class(app_client, db_session, code="RSF", n=1, with_teacher=False, cnic_base=2500)
    await _as_teacher(app_client, teacher)
    assert (await app_client.post(f"{R}/offerings/{other_offering}/submit")).status_code == 403                          # not their subject
    assert (await app_client.get(f"{R}/offerings/{other_offering}/sheet")).status_code == 403


async def test_students_see_no_marks_and_no_result_until_it_is_published(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2))
    student_id, username, enrollment_id = students[0]
    async def what_the_student_sees():
        await _login(app_client, username, SP)
        marks = (await app_client.get(f"{A}/enrollments/{enrollment_id}/marks")).json()
        mine = [e for e in (await app_client.get(f"{A}/enrollments/me")).json() if e["id"] == enrollment_id][0]
        return marks, mine["outcome"], mine["status"]
    assert await what_the_student_sees() == ([], None, "ENROLLED")                                                       # entered, but still a draft
    await _as_teacher(app_client, teacher)
    assert len((await app_client.get(f"{A}/enrollments/{enrollment_id}/marks")).json()) == 2                              # the teacher sees them throughout
    await app_client.post(f"{R}/offerings/{offering}/submit")
    assert await what_the_student_sees() == ([], None, "ENROLLED")                                                       # submitted
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/approve")
    assert await what_the_student_sees() == ([], None, "ENROLLED")                                                       # approved, not yet published
    await _login_admin(app_client)
    assert (await app_client.post(f"{R}/sheets/{offering}/publish")).json()["status"] == "PUBLISHED"
    marks, outcome, status = await what_the_student_sees()
    assert [m["obtained_marks"] for m in marks] == [18.0, 70.0] and outcome == "PASSED" and status == "COMPLETED"        # (18+70)/120 = 73%
    # a classmate still cannot read someone else's marks
    await _login(app_client, students[1][1], SP)
    assert (await app_client.get(f"{A}/enrollments/{enrollment_id}/marks")).status_code == 403


async def test_publishing_decides_every_students_result_and_tells_them_once(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70), (3, 10), (15, 60)))             # 73%, 11%, 62%
    await app_client.post(f"{R}/offerings/{offering}/submit")
    assert not (await db_session.execute(select(Notification).where(Notification.category == NotificationCategory.RESULT, Notification.title.like("Result published%")))).scalars().all()
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/approve")
    await app_client.post(f"{R}/sheets/{offering}/publish")
    roster = {e["id"]: e["outcome"] for e in (await app_client.get(f"{A}/offerings/{offering}/roster")).json()}
    assert [roster[s[2]] for s in students] == ["PASSED", "FAILED", "PASSED"]
    notes = (await db_session.execute(select(Notification).where(Notification.title.like("Result published for%")))).scalars().all()
    assert len(notes) == 3 and all(n.category == NotificationCategory.RESULT and n.link == "/dashboard/student/results" for n in notes)
    assert sorted(n.title.split(": ")[1] for n in notes) == ["Failed", "Passed", "Passed"]
    assert (await app_client.post(f"{R}/sheets/{offering}/publish")).status_code == 422                                   # not published twice


async def test_the_approver_can_send_marks_back_with_a_reason_and_the_teacher_fixes_them(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    assert (await app_client.post(f"{R}/sheets/{offering}/return", json={"reason": "no"})).status_code == 422             # a real reason
    back = (await app_client.post(f"{R}/sheets/{offering}/return", json={"reason": "Final marks for the second student look too low."})).json()
    assert back["status"] == "RETURNED" and back["can_edit"] is True and back["return_reason"].startswith("Final marks")
    note = (await db_session.execute(select(Notification).where(Notification.title.like("Marks sent back%")))).scalars().one()
    assert "second student" in note.message
    await _as_teacher(app_client, teacher)
    sheet = (await app_client.get(f"{R}/offerings/{offering}/sheet")).json()
    assert sheet["status"] == "RETURNED" and sheet["return_reason"].startswith("Final marks") and sheet["can_submit"] is True
    assert (await _mark(app_client, students[1][2], c2, 45)).status_code == 201                                           # editable again
    again = (await app_client.post(f"{R}/offerings/{offering}/submit")).json()
    assert again["status"] == "SUBMITTED" and again["resubmission_count"] == 1 and again["return_reason"] is None
    await _login_admin(app_client)
    assert (await app_client.post(f"{R}/sheets/{offering}/publish")).status_code == 422                                   # must be approved first
    assert (await app_client.post(f"{R}/sheets/{offering}/approve")).json()["status"] == "APPROVED"
    assert (await app_client.post(f"{R}/sheets/{offering}/approve")).status_code == 422                                   # already approved
    again = await app_client.post(f"{R}/sheets/{offering}/return", json={"reason": "Spotted another error after approving."})
    assert again.status_code == 200 and again.json()["status"] == "RETURNED"                                              # an approved sheet can still be pulled back


async def test_only_the_approver_decides(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    for call in (app_client.post(f"{R}/sheets/{offering}/approve"), app_client.post(f"{R}/sheets/{offering}/publish"), app_client.get(f"{R}/sheets"),
                 app_client.get(f"{R}/sheets/{offering}"), app_client.post(f"{R}/publish-approved"), app_client.post(f"{R}/sheets/{offering}/return", json={"reason": "I disagree with myself"})):
        assert (await call).status_code == 403
    await _login(app_client, students[0][1], SP)
    assert (await app_client.get(f"{R}/sheets")).status_code == 403 and (await app_client.post(f"{R}/offerings/{offering}/submit")).status_code == 403
    app_client.cookies.clear()
    assert (await app_client.get(f"{R}/sheets")).status_code == 401


async def test_after_publication_only_the_administration_can_correct_a_mark_and_it_is_recorded(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70), (3, 10), (15, 60)))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    await app_client.post(f"{R}/sheets/{offering}/approve"); await app_client.post(f"{R}/sheets/{offering}/publish")
    failed_enrollment = students[1][2]
    await _as_teacher(app_client, teacher)
    denied = await _mark(app_client, failed_enrollment, c2, 80)
    assert denied.status_code == 422 and "Only the administration" in denied.text
    await _login_admin(app_client)
    assert (await _mark(app_client, failed_enrollment, c2, 80)).status_code == 422                                        # a reason is required
    assert (await _mark(app_client, failed_enrollment, c2, 80, correction_reason="ok")).status_code == 422
    fixed = await _mark(app_client, failed_enrollment, c2, 80, correction_reason="Mark was mis-keyed from the answer sheet")
    assert fixed.status_code == 201 and fixed.json()["obtained_marks"] == 80.0
    roster = {e["id"]: e["outcome"] for e in (await app_client.get(f"{A}/offerings/{offering}/roster")).json()}
    assert roster[failed_enrollment] == "PASSED"                                                                          # (3+80)/120 = 69%: the result follows the correction
    sheet = (await app_client.get(f"{R}/sheets/{offering}")).json()
    assert sheet["correction_count"] == 1 and sheet["status"] == "PUBLISHED"
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "student_mark.corrected"))).scalars().one()
    assert log.before["obtained_marks"] == 10.0 and log.after["obtained_marks"] == 80.0 and "mis-keyed" in str(log.log_metadata)      # what it was, what it became, and why
    told = (await db_session.execute(select(Notification).where(Notification.title.like("Your result for%was corrected%")))).scalars().all()
    assert len(told) == 1 and "Passed" in told[0].title
    await _login(app_client, students[1][1], SP)
    assert [m["obtained_marks"] for m in (await app_client.get(f"{A}/enrollments/{failed_enrollment}/marks")).json()] == [3.0, 80.0]


async def test_the_approver_reviews_the_whole_class_with_each_students_projected_result(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70), (3, 10), (15, 60)))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    await _login_admin(app_client)
    r = (await app_client.get(f"{R}/sheets/{offering}")).json()
    assert [a["name"] for a in r["assessments_list"]] == ["RS1 Quiz", "RS1 Final"] and [a["max_marks"] for a in r["assessments_list"]] == [20.0, 100.0]
    assert len(r["rows"]) == 3 and all(x["is_projection"] is True for x in r["rows"])                                      # nothing is decided yet: these are projections
    by_roll = sorted(r["rows"], key=lambda x: x["roll_number"])
    assert [x["marks"] for x in by_roll] == [[18.0, 70.0], [3.0, 10.0], [15.0, 60.0]] and [x["percentage"] for x in by_roll] == [73.33, 10.83, 62.5]
    assert [x["outcome"] for x in by_roll] == ["PASSED", "FAILED", "PASSED"]
    st = r["stats"]
    assert (st["students"], st["passed"], st["failed"], st["pass_rate"]) == (3, 2, 1, 66.7) and st["highest"] == 73.33 and st["lowest"] == 10.83 and st["average"] == 48.9
    assert (await app_client.get(f"{A}/offerings/{offering}/roster")).json()[0]["outcome"] is None                        # and nothing was saved by looking
    await app_client.post(f"{R}/sheets/{offering}/approve"); await app_client.post(f"{R}/sheets/{offering}/publish")
    assert all(x["is_projection"] is False for x in (await app_client.get(f"{R}/sheets/{offering}")).json()["rows"])


async def test_the_list_by_status_and_publishing_everything_approved_on_result_day(app_client, admin_user, db_session):
    o1, s1, a1, _ = await _class(app_client, db_session, code="RA1", n=2, with_teacher=False, cnic_base=3000)
    o2, s2, a2, _ = await _class(app_client, db_session, code="RA2", n=2, with_teacher=False, cnic_base=4000)
    o3, s3, a3, _ = await _class(app_client, db_session, code="RA3", n=2, with_teacher=False, cnic_base=5000)
    await _login_admin(app_client)
    for st, a in ((s1, a1), (s2, a2), (s3, a3)):
        await _fill(app_client, st, a, scores=((10, 60), (12, 70)))
    for o in (o1, o2, o3):
        await app_client.post(f"{R}/offerings/{o}/submit")
    assert {x["course_code"] for x in (await app_client.get(f"{R}/sheets", params={"status": "SUBMITTED"})).json()} >= {"RA1-C1", "RA2-C1", "RA3-C1"}
    for o in (o1, o2, o3):
        await app_client.post(f"{R}/sheets/{o}/approve")
    row = next(x for x in (await app_client.get(f"{R}/sheets", params={"status": "APPROVED"})).json() if x["course_code"] == "RA1-C1")
    assert row["students"] == 2 and row["assessments"] == 2 and row["missing_students"] == 0 and row["approved_at"]
    # something goes wrong with ONE course after approval (a mark goes missing): it must not stop the others being published
    await db_session.execute(delete(StudentMark).where(StudentMark.enrollment_id == s3[0][2], StudentMark.offering_assessment_id == a3[1]))
    await db_session.commit()
    done = (await app_client.post(f"{R}/publish-approved")).json()
    assert done["published"] == 2 and len(done["failed"]) == 1 and "missing for 1 student" in done["failed"][0]["reason"] and done["failed"][0]["course_offering_id"] == o3
    assert {x["course_code"] for x in (await app_client.get(f"{R}/sheets", params={"status": "PUBLISHED"})).json()} >= {"RA1-C1", "RA2-C1"}
    assert [x["course_code"] for x in (await app_client.get(f"{R}/sheets", params={"status": "APPROVED"})).json()] == ["RA3-C1"]            # still waiting, not lost
    await _login(app_client, s1[0][1], SP)
    assert len((await app_client.get(f"{A}/enrollments/{s1[0][2]}/marks")).json()) == 2                                                    # RA1's students can see theirs
    await _login(app_client, s3[1][1], SP)
    assert (await app_client.get(f"{A}/enrollments/{s3[1][2]}/marks")).json() == []                                                        # RA3's cannot


async def test_a_student_who_withdraws_does_not_hold_up_the_sheet(app_client, admin_user, db_session):
    offering, students, (c1, c2), _ = await _class(app_client, db_session, code="RW1", n=3, with_teacher=False)
    await _login_admin(app_client)
    await _fill(app_client, students[:2], (c1, c2), scores=((10, 60), (12, 70)))                                           # the third student has no marks...
    assert (await app_client.post(f"{R}/offerings/{offering}/submit")).status_code == 422
    assert (await app_client.post(f"{A}/enrollments/{students[2][2]}/admin-withdraw")).status_code == 200                # ...because they have left the course
    assert (await app_client.post(f"{R}/offerings/{offering}/submit")).json()["status"] == "SUBMITTED"
    assert len((await app_client.get(f"{R}/sheets/{offering}")).json()["rows"]) == 2


async def test_results_that_already_existed_before_the_workflow_stay_visible(app_client, admin_user, db_session):
    """The migration marks every course that already had marks as PUBLISHED. Here: no sheet row at all means DRAFT, and a PUBLISHED row means visible."""
    from app.academics.models import ResultSheet, ResultSheetStatus

    offering, students, (c1, c2), _ = await _class(app_client, db_session, code="RV1", n=1, with_teacher=False)
    await _login_admin(app_client)
    await _fill(app_client, students, (c1, c2), scores=((18, 70),))
    await _login(app_client, students[0][1], SP)
    assert (await app_client.get(f"{A}/enrollments/{students[0][2]}/marks")).json() == []                                 # no sheet row: a draft
    db_session.add(ResultSheet(course_offering_id=offering, status=ResultSheetStatus.PUBLISHED))                         # what the migration does for old results
    await db_session.commit()
    assert len((await app_client.get(f"{A}/enrollments/{students[0][2]}/marks")).json()) == 2