"""Semester rollover wizard (app/academics/rollover.py): close the terms, promote (hold back, carry-over, undo within 7 days,
fees kept), start the next session with the same teachers, and copy fee structures as drafts. Each step previewed first."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.academics.models import CourseOffering, Enrollment, EnrollmentOutcome, EnrollmentStatus
from app.academics.rollover import RolloverRun
from app.academics.terms import AcademicTerm, TermStatus
from app.fees.models import FeeStructure, FeeStructureSemester, FeeStructureStatus
from app.students.models import Student
from tests.integration.test_academic_sessions import S, _setup, _students
from tests.integration.test_academics import _create_teacher

pytestmark = pytest.mark.asyncio
R = "/api/v1/rollover"


async def _session(client, db):
    progs, courses, t = await _setup(client, db)
    s = (await client.get(f"{S}/suggest")).json()
    fall = next(g for g in s["suggestions"] if g["name"] == "Fall 2026")
    await client.post(f"{S}/suggest/save", json={"items": [{"name": "Fall 2026", "term_ids": [x["id"] for x in fall["terms"]],
                                                            "batches": {x["id"]: x["batch_guess"] for x in fall["terms"] if x["batch_guess"]}}]})
    sid = next(x for x in (await client.get(S)).json()["sessions"] if x["name"] == "Fall 2026")["id"]
    return progs, courses, t, sid


async def test_the_four_steps(app_client, admin_user, db_session):
    progs, courses, t, sid = await _session(app_client, db_session)
    six = await _students(app_client, db_session, progs["bsn"], 6, ["A", "A", "B"])
    eight = await _students(app_client, db_session, progs["bsn"], 8, ["A"])
    # one student failed a 6th-semester subject (carry-over)
    off = (await db_session.execute(select(CourseOffering).where(CourseOffering.term_label == "BSN 6th Fall 2026", CourseOffering.section_label == "A"))).scalars().first()
    db_session.add(Enrollment(student_id=six[0], course_offering_id=off.id, status=EnrollmentStatus.ENROLLED, outcome=EnrollmentOutcome.FAILED,
                              enrolled_at=datetime.now(timezone.utc)))
    await db_session.commit()
    # a teacher who taught N701 before (for "same teachers")
    teacher_id, _ = await _create_teacher(app_client, db_session, code="ROTCH", cnic="61101-7777777-1")
    old = await app_client.post("/api/v1/academics/offerings", json={"course_id": courses["N701"], "term_label": "BSN 5th Spring 2026", "section_label": "A",
                                                                     "assigned_teacher_id": teacher_id})
    assert old.status_code == 201, old.text

    # ---- 1. close
    pv = (await app_client.get(f"{R}/{sid}/close")).json()
    row = next(r for r in pv["terms"] if r["label"] == "BSN 6th Fall 2026")
    assert row["status"] == "OPEN" and row["subjects"] == 4 and row["enrollments_open"] == 1
    closed = await app_client.post(f"{R}/{sid}/close", json={"term_ids": [row["term_id"]], "reason": "Results come from the university later"})
    assert closed.status_code == 200, closed.text
    db_session.expire_all()
    assert (await db_session.get(AcademicTerm, uuid.UUID(row["term_id"]))).status == TermStatus.CLOSED
    assert (await db_session.execute(select(Enrollment.status).where(Enrollment.student_id == six[0]))).scalar_one() == EnrollmentStatus.COMPLETED
    other = await app_client.post(f"{R}/{sid}/close", json={"term_ids": [str(uuid.uuid4())]})
    assert other.status_code == 422                                                   # not this session's term

    # ---- 2. promote: preview, hold one back, carry-over listed, last semester not promoted
    pp = (await app_client.get(f"{R}/{sid}/promote")).json()
    b6 = next(b for b in pp["batches"] if b["label"] == "BSN 6th Fall 2026")
    b8 = next(b for b in pp["batches"] if b["label"] == "BSN 8th Fall 2026")
    assert b6["status"] == "PROMOTE" and len(b6["students"]) == 3 and b8["status"] == "LAST"
    assert next(s for s in b6["students"] if s["id"] == str(six[0]))["carry_over"] == ["N601"]
    assert (await app_client.post(f"{R}/{sid}/promote", json={"hold": [{"student_id": str(six[2]), "reason": "x"}]})).status_code == 422
    go = await app_client.post(f"{R}/{sid}/promote", json={"hold": [{"student_id": str(six[2]), "reason": "Repeating the year"}]})
    assert go.status_code == 200, go.text
    assert go.json()["promoted"] == 2 and [h["roll_number"] for h in go.json()["held"]] and go.json()["carry_over"][0]["carry_over"] == ["N601"]
    db_session.expire_all()
    sems = {i: (await db_session.get(Student, i)).current_semester for i in six + eight}
    assert (sems[six[0]], sems[six[1]], sems[six[2]], sems[eight[0]]) == (7, 7, 6, 8)
    again = await app_client.post(f"{R}/{sid}/promote", json={})
    assert again.status_code == 422 and "already promoted" in again.text

    # undo: puts back everyone not changed since; a student moved by hand stays
    (await db_session.get(Student, six[1])).current_semester = 5
    await db_session.commit()
    run_id = go.json()["run"]["id"]
    undo = await app_client.post(f"{R}/runs/{run_id}/undo")
    assert undo.status_code == 200, undo.text
    assert undo.json()["put_back"] == 1 and len(undo.json()["left_as_is"]) == 1
    assert (await app_client.post(f"{R}/runs/{run_id}/undo")).status_code == 422
    db_session.expire_all()
    assert (await db_session.get(Student, six[0])).current_semester == 6
    # older than 7 days: refused
    (await db_session.get(Student, six[1])).current_semester = 6
    await db_session.commit()
    go2 = await app_client.post(f"{R}/{sid}/promote", json={})
    assert go2.status_code == 200 and go2.json()["promoted"] == 3
    run = await db_session.get(RolloverRun, uuid.UUID(go2.json()["run"]["id"]))
    run.created_at = datetime.now(timezone.utc) - timedelta(days=8)
    await db_session.commit()
    late = await app_client.post(f"{R}/runs/{run.id}/undo")
    assert late.status_code == 422 and "7 days" in late.text
    ov = (await app_client.get(f"{R}/{sid}")).json()
    assert len(ov["promotions"]) == 2 and ov["promotions"][0]["can_undo"] is False

    # ---- 3. next session, same teachers
    nx = (await app_client.get(f"{R}/{sid}/next", params={"new_name": "Spring 2027"})).json()
    assert next(r for r in nx["rows"] if r["label"] == "BSN 6th Fall 2026")["promoted"] == 3
    started = await app_client.post(f"{R}/{sid}/next", json={"new_name": "Spring 2027", "start_date": "2027-02-01", "end_date": "2027-06-30"})
    assert started.status_code == 200, started.text
    assert started.json()["teachers_set"] == 2                                       # N701 in sections A and B
    n701 = (await db_session.execute(select(CourseOffering).where(CourseOffering.term_label == "BSN 7th Spring 2027",
                                                                  CourseOffering.course_id == uuid.UUID(courses["N701"])))).scalars().all()
    assert {str(o.assigned_teacher_id) for o in n701} == {teacher_id}

    # ---- 4. fees: active structures of the batch term copied as drafts for the next semester
    ft = (await app_client.post("/api/v1/fees/types", json={"code": "ROT", "name": "Tuition"})).json()["id"]
    fs = await app_client.post("/api/v1/fees/structures", json={"fee_type_id": ft, "name": "BSN 6th tuition", "term_label": "BSN 6th Fall 2026",
                                                                "total_amount": 50000, "installment_count": 1, "effective_from": "2026-09-01",
                                                                "program_ids": [progs["bsn"]], "semester_numbers": [6]})
    assert fs.status_code == 201, fs.text
    db_session.expire_all()
    (await db_session.get(FeeStructure, uuid.UUID(fs.json()["id"]))).status = FeeStructureStatus.ACTIVE   # active (no charging needed here)
    await db_session.commit()
    fp = (await app_client.get(f"{R}/{sid}/fees", params={"new_name": "Spring 2027"})).json()
    mine = [x for x in fp["structures"] if x["structure_id"] == fs.json()["id"]]
    assert mine and mine[0]["status"] == "COPY" and mine[0]["to_term"] == "BSN 7th Spring 2027" and mine[0]["to_semester"] == 7
    cp = await app_client.post(f"{R}/{sid}/fees", json={"new_name": "Spring 2027", "percentage_change": 10, "effective_from": "2027-02-15"})
    assert cp.status_code == 200, cp.text
    draft = next(d for d in cp.json()["drafts"] if d["term"] == "BSN 7th Spring 2027")
    assert draft["amount"] == 55000
    db_session.expire_all()
    new = await db_session.get(FeeStructure, uuid.UUID(draft["id"]))
    assert new.status == FeeStructureStatus.DRAFT
    assert [x for x, in (await db_session.execute(select(FeeStructureSemester.semester_number).where(FeeStructureSemester.fee_structure_id == new.id))).all()] == [7]
    assert next(x for x in (await app_client.get(f"{R}/{sid}/fees", params={"new_name": "Spring 2027"})).json()["structures"]
                if x["structure_id"] == fs.json()["id"])["status"] == "EXISTS"
    page = (await app_client.get("/dashboard/admin/rollover")).text
    assert "rollover.js" in page and 'data-nav="rollover"' in page


async def test_only_administrators(app_client, admin_user, db_session):
    from tests.integration.test_attendance_and_grading import _login_admin
    from tests.integration.test_fees import _create_accounts_officer

    await _login_admin(app_client)
    await _create_accounts_officer(app_client, db_session, code="ROACC")
    assert (await app_client.get(f"{R}/{uuid.uuid4()}/close")).status_code == 403
