"""A datesheet for a whole session (papers from every batch term in it), and the session's report."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.academics.terms import AcademicTerm
from app.core.config import settings
from app.exams.models import ExamSchedule
from tests.integration.test_attendance_and_grading import _login_admin

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"
S = "/api/v1/academics/sessions"
E = "/api/v1/exams"


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _setup(client):
    await _login_admin(client)
    prog = (await client.post("/api/v1/programs", json={"code": "DSN", "name": "BS DS Nursing", "duration_years": 4, "total_semesters": 8})).json()["id"]
    offs = {}
    for label, sem in (("DSN 2nd Fall 2026", 2), ("DSN 4th Fall 2026", 4), ("DSN 3rd Spring 2026", 3)):
        start, end = ("2026-02-01", "2026-06-30") if "Spring" in label else ("2026-09-01", "2027-01-15")
        assert (await client.post(f"{A}/academic-terms", json={"label": label, "start_date": start, "end_date": end,
                                                               "program_id": prog, "semester": sem})).status_code == 201
        cid = (await client.post(f"{A}/courses", json={"code": f"D{sem}01", "title": f"Course {sem}", "credit_hours": 3})).json()["id"]
        r = await client.post(f"{A}/offerings", json={"course_id": cid, "term_label": label, "section_label": "A"})
        assert r.status_code == 201, r.text
        offs[label] = r.json()["id"]
    s = (await client.get(f"{S}/suggest")).json()
    items = [{"name": g["name"], "term_ids": [x["id"] for x in g["terms"] if not x.get("by_dates")]} for g in s["suggestions"]]
    assert (await client.post(f"{S}/suggest/save", json={"items": items})).status_code == 200
    sessions = {x["name"]: x["id"] for x in (await client.get(S)).json()["sessions"]}
    return offs, sessions


async def test_one_datesheet_for_a_whole_session(app_client, admin_user, db_session):
    offs, sessions = await _setup(app_client)
    r = await app_client.post(f"{E}/schedules", json={"session_id": sessions["Fall 2026"], "name": "Final"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    d = (await app_client.get(f"{E}/schedules/{sid}")).json()
    assert d["term_label"] == "Fall 2026" and d["session_id"] == sessions["Fall 2026"]
    assert d["terms"] == ["DSN 2nd Fall 2026", "DSN 4th Fall 2026"]
    # papers from both batches of the session; not from a term outside it
    for label, day in (("DSN 2nd Fall 2026", "2027-01-05"), ("DSN 4th Fall 2026", "2027-01-06")):
        p = await app_client.post(f"{E}/schedules/{sid}/papers", json={"course_offering_id": offs[label], "exam_date": day,
                                                                       "start_time": "09:00", "end_time": "12:00", "room": "Hall"})
        assert p.status_code in (200, 201), p.text
    out = await app_client.post(f"{E}/schedules/{sid}/papers", json={"course_offering_id": offs["DSN 3rd Spring 2026"], "exam_date": "2026-06-10",
                                                                     "start_time": "09:00", "end_time": "12:00", "room": "Hall"})
    assert out.status_code == 422 and "not in the Fall 2026 session" in out.text
    assert len((await app_client.get(f"{E}/schedules/{sid}")).json()["papers"]) == 2

    # renaming a TERM never touches the session's datesheet; renaming the SESSION does
    t = (await db_session.execute(select(AcademicTerm).where(AcademicTerm.label == "DSN 2nd Fall 2026"))).scalar_one()
    assert (await app_client.patch(f"{A}/academic-terms/{t.id}", json={"label": "DSN 2nd Fall-26"})).status_code == 200
    assert (await db_session.get(ExamSchedule, sid)).term_label == "Fall 2026"
    assert (await app_client.patch(f"{S}/{sessions['Fall 2026']}", json={"name": "Fall 2026 (Main)"})).status_code == 200
    sched = await db_session.get(ExamSchedule, sid)
    await db_session.refresh(sched)
    assert sched.term_label == "Fall 2026 (Main)"
    # a session with its own datesheets cannot be removed
    assert (await app_client.delete(f"{S}/{sessions['Fall 2026']}")).status_code == 422
    # a per-term datesheet still works as before
    assert (await app_client.post(f"{E}/schedules", json={"term_label": "DSN 4th Fall 2026", "name": "Midterm"})).status_code == 201


async def test_session_report(app_client, admin_user, db_session):
    import uuid as _uuid

    from sqlalchemy import update

    from app.academics.terms import ALL_BATCHES
    from app.programs.models import Program
    from app.students.models import Student
    from tests.integration.test_attendance_and_grading import _create_and_activate_student

    a, _ = await _create_and_activate_student(app_client, cnic="76501-0000001-1")
    b, _ = await _create_and_activate_student(app_client, cnic="76501-0000002-1")
    _offs, sessions = await _setup(app_client)
    prog = (await db_session.execute(select(Program).where(Program.code == "DSN"))).scalar_one()
    await db_session.execute(update(Student).where(Student.id == _uuid.UUID(a)).values(program_id=prog.id, current_semester=2))
    await db_session.execute(update(Student).where(Student.id == _uuid.UUID(b)).values(program_id=prog.id, current_semester=4))
    await db_session.commit()
    await _login_admin(app_client)
    fee_type = (await app_client.post("/api/v1/fees/types", json={"code": "STU", "name": "Session Tuition"})).json()["id"]
    r = await app_client.post("/api/v1/fees/structures", json={"fee_type_id": fee_type, "total_amount": 1000, "installment_count": 1,
                                                              "effective_from": "2026-11-01", "name": "Tuition", "term_label": ALL_BATCHES})
    assert r.status_code == 201, r.text
    assert (await app_client.post(f"/api/v1/fees/structures/{r.json()['id']}/activate")).status_code == 200
    r = await app_client.get(f"{S}/{sessions['Fall 2026']}/report")
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["term"] for x in d["terms"]] == ["DSN 2nd Fall 2026", "DSN 4th Fall 2026"]
    row = d["terms"][0]
    assert row["program"] == "DSN" and row["semester"] == 2 and row["courses"] == 1 and row["sections"] == 1
    assert row["results"]["DRAFT"] == 1 and row["students"] == 0
    # each student's fee counts under their own batch term
    assert [x["fees"]["charged"] for x in d["terms"]] == [1000, 1000] and [x["fees"]["remaining"] for x in d["terms"]] == [1000, 1000]
    assert d["totals"]["courses"] == 2 and d["totals"]["terms"] == 2 and d["totals"]["fees"]["charged"] == 2000
