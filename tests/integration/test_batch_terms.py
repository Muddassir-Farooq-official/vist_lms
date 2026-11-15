"""Batches run on their own dates, so a term can belong to one batch (program + semester). A fee charged to several
batches is set to "each student's own term": every student's charge shows the term of their own batch."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select, update

from app.academics.terms import ALL_BATCHES, AcademicTerm
from app.audit.models import AuditLog
from app.core.config import settings
from app.fees.models import FeeAssignment, FeeStructure
from app.fees.service import FeeStructureService
from app.programs.models import Program
from app.students.models import Student
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _labels(db, structure_id) -> dict:
    db.expire_all()
    rows = (await db.execute(select(FeeAssignment).where(FeeAssignment.fee_structure_id == uuid.UUID(structure_id)))).scalars().all()
    return {str(a.payer_id): a.term_label for a in rows}


async def test_each_student_sees_their_own_batch_term(app_client, admin_user, db_session):
    six, _ = await _create_and_activate_student(app_client, cnic="76401-0000001-1")
    eight, _ = await _create_and_activate_student(app_client, cnic="76401-0000002-1")
    loose, _ = await _create_and_activate_student(app_client, cnic="76401-0000003-1")
    bsn = Program(code="BSN", name="BS Nursing", duration_years=4, total_semesters=8)
    db_session.add(bsn)
    await db_session.flush()
    bsn_id = bsn.id
    await db_session.execute(update(Student).where(Student.id == uuid.UUID(six)).values(program_id=bsn_id, current_semester=6))
    await db_session.execute(update(Student).where(Student.id == uuid.UUID(eight)).values(program_id=bsn_id, current_semester=8))
    await db_session.execute(update(Student).where(Student.id == uuid.UUID(loose)).values(program_id=None, current_semester=None))
    await db_session.commit()
    await _login_admin(app_client)

    # terms tied to their batch
    terms = "/api/v1/academics/academic-terms"
    t6 = await app_client.post(terms, json={"label": "Fall 2026 BSN 6TH", "start_date": "2026-10-01", "end_date": "2027-01-15",
                                            "program_id": str(bsn_id), "semester": 6})
    assert t6.status_code == 201, t6.text
    assert t6.json()["program_id"] == str(bsn_id) and t6.json()["semester"] == 6
    t8 = await app_client.post(terms, json={"label": "Fall 2026 BSN 8TH", "start_date": "2026-11-09", "end_date": "2027-01-31",
                                            "program_id": str(bsn_id), "semester": 8})
    assert t8.status_code == 201, t8.text
    assert (await app_client.post(terms, json={"label": "Fall 2026 X", "start_date": "2026-10-01", "end_date": "2027-01-15",
                                               "program_id": str(bsn_id)})).status_code in (400, 422)        # program without semester
    assert (await app_client.post(terms, json={"label": "all  batches", "start_date": "2026-10-01", "end_date": "2027-01-15"})).status_code in (400, 422)

    fee_type = (await app_client.post("/api/v1/fees/types", json={"code": "TUI", "name": "Tuition Fee"})).json()["id"]
    base = {"fee_type_id": fee_type, "total_amount": 1000, "installment_count": 1, "effective_from": "2026-11-01"}

    # one batch's term on a fee for every program is refused, with the way out
    bad = await app_client.post("/api/v1/fees/structures", json={**base, "name": "ID card", "term_label": "Fall 2026 BSN 6TH"})
    assert bad.status_code in (400, 409, 422), bad.text
    assert "Each student's own term" in bad.json()["error"]["message"]
    # ... but fine on a fee for exactly that batch
    ok = await app_client.post("/api/v1/fees/structures", json={**base, "name": "Lab", "term_label": "Fall 2026 BSN 6TH",
                                                                "program_ids": [str(bsn_id)], "semester_numbers": [6]})
    assert ok.status_code == 201, ok.text

    # each student's own term
    r = await app_client.post("/api/v1/fees/structures", json={**base, "name": "Tuition", "term_label": ALL_BATCHES})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert (await app_client.post(f"/api/v1/fees/structures/{sid}/activate")).status_code == 200
    assert await _labels(db_session, sid) == {six: "Fall 2026 BSN 6TH", eight: "Fall 2026 BSN 8TH", loose: None}
    listed = (await app_client.get("/api/v1/fees/assignments", params={"fee_structure_id": sid})).json()
    items = listed["items"] if isinstance(listed, dict) else listed
    shown = {i["payer_id"]: i["term_label"] for i in items}
    assert shown[eight] == "Fall 2026 BSN 8TH" and shown[loose] is None            # never the words "All batches"
    filtered = (await app_client.get("/api/v1/fees/assignments", params={"term_label": "Fall 2026 BSN 8TH"})).json()
    f_items = filtered["items"] if isinstance(filtered, dict) else filtered
    assert {i["payer_id"] for i in f_items} == {eight}

    # renaming a term renames it on the student charges too
    t8_id = t8.json()["id"]
    rn = await app_client.patch(f"{terms}/{t8_id}", json={"label": "Fall 2026 BSN 8th"})
    assert rn.status_code == 200, rn.text
    assert (await _labels(db_session, sid))[eight] == "Fall 2026 BSN 8th"

    # an existing fee with a wrong single label: switched to each student's own term, then back
    old = await app_client.post("/api/v1/fees/structures", json={**base, "name": "ID card old", "term_label": "Fall 2026"})
    old_id = old.json()["id"]
    assert (await app_client.post(f"/api/v1/fees/structures/{old_id}/activate")).status_code == 200
    ch = await app_client.post(f"/api/v1/fees/structures/{old_id}/term", json={"term_label": ALL_BATCHES})
    assert ch.status_code == 200, ch.text
    assert ch.json()["student_terms_filled"] == 2 and ch.json()["students_without_batch_term"] == 1
    assert await _labels(db_session, old_id) == {six: "Fall 2026 BSN 6TH", eight: "Fall 2026 BSN 8th", loose: None}
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "fee_structure.term_changed"))).scalars().one()
    assert log.before == {"term_label": "Fall 2026"} and log.after == {"term_label": ALL_BATCHES}
    # one batch's term cannot be put on it (it covers everyone)
    assert (await app_client.post(f"/api/v1/fees/structures/{old_id}/term", json={"term_label": "Fall 2026 BSN 6TH"})).status_code in (400, 409, 422)
    back = await app_client.post(f"/api/v1/fees/structures/{old_id}/term", json={"term_label": "Fall 2026"})
    assert back.status_code == 200 and back.json()["student_terms_cleared"] == 2
    assert set((await _labels(db_session, old_id)).values()) == {None}

    # the batch can be cleared on a term
    cl = await app_client.patch(f"{terms}/{t6.json()['id']}", json={"program_id": None, "semester": None})
    assert cl.status_code == 200 and cl.json()["program_id"] is None

    # a student promoted or admitted later: charged only while their own batch term runs
    db_session.expire_all()
    structure = await db_session.get(FeeStructure, uuid.UUID(sid))
    student8 = await db_session.get(Student, uuid.UUID(eight))
    svc = FeeStructureService(db_session)
    assert await svc._still_current(structure, student8) is True
    await db_session.execute(update(AcademicTerm).where(AcademicTerm.id == uuid.UUID(t8_id)).values(start_date=None, end_date=date(2026, 1, 1)))
    await db_session.flush()
    db_session.expire_all()
    assert await svc._still_current(await db_session.get(FeeStructure, uuid.UUID(sid)), await db_session.get(Student, uuid.UUID(eight))) is False
    await db_session.rollback()
