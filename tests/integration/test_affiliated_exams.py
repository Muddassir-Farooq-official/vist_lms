"""An affiliated program (e.g. KMU takes the mid and final): the institute cannot add, enter or schedule those parts; it
keeps internal marks (quizzes ...), prints the internal award list with Reg Nos, publishing decides nothing, and the
university's result imported by Reg No is the result (transcript, GPA)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.academics.models import Enrollment, EnrollmentResult
from app.core.config import settings
from app.students.models import Student
from tests.integration.test_attendance_and_grading import TERM_START, _get_or_create_assessment_type, _login_admin, _publish_results
from tests.integration.test_grading_tables import _table
from tests.integration.test_result_sheets import _as_teacher, _class

pytestmark = pytest.mark.asyncio
R = "/api/v1/results"
A = "/api/v1/academics"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_affiliated_program_internal_marks_award_list_and_university_result(app_client, admin_user, db_session):
    offering, students, (quiz_oa, final_oa), teacher = await _class(app_client, db_session, code="AF1", n=2)
    await _login_admin(app_client)
    s1, s2 = (await db_session.get(Student, uuid.UUID(students[0][0]))), (await db_session.get(Student, uuid.UUID(students[1][0])))
    program_id, s1_id, s2_roll = s1.program_id, s1.id, s2.roll_number
    table = (await app_client.post(f"{R}/grading-tables", json=_table("KMU test", body="Khyber Medical University"))).json()
    assert (await app_client.put(f"{R}/programs/{program_id}/grading-table", json={"scheme_id": table["id"]})).status_code == 200
    assert (await app_client.put(f"/api/v1/students/{s1.id}/reg-number", json={"reg_number": "KMU-AF1-001"})).status_code == 200

    # who conducts the exams: the university takes the mid and the final
    refused = await app_client.put(f"{R}/programs/{program_id}/exam-body", json={"exam_body": "AFFILIATED", "university_parts": []})
    assert refused.status_code == 422
    ok = await app_client.put(f"{R}/programs/{program_id}/exam-body", json={"exam_body": "AFFILIATED", "university_parts": ["final", "MID"]})
    assert ok.status_code == 200 and ok.json()["university_parts"] == ["MID", "FINAL"]
    listed = {p["id"]: p for p in (await app_client.get(f"{R}/programs-exam-mode")).json()}
    assert listed[str(program_id)]["exam_body"] == "AFFILIATED"

    # the catalog: "AF1 Final" is a FINAL part; a mid part too. Neither can be added to the subject or entered any more.
    types = {t["name"]: t["id"] for t in (await app_client.get(f"{A}/assessment-types")).json()}
    await app_client.put(f"{A}/assessment-types/{types['AF1 Final']}/component", json={"component": "FINAL"})
    mid = await _get_or_create_assessment_type(app_client, "AF1 Mid", 30)
    await app_client.put(f"{A}/assessment-types/{mid}/component", json={"component": "MID"})
    no_mid = await app_client.post(f"{A}/offerings/{offering}/assessments", json={"assessment_type_id": mid})
    assert no_mid.status_code == 422 and "conducted by Khyber Medical University" in no_mid.text
    no_final_mark = await app_client.post(f"{A}/enrollments/{students[0][2]}/marks/{final_oa}", json={"obtained_marks": 50})
    assert no_final_mark.status_code == 422 and "conducted by" in no_final_mark.text
    # nor can the institute put the subject on its own mid-term datesheet
    sched = (await app_client.post("/api/v1/exams/schedules", json={"term_label": "Fall 2026", "name": "Midterm", "assessment_type_id": mid})).json()
    paper = await app_client.post(f"/api/v1/exams/schedules/{sched['id']}/papers", json={
        "course_offering_id": offering, "exam_date": str(TERM_START), "start_time": "09:00", "end_time": "11:00", "room": "Hall"})
    assert paper.status_code == 422 and "datesheet" in paper.text

    # the old final assessment is removed; teachers enter quizzes only: a second quiz is added
    assert (await app_client.delete(f"{A}/offering-assessments/{final_oa}")).status_code == 204
    quiz2 = await _get_or_create_assessment_type(app_client, "AF1 Quiz 2", 10)
    quiz2_oa = (await app_client.post(f"{A}/offerings/{offering}/assessments", json={"assessment_type_id": quiz2})).json()["id"]
    split = await app_client.put(f"{R}/mark-splits", json={"program_id": str(program_id), "values": {"SESSIONAL": 20, "MID": 30, "FINAL": 50}})
    assert split.status_code == 200
    await _as_teacher(app_client, teacher)
    for (_sid, _u, e), (q1, q2) in zip(students, ((15, 9), (10, 5))):
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{quiz_oa}", json={"obtained_marks": q1})).status_code == 201
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{quiz2_oa}", json={"obtained_marks": q2})).status_code == 201

    # the internal award list: (15 + 9) / 30 scaled to the 20 internal marks = 16
    csv_text = (await app_client.get(f"{R}/sheets/{offering}/internal-award-list.csv")).text
    lines = [line for line in csv_text.lstrip("﻿").splitlines() if line]
    assert lines[0].startswith("roll_number,reg_number,student_name") and lines[0].endswith("internal_total (/20)")
    first = next(line for line in lines[1:] if "KMU-AF1-001" in line)
    assert first.endswith(",16")
    pdf = await app_client.get(f"{R}/sheets/{offering}/internal-award-list.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # publishing shows the internal marks but decides nothing
    await _login_admin(app_client)
    review = (await app_client.get(f"{R}/sheets/{offering}")).json()
    assert review["affiliated"]["university_parts"] == ["Final / terminal", "Mid-term"] or set(review["affiliated"]["university_parts"]) == {"Mid-term", "Final / terminal"}
    await _publish_results(app_client, offering)
    db_session.expire_all()
    e1 = await db_session.get(Enrollment, uuid.UUID(students[0][2]))
    assert e1.outcome is None
    assert (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == e1.id))).scalar_one_or_none() is None

    # the university's result, by Reg No (and roll number for the student without one)
    course_code = (await app_client.get(f"{A}/courses/{(await app_client.get(f'{A}/offerings/{offering}')).json()['course_id']}")).json()["code"]
    files = lambda body: {"file": ("u.csv", body.encode(), "text/csv")}                                         # noqa: E731
    bad = (f"reg_number,roll_number,course_code,marks_obtained,marks_total,grade,result\n"
           f"KMU-XXX,,{course_code},70,100,,\nKMU-AF1-001,,{course_code},120,100,,\n,{s2_roll},{course_code},,,Z,\n")
    chk = (await app_client.post(f"{R}/university/import", params={"program_id": str(program_id), "term_label": "Fall 2026", "dry_run": True}, files=files(bad))).json()
    assert chk["error_count"] == 3
    good = (f"reg_number,roll_number,course_code,marks_obtained,marks_total,grade,result\n"
            f"kmu-af1-001,,{course_code},82,100,,\n,{s2_roll},{course_code},,,C,FAIL\n")
    params = {"program_id": str(program_id), "term_label": "fall 2026"}
    dry = (await app_client.post(f"{R}/university/import", params={**params, "dry_run": True}, files=files(good))).json()
    assert dry["error_count"] == 0 and dry["result_count"] == 2 and dry["passed"] == 1 and dry["failed"] == 1
    done = (await app_client.post(f"{R}/university/import", params={**params, "dry_run": False}, files=files(good))).json()
    assert done["applied"] == 2
    db_session.expire_all()
    r1 = (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == uuid.UUID(students[0][2])))).scalar_one()
    assert (r1.source, r1.letter_grade, r1.outcome, float(r1.percentage)) == ("UNIVERSITY", "A", "PASSED", 82.0)
    r2 = (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == uuid.UUID(students[1][2])))).scalar_one()
    assert (r2.letter_grade, r2.outcome) == ("C", "FAILED")                                                   # the university said FAIL
    transcript = (await app_client.get(f"{R}/students/{s1_id}/transcript")).json()
    assert transcript["student"]["reg_number"] == "KMU-AF1-001"
    assert any(c["letter_grade"] == "A" for t in transcript["terms"] for c in t.get("courses", t.get("results", [])))

    # importing again replaces (the university revised it), and says so
    again = (await app_client.post(f"{R}/university/import", params={**params, "dry_run": True}, files=files(good))).json()
    assert again["replacing"] == 2

    # supplementary (re-sit): only for the subject the student failed; it counts from now on; the first attempt is kept
    from app.academics.models import UniversityResultAttempt

    supp = {**params, "kind": "SUPPLEMENTARY"}
    passed_one = f"reg_number,roll_number,course_code,marks_obtained,marks_total,grade,result\nKMU-AF1-001,,{course_code},90,100,,\n"
    refused = (await app_client.post(f"{R}/university/import", params={**supp, "dry_run": True}, files=files(passed_one))).json()
    assert refused["error_count"] == 1 and "nothing to re-sit" in refused["errors"][0]["error"]
    resit = f"reg_number,roll_number,course_code,marks_obtained,marks_total,grade,result\n,{s2_roll},{course_code},71,100,,\n"
    dry = (await app_client.post(f"{R}/university/import", params={**supp, "dry_run": True}, files=files(resit))).json()
    assert dry["error_count"] == 0 and dry["results"][0]["replaces"] == "C (first attempt)" and dry["results"][0]["attempt"] == "supplementary"
    done = (await app_client.post(f"{R}/university/import", params={**supp, "dry_run": False}, files=files(resit))).json()
    assert done["applied"] == 1
    db_session.expire_all()
    r2 = (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == uuid.UUID(students[1][2])))).scalar_one()
    assert (r2.outcome, r2.supplementary, float(r2.percentage)) == ("PASSED", True, 71.0)
    tries = (await db_session.execute(select(UniversityResultAttempt).where(UniversityResultAttempt.enrollment_id == uuid.UUID(students[1][2]))
                                      .order_by(UniversityResultAttempt.attempt_no))).scalars().all()
    assert [(a.attempt_no, a.kind, a.letter_grade, a.outcome) for a in tries] == [(1, "REGULAR", "C", "FAILED"), (2, "SUPPLEMENTARY", r2.letter_grade, "PASSED")]
    # a regular import can no longer overwrite it; a supplementary re-import revises the same attempt
    overwrite = (await app_client.post(f"{R}/university/import", params={**params, "dry_run": True}, files=files(resit))).json()
    assert overwrite["error_count"] == 1 and "already has a supplementary result" in overwrite["errors"][0]["error"]
    revised = f"reg_number,roll_number,course_code,marks_obtained,marks_total,grade,result\n,{s2_roll},{course_code},75,100,,\n"
    assert (await app_client.post(f"{R}/university/import", params={**supp, "dry_run": False}, files=files(revised))).json()["applied"] == 1
    db_session.expire_all()
    tries = (await db_session.execute(select(UniversityResultAttempt).where(UniversityResultAttempt.enrollment_id == uuid.UUID(students[1][2]))
                                      .order_by(UniversityResultAttempt.attempt_no))).scalars().all()
    assert len(tries) == 2 and float(tries[1].percentage) == 75.0 and tries[1].revised == 1
    s2_id = (await db_session.get(Enrollment, uuid.UUID(students[1][2]))).student_id
    tr = (await app_client.get(f"{R}/students/{s2_id}/transcript")).json()
    row = next(c for t in tr["terms"] for c in t.get("courses", []) if c["course_code"] == course_code)
    assert row["supplementary"] is True and row["outcome"] == "PASSED"

    # back to degree-awarding: everything is allowed again
    assert (await app_client.put(f"{R}/programs/{program_id}/exam-body", json={"exam_body": "INSTITUTE"})).json()["university_parts"] == []
    assert (await app_client.get(f"{R}/sheets/{offering}")).json()["affiliated"] is None
