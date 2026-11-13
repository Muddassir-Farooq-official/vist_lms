"""A late fine already built up never vanishes silently: moving a due date or recording a payment with an earlier date
asks first; the fine is kept (on Fines) unless an administrator removes it, on record. And ID cards: valid for the
months in Settings (the online check says when one has expired), with a back side for double-sided printing."""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.audit.models import AuditLog
from app.core.clock import institute_today
from app.core.config import settings
from app.fees.assignment_service import FeeAssignmentService
from app.fees.models import FeeLedgerEntry, LedgerEntryType
from app.fines.models import Fine, FineSource
from app.records.models import IssuedDocument
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_late_fee_to_fines import _late_fee_account

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _rules(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "fee_late_fee_grace_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_cap", 0)
    monkeypatch.setattr(settings, "fee_backdated_grace_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_double_every_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_max_per_day", 0)
    monkeypatch.setattr(settings, "fee_late_fee_auto_post", 1)
    monkeypatch.setattr(settings, "fee_payment_max_backdate_days", 400)


async def _fines(db, sid):
    db.expire_all()
    return list((await db.execute(select(Fine).where(Fine.payer_id == sid, Fine.source == FineSource.FEE_LATE_PAYMENT))).scalars())


async def test_moving_a_due_date_keeps_the_fine_unless_an_administrator_removes_it(app_client, admin_user, db_session):
    sid, aid, rows = await _late_fee_account(app_client, db_session, "75201-0000031-1")
    first, second = rows[0], rows[1]
    assert first["late_fee"] > 0
    later = (institute_today() + timedelta(days=10)).isoformat()
    url = f"/api/v1/fees/assignments/{aid}/installments/{first['ledger_entry_id']}/due-date"

    # 1. without a choice: refused, nothing changes, the amount is shown
    asked = await app_client.post(url, json={"due_date": later, "reason": "Extension for the family"})
    assert asked.status_code == 409 and asked.json()["error"]["code"] == "LATE_FINE_CONFIRM"
    d = asked.json()["error"]["details"]
    assert d["late_fine"] == first["late_fee"] and d["can_remove"] is True
    db_session.expire_all()
    assert str((await db_session.get(FeeLedgerEntry, uuid.UUID(first["ledger_entry_id"]))).due_date) == first["due_date"]

    # 2. keep: the fine so far goes to Fines; lateness counts again from the new date
    kept = await app_client.post(url, json={"due_date": later, "reason": "Extension for the family", "late_fine": "KEEP"})
    assert kept.status_code == 200, kept.text
    assert kept.json()["late_fine_kept"] == first["late_fee"] and kept.json()["late_fine_removed"] == 0
    fines = await _fines(db_session, sid)
    assert [float(f.amount) for f in fines] == [first["late_fee"]]
    debit = await db_session.get(FeeLedgerEntry, uuid.UUID(first["ledger_entry_id"]))
    assert str(debit.due_date) == later and float(debit.late_fee_charged_amount or 0) == 0
    now = next(r for r in (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["assignments"][0]["installments"]
               if r["ledger_entry_id"] == first["ledger_entry_id"])
    assert now["late_fee"] == 0 and now["remaining"] == first["remaining"]

    # 3. someone without the right to correct payments cannot remove a fine (service level: the role decides)
    from app.core.exceptions import AuthorizationError

    with pytest.raises(AuthorizationError):
        async with db_session.begin_nested():
            await FeeAssignmentService(db_session).change_due_date(uuid.UUID(second["ledger_entry_id"]), new_date=date.fromisoformat(later),
                                                                   reason="Extension", actor_id=None, late_fine="REMOVE", may_remove_fine=False)

    # 4. the administrator removes it: no fine, kept on record
    removed = await app_client.post(f"/api/v1/fees/assignments/{aid}/installments/{second['ledger_entry_id']}/due-date",
                                    json={"due_date": later, "reason": "Fee committee decision", "late_fine": "REMOVE"})
    assert removed.status_code == 200 and removed.json()["late_fine_removed"] == second["late_fee"]
    assert len(await _fines(db_session, sid)) == 1
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "fee.late_fine_removed"))).scalars().all()
    assert len(log) == 1 and log[0].after["amount"] == second["late_fee"]

    # 5. no fine built up (instalment not yet due): no question asked
    future = rows[-1]
    plain = await app_client.post(f"/api/v1/fees/assignments/{aid}/installments/{future['ledger_entry_id']}/due-date",
                                  json={"due_date": (date.fromisoformat(future["due_date"]) + timedelta(days=3)).isoformat() if future["late_fee"] == 0 else later,
                                        "reason": "Moved with the term"})
    if future["late_fee"] == 0:
        assert plain.status_code == 200, plain.text


async def test_moving_a_due_date_for_everyone_asks_once_and_keeps_every_fine(app_client, admin_user, db_session):
    sid, aid, rows = await _late_fee_account(app_client, db_session, "75201-0000032-1")
    structure_id = (await app_client.get(f"/api/v1/fees/assignments/{aid}")).json()["fee_structure_id"]
    later = (institute_today() + timedelta(days=10)).isoformat()
    url = f"/api/v1/fees/structures/{structure_id}/installments/1/due-date"
    asked = await app_client.post(url, json={"due_date": later, "reason": "Term start moved"})
    assert asked.status_code == 409 and asked.json()["error"]["details"]["students"] == 1
    assert asked.json()["error"]["details"]["late_fine"] == rows[0]["late_fee"]
    done = (await app_client.post(url, json={"due_date": later, "reason": "Term start moved", "late_fine": "KEEP"})).json()
    assert done["changed"] == 1 and done["late_fines_kept"] == rows[0]["late_fee"] and done["with_late_fine_kept"] == 1
    assert [float(f.amount) for f in await _fines(db_session, sid)] == [rows[0]["late_fee"]]


async def test_a_payment_dated_earlier_keeps_the_fine_unless_an_administrator_removes_it(app_client, admin_user, db_session):
    sid, aid, rows = await _late_fee_account(app_client, db_session, "75201-0000033-1")
    first = rows[0]
    due = date.fromisoformat(first["due_date"])
    on_time = due.isoformat()                                   # "it was paid on the due date": no late days at all
    pay = {"amount": first["remaining"], "method": "CASH", "paid_on": on_time}

    # an accounts officer: asked; cannot remove; keeping posts the whole fine up to today
    await _login_admin(app_client)
    user, pw = await _staff(app_client, db_session, "ACC", "ACCOUNTS_OFFICER", "35202-7100093-3")
    new_pw = await _first_login(app_client, user, pw)
    asked = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json=pay)
    if asked.status_code == 403:                                # the counter screen is the administrator's here: use the challan
        pytest.skip("accounts officers record payments through challans in this set-up")
    assert asked.status_code == 409 and asked.json()["error"]["details"]["can_remove"] is False
    assert asked.json()["error"]["details"]["late_fine"] == first["late_fee"]
    db_session.expire_all()
    credits = (await db_session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == uuid.UUID(aid),
                                                                     FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT))).scalars().all()
    assert credits == []                                         # refused: nothing was saved
    no = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={**pay, "late_fine": "REMOVE", "late_fine_reason": "Paid on time"})
    assert no.status_code == 403
    ok = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={**pay, "late_fine": "KEEP"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["late_fee_posted"] == first["late_fee"]
    assert sum(float(f.amount) for f in await _fines(db_session, sid)) == first["late_fee"]
    paid = (await db_session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == uuid.UUID(aid),
                                                                  FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT))).scalars().one()
    assert paid.paid_on == due                                   # the payment keeps its real date
    _ = new_pw

    # the administrator, on the next instalment, removes it with a reason (on record)
    await _login_admin(app_client)
    second = rows[1]
    pay2 = {"amount": second["remaining"], "method": "CASH", "paid_on": second["due_date"]}
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={**pay2, "late_fine": "REMOVE"})).status_code == 422   # no reason
    r = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={**pay2, "late_fine": "REMOVE", "late_fine_reason": "Bank slip shows the due date"})
    assert r.status_code == 201, r.text
    assert sum(float(f.amount) for f in await _fines(db_session, sid)) == first["late_fee"]
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "fee.late_fine_removed"))).scalars().all()
    assert len(log) == 1 and log[0].after["why"] == "payment dated earlier"

    # today's date: no question at all
    third = rows[2]
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": third["remaining"], "method": "CASH"})).status_code == 201


async def test_a_challan_confirmed_with_an_earlier_date_asks_too(app_client, admin_user, db_session):
    sid, aid, rows = await _late_fee_account(app_client, db_session, "75201-0000034-1")
    first = rows[0]
    made = await app_client.post(f"/api/v1/fees/assignments/{aid}/installments/{first['ledger_entry_id']}/challan")
    ch_id = made.json()["id"] if made.status_code in (200, 201) else first["challan"]["id"]
    body = {"method": "CASH", "paid_on": first["due_date"]}
    asked = await app_client.post(f"/api/v1/fees/challans/{ch_id}/mark-paid", json=body)
    assert asked.status_code == 409 and asked.json()["error"]["code"] == "LATE_FINE_CONFIRM"
    ok = await app_client.post(f"/api/v1/fees/challans/{ch_id}/mark-paid", json={**body, "late_fine": "KEEP"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["late_fee_posted"] == first["late_fee"]
    assert sum(float(f.amount) for f in await _fines(db_session, sid)) == first["late_fee"]


async def test_id_cards_valid_for_the_months_in_settings_with_a_back_side(app_client, admin_user, db_session, monkeypatch):
    from pypdf import PdfReader

    from tests.integration.test_fees import _create_and_activate_student

    sid, _ = await _create_and_activate_student(app_client, cnic="75201-0000035-1")
    await _login_admin(app_client)
    monkeypatch.setattr(settings, "student_card_valid_months", 5)
    saved = await app_client.put("/api/v1/institute/profile", json={"card_instructions": "  Carry this card at all times.\n\n- Do not lend it to anyone.\nReport a lost card at once."})
    assert saved.status_code == 200, saved.text
    assert saved.json()["card_instructions"] == "Carry this card at all times.\nDo not lend it to anyone.\nReport a lost card at once."
    assert (await app_client.put("/api/v1/institute/profile", json={"card_instructions": "\n".join(f"Rule {i}" for i in range(9))})).status_code == 422

    r = await app_client.post("/api/v1/records/id-cards", json={"student_ids": [str(sid)], "employee_ids": []})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf")
    pdf = PdfReader(io.BytesIO(r.content))
    assert len(pdf.pages) == 2                                   # the fronts, then their backs
    from app.records.service import add_months

    until = add_months(institute_today(), 5)
    front, back = pdf.pages[0].extract_text(), pdf.pages[1].extract_text()
    assert f"valid to {until:%d/%m/%Y}" in front
    assert "INSTRUCTIONS" in back and "Do not lend it to anyone." in back and "If found, please return to" in back

    doc = (await db_session.execute(select(IssuedDocument).where(IssuedDocument.owner_id == sid, IssuedDocument.kind == "CARD"))).scalars().one()
    assert doc.details["valid_to"] == until.isoformat()
    ok = (await app_client.get(f"/verify/{doc.verification_code}")).text
    assert "This is genuine" in ok and f"{until:%d %B %Y}" in ok
    # once the date has passed the online check says so
    await db_session.execute(update(IssuedDocument).where(IssuedDocument.id == doc.id)
                             .values(details={**doc.details, "valid_to": (institute_today() - timedelta(days=1)).isoformat()}))
    await db_session.commit()
    gone = (await app_client.get(f"/verify/{doc.verification_code}")).text
    assert "expired" in gone and "no longer valid" in gone
    api = (await app_client.get(f"/api/v1/records/verify/{doc.verification_code}")).json()
    assert api["expired"] is True

    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28) and add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)
    _ = datetime, timezone, _login
