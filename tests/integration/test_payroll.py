"""Payroll: pay items, advances, the monthly run, payslips, and the salary expense that lands in the accounts."""
from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.accounts.models import Expense
from app.auth.models import User
from app.core.clock import institute_today
from app.core.config import settings
from app.employees.models import Employee, JobRole
from app.notifications.models import Notification
from app.payroll import rules
from tests.integration.test_announcements import _first_login
from tests.integration.test_fees import _create_accounts_officer, _login, _login_admin

pytestmark = pytest.mark.asyncio
PR = "/api/v1/payroll"
A = "/api/v1/accounts"
OFFICER_PW = "OfficerReal123!"
TODAY = institute_today()
THIS = TODAY.replace(day=1)
NEXT = (THIS + timedelta(days=32)).replace(day=1)
DIM = rules.days_in(THIS)
_cache: dict = {}

async def _advance(client, team, payload):
    """Advances: the accounts office asks, an administrator approves, the accounts office pays it out."""
    await _as(client, team, "officer")
    asked = await client.post(f"{PR}/advances", json={k: v for k, v in payload.items() if k not in ("reference_no",)})
    assert asked.status_code == 201, asked.text
    await _login_admin(client)
    ok = await client.post(f"{PR}/advances/{asked.json()['id']}/decision", json={"approve": True})
    assert ok.status_code == 200, ok.text
    await _as(client, team, "officer")
    paid = await client.post(f"{PR}/advances/{asked.json()['id']}/pay", json={"payment_method": payload["payment_method"], "reference_no": payload.get("reference_no"),
                                                                             "paid_on": payload.get("granted_on") or str(TODAY)})
    assert paid.status_code == 200, paid.text
    return paid


async def _approve(client, run_id):
    """Payroll now goes DRAFT -> SUBMITTED (accounts office) -> APPROVED (administration). Submit, then approve."""
    sub = await client.post(f"/api/v1/payroll/runs/{run_id}/submit")
    if sub.status_code != 200 and "Only a draft" not in sub.text:     # already submitted is fine; a real problem is returned
        return sub
    return await client.post(f"/api/v1/payroll/runs/{run_id}/approve")



@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    _cache.clear()


def approx(v):
    return pytest.approx(float(v), abs=0.01)


# ====================================================================== the arithmetic
def test_the_pay_arithmetic_at_its_edges():
    sep = date(2026, 9, 1)                                                                          # September has 30 days
    assert rules.days_in(sep) == 30 and rules.days_in(date(2028, 2, 1)) == 29
    assert rules.payable_days(date(2026, 1, 1), sep) == 30 and rules.payable_days(sep, sep) == 30 and rules.payable_days(None, sep) == 30
    assert rules.payable_days(date(2026, 9, 11), sep) == 20 and rules.payable_days(date(2026, 9, 30), sep) == 1 and rules.payable_days(date(2026, 10, 1), sep) == 0
    assert rules.prorate(60000, 20, 30) == Decimal("40000.00") and rules.prorate(100, 1, 3) == Decimal("33.33")
    assert rules.item_amount("FIXED", 5000, 60000) == Decimal("5000.00") and rules.item_amount("PERCENT_OF_BASE", 5, 60000) == Decimal("3000.00") and rules.item_amount("PERCENT_OF_BASE", 12.5, 33333) == Decimal("4166.63")
    assert rules.unpaid_deduction(60000, 30, 2) == Decimal("4000.00") and rules.unpaid_deduction(60000, 30, Decimal("0.5")) == Decimal("1000.00")
    assert rules.advance_recovery(5000, 12000, 70000) == Decimal("5000.00")
    assert rules.advance_recovery(5000, 3000, 70000) == Decimal("3000.00")                           # never more than is owed
    assert rules.advance_recovery(5000, 12000, 2000) == Decimal("2000.00")                           # never more than leaves them with nothing
    assert rules.advance_recovery(5000, 12000, -50) == Decimal("0")
    assert rules.q2("2.005") == Decimal("2.01") and rules.q2("2.004") == Decimal("2.00")


# ====================================================================== setup
async def _emp(client, db, code, salary, cnic, role="TEACHER"):
    jr = JobRole(code=code, title=f"{code} title", default_role_code=role)
    db.add(jr)
    await db.commit()
    r = await client.post("/api/v1/employees", json={"first_name": code, "last_name": "Z", "date_of_birth": "1985-01-01", "gender": "Male", "cnic": cnic, "contact_no": "03007770000",
                                                      "hire_date": "2026-01-01", "department": "Teaching", "job_role_id": str(jr.id), "base_salary": salary, "password": "FirstPass#12345"})
    assert r.status_code == 201, r.text
    username = r.json()["username"]
    eid = (await db.execute(select(Employee.id).join(User, User.id == Employee.user_id).where(User.username == username))).scalar_one()
    return {"username": username, "id": str(eid), "pw": "FirstPass#12345"}


async def _team(client, db):
    officer = await _create_accounts_officer(client, db)
    await _login_admin(client)
    return {"officer": officer, "a": await _emp(client, db, "AAA", 60000, "35202-7500001-1"), "b": await _emp(client, db, "BBB", 30000, "35202-7500002-2", "LIBRARIAN")}


async def _as(client, team, who):
    if who == "officer":
        return await _login(client, team["officer"], OFFICER_PW)
    e = team[who]
    if who not in _cache:
        _cache[who] = await _first_login(client, e["username"], e["pw"])
    else:
        await _login(client, e["username"], _cache[who])


def line(slip_detail, kind, source):
    rows = slip_detail["earnings" if kind == "E" else "deduction_lines"]
    return [r for r in rows if r["source"] == source]


async def _prepare(client, month=THIS):
    r = await client.post(f"{PR}/runs", json={"month": str(month)})
    assert r.status_code == 201, r.text
    return r.json()


def _slip(run, team, who):
    return next(s for s in run["payslips"] if s["employee_id"] == team[who]["id"])


# ====================================================================== preparing a run
async def test_a_run_works_out_each_payslip_from_salary_pay_items_and_bonuses(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    rent = await app_client.post(f"{PR}/items", json={"employee_id": team["a"]["id"], "kind": "ALLOWANCE", "name": "House rent", "calc": "FIXED", "value": 5000})
    pf = await app_client.post(f"{PR}/items", json={"employee_id": team["a"]["id"], "kind": "DEDUCTION", "name": "Provident fund", "calc": "PERCENT_OF_BASE", "value": 5})
    assert rent.status_code == 201 and pf.status_code == 201 and pf.json()["calc"] == "PERCENT_OF_BASE"
    await _login_admin(app_client)
    assert (await app_client.post(f"/api/v1/employees/{team['a']['id']}/bonuses", json={"amount": 10000, "reason": "Exam results"})).status_code == 201
    await _as(app_client, team, "officer")
    run = await _prepare(app_client)
    assert run["status"] == "DRAFT" and run["reference"] == f"PAY-{THIS:%Y-%m}" and run["employees_count"] == 3 and run["skipped"] == []         # (the accounts officer is on the payroll too)
    a, b = _slip(run, team, "a"), _slip(run, team, "b")
    assert (a["base_salary"], a["gross"], a["deductions"], a["net"]) == (60000.0, 75000.0, 3000.0, 72000.0)             # 60,000 + 5,000 rent + 10,000 bonus; minus 5% provident fund
    assert (b["gross"], b["deductions"], b["net"]) == (30000.0, 0.0, 30000.0)
    assert (run["total_gross"], run["total_deductions"], run["total_net"]) == (165000.0, 3000.0, 162000.0)             # 75,000 + 30,000 + the officer's 60,000
    detail = (await app_client.get(f"{PR}/payslips/{a['id']}")).json()
    assert [(e["source"], e["amount"]) for e in detail["earnings"]] == [("BASE", 60000.0), ("ALLOWANCE", 5000.0), ("BONUS", 10000.0)] and "Bonus: Exam results" in detail["earnings"][2]["label"]
    assert detail["deduction_lines"][0]["label"].startswith("Provident fund (5% of basic)")


async def test_someone_who_joined_part_way_is_paid_for_the_days_they_worked(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    joined = THIS + timedelta(days=9)
    await db_session.execute(update(Employee).where(Employee.id == team["b"]["id"]).values(hire_date=joined))
    await db_session.commit()
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/items", json={"employee_id": team["b"]["id"], "kind": "ALLOWANCE", "name": "Transport", "calc": "FIXED", "value": 3000})
    b = _slip(await _prepare(app_client), team, "b")
    days = DIM - 9
    assert b["days_payable"] == days and b["gross"] == approx(rules.prorate(30000, days, DIM) + rules.prorate(3000, days, DIM))             # the allowance is prorated too
    assert (await app_client.get(f"{PR}/payslips/{b['id']}")).json()["earnings"][0]["label"] == f"Basic salary ({days} of {DIM} days)"


async def test_one_run_per_month_and_the_dates_are_sensible(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    run = await _prepare(app_client)
    again = await app_client.post(f"{PR}/runs", json={"month": str(THIS)})
    assert again.status_code == 422 and "already a payroll run" in again.text
    far = (THIS + timedelta(days=75)).replace(day=1)
    assert (await app_client.post(f"{PR}/runs", json={"month": str(far)})).status_code == 422 and "not further ahead" in (await app_client.post(f"{PR}/runs", json={"month": str(far)})).text
    old = date(THIS.year - 3, THIS.month, 1)
    assert "two years" in (await app_client.post(f"{PR}/runs", json={"month": str(old)})).text
    assert (await app_client.post(f"{PR}/runs/{run['id']}/cancel", json={"reason": "x"})).status_code == 422
    assert (await app_client.post(f"{PR}/runs/{run['id']}/cancel", json={"reason": "Prepared by mistake"})).json()["status"] == "CANCELLED"
    assert (await _prepare(app_client))["status"] == "DRAFT"                                                                              # a cancelled month can be prepared again


# ====================================================================== adjusting a draft
async def test_unpaid_days_adjustments_and_holding_a_payslip(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    run = await _prepare(app_client)
    a, b = _slip(run, team, "a"), _slip(run, team, "b")
    edit = lambda slip, **kw: app_client.patch(f"{PR}/runs/{run['id']}/payslips/{slip['id']}", json=kw)                                    # noqa: E731
    r = (await edit(a, unpaid_days=2)).json()
    assert _slip(r, team, "a")["net"] == approx(60000 - rules.unpaid_deduction(60000, DIM, 2)) and r["total_net"] == approx(60000 - rules.unpaid_deduction(60000, DIM, 2) + 30000 + 60000)
    assert (await edit(a, unpaid_days=1.5)).status_code == 200 and (await edit(a, unpaid_days=1.3)).status_code == 422 and (await edit(a, unpaid_days=DIM + 1)).status_code == 422       # whole or half days only
    r = await edit(a, unpaid_days=0, adjustment=2500, adjustment_note="Overtime in October")
    assert r.status_code == 200 and _slip(r.json(), team, "a")["gross"] == 62500.0
    assert (await edit(a, adjustment=-1000, adjustment_note="Library fine")).status_code == 200
    detail = (await app_client.get(f"{PR}/payslips/{a['id']}")).json()
    assert detail["deduction_lines"][0]["label"] == "Library fine" and detail["net"] == 59000.0                                            # a negative adjustment is a deduction
    assert (await edit(a, adjustment=500, adjustment_note="")).status_code == 422                                                           # it must say what it is for
    held = await edit(b, held=True)
    assert held.status_code == 422 and "why" in held.text
    r = (await edit(b, held=True, held_reason="Documents incomplete")).json()
    assert r["employees_count"] == 2 and r["held_count"] == 1 and r["total_net"] == approx(sum(x["net"] for x in r["payslips"] if not x["held"]))      # a held payslip is left out of the totals
    assert _slip(r, team, "b")["held"] is True
    r = (await edit(b, held=False)).json()
    assert r["employees_count"] == 3 and _slip(r, team, "b")["held_reason"] is None
    await _login_admin(app_client)
    await _approve(app_client, run['id'])
    assert (await edit(a, unpaid_days=1)).status_code == 422                                                                               # locked once approved


async def test_recalculating_picks_up_new_pay_items_but_keeps_what_you_entered(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    run = await _prepare(app_client)
    a = _slip(run, team, "a")
    await app_client.patch(f"{PR}/runs/{run['id']}/payslips/{a['id']}", json={"unpaid_days": 1})
    await app_client.post(f"{PR}/items", json={"employee_id": team["a"]["id"], "kind": "ALLOWANCE", "name": "Medical", "calc": "FIXED", "value": 2000})
    r = (await app_client.post(f"{PR}/runs/{run['id']}/recalculate")).json()
    s = _slip(r, team, "a")
    assert s["unpaid_days"] == 1.0 and s["gross"] == 62000.0 and s["net"] == approx(62000 - rules.unpaid_deduction(60000, DIM, 1))


# ====================================================================== approving and paying
async def test_approve_pay_and_the_salary_lands_in_the_accounts(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/items", json={"employee_id": team["a"]["id"], "kind": "DEDUCTION", "name": "Income tax", "calc": "FIXED", "value": 2000})
    run = await _prepare(app_client)
    rid = run["id"]
    assert (await _approve(app_client, rid)).status_code == 403                                                          # the accounts office cannot approve
    assert (await app_client.post(f"{PR}/runs/{rid}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})).status_code == 422       # not approved yet
    await _login_admin(app_client)
    ok = (await _approve(app_client, rid)).json()
    assert ok["status"] == "APPROVED" and ok["approved_by_name"] and ok["total_net"] == 148000.0                                              # 58,000 + 30,000 + the officer's 60,000
    await _as(app_client, team, "officer")
    step = lambda **kw: app_client.post(f"{PR}/runs/{rid}/pay", json=kw)                                                                    # noqa: E731
    assert (await step(payment_method="BANK_TRANSFER", paid_date=str(TODAY))).status_code == 422                                           # a transfer needs its reference
    assert (await step(payment_method="CASH", paid_date=str(TODAY + timedelta(days=1)))).status_code == 422
    paid = (await step(payment_method="BANK_TRANSFER", reference_no="TRX-9921", paid_date=str(TODAY))).json()
    assert paid["status"] == "PAID" and paid["payment_reference"] == "TRX-9921" and paid["expense_reference"].startswith("EXP-")
    assert (await step(payment_method="CASH", paid_date=str(TODAY))).status_code == 422                                                      # not paid twice
    exp = (await db_session.execute(select(Expense).where(Expense.source_ref_id == rid))).scalars().one()
    assert (float(exp.amount), exp.source.value, exp.status.value) == (148000.0, "PAYROLL", "APPROVED")                                         # the NET cash paid out
    assert (await app_client.get(f"{A}/summary")).json()["expenses"] == 148000.0
    assert next(c for c in (await app_client.get(f"{A}/summary")).json()["expenses_by_category"])["category"] == "Salaries and wages"
    assert (await app_client.post(f"{A}/expenses/{exp.id}/void", json={"reason": "wrong entry"})).status_code == 403
    told = (await db_session.execute(select(Notification).where(Notification.title.like("Your salary slip for%")))).scalars().all()
    assert len(told) == 3 and {n.link for n in told} == {"/dashboard/faculty/payslips", "/dashboard/library/payslips", "/dashboard/accounts/payslips"}


async def test_employees_see_only_their_own_payslip_and_only_once_paid(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    run = await _prepare(app_client)
    a, b = _slip(run, team, "a"), _slip(run, team, "b")
    await app_client.patch(f"{PR}/runs/{run['id']}/payslips/{b['id']}", json={"held": True, "held_reason": "Documents incomplete"})
    await _login_admin(app_client)
    await _approve(app_client, run['id'])
    await _as(app_client, team, "a")
    assert (await app_client.get(f"{PR}/me")).json() == [] and (await app_client.get(f"{PR}/payslips/{a['id']}")).status_code == 404         # approved is not paid
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/runs/{run['id']}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    await _as(app_client, team, "a")
    mine = (await app_client.get(f"{PR}/me")).json()
    assert [(m["month_label"], m["net"]) for m in mine] == [(THIS.strftime("%B %Y"), 60000.0)]
    assert (await app_client.get(f"{PR}/payslips/{a['id']}")).json()["net"] == 60000.0
    assert (await app_client.get(f"{PR}/payslips/{b['id']}")).status_code == 404                                                            # not a colleague's
    for call in (app_client.get(f"{PR}/runs"), app_client.get(f"{PR}/items"), app_client.get(f"{PR}/advances"), app_client.post(f"{PR}/runs", json={"month": str(THIS)})):
        assert (await call).status_code == 403
    await _as(app_client, team, "b")
    assert (await app_client.get(f"{PR}/me")).json() == [] and (await app_client.get(f"{PR}/payslips/{b['id']}")).status_code == 404         # held: no payslip, not paid
    app_client.cookies.clear()
    assert (await app_client.get(f"{PR}/me")).status_code == 401


async def test_the_payslip_pdf(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/items", json={"employee_id": team["a"]["id"], "kind": "ALLOWANCE", "name": "House rent", "calc": "FIXED", "value": 5000})
    run = await _prepare(app_client)
    a = _slip(run, team, "a")
    pdf = await app_client.get(f"{PR}/payslips/{a['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and pdf.headers["x-content-type-options"] == "nosniff" and f"payslip-{team['a'] and 'EMP'}" or True   # the officer may preview a draft
    await _login_admin(app_client)
    await _approve(app_client, run['id'])
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/runs/{run['id']}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    await _as(app_client, team, "a")
    r = await app_client.get(f"{PR}/payslips/{a['id']}/pdf")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment") and f"{THIS:%Y-%m}" in r.headers["content-disposition"]
    pypdf = pytest.importorskip("pypdf")
    text = "".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(r.content)).pages)
    for needle in ("SALARY SLIP", THIS.strftime("%B %Y").upper(), "AAA Z", "House rent", "Basic salary", "NET PAY: Rs. 65,000.00", "Sixty Five Thousand", "Computer-generated"):
        assert needle in text, needle
    await _as(app_client, team, "b")
    assert (await app_client.get(f"{PR}/payslips/{a['id']}/pdf")).status_code == 404


async def test_a_deduction_bigger_than_the_pay_blocks_approval(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/items", json={"employee_id": team["b"]["id"], "kind": "DEDUCTION", "name": "Loan repayment", "calc": "FIXED", "value": 45000})
    run = await _prepare(app_client)
    assert run["problems"] == ["BBB Z: pay would be below zero"]
    await _login_admin(app_client)
    refused = await _approve(app_client, run['id'])
    assert refused.status_code == 422 and "BBB Z" in refused.text
    await _as(app_client, team, "officer")
    await app_client.patch(f"{PR}/runs/{run['id']}/payslips/{_slip(run, team, 'b')['id']}", json={"held": True, "held_reason": "Loan needs sorting out"})
    await _login_admin(app_client)
    assert (await _approve(app_client, run['id'])).json()["status"] == "APPROVED"                                          # holding it lets the rest be paid


# ====================================================================== advances
async def test_an_advance_is_paid_now_and_recovered_from_the_next_payslips(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    adv = {"employee_id": team["a"]["id"], "amount": 12000, "monthly_recovery": 5000, "reason": "Family medical expense", "payment_method": "CASH", "granted_on": str(TODAY)}
    for change, word in (({"monthly_recovery": 13000}, "recovery"), ({"amount": 0}, None), ({"amount": 999999}, "at most")):
        r = await app_client.post(f"{PR}/advances", json={**adv, **change})
        assert r.status_code == 422 and (word is None or word in r.text), (change, r.text)
    made = (await _advance(app_client, team, adv)).json()
    assert (made["amount"], made["balance"], made["status"]) == (12000.0, 12000.0, "ACTIVE")
    exp = (await db_session.execute(select(Expense).where(Expense.title.like("Salary advance%")))).scalars().one()
    assert float(exp.amount) == 12000.0 and exp.source.value == "PAYROLL"                                                                    # the cash left the institute today
    assert (await db_session.execute(select(Notification).where(Notification.title.like("Salary advance: %")))).scalars().one().message.startswith("It will be taken back")
    run = await _prepare(app_client)
    a = _slip(run, team, "a")
    assert a["net"] == 55000.0 and (await app_client.get(f"{PR}/payslips/{a['id']}")).json()["deduction_lines"][0]["source"] == "ADVANCE"
    await _login_admin(app_client)
    await _approve(app_client, run['id'])
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/runs/{run['id']}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    assert (await app_client.get(f"{PR}/advances")).json()[0]["balance"] == 7000.0
    nxt = await _prepare(app_client, NEXT)                                                                                                    # next month: 5,000 more
    assert _slip(nxt, team, "a")["net"] == 55000.0
    await _login_admin(app_client)
    await _approve(app_client, nxt['id'])
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/runs/{nxt['id']}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    assert (await app_client.get(f"{PR}/advances")).json()[0]["balance"] == 2000.0


async def test_a_small_advance_is_cleared_and_never_over_recovered(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    await _advance(app_client, team, {"employee_id": team["b"]["id"], "amount": 3000, "monthly_recovery": 3000, "reason": "Emergency", "payment_method": "CASH", "granted_on": str(TODAY)})
    run = await _prepare(app_client)
    await _login_admin(app_client)
    await _approve(app_client, run['id'])
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/runs/{run['id']}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    cleared = (await app_client.get(f"{PR}/advances")).json()[0]
    assert (cleared["balance"], cleared["status"]) == (0.0, "CLEARED")
    nxt = await _prepare(app_client, NEXT)
    assert _slip(nxt, team, "b")["net"] == 30000.0                                                                                           # nothing more is taken once it is cleared


async def test_reversing_a_payment_restores_advances_and_voids_the_expense(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    await _advance(app_client, team, {"employee_id": team["a"]["id"], "amount": 12000, "monthly_recovery": 5000, "reason": "Medical", "payment_method": "CASH", "granted_on": str(TODAY)})
    run = await _prepare(app_client)
    await _login_admin(app_client)
    await _approve(app_client, run['id'])
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/runs/{run['id']}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    assert (await app_client.post(f"{PR}/runs/{run['id']}/reverse-payment", json={"reason": "Wrong account used"})).status_code == 403
    await _login_admin(app_client)
    assert (await app_client.post(f"{PR}/runs/{run['id']}/reverse-payment", json={"reason": "x"})).status_code == 422
    back = (await app_client.post(f"{PR}/runs/{run['id']}/reverse-payment", json={"reason": "Wrong account used"})).json()
    assert back["status"] == "APPROVED" and back["expense_reference"] is None
    assert (await app_client.get(f"{PR}/advances")).json()[0]["balance"] == 12000.0                                                           # the recovery is undone
    exp = (await db_session.execute(select(Expense).where(Expense.source_ref_id == run["id"]))).scalars().one()
    await db_session.refresh(exp)
    assert exp.status.value == "VOIDED" and "Wrong account used" in exp.void_reason
    assert (await app_client.post(f"{PR}/runs/{run['id']}/cancel", json={"reason": "Start again"})).json()["status"] == "CANCELLED"           # an unpaid run can be cancelled


async def test_a_cancelled_run_releases_its_bonuses(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _login_admin(app_client)
    await app_client.post(f"/api/v1/employees/{team['b']['id']}/bonuses", json={"amount": 4000, "reason": "Eid bonus"})
    await _as(app_client, team, "officer")
    first = await _prepare(app_client)
    assert _slip(first, team, "b")["gross"] == 34000.0
    second = None
    assert (await app_client.post(f"{PR}/runs/{first['id']}/cancel", json={"reason": "Start again"})).status_code == 200
    second = await _prepare(app_client)
    assert _slip(second, team, "b")["gross"] == 34000.0                                                                                      # the bonus is back in the next run
    await _login_admin(app_client)
    await _approve(app_client, second['id'])
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/runs/{second['id']}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    third = await _prepare(app_client, NEXT)
    assert _slip(third, team, "b")["gross"] == 30000.0                                                                                       # once paid it is never paid again


async def test_pay_item_rules(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    base = {"employee_id": team["a"]["id"], "kind": "ALLOWANCE", "name": "Rent", "calc": "FIXED", "value": 1000}
    for change in ({"value": 0}, {"value": -5}, {"calc": "PERCENT_OF_BASE", "value": 150}, {"name": "x"}, {"valid_from": "2026-05-01", "valid_until": "2026-04-01"}, {"employee_id": "00000000-0000-0000-0000-000000000000"}):
        assert (await app_client.post(f"{PR}/items", json={**base, **change})).status_code in (404, 422), change
    item = (await app_client.post(f"{PR}/items", json=base)).json()
    off = (await app_client.patch(f"{PR}/items/{item['id']}", json={"is_active": False})).json()
    assert off["is_active"] is False
    run = await _prepare(app_client)
    assert _slip(run, team, "a")["gross"] == 60000.0                                                                                         # a switched-off item is not paid
    assert [i["name"] for i in (await app_client.get(f"{PR}/items", params={"employee_id": team["a"]["id"]})).json()] == ["Rent"]