"""Grading part 2: re-sit only the failed part (or both papers), the class result / promotion list with undo, and the
award list, result cards and gazette printed with the affiliating body."""
from __future__ import annotations

import io
import uuid

import pytest
from sqlalchemy import select

from app.academics.models import EnrollmentResult
from app.core.config import settings
from app.students.models import Student
from tests.integration.test_attendance_and_grading import _get_or_create_assessment_type, _login_admin
from tests.integration.test_grading_tables import _table
from tests.integration.test_result_sheets import _as_teacher, _class

pytestmark = pytest.mark.asyncio
R = "/api/v1/results"
A = "/api/v1/academics"
S = "/api/v1/supplementary"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


async def test_resit_failed_part_class_results_promotion_and_documents(app_client, admin_user, db_session):
    offering, students, (quiz_oa, final_oa), teacher = await _class(app_client, db_session, code="GP2", n=3, cnic_base=4400)
    await _login_admin(app_client)
    program_id = (await db_session.get(Student, uuid.UUID(students[0][0]))).program_id
    table = (await app_client.post(f"{R}/grading-tables", json=_table("Part2 Univ", min_gpa_promotion=2.0, probation_gpa=1.5))).json()
    await app_client.put(f"{R}/programs/{program_id}/grading-table", json={"scheme_id": table["id"]})
    types = {t["name"]: t["id"] for t in (await app_client.get(f"{A}/assessment-types")).json()}
    await app_client.put(f"{A}/assessment-types/{types['GP2 Final']}/component", json={"component": "FINAL"})
    mid = await _get_or_create_assessment_type(app_client, "GP2 Mid", 30)
    await app_client.put(f"{A}/assessment-types/{mid}/component", json={"component": "MID"})
    prac = (await app_client.post(f"{A}/assessment-types", json={"name": "GP2 Practical", "max_marks": 50, "component": "PRACTICAL"})).json()["id"]
    mid_oa = (await app_client.post(f"{A}/offerings/{offering}/assessments", json={"assessment_type_id": mid})).json()["id"]
    prac_oa = (await app_client.post(f"{A}/offerings/{offering}/assessments", json={"assessment_type_id": prac})).json()["id"]
    await app_client.put(f"{R}/mark-splits", json={"program_id": str(program_id), "values": {"SESSIONAL": 20, "MID": 30, "FINAL": 50, "PRACTICAL": 50}})

    # s1 fails practical only, s2 fails both, s3 passes everything
    marks = {students[0][2]: (16, 21, 60, 20), students[1][2]: (4, 6, 20, 10), students[2][2]: (18, 27, 80, 45)}
    await _as_teacher(app_client, teacher)
    for e, (q, m, _f, _p) in marks.items():
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{quiz_oa}", json={"obtained_marks": q})).status_code == 201
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{mid_oa}", json={"obtained_marks": m})).status_code == 201
    await _login_admin(app_client)
    for e, (_q, _m, f, p) in marks.items():
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{final_oa}", json={"obtained_marks": f})).status_code == 201
        assert (await app_client.post(f"{A}/enrollments/{e}/marks/{prac_oa}", json={"obtained_marks": p})).status_code == 201

    # award list (before publishing: a projection) carries the affiliating body
    award = await app_client.get(f"{R}/sheets/{offering}/award-list.pdf")
    assert award.status_code == 200
    text = _text(award.content)
    assert "AWARD LIST" in text and "Affiliated with Test University" in text and "Fail (Practical)" in text

    await _as_teacher(app_client, teacher)
    assert (await app_client.post(f"{R}/offerings/{offering}/submit")).status_code == 200
    await _login_admin(app_client)
    assert (await app_client.post(f"{R}/sheets/{offering}/approve")).status_code == 200
    assert (await app_client.post(f"{R}/sheets/{offering}/publish")).status_code == 200
    term = (await app_client.get(f"{A}/offerings/{offering}")).json()["term_label"]
    semester = (await db_session.get(Student, uuid.UUID(students[0][0]))).current_semester

    # class results: GPA and status from the table's limits; position holder; who must clear what
    cls = (await app_client.get(f"{R}/class", params={"program_id": str(program_id), "semester": semester, "term_label": term})).json()
    by = {r["student_id"]: r for r in cls["rows"]}
    s1, s2, s3 = (by[st[0]] for st in students)
    assert s1["failed"] == ["GP2-101 (Practical)"] or s1["failed"][0].endswith("(Practical)")
    assert s2["failed"][0].endswith("(Theory & Practical)")
    assert s3["status"] == "PROMOTED" and s3["position"] == 1 and s1["status"] == "NOT_PROMOTED" and s2["status"] == "NOT_PROMOTED"
    gaz = await app_client.get(f"{R}/class/gazette.pdf", params={"program_id": str(program_id), "semester": semester, "term_label": term})
    gtext = _text(gaz.content)
    assert "RESULT NOTIFICATION" in gtext and "Position holders" in gtext and "Affiliated with Test University" in gtext
    cards = await app_client.get(f"{R}/class/result-cards.pdf", params={"program_id": str(program_id), "semester": semester, "term_label": term})
    ctext = _text(cards.content)
    assert ctext.count("SEMESTER RESULT CARD") == 3 and "Fail (Practical)" in ctext

    # supplementary: s1 re-sits only the practical; s2 re-sits both papers
    elig = {x["enrollment_id"]: x for x in (await app_client.get(f"{S}/eligible", params={"term_label": term})).json()}
    assert elig[students[0][2]]["failed_part"] == "PRACTICAL" and elig[students[1][2]]["failed_part"] == "THEORY+PRACTICAL"
    assert (await app_client.post(f"{S}/register", json={"enrollment_ids": [students[0][2], students[1][2]]})).status_code == 201
    att = {x["enrollment_id"]: x for x in (await app_client.get(f"{S}/attempts", params={"term_label": term})).json()}
    one, two = att[students[0][2]], att[students[1][2]]
    assert one["component"] == "GP2 Practical" and one["second"] is None
    assert two["component"] == "GP2 Final" and two["second"]["component"] == "GP2 Practical"
    assert (await app_client.post(f"{S}/attempts/{two['id']}/mark", json={"obtained_marks": 90})).status_code == 422       # practical paper missing
    m2 = (await app_client.post(f"{S}/attempts/{two['id']}/mark", json={"obtained_marks": 90, "second_obtained_marks": 45})).json()
    assert m2["preview"]["outcome"] == "PASSED" and m2["preview"]["capped"] is True and m2["preview"]["percentage"] == 60
    m1 = (await app_client.post(f"{S}/attempts/{one['id']}/mark", json={"obtained_marks": 40})).json()
    assert m1["preview"]["outcome"] == "PASSED" and m1["preview"]["percentage"] == 60                                        # re-sit cap 60%
    assert (await app_client.post(f"{S}/publish", json={"attempt_ids": [one["id"], two["id"]]})).status_code == 200
    db_session.expire_all()
    r1 = (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == uuid.UUID(students[0][2])))).scalar_one()
    assert r1.outcome == "PASSED" and r1.failed_part is None and float(r1.percentage) == 60 and r1.supplementary

    # promotion: everyone now passes; promote, then undo
    cls = (await app_client.get(f"{R}/class", params={"program_id": str(program_id), "semester": semester, "term_label": term})).json()
    ids = [r["student_id"] for r in cls["rows"] if r["status"] in ("PROMOTED", "PROBATION")]
    assert len(ids) == 3
    pr = await app_client.post(f"{R}/class/promote", json={"program_id": str(program_id), "semester": semester, "student_ids": ids})
    assert pr.status_code == 200 and pr.json()["promoted"] == 3
    db_session.expire_all()
    assert (await db_session.get(Student, uuid.UUID(students[0][0]))).current_semester == semester + 1
    un = await app_client.post(f"{R}/class/promote/undo", json={"batch": pr.json()["batch"]})
    assert un.status_code == 200 and un.json()["moved_back"] == 3
    assert (await app_client.post(f"{R}/class/promote/undo", json={"batch": pr.json()["batch"]})).status_code in (409, 422)
    db_session.expire_all()
    assert (await db_session.get(Student, uuid.UUID(students[0][0]))).current_semester == semester
