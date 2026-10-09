from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Notify", "last_name": "Test",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "78001-0000001-1", "contact_no": "03007780000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "78001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03007789999",
        "present_address": "Somewhere", "province": "Punjab", "district": "Lahore", "city": "Lahore",
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


async def _create_active_fee_structure(app_client, *, code, total_amount=1000) -> str:
    type_resp = await app_client.post("/api/v1/fees/types", json={"code": code, "name": f"{code} Fee"})
    assert type_resp.status_code == 201, type_resp.text
    structure_resp = await app_client.post(
        "/api/v1/fees/structures",
        json={
            "fee_type_id": type_resp.json()["id"], "name": f"{code} Structure", "term_label": "Fall 2026",
            "total_amount": total_amount, "effective_from": "2026-09-01",
        },
    )
    assert structure_resp.status_code == 201, structure_resp.text
    structure_id = structure_resp.json()["id"]
    activate_resp = await app_client.post(f"/api/v1/fees/structures/{structure_id}/activate")
    assert activate_resp.status_code == 200, activate_resp.text
    return structure_id


class TestFeeAssignmentNotification:
    async def test_a_new_fee_charge_notifies_the_student(self, app_client, admin_user, db_session):
        _student_id, username = await _create_and_activate_student(app_client, cnic="81001-0000001-1")
        await _login_admin(app_client)
        await _create_active_fee_structure(app_client, code="NOTIFY1", total_amount=1500)

        await _login(app_client, username, "StudentPass123!")
        notifications = await app_client.get("/api/v1/notifications/me")
        assert notifications.status_code == 200
        matching = [n for n in notifications.json() if n["category"] == "FEE" and "1500" in n["message"]]
        assert len(matching) == 1, notifications.json()
        assert "new fee" in matching[0]["title"].lower()


class TestPaymentNotifications:
    async def test_recording_a_manual_payment_notifies_the_student(self, app_client, admin_user, db_session):
        student_id, username = await _create_and_activate_student(app_client, cnic="81002-0000001-1")
        await _login_admin(app_client)
        await _create_active_fee_structure(app_client, code="NOTIFY2", total_amount=2000)

        assignments = (await app_client.get("/api/v1/fees/assignments")).json()
        assignment_id = next(a["id"] for a in assignments if a["payer_id"] == student_id)
        pay_resp = await app_client.post(
            f"/api/v1/fees/assignments/{assignment_id}/payments", json={"amount": 2000, "description": "Paid at counter", "method": "CASH"}
        )
        assert pay_resp.status_code == 201, pay_resp.text

        await _login(app_client, username, "StudentPass123!")
        notifications = await app_client.get("/api/v1/notifications/me")
        matching = [n for n in notifications.json() if n["category"] == "FEE" and "recorded" in n["title"].lower()]
        assert len(matching) == 1, notifications.json()

    async def test_simulated_challan_payment_notifies_the_student(self, app_client, admin_user, db_session):
        _student_id, username = await _create_and_activate_student(app_client, cnic="81003-0000001-1")
        await _login_admin(app_client)
        await _create_active_fee_structure(app_client, code="NOTIFY3", total_amount=900)

        await _login(app_client, username, "StudentPass123!")
        my_assignments = (await app_client.get("/api/v1/fees/assignments/me")).json()
        assignment_id = my_assignments[0]["id"]
        challans = (await app_client.get(f"/api/v1/fees/assignments/me/{assignment_id}/challans")).json()
        challan_id = challans[0]["id"]

        await _login_admin(app_client)
        pay_resp = await app_client.post(f"/api/v1/fees/challans/{challan_id}/simulate-payment")
        assert pay_resp.status_code == 200, pay_resp.text

        await _login(app_client, username, "StudentPass123!")
        notifications = await app_client.get("/api/v1/notifications/me")
        matching = [n for n in notifications.json() if n["category"] == "FEE" and "received" in n["title"].lower()]
        assert len(matching) == 1, notifications.json()

    async def test_reversing_a_payment_notifies_the_student(self, app_client, admin_user, db_session):
        _student_id, username = await _create_and_activate_student(app_client, cnic="81004-0000001-1")
        await _login_admin(app_client)
        await _create_active_fee_structure(app_client, code="NOTIFY4", total_amount=700)

        await _login(app_client, username, "StudentPass123!")
        my_assignments = (await app_client.get("/api/v1/fees/assignments/me")).json()
        my_challans = (await app_client.get(f"/api/v1/fees/assignments/me/{my_assignments[0]['id']}/challans")).json()
        challan_id = my_challans[0]["id"]

        await _login_admin(app_client)
        await app_client.post(f"/api/v1/fees/challans/{challan_id}/mark-paid", json={"note": "bank confirmed", "method": "BANK_DEPOSIT", "reference": "HBL-0003"})
        reverse_resp = await app_client.post(
            f"/api/v1/fees/challans/{challan_id}/mark-unpaid", json={"reason": "recorded in error"}
        )
        assert reverse_resp.status_code == 200, reverse_resp.text

        await _login(app_client, username, "StudentPass123!")
        notifications = await app_client.get("/api/v1/notifications/me")
        matching = [n for n in notifications.json() if n["category"] == "FEE" and "reversed" in n["title"].lower()]
        assert len(matching) == 1, notifications.json()