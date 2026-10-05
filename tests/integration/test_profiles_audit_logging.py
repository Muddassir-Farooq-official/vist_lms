from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Audit", "last_name": "Test",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "85001-0000001-1", "contact_no": "03008550000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "85001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03008559999",
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


class TestQualificationAuditLogging:
    """Regression tests for the fix: app/profiles/service.py had NO
    audit logging at all — self-editable academic-credential data with
    real incentive to falsify and zero way to see who changed what."""

    async def test_adding_a_qualification_is_audited(self, app_client, admin_user, db_session):
        student_id, username = await _create_and_activate_student(app_client, cnic="86001-0000001-1")
        await _login(app_client, username, "StudentPass123!")
        add_resp = await app_client.post(
            "/api/v1/profiles/qualifications",
            params={"party_type": "STUDENT", "party_id": student_id},
            json={"degree_title": "BSc Nursing", "institution_name": "Vertex Institute", "year_completed": 2026},
        )
        assert add_resp.status_code == 201, add_resp.text
        qualification_id = add_resp.json()["id"]

        await _login_admin(app_client)
        audit_resp = await app_client.get("/api/v1/audit", params={"entity_type": "qualification"})
        assert audit_resp.status_code == 200
        matching = [
            e for e in audit_resp.json()["items"]
            if e["entity_id"] == qualification_id and e["action"] == "qualification.added"
        ]
        assert len(matching) == 1, audit_resp.json()

    async def test_updating_a_qualification_is_audited_with_before_and_after(self, app_client, admin_user, db_session):
        student_id, username = await _create_and_activate_student(app_client, cnic="86002-0000001-1")
        await _login(app_client, username, "StudentPass123!")
        add_resp = await app_client.post(
            "/api/v1/profiles/qualifications",
            params={"party_type": "STUDENT", "party_id": student_id},
            json={"degree_title": "BSc Nursing", "institution_name": "Vertex Institute", "year_completed": 2026},
        )
        qualification_id = add_resp.json()["id"]

        update_resp = await app_client.patch(
            f"/api/v1/profiles/qualifications/{qualification_id}", json={"degree_title": "BSc Nursing (Honours)"}
        )
        assert update_resp.status_code == 200, update_resp.text

        await _login_admin(app_client)
        audit_resp = await app_client.get("/api/v1/audit", params={"entity_type": "qualification"})
        matching = [
            e for e in audit_resp.json()["items"]
            if e["entity_id"] == qualification_id and e["action"] == "qualification.updated"
        ]
        assert len(matching) == 1, audit_resp.json()
        assert matching[0]["before"]["degree_title"] == "BSc Nursing"
        assert matching[0]["after"]["degree_title"] == "BSc Nursing (Honours)"

    async def test_deleting_a_qualification_is_audited(self, app_client, admin_user, db_session):
        student_id, username = await _create_and_activate_student(app_client, cnic="86003-0000001-1")
        await _login(app_client, username, "StudentPass123!")
        add_resp = await app_client.post(
            "/api/v1/profiles/qualifications",
            params={"party_type": "STUDENT", "party_id": student_id},
            json={"degree_title": "BSc Nursing", "institution_name": "Vertex Institute", "year_completed": 2026},
        )
        qualification_id = add_resp.json()["id"]

        delete_resp = await app_client.delete(f"/api/v1/profiles/qualifications/{qualification_id}")
        assert delete_resp.status_code == 204

        await _login_admin(app_client)
        audit_resp = await app_client.get("/api/v1/audit", params={"entity_type": "qualification"})
        matching = [
            e for e in audit_resp.json()["items"]
            if e["entity_id"] == qualification_id and e["action"] == "qualification.deleted"
        ]
        assert len(matching) == 1, audit_resp.json()
        assert matching[0]["before"]["degree_title"] == "BSc Nursing"