"""Staff leave applications (app/timekeeping/leave.py): apply, approve / refuse, the allowances, and payroll."""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.payroll import rules
from tests.integration.test_fees import _login_admin
from tests.integration.test_payroll import PR, THIS, _as, _cache, _prepare, _slip, _team

pytestmark = pytest.mark.asyncio
L = "/api/v1/staff-attendance/leave"
MON = THIS + timedelta(days=14 + (7 - (THIS + timedelta(days=14)).weekday()) % 7)      # a Monday in the second half of this month
TUE, WED = MON + timedelta(days=1), MON + timedelta(days=2)
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
NOTE = {"note": ("doctor-note.pdf", PDF, "application/pdf")}


@pytest.fixture(autouse=True)
def _rules(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "payroll_casual_leaves_teacher", 2)
    monkeypatch.setattr(settings, "payroll_casual_leaves_staff", 2)
    monkeypatch.setattr(settings, "payroll_sick_leaves_per_year", 8)
    _cache.clear()


async def test_apply_approve_and_the_days_are_excused(app_client, admin_user, db_session):
    from app.timekeeping.models import StaffAttendance

    team = await _team(app_client, db_session)
    await _as(app_client, team, "a")
    r = await app_client.post(L, data={"kind": "CASUAL", "first_day": str(MON), "last_day": str(TUE), "reason": "Sister's wedding"})
    assert r.status_code == 201, r.text
    assert r.json()["days"] == 2 and r.json()["status"] == "REQUESTED"
    clash = await app_client.post(L, data={"kind": "SICK", "first_day": str(TUE), "last_day": str(TUE), "reason": "Fever and cough"}, files=NOTE)
    assert clash.status_code == 422 and "already have leave" in clash.text
    mine = (await app_client.get(f"{L}/me")).json()
    assert mine["balances"]["casual_left_month"] == 2 and len(mine["requests"]) == 1          # only approved leave uses the allowance

    await _login_admin(app_client)
    waiting = (await app_client.get(L, params={"status": "REQUESTED"})).json()
    (row,) = [x for x in waiting if x["employee_id"] == team["a"]["id"]]
    assert row["balances"]["casual_left_month"] == 2
    no_reason = await app_client.post(f"{L}/{row['id']}/decide", json={"approve": False})
    assert no_reason.status_code == 422
    ok = await app_client.post(f"{L}/{row['id']}/decide", json={"approve": True})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    days = (await db_session.execute(select(StaffAttendance).where(StaffAttendance.employee_id == uuid.UUID(team["a"]["id"]))
                                     .execution_options(populate_existing=True))).scalars().all()
    excused = sorted(d.work_date for d in days if d.excused)
    assert excused == [MON, TUE] and all(d.excuse_reason.startswith("Casual leave: Sister") for d in days if d.excused)
    assert (await app_client.post(f"{L}/{row['id']}/decide", json={"approve": True})).status_code == 422           # decided once

    await _as(app_client, team, "a")
    assert (await app_client.get(f"{L}/me")).json()["balances"]["casual_left_month"] == 0
    over = await app_client.post(L, data={"kind": "CASUAL", "first_day": str(WED), "last_day": str(WED), "reason": "One more day please"})
    assert over.status_code == 422 and "unpaid" in over.text                                                     # beyond the allowance


async def test_unpaid_leave_is_deducted_on_the_payslip(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "b")
    r = await app_client.post(L, data={"kind": "CASUAL", "first_day": str(MON), "last_day": str(WED), "reason": "Travelling home"})
    assert r.status_code == 422                                                                                  # 3 casual days > 2 allowed
    r = await app_client.post(L, data={"kind": "UNPAID", "first_day": str(WED), "last_day": str(WED), "reason": "Personal work"})
    assert r.status_code == 201, r.text
    await _login_admin(app_client)
    assert (await app_client.post(f"{L}/{r.json()['id']}/decide", json={"approve": True})).status_code == 200
    run = await _prepare(app_client)
    s = _slip(run, team, "b")
    detail = (await app_client.get(f"{PR}/payslips/{s['id']}")).json()
    (ded,) = [x for x in detail["deduction_lines"] if x["source"] == "UNPAID_LEAVE"]
    assert ded["amount"] == pytest.approx(float(rules.unpaid_deduction(30000, int(s["working_days"]), Decimal(1))), abs=0.01)


async def test_approve_as_another_kind_and_the_sick_allowance(app_client, admin_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "payroll_sick_leaves_per_year", 1)
    team = await _team(app_client, db_session)
    await _as(app_client, team, "a")
    too_long = await app_client.post(L, data={"kind": "SICK", "first_day": str(MON), "last_day": str(TUE), "reason": "Typhoid fever"}, files=NOTE)
    assert too_long.status_code == 422 and "Sick leave" in too_long.text
    r = await app_client.post(L, data={"kind": "CASUAL", "first_day": str(MON), "last_day": str(TUE), "reason": "Typhoid fever"})
    assert r.status_code == 201
    await _login_admin(app_client)
    done = await app_client.post(f"{L}/{r.json()['id']}/decide", json={"approve": True, "as_kind": "UNPAID", "note": "Casual kept for later"})
    assert done.status_code == 200 and done.json()["kind"] == "UNPAID" and "Approved as unpaid leave" in done.json()["decision_note"]


async def test_only_the_applicant_can_withdraw_and_only_while_waiting(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "a")
    r = await app_client.post(L, data={"kind": "SICK", "first_day": str(MON), "last_day": str(MON), "reason": "Dentist appointment"}, files=NOTE)
    lid = r.json()["id"]
    await _as(app_client, team, "b")
    assert (await app_client.post(f"{L}/{lid}/cancel")).status_code == 404
    await _as(app_client, team, "a")
    assert (await app_client.post(f"{L}/{lid}/cancel")).json()["status"] == "CANCELLED"
    assert (await app_client.post(f"{L}/{lid}/cancel")).status_code == 422


async def test_approved_casual_leave_uses_up_the_monthly_allowance_in_payroll(db_session):
    from app.payroll.models import Payslip
    from app.payroll.service import PayrollService

    slip = Payslip(attendance_override=False)
    att = {"recorded": 20, "absent": 3, "late": 0, "early": 0, "missing_out": 0, "present": 17, "scheduled": 20, "excused": 2}
    PayrollService(db_session)._apply_attendance(slip, att, True, casual_on_leave=0)
    assert slip.casual_leaves == 2                                                    # 2 of the 3 absences forgiven
    PayrollService(db_session)._apply_attendance(slip, att, True, casual_on_leave=2)
    assert slip.casual_leaves == 0                                                    # the allowance went on approved leave


async def test_sick_leave_needs_a_real_medical_note_that_only_the_applicant_and_the_office_can_open(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "a")
    body = {"kind": "SICK", "first_day": str(MON), "last_day": str(MON), "reason": "Fever and flu"}
    none = await app_client.post(L, data=body)
    assert none.status_code == 422 and "medical note" in none.text
    fake = await app_client.post(L, data=body, files={"note": ("note.pdf", b"<html>not a pdf</html>", "application/pdf")})
    assert fake.status_code == 422
    word = await app_client.post(L, data=body, files={"note": ("note.docx", b"PK\x03\x04 word file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert word.status_code == 422 and "PDF or a photo" in word.text
    ok = await app_client.post(L, data=body, files=NOTE)
    assert ok.status_code == 201, ok.text
    url = ok.json()["note_url"]
    assert ok.json()["note_name"] == "doctor-note.pdf"
    mine = await app_client.get(url)
    assert mine.status_code == 200 and mine.content == PDF and mine.headers["content-type"] == "application/pdf"
    await _as(app_client, team, "b")
    assert (await app_client.get(url)).status_code == 404                      # another member of staff cannot open it
    await _login_admin(app_client)
    assert (await app_client.get(url)).content == PDF


async def test_approving_as_sick_needs_a_note_and_the_dashboard_counts_what_waits(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "a")
    r = await app_client.post(L, data={"kind": "UNPAID", "first_day": str(MON), "last_day": str(MON), "reason": "Was unwell at home"})
    await _login_admin(app_client)
    assert (await app_client.get(f"{L}/count")).json() == {"waiting": 1}
    sick = await app_client.post(f"{L}/{r.json()['id']}/decide", json={"approve": True, "as_kind": "SICK"})
    assert sick.status_code == 422 and "medical note" in sick.text
    assert (await app_client.post(f"{L}/{r.json()['id']}/decide", json={"approve": True})).status_code == 200
    assert (await app_client.get(f"{L}/count")).json() == {"waiting": 0}
