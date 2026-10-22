"""People and administration controls (launch step 6c): guardian and CNIC corrections, approved leave
reaching attendance, sign-in lockout that a stranger cannot trigger, documents kept when replaced,
staff seeing their own attendance, read-only student lookup for oversight roles, the HOD's department
scope, money back for returned store items, and what payroll owes onward."""
from __future__ import annotations

import io
from datetime import date, timedelta

import pytest
from sqlalchemy import select, update

from app.audit.models import AuditLog
from app.core.config import settings
from app.employees.models import Employee
from app.parents.models import ParentStudentLink
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_fees import _create_and_activate_student, _login, _login_admin, _student_payload
from tests.integration.test_payroll import _emp

pytestmark = pytest.mark.asyncio
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_guardian_and_cnic_corrections_need_a_reason_and_cut_the_old_parent_login(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="78001-0000001-1")
    await _login_admin(app_client)
    made = (await app_client.post(f"/api/v1/parents/students/{sid}")).json()
    assert made["temporary_password"]

    no_reason = await app_client.post(f"/api/v1/students/{sid}/identity", json={"guardian_cnic": "78009-1111111-1", "reason": "typo"})
    assert no_reason.status_code == 422
    bad = await app_client.post(f"/api/v1/students/{sid}/identity", json={"student_cnic": "123", "reason": "Wrong digits on the form"})
    assert bad.status_code == 422
    other = await app_client.post("/api/v1/students", json=_student_payload(cnic="78001-0000002-2", guardian_cnic="78009-2222222-2"))
    taken = await app_client.post(f"/api/v1/students/{sid}/identity", json={"student_cnic": other.json()["cnic"] if "cnic" in other.json() else "78001-0000002-2",
                                                                            "reason": "Wrong digits on the form"})
    assert taken.status_code in (409, 422)

    ok = await app_client.post(f"/api/v1/students/{sid}/identity", json={"student_cnic": "78001-0000009-9", "guardian_cnic": "7800911111111",
                                                                         "guardian_name": "Muhammad Aslam", "reason": "Guardian CNIC was typed wrong at admission"})
    assert ok.status_code == 200, ok.text
    access = (await app_client.get(f"/api/v1/parents/students/{sid}")).json()
    assert access["guardian_cnic"] == "78009-1111111-1" and access["guardian_name"] == "Muhammad Aslam" and not access["linked"]
    assert (await db_session.execute(select(ParentStudentLink).where(ParentStudentLink.student_id == sid))).first() is None
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "student.identity_corrected"))).scalars().one()
    assert log.after["reason"].startswith("Guardian CNIC") and log.before["cnic"] == "78001-0000001-1"
    same = await app_client.post(f"/api/v1/students/{sid}/identity", json={"student_cnic": "78001-0000009-9", "reason": "Nothing to change at all"})
    assert same.status_code == 422


async def test_approved_leave_excuses_staff_attendance_days(app_client, admin_user, db_session):
    await _login_admin(app_client)
    emp = await _emp(app_client, db_session, "LVE", 30000, "35202-7900001-1", "LIBRARIAN")
    t = await app_client.post("/api/v1/applications/types", json={"name": "Sick leave (test)", "requires_dates": True, "excuses_attendance": True})
    assert t.status_code == 201 and t.json()["excuses_attendance"] is True
    await _first_login(app_client, emp["username"], emp["pw"])
    start = date.today() - timedelta(days=3)
    a = await app_client.post("/api/v1/applications", data={"type_id": t.json()["id"], "subject": "Fever", "details": "Doctor advised rest for two days.",
                                                            "from_date": start.isoformat(), "to_date": (start + timedelta(days=1)).isoformat()})
    assert a.status_code == 201, a.text
    await _login_admin(app_client)
    assert (await app_client.post(f"/api/v1/applications/{a.json()['id']}/review", json={"action": "APPROVE", "note": "Get well soon"})).status_code == 200
    month = (await app_client.get(f"/api/v1/staff-attendance/month/{emp['id']}", params={"month": start.replace(day=1).isoformat()})).json()
    excused = [d for d in month["days"] if d and d["excused"]]
    assert excused and all("Sick leave (test)" in d["excuse_reason"] for d in excused)


async def test_wrong_passwords_from_elsewhere_do_not_lock_out_the_real_user(app_client, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 3)
    good = {"username": "admin_test", "password": "AdminPass123!"}
    for _ in range(5):                                             # successful sign-ins are never counted
        assert (await app_client.post("/api/v1/auth/login", json=good)).status_code == 200
    # a stranger from another address guesses
    for _ in range(3):
        r = await app_client.post("/api/v1/auth/login", json={**good, "password": "guess"}, headers={"X-Forwarded-For": "203.0.113.9"})
        assert r.status_code in (401, 429)
    # the account is now locked for unknown places, but the admin's usual computer still gets in
    assert (await app_client.post("/api/v1/auth/login", json=good)).status_code == 200


async def test_replaced_documents_are_kept_and_recorded(app_client, admin_user, db_session):
    sid, user = await _create_and_activate_student(app_client, cnic="78002-0000001-1")
    await _login_admin(app_client)
    up = lambda: app_client.post(f"/api/v1/students/{sid}/certificate", files={"file": ("c.pdf", io.BytesIO(PDF), "application/pdf")})  # noqa: E731
    assert (await up()).status_code == 200
    assert (await up()).status_code == 200
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.action == "student.certificate_replaced"))).scalars().all()
    assert len(rows) == 1 and rows[0].before["archived_as"].startswith("archive/")
    from pathlib import Path

    assert (Path(settings.storage_local_path) / rows[0].before["archived_as"]).exists()


async def test_staff_see_their_own_attendance(app_client, admin_user, db_session):
    await _login_admin(app_client)
    emp = await _emp(app_client, db_session, "ME1", 30000, "35202-7900002-2", "LIBRARIAN")
    assert (await app_client.post("/api/v1/staff-attendance/mark", json={"employee_id": emp["id"], "action": "IN"})).status_code == 200
    await _first_login(app_client, emp["username"], emp["pw"])
    me = await app_client.get("/api/v1/staff-attendance/me")
    assert me.status_code == 200 and me.json()["employee_id"] == emp["id"] and any(d and d["check_in"] for d in me.json()["days"])
    page = await app_client.get("/dashboard/library/payslips")
    assert page.status_code == 200 and "ma-month" in page.text and "My pay &amp; attendance" in page.text


async def test_principal_and_hod_can_look_students_up_read_only(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="78003-0000001-1")
    await _login_admin(app_client)
    pr, first = await _staff(app_client, db_session, "PRN", "PRINCIPAL", "35202-7900003-3")
    await _first_login(app_client, pr, first)
    found = (await app_client.get("/api/v1/students", params={"q": "78003-0000001-1"})).json()
    assert [s["id"] for s in found["items"]] == [sid]
    assert (await app_client.get(f"/api/v1/students/{sid}/full-profile")).status_code == 200
    assert (await app_client.get(f"/api/v1/results/students/{sid}/transcript")).status_code == 200
    assert (await app_client.patch(f"/api/v1/students/{sid}", json={"first_name": "Changed"})).status_code == 403
    assert (await app_client.get("/api/v1/students/export")).status_code == 403
    page = await app_client.get("/dashboard/principal/students")
    assert page.status_code == 200 and "student-lookup.js" in page.text


async def test_hod_scope_follows_their_department(app_client, admin_user, db_session):
    from app.core.scope import hod_department, teacher_ids_in

    await _login_admin(app_client)
    hod = await _emp(app_client, db_session, "HOD", 1, "35202-7900004-4", "HOD")
    t1 = await _emp(app_client, db_session, "TC1", 1, "35202-7900005-5", "TEACHER")
    t2 = await _emp(app_client, db_session, "TC2", 1, "35202-7900006-6", "TEACHER")
    await db_session.execute(update(Employee).where(Employee.id == t2["id"]).values(department="Nursing"))
    await db_session.execute(update(Employee).where(Employee.id == hod["id"]).values(department=" teaching "))
    await db_session.commit()

    hod_user = (await db_session.execute(select(Employee.user_id).where(Employee.id == hod["id"]))).scalar_one()

    class U:
        roles = ["HOD"]
        id = hod_user
    dept = await hod_department(db_session, U)
    assert dept == "teaching"
    mine = {str(i) for i in await teacher_ids_in(db_session, dept)}
    assert t1["id"] in mine and t2["id"] not in mine
    U.roles = ["HOD", "ADMIN"]
    assert await hod_department(db_session, U) is None          # the administration always sees everything


async def test_returned_store_items_give_the_money_back(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="78004-0000001-1")
    await _login_admin(app_client)
    item = (await app_client.post("/api/v1/inventory/items", json={"name": "Lab coat", "category": "Uniform", "unit": "piece", "sale_price": 1000,
                                                                   "opening_quantity": 10, "opening_unit_cost": 600})).json()
    sale = (await app_client.post("/api/v1/inventory/issue", json={"item_id": item["id"], "quantity": 2, "party_type": "STUDENT", "party_id": sid,
                                                                   "charge_mode": "PAID_NOW", "payment_method": "CASH"})).json()
    back = await app_client.post(f"/api/v1/inventory/movements/{sale['id']}/return", json={"quantity": 1, "refund": True, "refund_method": "CASH"})
    assert back.status_code == 201, back.text
    book = (await app_client.get("/api/v1/accounts/book", params={"kind": "EXPENSE"})).json()
    assert any(b["category"] == "Store refunds" and b["amount"] == 1000.0 for b in book)

    dues = (await app_client.post("/api/v1/inventory/issue", json={"item_id": item["id"], "quantity": 2, "party_type": "STUDENT", "party_id": sid,
                                                                   "charge_mode": "ADD_TO_DUES"})).json()
    assert (await app_client.post(f"/api/v1/inventory/movements/{dues['id']}/return", json={"quantity": 1, "refund": True})).status_code == 201
    fine = [f for f in (await app_client.get("/api/v1/fines", params={"source": "INVENTORY"})).json() if f["id"] == dues["fine_id"]][0]
    assert (fine["amount"], fine["status"]) == (1000.0, "PENDING")
    assert (await app_client.post(f"/api/v1/inventory/movements/{dues['id']}/return", json={"quantity": 1, "refund": True})).status_code == 201
    fine = [f for f in (await app_client.get("/api/v1/fines", params={"source": "INVENTORY"})).json() if f["id"] == dues["fine_id"]][0]
    assert fine["status"] == "WAIVED"
    free = (await app_client.post("/api/v1/inventory/issue", json={"item_id": item["id"], "quantity": 1, "party_type": "DEPARTMENT", "department": "Lab"})).json()
    assert (await app_client.post(f"/api/v1/inventory/movements/{free['id']}/return", json={"refund": True})).status_code == 422


async def test_payroll_shows_what_it_owes_onward(app_client, admin_user):
    await _login_admin(app_client)
    r = await app_client.get("/api/v1/payroll/withholdings", params={"year": date.today().year})
    assert r.status_code == 200 and set(r.json()) >= {"heads", "months", "total", "security_held_now"}


async def test_send_now_answers_quickly(app_client, admin_user, db_session, monkeypatch):
    from app.sms.service import SmsService

    await _login_admin(app_client)
    seen = {}

    async def fake(self, *, limit=100, client=None, time_budget=None):
        seen.update(limit=limit, time_budget=time_budget)
        return {"processed": 0, "sent": 0, "failed": 0, "logged": 0}
    monkeypatch.setattr(SmsService, "send_pending", fake)
    assert (await app_client.post("/api/v1/sms/send-now")).status_code == 200
    assert seen["time_budget"] and seen["time_budget"] <= 15 and seen["limit"] <= 50