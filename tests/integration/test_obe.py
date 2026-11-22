"""Outcome-based education (app/obe): PLOs per program with pass levels, a teacher's CLOs mapped to PLOs, assessments
tagged whole or by question part, part marks, submit / send back / lock, and CLO and PLO attainment from the marks."""
from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from app.students.models import Student
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_result_sheets import _as_teacher, _class, _fill

pytestmark = pytest.mark.asyncio
O = "/api/v1/obe"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_outcomes_from_plos_to_attainment(app_client, admin_user, db_session):
    offering, students, (quiz, final), teacher = await _class(app_client, db_session, code="OBE", n=3)
    await _fill(app_client, students, (quiz, final), scores=((18, 70), (10, 20), (15, 60)))
    program_id = (await db_session.get(Student, uuid.UUID(students[0][0]))).program_id

    # the exam office: PLOs and pass levels
    await _login_admin(app_client)
    assert (await app_client.put(f"{O}/programs/{program_id}", json={"plos": [{"code": "PLO1", "title": "x"}]})).status_code == 422
    r = await app_client.put(f"{O}/programs/{program_id}", json={"plos": [{"code": "plo1", "title": "Apply nursing knowledge"},
                                                                       {"code": "PLO2", "title": "Communicate with patients"}],
                                                              "student_pct": 50, "batch_pct": 60})
    assert r.status_code == 200, r.text
    plo1, plo2 = [p["id"] for p in r.json()["plos"]]
    assert [p["code"] for p in r.json()["plos"]] == ["PLO1", "PLO2"]

    # the teacher: CLOs, mapping, tags
    await _as_teacher(app_client, teacher)
    cf = (await app_client.get(f"{O}/offerings/{offering}")).json()
    assert cf["status"] == "DRAFT" and len(cf["plos"]) == 2 and len(cf["assessments"]) == 2
    bad = await app_client.put(f"{O}/offerings/{offering}/clos", json={"clos": [{"code": "CLO1", "text": "Explain care plans", "bloom": "Z9"}]})
    assert bad.status_code == 422
    cf = (await app_client.put(f"{O}/offerings/{offering}/clos", json={"clos": [
        {"code": "CLO1", "text": "Explain care plans", "bloom": "C2", "plo_ids": [plo1]},
        {"code": "CLO2", "text": "Talk to a patient about medicines", "bloom": "C3", "plo_ids": [plo2]}]})).json()
    clo1, clo2 = [c["id"] for c in cf["clos"]]
    early = await app_client.post(f"{O}/offerings/{offering}/submit")
    assert early.status_code == 422 and "No assessment measures" in early.text
    assert (await app_client.put(f"{O}/offerings/{offering}/assessments/{quiz}/items", json={"items": [{"max_marks": 25, "clo_id": clo1}]})).status_code == 422
    mixed = await app_client.put(f"{O}/offerings/{offering}/assessments/{final}/items", json={"items": [{"max_marks": 40, "clo_id": clo1},
                                                                                                         {"label": "Q2", "max_marks": 60, "clo_id": clo2}]})
    assert mixed.status_code == 422
    assert (await app_client.put(f"{O}/offerings/{offering}/assessments/{quiz}/items", json={"items": [{"max_marks": 20, "clo_id": clo1}]})).status_code == 200
    cf = (await app_client.put(f"{O}/offerings/{offering}/assessments/{final}/items", json={"items": [
        {"label": "Q1", "max_marks": 40, "clo_id": clo1}, {"label": "Q2", "max_marks": 60, "clo_id": clo2}]})).json()
    fa = next(a for a in cf["assessments"] if a["id"] == final)
    assert fa["parts"] is True and [i["label"] for i in fa["items"]] == ["Q1", "Q2"]

    # question-part marks
    grid = (await app_client.get(f"{O}/offerings/{offering}/assessments/{final}/marks")).json()
    q1, q2 = [p["id"] for p in grid["parts"]]
    by_roll = {s["enrollment_id"]: s for s in grid["students"]}
    order = [st[2] for st in students]                                    # enrollment ids, in the _fill order
    assert set(order) == set(by_roll)
    too_many = await app_client.put(f"{O}/offerings/{offering}/assessments/{final}/marks",
                                    json={"entries": [{"item_id": q1, "enrollment_id": order[0], "obtained": 41}]})
    assert too_many.status_code == 422
    entries = []
    for e, (a, b) in zip(order, ((30, 40), (10, 10), (25, 25))):
        entries += [{"item_id": q1, "enrollment_id": e, "obtained": a}, {"item_id": q2, "enrollment_id": e, "obtained": b}]
    assert (await app_client.put(f"{O}/offerings/{offering}/assessments/{final}/marks", json={"entries": entries})).json()["saved"] == 6

    # submit, send back with a note, submit again, lock
    assert (await app_client.post(f"{O}/offerings/{offering}/submit")).json()["status"] == "SUBMITTED"
    assert (await app_client.put(f"{O}/offerings/{offering}/clos", json={"clos": []})).status_code == 422            # submitted: fixed
    assert (await app_client.post(f"{O}/offerings/{offering}/decide", json={"lock": True})).status_code == 403        # teachers cannot lock
    await _login_admin(app_client)
    files = (await app_client.get(f"{O}/files", params={"status": "SUBMITTED"})).json()
    assert any(f["offering_id"] == offering for f in files)
    assert (await app_client.post(f"{O}/offerings/{offering}/decide", json={"lock": False})).status_code == 422       # a note is needed
    assert (await app_client.post(f"{O}/offerings/{offering}/decide", json={"lock": False, "note": "Add a Bloom level to CLO2"})).status_code == 200
    await _as_teacher(app_client, teacher)
    assert (await app_client.get(f"{O}/offerings/{offering}")).json()["note"] == "Add a Bloom level to CLO2"
    assert (await app_client.post(f"{O}/offerings/{offering}/submit")).status_code == 200
    await _login_admin(app_client)
    assert (await app_client.post(f"{O}/offerings/{offering}/decide", json={"lock": True})).json()["status"] == "LOCKED"
    gone = await app_client.put(f"{O}/programs/{program_id}", json={"plos": [{"id": plo1, "code": "PLO1", "title": "Apply nursing knowledge"}]})
    assert gone.status_code == 422 and "locked" in gone.text                                                         # PLO2 is used by a locked file

    # attainment: CLO1 = quiz (/20) + Q1 (/40); CLO2 = Q2 (/60)
    att = (await app_client.get(f"{O}/offerings/{offering}/attainment")).json()
    c1 = next(c for c in att["clos"] if c["code"] == "CLO1")
    c2 = next(c for c in att["clos"] if c["code"] == "CLO2")
    assert (c1["assessed"], c1["reached"], c1["batch_pct"], c1["attained"]) == (3, 2, 66.7, True)          # 80%, 33.3%, 66.7%
    assert (c2["reached"], c2["attained"]) == (1, False)                                                    # 66.7%, 16.7%, 41.7%
    assert c1["average"] == round((80 + 100 * 20 / 60 + 100 * 40 / 60) / 3, 1)
    rep = (await app_client.get(f"{O}/programs/{program_id}/report")).json()
    p = {x["code"]: x for x in rep["plos"]}
    assert p["PLO1"]["attained"] is True and p["PLO2"]["attained"] is False and rep["classes"] >= 1
    for page in ("/dashboard/admin/obe", "/dashboard/exam/obe"):
        html = (await app_client.get(page)).text
        assert "obe-office.js" in html and "obe-course-file.js" in html
    await _as_teacher(app_client, teacher)
    mine = (await app_client.get(f"{O}/my-classes")).json()
    assert [m["offering_id"] for m in mine] == [offering] and mine[0]["status"] == "LOCKED"
    assert (await app_client.get(f"{O}/programs/{program_id}")).status_code == 403                         # PLOs: exam office only
