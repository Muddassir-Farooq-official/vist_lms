from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Book", "last_name": "Borrower",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "40001-0000001-1", "contact_no": "03004440000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "40001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03004449999",
        "present_address": "Somewhere", "province": "Punjab", "district": "Lahore", "city": "Lahore",
    }
    payload.update(overrides)
    return payload


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Lib", "last_name": "Rarian",
        "date_of_birth": "1985-01-01", "gender": "Male",
        "cnic": "40501-0000001-1", "contact_no": "03004450000",
        "hire_date": "2026-08-01", "department": "Library",
        "job_role_id": str(job_role_id), "base_salary": 50000,
        "password": "LibrarianPass123!",
    }
    payload.update(overrides)
    return payload


async def _login(app_client, username, password):
    resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


async def _login_admin(app_client):
    await _login(app_client, ADMIN["username"], ADMIN["password"])


async def _create_librarian(app_client, db_session, *, code, cnic) -> str:
    from app.employees.models import JobRole

    job_role = JobRole(code=code, title="Librarian", default_role_code="LIBRARIAN")
    db_session.add(job_role)
    await db_session.commit()
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id, cnic=cnic))
    assert resp.status_code == 201, resp.text
    username = resp.json()["username"]
    await _login(app_client, username, "LibrarianPass123!")
    change_resp = await app_client.post(
        "/api/v1/auth/change-password", json={"current_password": "LibrarianPass123!", "new_password": "LibrarianReal123!"}
    )
    assert change_resp.status_code == 204
    await _login(app_client, username, "LibrarianReal123!")
    return username


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


class TestLibraryFineIntegration:
    """Regression tests for the fix: returning a book with a fine amount
    only ever stored a local float on the book_issues row — it never
    created an actual Fine, despite FineSource.LIBRARY and
    Fine.related_entity_id existing in app/fines/models.py specifically
    for this connection. Updated for the request/approval workflow: the
    fine a librarian's return creates is a REQUEST (PENDING_APPROVAL),
    not an immediately-chargeable fine — invisible to the student and
    unpayable until an admin approves it."""

    async def _issue_a_book(self, app_client, student_id):
        book_resp = await app_client.post(
            "/api/v1/library/books", json={"title": "Overdue Test Book", "author": "A. Uthor", "initial_copies": 1}
        )
        assert book_resp.status_code == 201, book_resp.text
        book_id = book_resp.json()["id"]
        copies_resp = await app_client.get(f"/api/v1/library/books/{book_id}/copies")
        copy_id = copies_resp.json()[0]["id"]
        issue_resp = await app_client.post(
            "/api/v1/library/issues",
            json={"book_copy_id": copy_id, "borrower_type": "STUDENT", "borrower_id": student_id},
        )
        assert issue_resp.status_code == 201, issue_resp.text
        return issue_resp.json()["id"]

    async def test_returning_a_book_late_with_a_fine_creates_a_pending_approval_request(
        self, app_client, admin_user, db_session
    ):
        student_id, _ = await _create_and_activate_student(app_client, cnic="41001-0000001-1")
        await _login_admin(app_client)
        issue_id = await self._issue_a_book(app_client, student_id)
        librarian_username = await _create_librarian(app_client, db_session, code="LIBFINE1", cnic="40502-0000001-1")

        await _login(app_client, librarian_username, "LibrarianReal123!")
        return_resp = await app_client.post(
            f"/api/v1/library/issues/{issue_id}/return",
            json={"is_lost": False, "fine_amount": 150, "condition_notes": "Returned 5 days late"},
        )
        assert return_resp.status_code == 200, return_resp.text
        assert return_resp.json()["fine_amount"] == 150

        await _login_admin(app_client)
        fines_resp = await app_client.get("/api/v1/fines")
        matching = [f for f in fines_resp.json() if f["payer_id"] == student_id and f["amount"] == 150]
        assert len(matching) == 1, fines_resp.json()
        fine = matching[0]
        assert fine["source"] == "LIBRARY"
        assert fine["status"] == "PENDING_APPROVAL"
        assert "late" in fine["reason"].lower()

    async def test_a_pending_approval_fine_is_invisible_to_the_student_until_approved(
        self, app_client, admin_user, db_session
    ):
        student_id, username = await _create_and_activate_student(app_client, cnic="41002-0000001-1")
        await _login_admin(app_client)
        issue_id = await self._issue_a_book(app_client, student_id)
        librarian_username = await _create_librarian(app_client, db_session, code="LIBFINE2", cnic="40503-0000001-1")

        await _login(app_client, librarian_username, "LibrarianReal123!")
        await app_client.post(
            f"/api/v1/library/issues/{issue_id}/return", json={"is_lost": False, "fine_amount": 200}
        )

        await _login(app_client, username, "StudentPass123!")
        my_fines_resp = await app_client.get("/api/v1/fines/me")
        assert my_fines_resp.status_code == 200
        assert my_fines_resp.json() == []  # the request exists but must not be visible yet

    async def test_approving_the_request_makes_it_visible_and_payable(self, app_client, admin_user, db_session):
        student_id, username = await _create_and_activate_student(app_client, cnic="41003-0000001-1")
        await _login_admin(app_client)
        issue_id = await self._issue_a_book(app_client, student_id)
        librarian_username = await _create_librarian(app_client, db_session, code="LIBFINE3", cnic="40504-0000001-1")

        await _login(app_client, librarian_username, "LibrarianReal123!")
        await app_client.post(
            f"/api/v1/library/issues/{issue_id}/return", json={"is_lost": False, "fine_amount": 300}
        )

        await _login_admin(app_client)
        fines_resp = await app_client.get("/api/v1/fines")
        fine_id = next(f["id"] for f in fines_resp.json() if f["payer_id"] == student_id and f["amount"] == 300)

        approve_resp = await app_client.post(f"/api/v1/fines/{fine_id}/approve", json={"note": "Confirmed with librarian"})
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["status"] == "PENDING"
        assert approve_resp.json()["approved_by_name"] == "admin_test"

        await _login(app_client, username, "StudentPass123!")
        my_fines_resp = await app_client.get("/api/v1/fines/me")
        assert len(my_fines_resp.json()) == 1
        assert my_fines_resp.json()[0]["amount"] == 300

        await _login_admin(app_client)
        pay_resp = await app_client.post(f"/api/v1/fines/{fine_id}/mark-paid", json={"note": "Paid at library desk"})
        assert pay_resp.status_code == 200, pay_resp.text
        assert pay_resp.json()["status"] == "PAID"

    async def test_rejecting_the_request_keeps_it_permanently_invisible_to_the_student(
        self, app_client, admin_user, db_session
    ):
        student_id, username = await _create_and_activate_student(app_client, cnic="41004-0000001-1")
        await _login_admin(app_client)
        issue_id = await self._issue_a_book(app_client, student_id)
        librarian_username = await _create_librarian(app_client, db_session, code="LIBFINE4", cnic="40505-0000001-1")

        await _login(app_client, librarian_username, "LibrarianReal123!")
        await app_client.post(
            f"/api/v1/library/issues/{issue_id}/return", json={"is_lost": False, "fine_amount": 400}
        )

        await _login_admin(app_client)
        fines_resp = await app_client.get("/api/v1/fines")
        fine_id = next(f["id"] for f in fines_resp.json() if f["payer_id"] == student_id and f["amount"] == 400)

        reject_resp = await app_client.post(f"/api/v1/fines/{fine_id}/reject", json={"note": "Amount looks wrong, ask librarian to resubmit"})
        assert reject_resp.status_code == 200, reject_resp.text
        assert reject_resp.json()["status"] == "REJECTED"

        await _login(app_client, username, "StudentPass123!")
        my_fines_resp = await app_client.get("/api/v1/fines/me")
        assert my_fines_resp.json() == []

    async def test_returning_a_book_with_no_fine_creates_no_fine_record(self, app_client, admin_user, db_session):
        """The fix must not create a spurious Fine for an on-time,
        no-charge return."""
        student_id, _ = await _create_and_activate_student(app_client, cnic="41005-0000001-1")
        await _login_admin(app_client)
        issue_id = await self._issue_a_book(app_client, student_id)

        return_resp = await app_client.post(f"/api/v1/library/issues/{issue_id}/return", json={"is_lost": False})
        assert return_resp.status_code == 200, return_resp.text

        fines_resp = await app_client.get("/api/v1/fines")
        matching = [f for f in fines_resp.json() if f["payer_id"] == student_id]
        assert len(matching) == 0, fines_resp.json()