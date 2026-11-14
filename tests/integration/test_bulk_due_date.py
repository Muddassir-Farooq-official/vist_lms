"""Moving one instalment's due date for only some students on a fee: one program, semester and/or section, with a
preview of how many students and late fines first. Administration only."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.audit.models import AuditLog
from app.core.clock import institute_today
from app.core.config import settings
from app.fees.models import FeeAssignment, FeeLedgerEntry, LedgerEntryType
from app.programs.models import Program
from app.students.models import Student
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login_admin
from tests.integration.test_fees import _create_active_fee_structure

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _due(db, sid) -> list:
    db.expire_all()
    return list((await db.execute(select(FeeLedgerEntry.due_date).join(FeeAssignment, FeeAssignment.id == FeeLedgerEntry.fee_assignment_id)
                                  .where(FeeAssignment.payer_id == sid, FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT)
                                  .order_by(FeeLedgerEntry.installment_number))).scalars())


async def test_a_due_date_moves_only_for_the_chosen_semester_and_section(app_client, admin_user, db_session):
    a, _ = await _create_and_activate_student(app_client, cnic="75301-0000001-1")
    b, _ = await _create_and_activate_student(app_client, cnic="75301-0000002-1")
    c, _ = await _create_and_activate_student(app_client, cnic="75301-0000003-1")
    bsn, bs = Program(code="BSN", name="BS Nursing", duration_years=4, total_semesters=8), Program(code="BSCS", name="BS CS", duration_years=4, total_semesters=8)
    db_session.add_all([bsn, bs])
    await db_session.flush()
    bsn_id, bs_id = bsn.id, bs.id
    await db_session.execute(update(Student).where(Student.id == uuid.UUID(a)).values(program_id=bsn_id, current_semester=6, section_label="A"))
    await db_session.execute(update(Student).where(Student.id == uuid.UUID(b)).values(program_id=bsn_id, current_semester=6, section_label="B"))
    await db_session.execute(update(Student).where(Student.id == uuid.UUID(c)).values(program_id=bs_id, current_semester=2, section_label="A"))
    await db_session.commit()
    await _login_admin(app_client)
    structure_id = await _create_active_fee_structure(app_client, total_amount=60000, installment_count=2)
    before = {s: await _due(db_session, uuid.UUID(s)) for s in (a, b, c)}
    new = (institute_today() + timedelta(days=45)).isoformat()
    url = f"/api/v1/fees/structures/{structure_id}/installments/2/due-date"
    program_id = str(bsn_id)

    # preview: how many, changes nothing
    p = await app_client.post(url, json={"due_date": new, "reason": "preview", "preview": True, "semester": 6})
    assert p.status_code == 200 and p.json()["students"] == 2 and p.json()["with_late_fine"] == 0
    assert (await app_client.post(url, json={"due_date": new, "reason": "preview", "preview": True, "semester": 6, "section": "a"})).json()["students"] == 1
    pr = await app_client.post(url, json={"due_date": new, "reason": "preview", "preview": True, "program_id": program_id})
    assert pr.status_code == 200 and pr.json()["students"] == 2, pr.text
    assert {s: await _due(db_session, uuid.UUID(s)) for s in (a, b, c)} == before

    # semester 6, section A only
    r = await app_client.post(url, json={"due_date": new, "reason": "6th semester A: exams moved", "semester": 6, "section": "A"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 1
    assert str((await _due(db_session, uuid.UUID(a)))[1]) == new
    assert (await _due(db_session, uuid.UUID(b))) == before[b] and (await _due(db_session, uuid.UUID(c))) == before[c]
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "fee.due_date_changed_for_all"))).scalars().one()
    assert log.after["only"] == {"program_id": None, "semester": 6, "section": "A"} and log.after["changed"] == 1

    # no filter: everyone else on the fee (a is already on that date)
    r2 = (await app_client.post(url, json={"due_date": new, "reason": "Everyone: term extended"})).json()
    assert r2["changed"] == 2 and r2["already_on_that_date"] == 1
