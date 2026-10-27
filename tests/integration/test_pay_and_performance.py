"""Absences after a paid payroll carried to the next one; an employee's own security deposit; the staff scorecard."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import update

from app.core.clock import institute_today
from app.core.config import settings
from app.payroll import rules as pay_rules
from app.payroll.models import Payslip
from app.performance.service import combine, grade
from tests.integration.test_announcements import _first_login
from tests.integration.test_fees import _login_admin
from tests.integration.test_timekeeping_payroll import LAST, PR, _approve, _import, _month_rows, _setup

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _pay(client, run_id):
    assert (await _approve(client, run_id)).status_code == 200
    paid = await client.post(f"{PR}/runs/{run_id}/pay", json={"payment_method": "CASH", "paid_date": institute_today().isoformat()})
    assert paid.status_code == 200, paid.text


def test_the_score_arithmetic():
    assert combine({"attendance": 1.0, "punctuality": 1.0, "tasks": None, "conduct": 1.0}) == 100          # no tasks: not a penalty
    assert combine({"attendance": 0.5, "punctuality": 1.0, "tasks": 1.0, "conduct": 1.0}) == 80             # 40*0.5 + 25 + 25 + 10
    assert combine({"attendance": None, "punctuality": None, "tasks": 1.0, "conduct": 1.0}) is None         # no working days: not scored
    assert [grade(s) for s in (95, 80, 65, 40, None)] == ["Excellent", "Good", "Fair", "Needs attention", "Not enough data"]


async def test_absences_after_a_paid_payroll_are_taken_once_next_month(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    await _import(app_client, await _month_rows(codes["STF"], absent={3, 4, 5, 6}))           # 4 absent, 2 forgiven as casual leave
    run = (await app_client.post(f"{PR}/runs", json={"month": LAST.isoformat(), "working_days": 26})).json()
    await _pay(app_client, run["id"])
    # As if last month's payroll had been worked out before the 5th and 6th: it only knew of 2 absences (both forgiven).
    await db_session.execute(update(Payslip).where(Payslip.run_id == run["id"], Payslip.employee_code == codes["STF"]).values(absent_days=2, casual_leaves=2))
    await db_session.commit()

    nxt = (await app_client.post(f"{PR}/runs", json={"month": institute_today().replace(day=1).isoformat()})).json()
    slip = next(x for x in (await app_client.get(f"{PR}/runs/{nxt['id']}")).json()["payslips"] if x["employee_code"] == codes["STF"])
    lines = (await app_client.get(f"{PR}/payslips/{slip['id']}")).json()["deduction_lines"]
    carried = [ln for ln in lines if ln["source"] == "LATE_ABSENT"]
    assert len(carried) == 1 and "2 days absent" in carried[0]["label"] and LAST.strftime("%B %Y") in carried[0]["label"]
    assert carried[0]["amount"] == pytest.approx(float(pay_rules.unpaid_deduction(Decimal(30000), 26, 2)), abs=0.01)
    # Recalculating does not take it twice; nobody else is charged.
    again = (await app_client.post(f"{PR}/runs/{nxt['id']}/recalculate")).json()
    slip = next(x for x in again["payslips"] if x["employee_code"] == codes["STF"])
    assert [ln["source"] for ln in (await app_client.get(f"{PR}/payslips/{slip['id']}")).json()["deduction_lines"]].count("LATE_ABSENT") == 1
    other = next(x for x in again["payslips"] if x["employee_code"] == codes["TCH"])
    assert not any(ln["source"] == "LATE_ABSENT" for ln in (await app_client.get(f"{PR}/payslips/{other['id']}")).json()["deduction_lines"])


async def test_an_employee_sees_their_own_security_deposit(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    settings.payroll_security_percent = 10
    try:
        assert (await app_client.put(f"{PR}/settings/{s['id']}", json={"security_percent": None, "security_opening": 1000})).status_code == 200
        run = (await app_client.post(f"{PR}/runs", json={"month": LAST.isoformat()})).json()
        await _pay(app_client, run["id"])
        await _first_login(app_client, s["username"], s["pw"])
        me = (await app_client.get(f"{PR}/me/security")).json()
        assert (me["percent"], me["held"], me["target"], me["remaining"], me["opening"]) == (10.0, 4000.0, 30000.0, 26000.0, 1000.0)
        assert [m["amount"] for m in me["months"]] == [3000.0]
    finally:
        settings.payroll_security_percent = 0.0
    await _login_admin(app_client)
    assert (await app_client.get(f"{PR}/me/security")).status_code == 404            # the administrator is not on the staff list


async def test_scorecard_ranks_staff_and_only_the_administration_sees_it(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    await _import(app_client, await _month_rows(codes["TCH"]))                                   # every day, on time
    await _import(app_client, await _month_rows(codes["STF"], absent={3, 4, 5}, late={6, 7}))     # some absences and late days
    card = (await app_client.get("/api/v1/performance", params={"month": LAST.isoformat()})).json()
    people = {p["employee_code"]: p for p in card["people"]}
    assert people[codes["TCH"]]["score"] == 100 and people[codes["TCH"]]["rank"] == 1
    assert people[codes["STF"]]["score"] < 100 and people[codes["STF"]]["rank"] == 2
    assert people[codes["NON"]]["score"] is None and people[codes["NON"]]["rank"] is None        # nothing recorded: not scored
    assert (await app_client.post(f"/api/v1/employees/{t['id']}/bonuses", json={"amount": 5000, "reason": "Best performer"})).status_code == 201
    card = (await app_client.get("/api/v1/performance", params={"month": LAST.isoformat()})).json()
    assert next(p for p in card["people"] if p["employee_code"] == codes["TCH"])["bonus_this_month"] == 5000.0
    await _first_login(app_client, s["username"], s["pw"])
    assert (await app_client.get("/api/v1/performance")).status_code == 403