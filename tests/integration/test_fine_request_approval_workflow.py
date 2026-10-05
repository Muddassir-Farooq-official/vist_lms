from __future__ import annotations

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Fine", "last_name": "Target",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "90001-0000001-1", "contact_no": "03009990000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "90001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03009999999",
        "present_address": "Somewhere", "province": "Punjab", "district": "Lahore", "city": "Lahore",
    }
    payload.update(overrides)
    return payload


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Staff", "last_name": "Member",
        "date_of_birth": "1985-01-01", "gender": "Male",
        "cnic": "95001-0000001-1", "contact_no": "03009950000",
        "hire_date": "2026-08-01", "department": "Admin",
        "job_role_id": str(job_role_id), "base_salary": 50000,
        "password": "StaffPass123!",
    }
    payload.update(overrides)
    return payload


async def _login(app_client, username, password):
    resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


async def _login_admin(app_client):
    await _login(app_client, ADMIN["username"], ADMIN["password"])


async def _create_and_activate_student(app_client, *, cnic, **overrides) -> tuple[str, str]:
    await _login_admin(app_client)
    create_resp = await app_client.post("/api/v1/students", json=_student_payload(cnic=cnic, **overrides))
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    student_id, username = body["id"], body["username"]
    await _login(app_client, username, "vertex123")
    change_resp = await app_client.post(
        "/api/v1/auth/change-password", json={"current_password": "vertex123", "new_password": "StudentPass123!"}
    )
    assert change_resp.status_code == 204
    await _login(app_client, username, "StudentPass123!")
    return student_id, username


async def _create_staff(app_client, db_session, *, code, role_code, cnic) -> tuple[str, str]:
    job_role = JobRole(code=code, title=code, default_role_code=role_code)
    db_session.add(job_role)
    await db_session.commit()
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id, cnic=cnic))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    username = body["username"]
    await _login(app_client, username, "StaffPass123!")
    change_resp = await app_client.post(
        "/api/v1/auth/change-password", json={"current_password": "StaffPass123!", "new_password": "StaffReal123!"}
    )
    assert change_resp.status_code == 204
    await _login(app_client, username, "StaffReal123!")
    return body["id"], username


class TestFineRequestDomainRestriction:
    async def test_librarian_request_is_automatically_sourced_as_library(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="91001-0000001-1")
        _, librarian_username = await _create_staff(app_client, db_session, code="LIB1", role_code="LIBRARIAN", cnic="96001-0000001-1")

        await _login(app_client, librarian_username, "StaffReal123!")
        resp = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 100, "reason": "Damaged book cover"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["source"] == "LIBRARY"
        assert resp.json()["status"] == "PENDING_APPROVAL"

    async def test_accounts_officer_request_is_automatically_sourced_as_other(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="91002-0000001-1")
        _, officer_username = await _create_staff(
            app_client, db_session, code="ACC1", role_code="ACCOUNTS_OFFICER", cnic="96002-0000001-1"
        )

        await _login(app_client, officer_username, "StaffReal123!")
        resp = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 250, "reason": "Bounced cheque handling fee"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["source"] == "OTHER"
        assert resp.json()["status"] == "PENDING_APPROVAL"

    async def test_a_librarian_cannot_request_a_fine_with_a_source_outside_their_domain(
        self, app_client, admin_user, db_session
    ):
        """Defense in depth: even calling the service layer directly
        with a mismatched source (bypassing the schema, which normally
        prevents this entirely since FineRequestCreate has no source
        field) must be rejected."""
        from app.core.exceptions import AuthorizationError
        from app.fines.models import FinePayerType, FineSource
        from app.fines.service import FineService

        student_id, _ = await _create_and_activate_student(app_client, cnic="91003-0000001-1")
        _, librarian_username = await _create_staff(
            app_client, db_session, code="LIB2", role_code="LIBRARIAN", cnic="96003-0000001-1"
        )
        librarian_user = (
            await db_session.execute(select(User).where(User.username == librarian_username))
        ).scalar_one()

        with pytest.raises(AuthorizationError):
            await FineService(db_session).request(
                payer_type=FinePayerType.STUDENT,
                payer_id=student_id,
                amount=500,
                reason="Trying to sneak past domain restriction",
                source=FineSource.DISCIPLINARY,
                actor_id=librarian_user.id,
                actor_roles=["LIBRARIAN"],
            )

    async def test_student_without_fine_request_permission_gets_403(self, app_client, admin_user, db_session):
        student_id, username = await _create_and_activate_student(app_client, cnic="91004-0000001-1")
        await _login(app_client, username, "StudentPass123!")
        resp = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 50, "reason": "Self-requesting, should fail"},
        )
        assert resp.status_code == 403


class TestFineApprovalIsAdminOnly:
    async def test_librarian_cannot_approve_their_own_request(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="92001-0000001-1")
        _, librarian_username = await _create_staff(
            app_client, db_session, code="LIB3", role_code="LIBRARIAN", cnic="96004-0000001-1"
        )
        await _login(app_client, librarian_username, "StaffReal123!")
        request_resp = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 120, "reason": "Damaged spine"},
        )
        fine_id = request_resp.json()["id"]

        approve_resp = await app_client.post(f"/api/v1/fines/{fine_id}/approve", json={"note": "self-approving"})
        assert approve_resp.status_code == 403

    async def test_accounts_officer_cannot_approve_a_request(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="92002-0000001-1")
        _, officer_username = await _create_staff(
            app_client, db_session, code="ACC2", role_code="ACCOUNTS_OFFICER", cnic="96005-0000001-1"
        )
        await _login(app_client, officer_username, "StaffReal123!")
        request_resp = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 90, "reason": "Late document fee"},
        )
        fine_id = request_resp.json()["id"]

        approve_resp = await app_client.post(f"/api/v1/fines/{fine_id}/approve", json={})
        assert approve_resp.status_code == 403

    async def test_admin_can_approve_and_reject(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="92003-0000001-1")
        _, librarian_username = await _create_staff(
            app_client, db_session, code="LIB4", role_code="LIBRARIAN", cnic="96006-0000001-1"
        )
        await _login(app_client, librarian_username, "StaffReal123!")

        r1 = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 60, "reason": "First request"},
        )
        r2 = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 70, "reason": "Second request"},
        )
        fine_id_1, fine_id_2 = r1.json()["id"], r2.json()["id"]

        await _login_admin(app_client)
        approve_resp = await app_client.post(f"/api/v1/fines/{fine_id_1}/approve", json={})
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "PENDING"

        reject_resp = await app_client.post(f"/api/v1/fines/{fine_id_2}/reject", json={"note": "Duplicate of the first request"})
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "REJECTED"

    async def test_rejecting_requires_a_note(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="92004-0000001-1")
        _, librarian_username = await _create_staff(
            app_client, db_session, code="LIB5", role_code="LIBRARIAN", cnic="96007-0000001-1"
        )
        await _login(app_client, librarian_username, "StaffReal123!")
        request_resp = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 45, "reason": "Some reason"},
        )
        fine_id = request_resp.json()["id"]

        await _login_admin(app_client)
        reject_resp = await app_client.post(f"/api/v1/fines/{fine_id}/reject", json={"note": ""})
        assert reject_resp.status_code == 422


class TestMyRequestsTracking:
    async def test_requester_can_see_status_of_their_own_requests(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="93001-0000001-1")
        _, librarian_username = await _create_staff(
            app_client, db_session, code="LIB6", role_code="LIBRARIAN", cnic="96008-0000001-1"
        )
        await _login(app_client, librarian_username, "StaffReal123!")
        request_resp = await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 80, "reason": "Tracking test"},
        )
        fine_id = request_resp.json()["id"]

        my_requests_resp = await app_client.get("/api/v1/fines/my-requests")
        assert my_requests_resp.status_code == 200
        assert len(my_requests_resp.json()) == 1
        assert my_requests_resp.json()[0]["id"] == fine_id
        assert my_requests_resp.json()[0]["status"] == "PENDING_APPROVAL"

        await _login_admin(app_client)
        await app_client.post(f"/api/v1/fines/{fine_id}/approve", json={})

        await _login(app_client, librarian_username, "StaffReal123!")
        my_requests_resp = await app_client.get("/api/v1/fines/my-requests")
        assert my_requests_resp.json()[0]["status"] == "PENDING"  # sees the approval reflected

    async def test_requests_are_not_mixed_between_different_requesters(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="93002-0000001-1")
        _, librarian_a = await _create_staff(app_client, db_session, code="LIB7", role_code="LIBRARIAN", cnic="96009-0000001-1")
        _, librarian_b = await _create_staff(app_client, db_session, code="LIB8", role_code="LIBRARIAN", cnic="96010-0000001-1")

        await _login(app_client, librarian_a, "StaffReal123!")
        await app_client.post(
            "/api/v1/fines/request",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 30, "reason": "From librarian A"},
        )

        await _login(app_client, librarian_b, "StaffReal123!")
        my_requests_resp = await app_client.get("/api/v1/fines/my-requests")
        assert my_requests_resp.json() == []


class TestAdminDirectIssueUnaffected:
    async def test_admin_direct_issue_is_immediately_pending_and_approved(self, app_client, admin_user, db_session):
        student_id, username = await _create_and_activate_student(app_client, cnic="94001-0000001-1")
        await _login_admin(app_client)
        resp = await app_client.post(
            "/api/v1/fines",
            json={"payer_type": "STUDENT", "payer_id": student_id, "amount": 500, "reason": "Direct admin fine", "source": "DISCIPLINARY"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "PENDING"
        assert resp.json()["approved_by_name"] == "admin_test"

        # Immediately visible to the student — no approval step needed.
        await _login(app_client, username, "StudentPass123!")
        my_fines_resp = await app_client.get("/api/v1/fines/me")
        assert my_fines_resp.status_code == 200
        assert len(my_fines_resp.json()) == 1
        assert my_fines_resp.json()[0]["amount"] == 500