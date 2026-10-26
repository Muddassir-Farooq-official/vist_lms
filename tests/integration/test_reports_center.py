"""Reports centre: who sees which reports, every report runs, and the figures agree with the operational screens."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clock import institute_today
from app.core.config import settings
from app.reports.center import REPORTS
from tests.integration.test_fees import _create_accounts_officer, _create_active_fee_structure, _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
R = "/api/v1/report-center"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _books(client):
    """Two students, one fee of Rs 10,000 in two installments; a scholarship, a cash payment, a bank payment, a reversal."""
    s1, _ = await _create_and_activate_student(client, cnic="74001-0000001-1")
    s2, _ = await _create_and_activate_student(client, cnic="74001-0000002-2")
    await _create_active_fee_structure(client, total_amount=10000, installment_count=2)
    await _login_admin(client)
    a = {x["payer_id"]: x["id"] for x in (await client.get("/api/v1/fees/assignments")).json()}
    assert (await client.post("/api/v1/fees/scholarships", json={"student_id": s1, "name": "Merit", "kind": "PERCENT", "value": 20,
                                                                 "valid_from": "2026-01-01", "reason": "Merit list"})).status_code == 201
    assert (await client.post(f"/api/v1/fees/assignments/{a[s1]}/payments", json={"amount": 3000, "method": "CASH"})).status_code == 201
    assert (await client.post(f"/api/v1/fees/assignments/{a[s2]}/payments", json={"amount": 5000, "method": "BANK_DEPOSIT", "reference": "HBL-1"})).status_code == 201
    assert (await client.post(f"/api/v1/fees/assignments/{a[s2]}/payments", json={"amount": 1000, "method": "CASH"})).status_code == 201
    led = [e for e in (await client.get(f"/api/v1/fees/assignments/{a[s2]}/ledger")).json() if e["entry_type"] == "CREDIT" and e["payment_method"] == "CASH"]
    assert (await client.post(f"/api/v1/fees/assignments/payments/{led[0]['id']}/reverse", json={"reason": "entered twice"})).status_code == 200
    return s1, s2, a


async def _run(client, key, **params):
    r = await client.get(f"{R}/run/{key}", params=params)
    assert r.status_code == 200, (key, r.text)
    return r.json()


async def test_every_report_runs_for_the_administrator(app_client, admin_user):
    s1, _s2, _a = await _books(app_client)
    month = institute_today().replace(day=1)
    payroll_needs = {"payroll.register"}
    for key, rep in REPORTS.items():
        params = {}
        if "student_id" in rep.needs:
            params["student_id"] = s1
        if "term" in rep.needs:
            params["term"] = "Fall 2026"
        if "item_id" in rep.needs or "check_id" in rep.needs:
            continue                                     # needs a store item / a physical check; covered in their own tests
        if key in payroll_needs:
            r = await app_client.get(f"{R}/run/{key}", params={"run_month": str(month)})
            assert r.status_code == 422 and "No payroll" in r.text
            continue
        d = await _run(app_client, key, **params)
        assert {"columns", "rows", "totals", "title"} <= set(d), key
        csv = await app_client.get(f"{R}/export/{key}", params=params)
        assert csv.status_code == 200 and d["title"] in csv.text.splitlines()[0], key


async def test_fee_position_matches_the_fee_dashboard(app_client, admin_user):
    await _books(app_client)
    d = await _run(app_client, "fees.position")
    dash = (await app_client.get("/api/v1/fees/dashboard")).json()
    t = d["totals"]
    assert t["payable"] == dash["total_charged"] == 18000.0           # 20,000 billed - 2,000 scholarship
    assert t["outstanding"] == dash["total_outstanding"] == 10000.0   # 18,000 - 3,000 - 5,000 (the reversed 1,000 does not count)
    assert t["paid"] == 8000.0 and t["discount"] == 2000.0 and t["students"] == 2
    by_head = await _run(app_client, "fees.position", group_by="fee_head")
    assert by_head["totals"]["payable"] == t["payable"]


async def test_cash_book_balance_and_statement(app_client, admin_user):
    s1, _s2, _a = await _books(app_client)
    today = institute_today()
    d = await _run(app_client, "accounts.cash_book", from_date=str(today), to_date=str(today))
    t = d["totals"]
    assert t["received"] == 4000.0 and t["paid"] == 1000.0 and t["balance"] == 3000.0       # 3,000 + 1,000 in, the 1,000 reversed out
    later = await _run(app_client, "accounts.cash_book", from_date=str(today + timedelta(days=1)), to_date=str(today + timedelta(days=2)))
    assert later["rows"] == [] and "Rs. 3,000.00" in later["subtitle"]                     # tomorrow opens with today's closing cash
    daily = await _run(app_client, "accounts.cash_book", from_date=str(today), to_date=str(today), view="daily")
    assert daily["rows"][0]["closing"] == 3000.0
    st = await _run(app_client, "fees.statement", student_id=s1)
    assert st["totals"]["balance"] == 5000.0                                                # 10,000 - 2,000 discount - 3,000 paid
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{s1}")).json()
    assert acct["summary"]["remaining"] == st["totals"]["balance"]


async def test_income_statement_matches_the_accounts_summary(app_client, admin_user):
    await _books(app_client)
    d = await _run(app_client, "accounts.income_statement")
    surplus = next(r for r in d["rows"] if r["line"].startswith("Surplus"))
    first = d["columns"][2]["key"]
    summ = (await app_client.get("/api/v1/accounts/summary", params={"from_date": first + "-01"})).json()
    assert surplus["total"] == summ["net"] == 8000.0


async def test_who_sees_which_reports(app_client, admin_user, db_session):
    await _create_accounts_officer(app_client, db_session)
    officer = (await app_client.get("/api/v1/auth/me")).json()
    cat = (await app_client.get(f"{R}/catalog")).json()
    keys = {r["key"] for g in cat["groups"] for r in g["reports"]}
    assert {"fees.position", "accounts.cash_book", "payroll.register", "store.valuation"} <= keys
    assert "staff.attendance" not in keys and "academics.result_analysis" not in keys     # no staff attendance, no academic oversight
    assert (await app_client.get(f"{R}/run/staff.attendance")).status_code == 404
    _sid, user = await _create_and_activate_student(app_client, cnic="74001-0000009-9")
    assert (await app_client.get(f"{R}/catalog")).status_code == 403                       # a student has no reports
    assert (await app_client.get(f"{R}/run/fees.position")).status_code == 404
    assert officer["username"]


async def test_store_ledger_and_csv_has_no_formulas(app_client, admin_user):
    await _login_admin(app_client)
    item = (await app_client.post("/api/v1/inventory/items", json={"name": "=HYPERLINK(\"x\")", "unit": "pcs", "opening_quantity": 10, "opening_unit_cost": 50})).json()
    d = await _run(app_client, "store.ledger", item_id=item["id"])
    assert d["totals"]["balance"] == 10.0 and d["rows"][0]["in"] == 10.0
    v = await app_client.get(f"{R}/export/store.valuation")
    assert "'=HYPERLINK" in v.text and ',=HYPERLINK' not in v.text


async def test_attendance_shortage_and_result_analysis_with_real_records(app_client, admin_user, db_session):
    import uuid as _uuid
    from datetime import datetime, time, timezone

    from app.academics.models import ClassSession, ClassSessionStatus, EnrollmentResult
    from app.attendance.models import AttendanceRecord, AttendanceStatus
    from tests.integration.test_attendance_and_grading import _setup_offering_with_enrolled_students

    oid, students = await _setup_offering_with_enrolled_students(app_client, code="RPT", n_students=2, cnic_base=7400)
    now = datetime.now(timezone.utc)
    sessions = []
    for i in range(10):
        s = ClassSession(course_offering_id=_uuid.UUID(oid), session_date=institute_today() - timedelta(days=20 - i), start_time=time(9), end_time=time(10),
                         room="R1", status=ClassSessionStatus.SCHEDULED)
        db_session.add(s)
        sessions.append(s)
    await db_session.flush()
    (good, _u1, e1), (weak, _u2, e2) = students
    for i, s in enumerate(sessions):
        db_session.add(AttendanceRecord(class_session_id=s.id, student_id=_uuid.UUID(good), status=AttendanceStatus.PRESENT, marked_at=now))
        db_session.add(AttendanceRecord(class_session_id=s.id, student_id=_uuid.UUID(weak),
                                        status=AttendanceStatus.PRESENT if i < 6 else AttendanceStatus.ABSENT, marked_at=now))   # 60%
    for eid, pct, grade, gp, outcome in ((e1, 85, "A", 4.0, "PASSED"), (e2, 40, "F", 0.0, "FAILED")):
        db_session.add(EnrollmentResult(enrollment_id=_uuid.UUID(eid), percentage=pct, total_obtained=pct, total_max=100, letter_grade=grade,
                                        grade_points=gp, credit_hours=3, outcome=outcome, computed_at=now))
    await db_session.commit()
    sh = await _run(app_client, "academics.attendance_shortage", term="Fall 2026")
    assert len(sh["rows"]) == 1
    row = sh["rows"][0]
    assert row["percent"] == 60.0 and row["held"] == 10 and row["need"] == 6      # 12 of 16 = 75%
    ra = await _run(app_client, "academics.result_analysis", term="Fall 2026")
    r = next(x for x in ra["rows"] if x["course"].startswith("RPT"))
    assert (r["enrolled"], r["results"], r["passed"], r["failed"], r["pass_pct"], r["g:A"], r["g:F"]) == (2, 2, 1, 1, 50.0, 1, 1)
    cs = await _run(app_client, "academics.class_strength")
    assert cs["totals"]["active"] >= 2
