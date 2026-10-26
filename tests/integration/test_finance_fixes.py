"""Regression tests for the finance audit findings (FIN-xx in the audit report).

Each test is the audit's reproduction scenario, now asserting the correct behaviour.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.clock import institute_today
from app.core.config import settings
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _setup(client, cnic, total=10000, n=2):
    sid, _user = await _create_and_activate_student(client, cnic=cnic)
    await _create_active_fee_structure(client, total_amount=total, installment_count=n)
    await _login_admin(client)
    aid = next(a for a in (await client.get("/api/v1/fees/assignments")).json() if a["payer_id"] == sid)["id"]
    return sid, aid, await _challans(client, sid)


async def _account(client, sid):
    return (await client.get(f"/api/v1/fees/accounts/students/{sid}")).json()


async def _challans(client, sid):
    return [r["challan"] for r in (await _account(client, sid))["assignments"][0]["installments"]]


async def _credits(client, aid):
    return [e for e in (await client.get(f"/api/v1/fees/assignments/{aid}/ledger")).json() if e["entry_type"] == "CREDIT"]


# ---------------------------------------------------------------- FIN-01: one reversal per payment
async def test_fin01_payment_cannot_be_reversed_twice(app_client, admin_user):
    sid, aid, ch = await _setup(app_client, "72001-0000001-1")
    assert (await app_client.post(f"/api/v1/fees/challans/{ch[0]['id']}/mark-paid", json={"method": "CASH"})).status_code == 200
    credit = (await _credits(app_client, aid))[0]
    assert (await app_client.post(f"/api/v1/fees/challans/{ch[0]['id']}/mark-unpaid", json={"reason": "entered in error"})).status_code == 200
    again = await app_client.post(f"/api/v1/fees/assignments/payments/{credit['id']}/reverse", json={"reason": "bounced cheque"})
    assert again.status_code == 422 and "already reversed" in again.text
    assert (await app_client.get(f"/api/v1/fees/assignments/{aid}")).json()["total_paid"] == 0.0
    # paid again: installment 1 is settled
    assert (await app_client.post(f"/api/v1/fees/challans/{ch[0]['id']}/mark-paid", json={"method": "CASH"})).status_code == 200
    inst1 = (await _account(app_client, sid))["assignments"][0]["installments"][0]
    assert inst1["paid"] == 5000.0 and inst1["remaining"] == 0.0


async def test_fin01_reversing_the_receipt_reopens_its_challan_and_mark_unpaid_does_not_double(app_client, admin_user):
    sid, aid, ch = await _setup(app_client, "72001-0000002-2")
    assert (await app_client.post(f"/api/v1/fees/challans/{ch[0]['id']}/mark-paid", json={"method": "CASH"})).status_code == 200
    credit = (await _credits(app_client, aid))[0]
    assert (await app_client.post(f"/api/v1/fees/assignments/payments/{credit['id']}/reverse", json={"reason": "bounced cheque"})).status_code == 200
    challan = (await _challans(app_client, sid))[0]
    assert challan["status"] == "PENDING"                         # payable again, not stuck as PAID
    assert (await app_client.get(f"/api/v1/fees/assignments/{aid}")).json()["total_paid"] == 0.0


# ---------------------------------------------------------------- FIN-02: no over-collection under concurrency
async def test_fin02_two_cashiers_at_once_cannot_overpay(app_client, admin_user, _engine, db_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.exceptions import BusinessRuleViolationError
    from app.fees.assignment_service import FeeAssignmentService
    from app.fees.models import FeeLedgerEntry, LedgerEntryType
    from app.fees.schemas import RecordPaymentRequest

    _sid, aid, _ = await _setup(app_client, "72001-0000003-3", total=10000, n=1)
    await db_session.commit()
    factory = async_sessionmaker(bind=_engine, expire_on_commit=False)

    async def pay():
        async with factory() as s:
            await FeeAssignmentService(s).record_payment(uuid.UUID(aid), RecordPaymentRequest(amount=10000, method="CASH"), actor_id=admin_user.id)
            await asyncio.sleep(0.3)
            await s.commit()

    results = await asyncio.gather(pay(), pay(), return_exceptions=True)
    assert sum(isinstance(r, BusinessRuleViolationError) for r in results) == 1, results
    async with factory() as s:
        total = (await s.execute(select(func.sum(FeeLedgerEntry.amount)).where(
            FeeLedgerEntry.fee_assignment_id == uuid.UUID(aid), FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT))).scalar_one()
    assert float(total) == 10000.0


# ---------------------------------------------------------------- FIN-03: challans follow their installments
async def test_fin03_counter_payment_cancels_the_settled_installments_challan(app_client, admin_user):
    sid, aid, ch = await _setup(app_client, "72001-0000004-4")
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 5000, "method": "CASH"})).status_code == 201
    rows = (await _account(app_client, sid))["assignments"][0]["installments"]
    assert rows[0]["status"] == "PAID" and rows[0]["challan"]["status"] == "CANCELLED"
    assert rows[1]["challan"]["status"] == "PENDING" and rows[1]["challan"]["amount"] == 5000.0
    second = await app_client.post(f"/api/v1/fees/challans/{ch[0]['id']}/mark-paid", json={"method": "CASH"})
    assert second.status_code == 422


async def test_fin03_partial_counter_payment_lowers_the_challan(app_client, admin_user):
    sid, aid, _ = await _setup(app_client, "72001-0000005-5")
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 2000, "method": "CASH"})).status_code == 201
    rows = (await _account(app_client, sid))["assignments"][0]["installments"]
    assert rows[0]["challan"]["status"] == "PENDING" and rows[0]["challan"]["amount"] == 3000.0


async def test_fin03_paying_a_later_challan_settles_that_installment(app_client, admin_user):
    sid, aid, ch = await _setup(app_client, "72001-0000006-6")
    assert (await app_client.post(f"/api/v1/fees/challans/{ch[1]['id']}/mark-paid", json={"method": "CASH"})).status_code == 200
    rows = (await _account(app_client, sid))["assignments"][0]["installments"]
    assert rows[1]["remaining"] == 0.0 and rows[1]["challan"]["status"] == "PAID"
    assert rows[0]["remaining"] == 5000.0 and rows[0]["challan"]["status"] == "PENDING"


async def test_fin03_reversal_brings_a_cancelled_challan_back(app_client, admin_user):
    sid, aid, _ = await _setup(app_client, "72001-0000007-7")
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 5000, "method": "CASH"})).status_code == 201
    credit = (await _credits(app_client, aid))[0]
    assert (await app_client.post(f"/api/v1/fees/assignments/payments/{credit['id']}/reverse", json={"reason": "entered twice"})).status_code == 200
    assert (await _challans(app_client, sid))[0]["status"] == "PENDING"


# ---------------------------------------------------------------- FIN-12: waived or cancelled fees collect nothing
async def test_fin12_waived_fee_needs_a_reason_and_its_challans_stop(app_client, admin_user):
    sid, aid, ch = await _setup(app_client, "72001-0000008-8")
    bare = await app_client.patch(f"/api/v1/fees/assignments/{aid}/status", json={"status": "WAIVED"})
    assert bare.status_code == 422
    ok = await app_client.patch(f"/api/v1/fees/assignments/{aid}/status", json={"status": "WAIVED", "reason": "Orphan student, principal's order"})
    assert ok.status_code == 200, ok.text
    assert all(c["status"] == "CANCELLED" for c in await _challans(app_client, sid))
    r = await app_client.post(f"/api/v1/fees/challans/{ch[0]['id']}/mark-paid", json={"method": "CASH"})
    assert r.status_code == 422
    back = await app_client.patch(f"/api/v1/fees/assignments/{aid}/status", json={"status": "ACTIVE", "reason": "Waiver entered by mistake"})
    assert back.status_code == 200
    assert all(c["status"] == "PENDING" for c in await _challans(app_client, sid))


# ---------------------------------------------------------------- FIN-07: closed books stay closed
async def test_fin07_refund_cannot_be_dated_inside_closed_books(app_client, admin_user, db_session):
    from app.accounts.period import set_closed_through
    from app.core.exceptions import BusinessRuleViolationError
    from app.fees.refund_models import FeeRefund, RefundKind, RefundStatus
    from app.fees.refund_service import RefundService

    _sid, aid, _ = await _setup(app_client, "72001-0000009-9")
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 10000, "method": "CASH"})).status_code == 201
    svc = RefundService(db_session)
    ref = FeeRefund(fee_assignment_id=uuid.UUID(aid), kind=RefundKind.WITHDRAWAL, amount=2000, write_off=2000, reason="left the institute",
                    requested_by=None, status=RefundStatus.REQUESTED)
    db_session.add(ref)
    await db_session.flush()
    await svc.decide(ref.id, approve=True, note="ok", actor_id=admin_user.id)
    await set_closed_through(db_session, institute_today() - timedelta(days=1), actor_id=admin_user.id, reason=None)
    with pytest.raises(BusinessRuleViolationError):
        await svc.pay(ref.id, method="CASH", reference=None, paid_on=institute_today() - timedelta(days=5), actor_id=admin_user.id)
    paid = await svc.pay(ref.id, method="CASH", reference=None, paid_on=institute_today(), actor_id=admin_user.id)
    assert paid.status == RefundStatus.PAID


# ---------------------------------------------------------------- FIN-09: scholarships respect their dates
async def test_fin09_scholarship_for_a_later_period_leaves_earlier_installments_alone(app_client, admin_user):
    sid, aid, _ = await _setup(app_client, "72001-0000010-1")
    future = (institute_today() + timedelta(days=3650)).isoformat()
    r = await app_client.post("/api/v1/fees/scholarships", json={"student_id": sid, "name": "Next term", "kind": "FIXED", "value": 3000,
                                                                 "valid_from": future, "reason": "Starts next term"})
    assert r.status_code == 201, r.text
    assert (await _account(app_client, sid))["summary"]["concession"] == 0.0


async def test_fin09_scholarship_covering_the_term_applies(app_client, admin_user):
    sid, aid, _ = await _setup(app_client, "72001-0000011-2")
    r = await app_client.post("/api/v1/fees/scholarships", json={"student_id": sid, "name": "Merit", "kind": "PERCENT", "value": 50,
                                                                 "valid_from": "2026-01-01", "reason": "Merit list"})
    assert r.status_code == 201, r.text
    acct = await _account(app_client, sid)
    assert acct["summary"]["concession"] == 5000.0 and acct["summary"]["remaining"] == 5000.0
    assert all(c["amount"] == 2500.0 for c in await _challans(app_client, sid))


# ---------------------------------------------------------------- FIN-08: advances need an administrator, within a limit
async def test_fin08_advance_needs_admin_approval_and_a_limit(app_client, admin_user, db_session):
    from tests.integration.test_payroll import PR, TODAY, _as, _team

    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    officer_emp = (await app_client.get(f"{PR}/employees", params={"q": team["officer"]})).json()
    if officer_emp:
        own = await app_client.post(f"{PR}/advances", json={"employee_id": officer_emp[0]["id"], "amount": 5000, "monthly_recovery": 1000,
                                                            "reason": "self", "payment_method": "CASH"})
        assert own.status_code == 422 and "yourself" in own.text
    too_much = await app_client.post(f"{PR}/advances", json={"employee_id": team["b"]["id"], "amount": 30001, "monthly_recovery": 1000,
                                                             "reason": "Big", "payment_method": "CASH"})
    assert too_much.status_code == 422 and "at most Rs. 30,000" in too_much.text        # 1 x basic salary of 30,000
    asked = (await app_client.post(f"{PR}/advances", json={"employee_id": team["b"]["id"], "amount": 20000, "monthly_recovery": 5000,
                                                          "reason": "Medical", "payment_method": "CASH"})).json()
    assert asked["status"] == "REQUESTED"
    early = await app_client.post(f"{PR}/advances/{asked['id']}/pay", json={"payment_method": "CASH", "paid_on": str(TODAY)})
    assert early.status_code == 422                                                       # not approved yet: nothing can be paid
    assert (await app_client.post(f"{PR}/advances/{asked['id']}/decision", json={"approve": True})).status_code == 403   # officer cannot approve
    second = await app_client.post(f"{PR}/advances", json={"employee_id": team["b"]["id"], "amount": 15000, "monthly_recovery": 5000,
                                                           "reason": "More", "payment_method": "CASH"})
    assert second.status_code == 422                                                      # 20,000 waiting + 15,000 > 30,000
    await app_client.get("/api/v1/auth/me")
    from app.accounts.models import Expense

    assert not (await db_session.execute(select(Expense).where(Expense.title.like("Salary advance%")))).scalars().all()   # no money moved yet
    await _login_admin(app_client)
    assert (await app_client.post(f"{PR}/advances/{asked['id']}/decision", json={"approve": True})).json()["status"] == "APPROVED"
    await _as(app_client, team, "officer")
    paid = (await app_client.post(f"{PR}/advances/{asked['id']}/pay", json={"payment_method": "CASH", "paid_on": str(TODAY)})).json()
    assert paid["status"] == "ACTIVE" and paid["balance"] == 20000.0


async def test_fin08_payroll_goes_through_submission(app_client, admin_user, db_session):
    from tests.integration.test_payroll import PR, _as, _prepare, _team

    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    run = await _prepare(app_client)
    await _login_admin(app_client)
    early = await app_client.post(f"{PR}/runs/{run['id']}/approve")
    assert early.status_code == 422 and "send this payroll for approval" in early.text
    await _as(app_client, team, "officer")
    assert (await app_client.post(f"{PR}/runs/{run['id']}/submit")).json()["status"] == "SUBMITTED"
    locked = await app_client.post(f"{PR}/runs/{run['id']}/recalculate")
    assert locked.status_code == 422                                                      # locked while waiting for approval
    await _login_admin(app_client)
    back = (await app_client.post(f"{PR}/runs/{run['id']}/send-back", json={"reason": "Check BBB's unpaid days"})).json()
    assert back["status"] == "DRAFT" and back["returned_reason"] == "Check BBB's unpaid days"


async def test_fin08_accounts_officer_cannot_see_staff_attendance(app_client, admin_user, db_session):
    from tests.integration.test_payroll import _as, _team

    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    for path in ("/api/v1/staff-attendance/board", f"/api/v1/staff-attendance/month?month={institute_today().replace(day=1)}"):
        assert (await app_client.get(path)).status_code == 403, path
    assert (await app_client.get("/api/v1/staff-attendance/me")).status_code == 200       # their own month stays visible
    assert (await app_client.get("/api/v1/payroll/runs")).status_code == 200


# ---------------------------------------------------------------- FIN-04/05/06: one set of numbers
async def test_fin04_dashboard_counts_scholarships_reversals_and_waivers(app_client, admin_user):
    sid, aid, _ = await _setup(app_client, "72001-0000020-1")
    r = await app_client.post("/api/v1/fees/scholarships", json={"student_id": sid, "name": "Merit 50", "kind": "PERCENT", "value": 50,
                                                                 "valid_from": "2026-01-01", "reason": "Merit list"})
    assert r.status_code == 201, r.text
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 1000, "method": "CASH"})).status_code == 201
    credit = (await _credits(app_client, aid))[0]
    assert (await app_client.post(f"/api/v1/fees/assignments/payments/{credit['id']}/reverse", json={"reason": "entered twice"})).status_code == 200
    dash = (await app_client.get("/api/v1/fees/dashboard")).json()
    acct = await _account(app_client, sid)
    assert dash["total_outstanding"] == acct["summary"]["remaining"] == 5000.0
    assert dash["total_charged"] == 5000.0 and dash["total_collected"] == 0.0
    a = (await app_client.get(f"/api/v1/fees/assignments/{aid}")).json()
    assert (a["total_payable"], a["total_remaining"]) == (5000.0, 5000.0)


async def test_fin05_profile_matches_the_fee_account(app_client, admin_user):
    sid, aid, _ = await _setup(app_client, "72001-0000021-2")
    assert (await app_client.patch(f"/api/v1/fees/assignments/{aid}/status", json={"status": "WAIVED", "reason": "Principal's waiver"})).status_code == 200
    prof = (await app_client.get(f"/api/v1/students/{sid}/full-profile")).json()
    assert prof["fees"]["total_outstanding"] == 0.0 and prof["fees"]["total_assignments"] == 0


async def test_fin06_collection_report_nets_reversals(app_client, admin_user):
    _sid, aid, _ = await _setup(app_client, "72001-0000022-3")
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 5000, "method": "CASH"})).status_code == 201
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 2000, "method": "CASH"})).status_code == 201
    credit = (await _credits(app_client, aid))[0]
    assert (await app_client.post(f"/api/v1/fees/assignments/payments/{credit['id']}/reverse", json={"reason": "entered twice"})).status_code == 200
    today = institute_today().isoformat()
    coll = (await app_client.get("/api/v1/finance-reports/collection", params={"from_date": today, "to_date": today})).json()
    summ = (await app_client.get("/api/v1/accounts/summary", params={"from_date": today, "to_date": today})).json()
    assert coll["total"] == summ["fees_collected"] == 7000.0 - float(credit["amount"])
    assert coll["received"] == 7000.0 and coll["by_method"]["CASH"] == coll["total"]


# ---------------------------------------------------------------- FIN-10: only the current term's fees are charged on admission
async def test_fin10_a_closed_terms_fee_is_not_charged_to_a_new_admission(app_client, admin_user, db_session):
    from app.academics.terms import AcademicTermService, TermStatus
    from tests.integration.test_fees import _student_payload

    await _login_admin(app_client)
    terms = AcademicTermService(db_session)
    old = await terms.create(label="Spring 2026", start_date=institute_today() - timedelta(days=300), end_date=institute_today() - timedelta(days=150),
                             actor_id=admin_user.id)
    t = (await app_client.post("/api/v1/fees/types", json={"code": "OLD", "name": "Old term tuition"})).json()
    s = (await app_client.post("/api/v1/fees/structures", json={"fee_type_id": t["id"], "name": "Tuition Spring 2026", "term_label": "Spring 2026",
                                                               "total_amount": 30000, "installment_count": 3,
                                                               "effective_from": str(institute_today() - timedelta(days=280))})).json()
    assert (await app_client.post(f"/api/v1/fees/structures/{s['id']}/activate")).status_code == 200
    await terms.update(old.id, start_date=None, end_date=None, status=TermStatus.CLOSED, actor_id=admin_user.id)
    prog = (await app_client.post("/api/v1/programs", json={"code": "BSX", "name": "BS X", "duration_years": 4, "total_semesters": 8})).json()["id"]
    r = await app_client.post("/api/v1/students", json=_student_payload(cnic="72001-0000023-4", program_id=prog, current_semester=1))
    assert r.status_code == 201, r.text
    assert (await _account(app_client, r.json()["id"]))["assignments"] == []


# ---------------------------------------------------------------- FIN-11: a room move never bills the same days twice
async def test_fin11_hostel_room_move_bills_each_day_once(app_client, admin_user):
    sid, _user = await _create_and_activate_student(app_client, cnic="72001-0000030-1")
    await _login_admin(app_client)
    month = institute_today().replace(day=1)
    a = (await app_client.post("/api/v1/hostel/rooms", json={"code": "A1", "capacity": 2, "monthly_fee": 10000})).json()
    b = (await app_client.post("/api/v1/hostel/rooms", json={"code": "B1", "capacity": 2, "monthly_fee": 10000})).json()
    st = (await app_client.post("/api/v1/hostel/stays", json={"room_id": a["id"], "student_id": sid, "start_date": month.isoformat()})).json()
    assert (await app_client.post("/api/v1/hostel/charges", json={"month": month.isoformat()})).status_code == 200
    assert (await app_client.post(f"/api/v1/hostel/stays/{st['id']}/move", json={"room_id": b["id"]})).status_code == 200
    await app_client.post("/api/v1/hostel/charges", json={"month": month.isoformat()})
    assert (await app_client.get("/api/v1/hostel/summary")).json()["charges_unpaid"] == 10000.0


# ---------------------------------------------------------------- FIN-14: bank reconciliation matches the slip number
async def test_fin14_reconciliation_matches_on_the_bank_reference(app_client, admin_user):
    _sid, aid, _ = await _setup(app_client, "72001-0000031-2")
    today = institute_today()
    await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 2500, "method": "BANK_DEPOSIT", "reference": "HBL-77881"})
    await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 2500, "method": "BANK_DEPOSIT", "reference": "HBL-99102"})
    csv_text = f"Date,Description,Credit\n{today.isoformat()},CASH DEP SLIP HBL99102 VIST,2500\n"
    r = await app_client.post("/api/v1/finance-reports/reconcile", files={"file": ("stmt.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    m = r.json()["matched"]
    assert len(m) == 1 and m[0]["matched_on"] == "bank reference and amount"
    receipt = next(e for e in await _credits(app_client, aid) if e["payment_reference"] == "HBL-99102")["receipt_number"]
    assert m[0]["book"]["receipt"] == receipt


# ---------------------------------------------------------------- previous dues (arrears from the manual system)
async def test_previous_dues_once_only_and_cancellable(app_client, admin_user):
    sid, _user = await _create_and_activate_student(app_client, cnic="72001-0000040-1")
    await _login_admin(app_client)
    roll = (await app_client.get(f"/api/v1/students/{sid}")).json()["roll_number"]
    body = {"student_id": sid, "amount": 18500, "due_date": str(institute_today()), "note": "Arrears from the manual register"}
    assert (await app_client.post("/api/v1/fees/opening-balances", json=body)).status_code == 201
    dup = await app_client.post("/api/v1/fees/opening-balances", json=body)
    assert dup.status_code == 422 and "already recorded" in dup.text
    csv_text = f"roll_number,amount,due_date,note\n{roll},18500,{institute_today().isoformat()},again\n"
    check = (await app_client.post("/api/v1/fees/opening-balances/check", files={"file": ("dues.csv", csv_text.encode(), "text/csv")})).json()
    assert check[0]["ok"] is False and "already recorded" in check[0]["errors"][0]
    acct = await _account(app_client, sid)
    prev = next(a for a in acct["assignments"] if a["is_previous_dues"])
    assert prev["remaining"] == 18500.0 and prev["installments"][0]["challan"]["status"] == "PENDING"
    listed = (await app_client.get("/api/v1/fees/opening-balances", params={"q": roll})).json()
    assert len(listed) == 1 and listed[0]["remaining"] == 18500.0
    assert (await app_client.post(f"/api/v1/fees/assignments/{prev['id']}/payments", json={"amount": 500, "method": "CASH"})).status_code == 201
    c = await app_client.post(f"/api/v1/fees/opening-balances/{listed[0]['ledger_entry_id']}/cancel", json={"reason": "Typed 18,500 instead of 1,850"})
    assert c.status_code == 200 and c.json()["written_off"] == 18000.0
    prev = next(a for a in (await _account(app_client, sid))["assignments"] if a["is_previous_dues"])
    assert prev["remaining"] == 0.0 and prev["paid"] == 500.0 and prev["installments"][0]["challan"]["status"] == "CANCELLED"


# ---------------------------------------------------------------- rows written by the old version (already in production)
async def test_old_style_unlinked_reversal_is_recognised(app_client, admin_user, db_session):
    """Before this fix, "mark unpaid" wrote a reversal that did not point at its payment. Such a payment must not be
    reversible a second time, and the receipt must show as VOID."""
    from datetime import datetime, timezone

    from app.fees.models import FeeLedgerEntry, LedgerEntryType
    from app.fees.payment_models import ChallanStatus, PaymentChallan

    sid, aid, ch = await _setup(app_client, "72001-0000050-1")
    assert (await app_client.post(f"/api/v1/fees/challans/{ch[0]['id']}/mark-paid", json={"method": "CASH"})).status_code == 200
    credit = (await _credits(app_client, aid))[0]
    challan = await db_session.get(PaymentChallan, uuid.UUID(ch[0]["id"]))
    # what the old mark_unpaid left behind: an unlinked reversal naming the challan, the challan PENDING, no link back
    db_session.add(FeeLedgerEntry(fee_assignment_id=uuid.UUID(aid), entry_type=LedgerEntryType.DEBIT, amount=5000, installment_number=1,
                                  due_date=institute_today(), description=f"Payment reversed — challan {challan.consumer_number} marked unpaid by staff",
                                  created_at=datetime.now(timezone.utc)))
    challan.status, challan.paid_entry_id, challan.gateway_reference, challan.paid_at = ChallanStatus.PENDING, None, None, None
    await db_session.flush()
    again = await app_client.post(f"/api/v1/fees/assignments/payments/{credit['id']}/reverse", json={"reason": "bounced cheque"})
    assert again.status_code == 422 and "already reversed" in again.text
    statement = (await app_client.get(f"/api/v1/fees/assignments/{aid}/statement")).json()
    assert next(l for l in statement["lines"] if l["id"] == credit["id"])["reversed"] is True
    assert (await app_client.get(f"/api/v1/fees/assignments/{aid}")).json()["total_paid"] == 0.0
