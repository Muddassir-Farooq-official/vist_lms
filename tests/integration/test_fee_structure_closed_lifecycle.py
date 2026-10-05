from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Close", "last_name": "Test",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "77001-0000001-1", "contact_no": "03007770001",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "77001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03007779998",
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


class TestFeeStructureClosedLifecycle:
    async def test_cannot_close_a_draft_structure(self, app_client, admin_user):
        await _login_admin(app_client)
        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "CLOSE1", "name": "Close Test 1"})
        structure_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={"fee_type_id": type_resp.json()["id"], "name": "Draft Structure", "term_label": "Fall 2026", "total_amount": 1000, "effective_from": "2026-09-01"},
        )
        structure_id = structure_resp.json()["id"]

        close_resp = await app_client.post(f"/api/v1/fees/structures/{structure_id}/close")
        assert close_resp.status_code == 422
        assert "draft" in close_resp.json()["error"]["message"].lower()

    async def test_cannot_close_an_already_closed_structure(self, app_client, admin_user):
        await _login_admin(app_client)
        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "CLOSE2", "name": "Close Test 2"})
        structure_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={"fee_type_id": type_resp.json()["id"], "name": "Close Twice", "term_label": "Fall 2026", "total_amount": 1000, "effective_from": "2026-09-01"},
        )
        structure_id = structure_resp.json()["id"]
        await app_client.post(f"/api/v1/fees/structures/{structure_id}/activate")

        first_close = await app_client.post(f"/api/v1/fees/structures/{structure_id}/close")
        assert first_close.status_code == 200

        second_close = await app_client.post(f"/api/v1/fees/structures/{structure_id}/close")
        assert second_close.status_code == 422
        assert "already closed" in second_close.json()["error"]["message"].lower()

    async def test_a_closed_structure_stops_charging_students_newly_matching_its_scope(
        self, app_client, admin_user, db_session
    ):
        """The actual regression this guards: activating, then closing
        BEFORE a matching student exists, must mean that student is
        never charged — a closed structure must behave as if it were
        never activated for anyone who shows up afterward."""
        await _login_admin(app_client)
        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "CLOSE3", "name": "Close Test 3"})
        structure_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={"fee_type_id": type_resp.json()["id"], "name": "Closed Before Student Existed", "term_label": "Fall 2026", "total_amount": 5000, "effective_from": "2026-09-01"},
        )
        structure_id = structure_resp.json()["id"]
        await app_client.post(f"/api/v1/fees/structures/{structure_id}/activate")
        close_resp = await app_client.post(f"/api/v1/fees/structures/{structure_id}/close")
        assert close_resp.status_code == 200
        assert close_resp.json()["status"] == "CLOSED"

        student_id, _ = await _create_and_activate_student(app_client, cnic="77002-0000001-1")

        await _login_admin(app_client)
        assignments = (await app_client.get("/api/v1/fees/assignments")).json()
        matching = [a for a in assignments if a["payer_id"] == student_id and a["total_charged"] == 5000]
        assert len(matching) == 0, assignments

    async def test_closing_does_not_touch_existing_assignments_or_their_dues(
        self, app_client, admin_user, db_session
    ):
        """Students already charged before closing must keep their full,
        payable dues — closing only stops NEW charges, per the
        documented policy ("existing dues remain payable")."""
        student_id, _ = await _create_and_activate_student(app_client, cnic="77003-0000001-1")

        await _login_admin(app_client)
        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "CLOSE4", "name": "Close Test 4"})
        structure_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={"fee_type_id": type_resp.json()["id"], "name": "Existing Dues Survive", "term_label": "Fall 2026", "total_amount": 3000, "effective_from": "2026-09-01"},
        )
        structure_id = structure_resp.json()["id"]
        await app_client.post(f"/api/v1/fees/structures/{structure_id}/activate")

        assignments_before = (await app_client.get("/api/v1/fees/assignments")).json()
        assignment = next(a for a in assignments_before if a["payer_id"] == student_id)
        assert assignment["total_charged"] == 3000

        close_resp = await app_client.post(f"/api/v1/fees/structures/{structure_id}/close")
        assert close_resp.status_code == 200

        assignments_after = (await app_client.get("/api/v1/fees/assignments")).json()
        assignment_after = next(a for a in assignments_after if a["payer_id"] == student_id)
        assert assignment_after["total_charged"] == 3000
        assert assignment_after["total_paid"] == 0  # still owed, still payable — untouched

    async def test_cloning_a_closed_structure_produces_a_fresh_draft_not_auto_active(
        self, app_client, admin_user
    ):
        """clone() must reset to DRAFT regardless of the source's
        status — a clone of a CLOSED structure must not silently start
        charging people without going through activate() again."""
        await _login_admin(app_client)
        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "CLOSE5", "name": "Close Test 5"})
        structure_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={"fee_type_id": type_resp.json()["id"], "name": "Source For Clone", "term_label": "Fall 2026", "total_amount": 1500, "effective_from": "2026-09-01"},
        )
        structure_id = structure_resp.json()["id"]
        await app_client.post(f"/api/v1/fees/structures/{structure_id}/activate")
        await app_client.post(f"/api/v1/fees/structures/{structure_id}/close")

        clone_resp = await app_client.post(
            f"/api/v1/fees/structures/{structure_id}/clone",
            json={"new_term_label": "Spring 2027", "percentage_change": 0, "effective_from": "2027-01-01"},
        )
        assert clone_resp.status_code == 201, clone_resp.text
        assert clone_resp.json()["status"] == "DRAFT"