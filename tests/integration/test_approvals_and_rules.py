"""Owner decisions 2, 8, 9, 10: second-person approval (fee and fine waivers, previous-dues write-offs, reopening
books, scholarships, purchases above the limit), hostel rent by days stayed, one attendance fine per term."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_today
from app.core.config import settings
from app.core.security import hash_password
from tests.integration.test_fees import _create_accounts_officer, _create_active_fee_structure, _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
AP = "/api/v1/approvals"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _second_admin(db):
    role = (await db.execute(select(Role).where(Role.code == Roles.ADMIN))).scalar_one()
    u = User(username="admin_two", password_hash=hash_password("AdminTwo123!x"), must_change_password=False)
    db.add(u)
    await db.flush()
    db.add(UserRole(user_id=u.id, role_id=role.id))
    await db.commit()
    return u


async def _as_two(client):
    await _login(client, "admin_two", "AdminTwo123!x")


async def _waiting(client):
    return (await client.get(AP)).json()


async def test_fee_waiver_waits_for_another_administrator(app_client, admin_user, db_session):
    await _second_admin(db_session)
    sid, _ = await _create_and_activate_student(app_client, cnic="76001-0000001-1")
    await _create_active_fee_structure(app_client, total_amount=12000, installment_count=2)
    await _login_admin(app_client)
    aid = (await app_client.get("/api/v1/fees/assignments")).json()[0]["id"]
    r = await app_client.patch(f"/api/v1/fees/assignments/{aid}/status", json={"status": "WAIVED", "reason": "Orphan student, board decision"})
    assert r.status_code == 202 and r.json()["pending_approval"]
    assert (await app_client.get(f"/api/v1/fees/assignments/{aid}")).json()["status"] == "ACTIVE"          # nothing changed yet
    again = await app_client.patch(f"/api/v1/fees/assignments/{aid}/status", json={"status": "WAIVED", "reason": "Orphan student, board decision"})
    assert again.status_code == 422                                                                           # already waiting
    req = (await _waiting(app_client))[0]
    assert req["kind"] == "FEE_STATUS" and req["amount"] == 12000.0
    own = await app_client.post(f"{AP}/{req['id']}/decide", json={"approve": True})
    assert own.status_code == 422 and "another administrator" in own.text                                     # not your own
    await _as_two(app_client)
    ok = await app_client.post(f"{AP}/{req['id']}/decide", json={"approve": True})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert (await app_client.get(f"/api/v1/fees/assignments/{aid}")).json()["status"] == "WAIVED"


async def test_scholarship_proposed_by_the_office_applies_only_after_approval(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="76001-0000002-2")
    await _create_active_fee_structure(app_client, total_amount=10000, installment_count=1)
    await _create_accounts_officer(app_client, db_session)
    r = await app_client.post("/api/v1/fees/scholarships", json={"student_id": sid, "name": "Need-based", "kind": "PERCENT", "value": 50,
                                                                 "valid_from": "2026-01-01", "reason": "Family income below limit"})
    assert r.status_code == 202, r.text
    await _login_admin(app_client)
    assert (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["summary"]["concession"] == 0.0
    req = next(x for x in await _waiting(app_client) if x["kind"] == "SCHOLARSHIP")
    rej = await app_client.post(f"{AP}/{req['id']}/decide", json={"approve": False})
    assert rej.status_code == 422                                                                             # a rejection needs a reason
    ok = await app_client.post(f"{AP}/{req['id']}/decide", json={"approve": True})
    assert ok.status_code == 200 and ok.json()["result"]["total_concession"] == 5000.0
    assert (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["summary"]["concession"] == 5000.0


async def test_reopening_books_and_fine_waiver_need_a_second_admin(app_client, admin_user, db_session):
    await _second_admin(db_session)
    await _login_admin(app_client)
    day = institute_today() - timedelta(days=10)
    assert (await app_client.put("/api/v1/accounts/period-close", json={"closed_through": str(day)})).status_code == 200   # closing: no approval
    r = await app_client.put("/api/v1/accounts/period-close", json={"closed_through": None, "reason": "Late bank statement to enter"})
    assert r.status_code == 202
    assert (await app_client.get("/api/v1/accounts/period-close")).json()["closed_through"] == str(day)
    sid, _ = await _create_and_activate_student(app_client, cnic="76001-0000003-3")
    await _login_admin(app_client)
    fine = (await app_client.post("/api/v1/fines", json={"payer_type": "STUDENT", "payer_id": sid, "amount": 500, "reason": "Broke a lab beaker"})).json()
    w = await app_client.post(f"/api/v1/fines/{fine['id']}/waive", json={"note": "Accident, not careless"})
    assert w.status_code == 202
    await _as_two(app_client)
    for req in await _waiting(app_client):
        assert (await app_client.post(f"{AP}/{req['id']}/decide", json={"approve": True})).status_code == 200
    assert (await app_client.get("/api/v1/accounts/period-close")).json()["closed_through"] is None
    from app.fines.models import Fine

    db_session.expire_all()
    assert (await db_session.get(Fine, uuid.UUID(fine["id"]))).status.value == "WAIVED"


async def test_sole_administrator_is_never_stuck(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="76001-0000004-4")
    await _create_active_fee_structure(app_client, total_amount=8000, installment_count=1)
    await _login_admin(app_client)
    aid = (await app_client.get("/api/v1/fees/assignments")).json()[0]["id"]
    r = await app_client.patch(f"/api/v1/fees/assignments/{aid}/status", json={"status": "CANCELLED", "reason": "Charged by mistake"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"


async def test_purchase_above_the_limit_needs_a_different_approver(app_client, admin_user, db_session):
    await _second_admin(db_session)
    await _login_admin(app_client)
    body = {"title": "Generator for the lab block", "justification": "Load-shedding stops practicals.",
            "items": [{"description": "15 kVA generator", "quantity": 1, "estimated_unit_price": 450000}]}
    po = (await app_client.post("/api/v1/purchases", json=body)).json()
    own = await app_client.post(f"/api/v1/purchases/{po['id']}/approve")
    assert own.status_code == 422 and "second-approval limit" in own.text
    small = (await app_client.post("/api/v1/purchases", json={**body, "items": [{"description": "Extension board", "quantity": 2, "estimated_unit_price": 1500}]})).json()
    assert (await app_client.post(f"/api/v1/purchases/{small['id']}/approve")).status_code == 200            # below the limit: as before
    await _as_two(app_client)
    assert (await app_client.post(f"/api/v1/purchases/{po['id']}/approve")).status_code == 200


# ====================================================================== hostel and attendance
async def test_leaving_the_hostel_mid_month_credits_the_unpaid_days(app_client, admin_user, db_session):
    from app.fines.models import Fine, FineStatus
    from app.hostel.service import HostelService

    sid, _ = await _create_and_activate_student(app_client, cnic="76001-0000005-5")
    await _login_admin(app_client)
    svc = HostelService(db_session)
    room = await svc.save_room({"code": "H-1", "capacity": 2, "monthly_fee": 9000}, room_id=None, actor_id=admin_user.id)
    today = institute_today()
    month = today.replace(day=1)
    stay = await svc.check_in(room_id=room.id, student_id=uuid.UUID(sid), start_date=month, bed_no=None, monthly_fee=None, actor_id=admin_user.id)
    out = await svc.charge_month(month, actor_id=admin_user.id)
    assert out["total"] == 9000.0
    import calendar
    days = calendar.monthrange(today.year, today.month)[1]
    if today.day == days:
        return                                                                                                # last day of the month: nothing to credit
    st = await svc.check_out(stay.id, end_date=today, reason="Moved home", actor_id=admin_user.id)
    await db_session.commit()
    stayed = today.day
    expected = round(9000 * stayed / days)
    assert st.credit_result["credited"] == 9000 - expected and st.credit_result["refund_due"] == 0
    fines = (await db_session.execute(select(Fine).where(Fine.payer_id == uuid.UUID(sid)).order_by(Fine.created_at))).scalars().all()
    assert [f.status for f in fines] == [FineStatus.WAIVED, FineStatus.PENDING] and float(fines[1].amount) == expected


async def test_one_attendance_fine_per_student_per_term(app_client, admin_user, db_session):
    from app.academics.models import Course, CourseOffering
    from app.attendance.service import AttendanceService
    from app.fines.models import Fine, FineSource
    from tests.integration.test_attendance_and_grading import _setup_offering_with_enrolled_students

    oid, students = await _setup_offering_with_enrolled_students(app_client, code="AFT", n_students=1, cnic_base=7600)
    sid = uuid.UUID(students[0][0])
    first = await db_session.get(CourseOffering, uuid.UUID(oid))
    c2 = Course(code="AFT-C2", title="Second course", credit_hours=3)
    db_session.add(c2)
    await db_session.flush()
    second = CourseOffering(course_id=c2.id, term_label=first.term_label, section_label=first.section_label)
    db_session.add(second)
    await db_session.flush()
    svc = AttendanceService(db_session)
    assert await svc._issue_attendance_fine_and_block(sid, 55.0, actor_id=admin_user.id, offering=first) is True
    assert await svc._issue_attendance_fine_and_block(sid, 60.0, actor_id=admin_user.id, offering=second) is False
    fines = (await db_session.execute(select(Fine).where(Fine.payer_id == sid, Fine.source == FineSource.ATTENDANCE))).scalars().all()
    assert len(fines) == 1 and "AFT-C1" in fines[0].reason and "AFT-C2" in fines[0].reason
