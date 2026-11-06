"""Teacher workload in credit hours, and student lists / counts by program, semester and section."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_attendance_and_grading import _setup_offering_with_enrolled_students
from tests.integration.test_teaching import _class_with_teacher, _employee

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def test_teacher_workload_counts_every_section_in_credit_hours(app_client, admin_user, db_session):
    off1, studs, teacher, _ = await _class_with_teacher(app_client, db_session, "WL1", n_students=3)
    off2, _s2 = await _setup_offering_with_enrolled_students(app_client, code="WL2", n_students=2, cnic_base=5300)
    assert (await app_client.patch(f"/api/v1/academics/offerings/{off2}", json={"assigned_teacher_id": teacher["id"]})).status_code == 200
    r = await app_client.get("/api/v1/report-center/run/academics.teacher_workload")
    assert r.status_code == 200, r.text
    code = (await app_client.get(f"/api/v1/employees/{teacher['id']}")).json()["employee_code"]
    row = next(x for x in r.json()["rows"] if x["code"] == code)
    ch1 = (await app_client.get(f"/api/v1/academics/offerings/{off1}")).json()["credit_hours"]
    ch2 = (await app_client.get(f"/api/v1/academics/offerings/{off2}")).json()["credit_hours"]
    assert (row["sections"], row["credit_hours"], row["students"]) == (2, ch1 + ch2, 5)
    assert "WL1" in row["programs"] and "WL2" in row["programs"] and row["weekly"] >= 1      # only WL1 has a timetable slot

    # one program only, and one line per section
    prog = (await app_client.get("/api/v1/programs")).json()
    wl2 = next(p["id"] for p in prog if p["code"] == "WL2")
    only = (await app_client.get("/api/v1/report-center/run/academics.teacher_workload", params={"program_id": wl2})).json()
    mine = [x for x in only["rows"] if x["teacher"].startswith("WL1A")]
    assert len(mine) == 1 and mine[0]["sections"] == 1 and mine[0]["credit_hours"] == ch2
    detail = (await app_client.get("/api/v1/report-center/run/academics.teacher_workload", params={"view": "section"})).json()
    assert sum(1 for x in detail["rows"] if x["teacher"].startswith("WL1A")) == 2
    csv = await app_client.get("/api/v1/report-center/export/academics.teacher_workload")
    assert csv.status_code == 200 and "Credit hours" in csv.text


async def test_student_list_by_program_semester_and_section_with_count(app_client, admin_user, db_session):
    off, studs = await _setup_offering_with_enrolled_students(app_client, code="CL1", n_students=3, cnic_base=5400)
    prog = next(p["id"] for p in (await app_client.get("/api/v1/programs")).json() if p["code"] == "CL1")
    # move one student to semester 2, section B
    sid = studs[0][0]
    assert (await app_client.patch(f"/api/v1/students/{sid}", json={"current_semester": 2, "section_label": "B"})).status_code == 200
    sem1 = (await app_client.get("/api/v1/students", params={"program_id": prog, "semester": 1})).json()
    sem2 = (await app_client.get("/api/v1/students", params={"program_id": prog, "semester": 2})).json()
    assert sem1["total"] == 2 and sem2["total"] == 1 and sem2["items"][0]["id"] == sid
    b = (await app_client.get("/api/v1/students", params={"program_id": prog, "section": "b"})).json()
    assert b["total"] == 1
    csv = await app_client.get("/api/v1/students/export", params={"program_id": prog, "semester": 1})
    assert csv.status_code == 200 and csv.text.strip().count("\n") == 2                       # header + 2 students
    over = next(p for p in (await app_client.get("/api/v1/programs/overview")).json() if p["code"] == "CL1")
    assert {x["semester"]: x["students"] for x in over["active_by_semester"]} == {1: 2, 2: 1}