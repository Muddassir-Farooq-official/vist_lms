"""Grading tables per affiliating body, picked per program; the mark split per program (overridable per subject); theory
and practical each passed on their own; Final/Practical marks entered by the exam office; published results frozen."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.academics.grading_schemes import Band, Rules
from app.academics.models import EnrollmentResult
from app.core.config import settings
from app.students.models import Student
from tests.integration.test_attendance_and_grading import _get_or_create_assessment_type, _login_admin, _publish_results
from tests.integration.test_result_sheets import _as_teacher, _class

R = "/api/v1/results"
A = "/api/v1/academics"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def test_sliding_points_inside_a_band():
    rules = Rules(None, "AUST", None, "SEMESTER", 50, 50, 50, 60, True,
                  [Band("A", 80, 4.0, None, True), Band("B+", 76, 3.9, 3.6, True), Band("B", 73, 3.5, 3.3, True), Band("D", 50, 1.7, 1.0, True),
                   Band("F", 0, 0.0, None, False)])
    assert rules.grade(76, True)[:2] == ("B+", 3.6)
    assert rules.grade(79, True)[:2] == ("B+", 3.9)
    assert rules.grade(77.5, True)[:2] == ("B+", 3.75)
    assert rules.grade(90, True)[:2] == ("A", 4.0)
    assert rules.grade(65, False)[:2] == ("F", 0.0)            # failed (e.g. practical) whatever the percentage


def _table(name="Test Univ", **over):
    body = {"name": name, "body": "Test University", "system": "SEMESTER", "pass_percent": 50, "theory_pass_percent": 50, "practical_pass_percent": 50,
            "resit_cap_percent": 60, "sliding_points": False, "is_draft": False,
            "bands": [{"letter": "A", "min_percent": 80, "grade_points": 4, "is_passing": True, "division": "1st Division"},
                      {"letter": "B", "min_percent": 65, "grade_points": 3, "is_passing": True, "division": "1st Division"},
                      {"letter": "C", "min_percent": 50, "grade_points": 2, "is_passing": True, "division": "2nd Division"},
                      {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}]}
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_tables_drafts_assignment_and_validation(app_client, admin_user, db_session):
    await _login_admin(app_client)
    added = (await app_client.post(f"{R}/grading-tables/drafts")).json()["added"]
    assert set(added) == {"AUST", "KMU", "Sarhad University", "Board (annual)"}
    assert (await app_client.post(f"{R}/grading-tables/drafts")).json()["added"] == []          # not twice
    tables = {t["name"]: t for t in (await app_client.get(f"{R}/grading-tables")).json()}
    assert tables["AUST"]["is_draft"] and tables["AUST"]["sliding_points"] and tables["AUST"]["resit_cap_percent"] == 60
    assert tables["Board (annual)"]["system"] == "ANNUAL" and tables["Board (annual)"]["bands"][0]["division"] == "1st Division"

    from tests.integration.test_module3_sections_allocation import _program

    prog = await _program(db_session, code="GTP")
    refused = await app_client.put(f"{R}/programs/{prog.id}/grading-table", json={"scheme_id": tables["AUST"]["id"]})
    assert refused.status_code in (409, 422) and "draft" in refused.text
    checked = {**{k: v for k, v in tables["AUST"].items() if k not in ("id", "programs")}, "is_draft": False}
    assert (await app_client.put(f"{R}/grading-tables/{tables['AUST']['id']}", json=checked)).status_code == 200
    assert (await app_client.put(f"{R}/programs/{prog.id}/grading-table", json={"scheme_id": tables["AUST"]["id"]})).status_code == 200
    assert (await app_client.delete(f"{R}/grading-tables/{tables['AUST']['id']}")).status_code in (409, 422)          # in use

    bad = _table("Bad", bands=[{"letter": "A", "min_percent": 50, "grade_points": 4, "is_passing": True},
                               {"letter": "F", "min_percent": 10, "grade_points": 0, "is_passing": False}])
    r = await app_client.post(f"{R}/grading-tables", json=bad)
    assert r.status_code in (409, 422) and "start at 0%" in r.text
    assert (await app_client.post(f"{R}/grading-tables", json=_table("KMU"))).status_code == 409                      # name taken


@pytest.mark.asyncio
async def test_split_theory_and_practical_office_marks_and_frozen_results(app_client, admin_user, db_session):
    offering, students, (quiz_oa, final_oa), teacher = await _class(app_client, db_session, code="GT1", n=2)
    await _login_admin(app_client)
    sid = students[0][0]
    program_id = (await db_session.get(Student, uuid.UUID(sid))).program_id
    table = (await app_client.post(f"{R}/grading-tables", json=_table())).json()
    assert (await app_client.put(f"{R}/programs/{program_id}/grading-table", json={"scheme_id": table["id"]})).status_code == 200

    # catalog: the class already has "GT1 Quiz" /20 (no part = Sessional) and "GT1 Final" /100; add Mid and Practical
    types = {t["name"]: t["id"] for t in (await app_client.get(f"{A}/assessment-types")).json()}
    assert (await app_client.put(f"{A}/assessment-types/{types['GT1 Final']}/component", json={"component": "FINAL"})).json()["component"] == "FINAL"
    mid = await _get_or_create_assessment_type(app_client, "GT1 Mid", 30)
    await app_client.put(f"{A}/assessment-types/{mid}/component", json={"component": "MID"})
    prac = (await app_client.post(f"{A}/assessment-types", json={"name": "GT1 Practical", "max_marks": 50, "component": "PRACTICAL"})).json()["id"]
    mid_oa = (await app_client.post(f"{A}/offerings/{offering}/assessments", json={"assessment_type_id": mid})).json()["id"]
    prac_oa = (await app_client.post(f"{A}/offerings/{offering}/assessments", json={"assessment_type_id": prac})).json()["id"]

    split = await app_client.put(f"{R}/mark-splits", json={"program_id": str(program_id), "values": {"SESSIONAL": 20, "MID": 30, "FINAL": 50, "PRACTICAL": 50}})
    assert split.status_code == 200, split.text

    # the teacher enters sessional and mid; final and practical are the exam office's
    await _as_teacher(app_client, teacher)
    e1, e2 = students[0][2], students[1][2]
    for e, q, m in ((e1, 16, 21), (e2, 18, 27)):
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{quiz_oa}", json={"obtained_marks": q})).status_code == 201
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{mid_oa}", json={"obtained_marks": m})).status_code == 201
    office = await app_client.post(f"{A}/enrollments/{e1}/marks/{final_oa}", json={"obtained_marks": 60})
    assert office.status_code == 422 and "exam office" in office.text
    await _login_admin(app_client)
    for e, f, p in ((e1, 60, 20), (e2, 80, 40)):
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{final_oa}", json={"obtained_marks": f})).status_code == 201
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{prac_oa}", json={"obtained_marks": p})).status_code == 201

    sheet = (await app_client.get(f"{R}/sheets/{offering}")).json()
    rows = {r["enrollment_id"]: r for r in sheet["rows"]}
    # student 1: sessional 16/20 -> 16, mid 21/30 -> 21, final 60/100 -> 30: theory 67%; practical 20/50 = 40%: failed practical
    r1 = rows[e1]
    assert r1["theory_percent"] == 67.0 and r1["practical_percent"] == 40.0 and r1["failed_parts"] == ["PRACTICAL"]
    assert r1["percentage"] == 58.0 and r1["outcome"] == "FAILED" and r1["grade"] == "F"
    # student 2: 18 + 27 + 40 = 85% theory, 80% practical, overall (85+40)/150 = 83.33% -> A
    r2 = rows[e2]
    assert r2["outcome"] == "PASSED" and r2["grade"] == "A" and r2["failed_parts"] == []

    # a theory-only subject: its own split overrides the program's
    course_id = (await app_client.get(f"{A}/offerings/{offering}")).json()["course_id"]
    assert (await app_client.put(f"{R}/mark-splits", json={"course_id": course_id, "values": {"SESSIONAL": 20, "MID": 30, "FINAL": 50}})).status_code == 200
    r1 = {r["enrollment_id"]: r for r in (await app_client.get(f"{R}/sheets/{offering}")).json()["rows"]}[e1]
    assert r1["practical_percent"] is None and r1["percentage"] == 67.0 and r1["outcome"] == "PASSED" and r1["grade"] == "B"

    # published results are frozen with their table, parts and division; editing the table later changes nothing
    await _publish_results(app_client, offering)
    frozen = (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == uuid.UUID(e1)))).scalar_one()
    assert frozen.scheme_name == "Test Univ" and frozen.letter_grade == "B" and float(frozen.theory_percent) == 67.0 and frozen.division == "1st Division"
    harsher = _table(pass_percent=70, bands=[{"letter": "A", "min_percent": 90, "grade_points": 4, "is_passing": True},
                                             {"letter": "F", "min_percent": 0, "grade_points": 0, "is_passing": False}])
    assert (await app_client.put(f"{R}/grading-tables/{table['id']}", json=harsher)).status_code == 200
    db_session.expire_all()
    again = (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == uuid.UUID(e1)))).scalar_one()
    assert again.letter_grade == "B" and again.outcome == "PASSED"
