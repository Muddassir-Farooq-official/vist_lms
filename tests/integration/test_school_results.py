"""School mode, stage 3: exams (totals per subject), marks by subject teachers (Absent = 0), remarks & skills by the class
teacher, publishing class by class, report cards (position with ties, grade, attendance, PDF), the year's weighted result,
and promotion to the next session."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.academics.models import CourseOffering, Enrollment, EnrollmentOutcome, EnrollmentStatus
from app.core.config import settings
from app.notifications.models import Notification
from app.school.models import SchoolPromotion
from app.school.results import grade, rank
from app.students.models import Student
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_parent_portal import _parent_login
from tests.integration.test_school_setup import _classes, _pupil

pytestmark = pytest.mark.asyncio
S = "/api/v1/school"


@pytest.fixture(autouse=True)
def _school(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "school_mode", 1)


async def test_grades_and_ties():
    assert [grade(p, None) for p in (85, 70, 65.5, 50, 45, 33, 32.9)] == ["A+", "A", "B", "C", "D", "E", "F"]
    assert [grade(p, "O_LEVEL") for p in (91, 80, 39)] == ["A*", "A", "U"]
    assert rank({"a": 90, "b": 80, "c": 80, "d": 70, "e": None}) == {"a": 1, "b": 2, "c": 2, "d": 4}


async def _school_with_class(client, db):
    await _login_admin(client)
    r = await client.post(f"{S}/setup", json={"term_label": "2026-27", "start_date": "2026-04-01", "end_date": "2027-03-31", "groups": ["PRIMARY", "MIDDLE"],
                                              "sections": ["A"]})
    assert r.status_code == 200, r.text
    c5 = (await _classes(client))["C5"]
    sec = c5["sections"][0]["id"]
    ct_user, ct_first = await _staff(client, db, "RCT", "TEACHER", "35202-7810001-1")
    en_user, en_first = await _staff(client, db, "RENG", "TEACHER", "35202-7820001-1")
    teachers = {t["name"][:4]: t["id"] for t in (await client.get(f"{S}/teachers")).json()}
    await client.patch(f"{S}/sections/{sec}", json={"class_teacher_id": teachers["RCT "]})
    pupils = [await _pupil(client, 60 + i) for i in range(3)]
    assert not (await client.post(f"{S}/sections/{sec}/students", json={"student_ids": pupils})).json()["failed"]
    detail = (await client.get(f"{S}/sections/{sec}")).json()
    english = next(s for s in detail["subjects"] if s["title"] == "English")
    await client.put(f"{S}/sections/{sec}/teachers", json={"teachers": {english["course_id"]: teachers["RENG"]}})
    made = (await client.post(f"/api/v1/parents/students/{pupils[0]}")).json()
    return {"c5": c5, "sec": sec, "subjects": detail["subjects"], "english": english, "pupils": pupils, "ct": (ct_user, ct_first), "en": (en_user, en_first),
            "parent": made}


async def _fill(client, exam_id, subjects, marks_by_student: dict, *, skip=None):
    """The office enters every subject's marks: marks_by_student[sid] is a % of each subject's total (None = Absent)."""
    for s in subjects:
        if skip and s["title"] == skip:
            continue
        sheet = (await client.get(f"{S}/exams/{exam_id}/marks/{s['offering_id']}")).json()
        entries = []
        for row in sheet["students"]:
            pct = marks_by_student[row["student_id"]]
            entries.append({"student_id": row["student_id"], "absent": pct is None, "marks": None if pct is None else round(sheet["total_marks"] * pct / 100, 2)})
        r = await client.put(f"{S}/exams/{exam_id}/marks/{s['offering_id']}", json={"entries": entries})
        assert r.status_code == 200, r.text


async def test_marks_report_cards_publishing_and_the_year(app_client, admin_user, db_session):
    w = await _school_with_class(app_client, db_session)
    p1, p2, p3 = w["pupils"]
    c5 = w["c5"]["id"]
    first = await app_client.post(f"{S}/exams", json={"kind": "FIRST_TERM"})
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    assert (await app_client.post(f"{S}/exams", json={"kind": "FIRST_TERM"})).status_code == 409
    totals = (await app_client.get(f"{S}/exams/{first_id}/classes/{c5}/totals")).json()
    computer = next(t for t in totals if t["subject"] == "Computer")
    assert computer["total_marks"] == 100
    assert (await app_client.put(f"{S}/exams/{first_id}/classes/{c5}/totals", json={"totals": {computer["course_id"]: 50}})).json()[-1]["total_marks"] == 50

    # the English teacher enters English; another subject is not theirs
    en_pw = await _first_login(app_client, *w["en"])
    mine = (await app_client.get(f"{S}/marks/mine")).json()
    assert [e["name"] for e in mine["exams"]] == ["1st Term"] and [c["subject"] for c in mine["classes"]] == ["English"]
    sheet = (await app_client.get(f"{S}/exams/{first_id}/marks/{w['english']['offering_id']}")).json()
    assert sheet["total_marks"] == 100 and len(sheet["students"]) == 3 and sheet["can_edit"]
    bad = await app_client.put(f"{S}/exams/{first_id}/marks/{w['english']['offering_id']}", json={"entries": [{"student_id": p1, "marks": 120}]})
    assert bad.status_code == 422
    ok = await app_client.put(f"{S}/exams/{first_id}/marks/{w['english']['offering_id']}",
                              json={"entries": [{"student_id": p1, "marks": 80}, {"student_id": p2, "marks": 80}, {"student_id": p3, "absent": True}]})
    assert ok.status_code == 200 and [r["absent"] for r in ok.json()["students"] if r["student_id"] == p3] == [True]
    urdu = next(s for s in w["subjects"] if s["title"] == "Urdu")
    assert (await app_client.get(f"{S}/exams/{first_id}/marks/{urdu['offering_id']}")).status_code == 403

    # the office: publishing needs every mark
    await _login_admin(app_client)
    early = await app_client.post(f"{S}/exams/{first_id}/classes/{c5}/publish")
    assert early.status_code == 422 and "missing" in early.text
    await _fill(app_client, first_id, w["subjects"], {p1: 80, p2: 80, p3: 20}, skip="English")

    # the class teacher's remarks and skills
    await _first_login(app_client, *w["ct"])
    notes = (await app_client.get(f"{S}/exams/{first_id}/remarks/{w['sec']}")).json()
    skill = notes["skills"][0]["id"]
    assert (await app_client.put(f"{S}/exams/{first_id}/remarks/{w['sec']}", json={"entries": [{"student_id": p1, "skills": {skill: "Z"}}]})).status_code == 422
    assert (await app_client.put(f"{S}/exams/{first_id}/remarks/{w['sec']}",
                                 json={"entries": [{"student_id": p1, "remarks": "A hard-working and polite student.", "skills": {skill: "A"}}]})).status_code == 200

    await _login_admin(app_client)
    pub = await app_client.post(f"{S}/exams/{first_id}/classes/{c5}/publish")
    assert pub.status_code == 200, pub.text
    assert next(c for c in pub.json()["classes"] if c["program_id"] == c5)["published"] is True
    parent_id = (await db_session.execute(select(Notification.user_id).where(Notification.title.like("%1st Term report card")).limit(1))).scalar_one_or_none()
    assert parent_id is not None
    # changes are refused once published
    assert (await app_client.put(f"{S}/exams/{first_id}/marks/{urdu['offering_id']}", json={"entries": [{"student_id": p1, "marks": 10}]})).status_code == 422

    card = (await app_client.get(f"{S}/students/{p1}/report-cards/{first_id}")).json()
    assert card["position"] == 1 and card["position_of"] == 3 and card["grade"] == "A+" and card["passed"] is True
    assert card["remarks"] == "A hard-working and polite student." and card["skills"][0]["rating"] == "A"
    assert next(s for s in card["subjects"] if s["subject"] == "Computer")["total"] == 50
    assert (await app_client.get(f"{S}/students/{p2}/report-cards/{first_id}")).json()["position"] == 1           # a tie shares 1st
    third = (await app_client.get(f"{S}/students/{p3}/report-cards/{first_id}")).json()
    assert third["position"] == 3 and third["passed"] is False
    assert next(s for s in third["subjects"] if s["subject"] == "English")["absent"] is True                     # Absent counts 0

    # the parent: the card and its PDF; an unpublished exam stays hidden
    final = (await app_client.post(f"{S}/exams", json={"kind": "FINAL"})).json()["id"]
    await _parent_login(app_client, w["parent"]["username"], w["parent"]["temporary_password"])
    cards = (await app_client.get(f"{S}/students/{p1}/report-cards")).json()
    assert [c["name"] for c in cards] == ["1st Term"]
    pdf = await app_client.get(cards[0]["pdf"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert (await app_client.get(f"{S}/students/{p1}/report-cards/{final}")).status_code == 404
    assert (await app_client.get(f"{S}/students/{p2}/report-cards")).status_code == 403

    # the year: 1st Term (20) and Final (50) held -> 20/70 and 50/70
    await _login_admin(app_client)
    await _fill(app_client, final, w["subjects"], {p1: 50, p2: 90, p3: 40})
    assert (await app_client.post(f"{S}/exams/{final}/classes/{c5}/publish")).status_code == 200
    year = (await app_client.get(f"{S}/students/{p1}/report-cards/{final}")).json()["year"]
    assert year["percentage"] == pytest.approx(round((80 * 20 + 50 * 50) / 70, 2), abs=0.05)
    _ = en_pw


async def test_promotion_to_the_next_session(app_client, admin_user, db_session):
    w = await _school_with_class(app_client, db_session)
    p1, p2, p3 = w["pupils"]
    c5 = w["c5"]["id"]
    final = (await app_client.post(f"{S}/exams", json={"kind": "FINAL"})).json()["id"]
    assert (await app_client.post(f"/api/v1/school/promotion/{c5}", json={"to_session": "2027-28", "items": [{"student_id": p1, "decision": "KEPT_BACK"}]})).status_code in (404, 422)
    await _fill(app_client, final, w["subjects"], {p1: 75, p2: 60, p3: 20})
    await app_client.post(f"{S}/exams/{final}/classes/{c5}/publish")
    nxt = await app_client.post(f"{S}/setup", json={"term_label": "2027-28", "start_date": "2027-04-01", "end_date": "2028-03-31", "groups": ["PRIMARY", "MIDDLE"],
                                                   "sections": ["A"]})
    assert nxt.status_code == 200

    lst = (await app_client.get(f"{S}/promotion/{c5}", params={"session_label": "2026-27"})).json()
    assert lst["final_published"] and [c["name"] for c in lst["next_classes"]] == ["Class 6"] and "2027-28" in lst["to_sessions"]
    rows = {r["student_id"]: r for r in lst["students"]}
    assert rows[p1]["passed"] is True and rows[p3]["passed"] is False and rows[p3]["failed_subjects"]
    c6 = lst["next_classes"][0]["id"]
    refused = await app_client.post(f"{S}/promotion/{c5}", json={"session_label": "2026-27", "to_session": "2027-28",
                                                                 "items": [{"student_id": p3, "decision": "PROMOTED", "to_program_id": c6}]})
    assert refused.status_code == 422 and "did not pass" in refused.text
    no_note = await app_client.post(f"{S}/promotion/{c5}", json={"session_label": "2026-27", "to_session": "2027-28",
                                                                 "items": [{"student_id": p3, "decision": "PROMOTED_ON_APPROVAL", "to_program_id": c6}]})
    assert no_note.status_code == 422
    r = await app_client.post(f"{S}/promotion/{c5}", json={"session_label": "2026-27", "to_session": "2027-28", "items": [
        {"student_id": p1, "decision": "PROMOTED", "to_program_id": c6}, {"student_id": p2, "decision": "PROMOTED", "to_program_id": c6},
        {"student_id": p3, "decision": "KEPT_BACK"}]})
    assert r.status_code == 200, r.text
    assert sorted((m["to_class"], m["decision"]) for m in r.json()["moved"]) == [("Class 5", "KEPT_BACK"), ("Class 6", "PROMOTED"), ("Class 6", "PROMOTED")]
    assert r.json()["failed"] == []
    db_session.expire_all()
    s1 = (await db_session.execute(select(Student).where(Student.id == uuid.UUID(p1)))).scalar_one()
    progs = (await _classes(app_client))
    assert str(s1.program_id) == c6 and s1.section_label == "A"
    assert len((await db_session.execute(select(SchoolPromotion))).scalars().all()) == 3
    again = await app_client.post(f"{S}/promotion/{c5}", json={"session_label": "2026-27", "to_session": "2027-28",
                                                               "items": [{"student_id": p3, "decision": "KEPT_BACK"}]})
    assert again.json()["failed"][0]["reason"] == "already decided"
    # the Final card now says where the student goes
    card = (await app_client.get(f"{S}/students/{p1}/report-cards/{final}")).json()
    assert card["promotion"] == {"decision": "PROMOTED", "to_class": "Class 6"}
    # ...and still shows last year's class, section, marks and position after the move
    assert (card["class"], card["section"], card["position_of"]) == ("Class 5", "A", 3) and card["position"] == 1 and card["passed"] is True
    assert card["percentage"] is not None and all(x["marks"] is not None for x in card["subjects"])
    kept = (await app_client.get(f"{S}/students/{p3}/report-cards/{final}")).json()
    assert kept["class"] == "Class 5" and kept["promotion"]["decision"] == "KEPT_BACK" and kept["passed"] is False
    # last year's subjects are closed on record (passed / failed by the year's result), not deleted
    old = (await db_session.execute(select(Enrollment.status, Enrollment.outcome).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                    .where(Enrollment.student_id == uuid.UUID(p3), CourseOffering.term_label == "2026-27"))).all()
    assert old and {st for st, _o in old} == {EnrollmentStatus.COMPLETED} and EnrollmentOutcome.FAILED in {o for _s, o in old}
    new_subjects = (await db_session.execute(select(func.count()).select_from(Enrollment).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                             .where(Enrollment.student_id == uuid.UUID(p3), CourseOffering.term_label == "2027-28",
                                                    Enrollment.status == EnrollmentStatus.ENROLLED))).scalar_one()
    assert new_subjects == len(old)                                   # repeating Class 5: the same subjects again
    cards = (await app_client.get(f"{S}/students/{p1}/report-cards")).json()
    assert [c["class"] for c in cards] == ["Class 5"]
    _ = progs


async def test_skills_list_and_the_pages(app_client, admin_user, db_session):
    w = await _school_with_class(app_client, db_session)
    skills = (await app_client.get(f"{S}/skills")).json()
    assert [s["name"] for s in skills][:2] == ["Discipline", "Punctuality"]
    new = [{"id": s["id"], "name": s["name"], "active": s["name"] != "Neatness"} for s in skills] + [{"name": "Sports"}]
    saved = (await app_client.put(f"{S}/skills", json={"skills": new})).json()
    assert "Sports" in [s["name"] for s in saved] and "Neatness" not in [s["name"] for s in (await app_client.get(f"{S}/skills")).json()]
    page = (await app_client.get("/dashboard/admin/classes", headers={"accept": "text/html"})).text
    assert "school-results.js" in page and 'data-tab="results"' in page
    await _first_login(app_client, *w["en"])
    page = await app_client.get("/dashboard/faculty/exam-marks", headers={"accept": "text/html"})
    assert page.status_code == 200 and "school-exam-marks.js" in page.text
    home = (await app_client.get("/dashboard/faculty", headers={"accept": "text/html"})).text
    assert 'href="/dashboard/faculty/exam-marks"' in home and 'href="/dashboard/faculty/grading"' not in home
    await _parent_login(app_client, w["parent"]["username"], w["parent"]["temporary_password"])
    page = await app_client.get("/dashboard/parent/report-cards", headers={"accept": "text/html"})
    assert page.status_code == 200 and "parent-report-cards.js" in page.text
