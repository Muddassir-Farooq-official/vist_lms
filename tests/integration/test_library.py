from __future__ import annotations

from datetime import date

import pytest

from app.students.models import Student, StudentStatus

pytestmark = pytest.mark.asyncio


async def _login_admin(app_client):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})


async def test_create_book_creates_real_copies(app_client, admin_user):
    await _login_admin(app_client)
    resp = await app_client.post(
        "/api/v1/library/books",
        json={"title": "Clean Code", "author": "Robert Martin", "initial_copies": 3},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_copies"] == 3
    assert body["available_copies"] == 3

    copies_resp = await app_client.get(f"/api/v1/library/books/{body['id']}/copies")
    copies = copies_resp.json()
    assert len(copies) == 3
    codes = {c["accession_code"] for c in copies}
    assert len(codes) == 3  # all unique
    assert all(c["status"] == "AVAILABLE" for c in copies)


async def test_add_more_copies(app_client, admin_user):
    await _login_admin(app_client)
    create = await app_client.post(
        "/api/v1/library/books", json={"title": "Book Two", "author": "A", "initial_copies": 1}
    )
    book_id = create.json()["id"]

    add_resp = await app_client.post(f"/api/v1/library/books/{book_id}/copies", json={"count": 2})
    assert add_resp.status_code == 201
    assert len(add_resp.json()) == 2

    detail = await app_client.get(f"/api/v1/library/books/{book_id}")
    assert detail.json()["total_copies"] == 3


async def test_issue_and_return_full_cycle(app_client, admin_user, db_session):
    student = Student(
        first_name="Test", last_name="Borrower", date_of_birth=date(2000, 1, 1), gender="Male",
        cnic="11111-2222233-4", contact_no="03001112222", marital_status="Single", religion="Islam",
        nationality="Pakistani", status=StudentStatus.ACTIVE, roll_number="LIB-01",
    )
    db_session.add(student)
    await db_session.commit()

    await _login_admin(app_client)
    create = await app_client.post(
        "/api/v1/library/books", json={"title": "Issued Book", "author": "A", "initial_copies": 1}
    )
    book_id = create.json()["id"]
    copies = await app_client.get(f"/api/v1/library/books/{book_id}/copies")
    copy_id = copies.json()[0]["id"]

    issue_resp = await app_client.post(
        "/api/v1/library/issues",
        json={"book_copy_id": copy_id, "borrower_type": "STUDENT", "borrower_id": str(student.id)},
    )
    assert issue_resp.status_code == 201
    issue_body = issue_resp.json()
    assert issue_body["status"] == "ISSUED"
    assert issue_body["borrower_name"] == "Test Borrower"

    # Copy must now be ISSUED, not AVAILABLE.
    copies_after_issue = await app_client.get(f"/api/v1/library/books/{book_id}/copies")
    assert copies_after_issue.json()[0]["status"] == "ISSUED"

    # Re-issuing the same (now unavailable) copy must fail.
    reissue = await app_client.post(
        "/api/v1/library/issues",
        json={"book_copy_id": copy_id, "borrower_type": "STUDENT", "borrower_id": str(student.id)},
    )
    assert reissue.status_code == 422

    # Return it.
    return_resp = await app_client.post(f"/api/v1/library/issues/{issue_body['id']}/return", json={})
    assert return_resp.status_code == 200
    assert return_resp.json()["status"] == "RETURNED"

    copies_after_return = await app_client.get(f"/api/v1/library/books/{book_id}/copies")
    assert copies_after_return.json()[0]["status"] == "AVAILABLE"

    # Returning again must fail — already returned.
    return_again = await app_client.post(f"/api/v1/library/issues/{issue_body['id']}/return", json={})
    assert return_again.status_code == 422


async def test_return_marked_lost_flips_copy_to_lost(app_client, admin_user, db_session):
    student = Student(
        first_name="Loser", last_name="OfBooks", date_of_birth=date(2000, 1, 1), gender="Female",
        cnic="22222-3333344-5", contact_no="03001112223", marital_status="Single", religion="Islam",
        nationality="Pakistani", status=StudentStatus.ACTIVE, roll_number="LIB-02",
    )
    db_session.add(student)
    await db_session.commit()

    await _login_admin(app_client)
    create = await app_client.post(
        "/api/v1/library/books", json={"title": "Lost Book", "author": "A", "initial_copies": 1}
    )
    book_id = create.json()["id"]
    copy_id = (await app_client.get(f"/api/v1/library/books/{book_id}/copies")).json()[0]["id"]

    issue = await app_client.post(
        "/api/v1/library/issues",
        json={"book_copy_id": copy_id, "borrower_type": "STUDENT", "borrower_id": str(student.id)},
    )
    issue_id = issue.json()["id"]

    lost_resp = await app_client.post(
        f"/api/v1/library/issues/{issue_id}/return", json={"is_lost": True, "fine_amount": 1500}
    )
    assert lost_resp.status_code == 200
    assert lost_resp.json()["status"] == "LOST"

    copy_after = (await app_client.get(f"/api/v1/library/books/{book_id}/copies")).json()[0]
    assert copy_after["status"] == "LOST"


async def test_borrower_search_finds_student(app_client, admin_user, db_session):
    student = Student(
        first_name="Findable", last_name="Person", date_of_birth=date(2000, 1, 1), gender="Male",
        cnic="33333-4444455-6", contact_no="03001112224", marital_status="Single", religion="Islam",
        nationality="Pakistani", status=StudentStatus.ACTIVE, roll_number="FND-01",
    )
    db_session.add(student)
    await db_session.commit()

    await _login_admin(app_client)
    resp = await app_client.get("/api/v1/library/borrowers/search", params={"q": "Findable"})
    assert resp.status_code == 200
    results = resp.json()
    assert any(r["display_name"] == "Findable Person" for r in results)


async def test_borrower_search_finds_student_by_full_name(app_client, admin_user, db_session):
    """Regression test: searching first_name/last_name separately used
    to miss a full "First Last" query entirely, since neither field
    alone contains the whole string with its space in the middle."""
    student = Student(
        first_name="Ali", last_name="Hassan", date_of_birth=date(2000, 1, 1), gender="Male",
        cnic="33333-5555566-7", contact_no="03001112225", marital_status="Single", religion="Islam",
        nationality="Pakistani", status=StudentStatus.ACTIVE, roll_number="FND-02",
    )
    db_session.add(student)
    await db_session.commit()

    await _login_admin(app_client)
    resp = await app_client.get("/api/v1/library/borrowers/search", params={"q": "Ali Hassan"})
    assert resp.status_code == 200
    results = resp.json()
    assert any(r["display_name"] == "Ali Hassan" for r in results)


async def test_librarian_has_full_library_access_but_nothing_else(app_client, db_session, seeded_roles):
    from app.auth.models import User, UserRole
    from app.auth.permissions import Roles
    from app.core.security import hash_password

    librarian = User(username="lib_test", password_hash=hash_password("LibPass123!"), must_change_password=False)
    db_session.add(librarian)
    await db_session.flush()
    db_session.add(UserRole(user_id=librarian.id, role_id=seeded_roles[Roles.LIBRARIAN].id))
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "lib_test", "password": "LibPass123!"})

    dashboard = await app_client.get("/api/v1/library/dashboard")
    assert dashboard.status_code == 200

    create_book = await app_client.post(
        "/api/v1/library/books", json={"title": "Librarian's Book", "author": "A", "initial_copies": 1}
    )
    assert create_book.status_code == 201

    blocked_employees = await app_client.get("/api/v1/employees")
    assert blocked_employees.status_code == 403

    blocked_programs = await app_client.post(
        "/api/v1/programs", json={"code": "XX", "name": "Test", "duration_years": 4, "total_semesters": 8}
    )
    assert blocked_programs.status_code == 403


async def test_non_library_role_cannot_issue_books(app_client, db_session, seeded_roles):
    from app.auth.models import User, UserRole
    from app.auth.permissions import Roles
    from app.core.security import hash_password

    teacher = User(username="lib_teacher", password_hash=hash_password("TeachPass123!"), must_change_password=False)
    db_session.add(teacher)
    await db_session.flush()
    db_session.add(UserRole(user_id=teacher.id, role_id=seeded_roles[Roles.TEACHER].id))
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "lib_teacher", "password": "TeachPass123!"})
    resp = await app_client.post(
        "/api/v1/library/issues",
        json={
            "book_copy_id": "00000000-0000-0000-0000-000000000000",
            "borrower_type": "STUDENT",
            "borrower_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert resp.status_code == 403


async def test_copy_status_cannot_change_while_issued(app_client, admin_user, db_session):
    student = Student(
        first_name="Holder", last_name="Ofbook", date_of_birth=date(2000, 1, 1), gender="Male",
        cnic="44444-5555566-7", contact_no="03001112225", marital_status="Single", religion="Islam",
        nationality="Pakistani", status=StudentStatus.ACTIVE, roll_number="LIB-03",
    )
    db_session.add(student)
    await db_session.commit()

    await _login_admin(app_client)
    create = await app_client.post(
        "/api/v1/library/books", json={"title": "Guarded Book", "author": "A", "initial_copies": 1}
    )
    book_id = create.json()["id"]
    copy_id = (await app_client.get(f"/api/v1/library/books/{book_id}/copies")).json()[0]["id"]

    await app_client.post(
        "/api/v1/library/issues",
        json={"book_copy_id": copy_id, "borrower_type": "STUDENT", "borrower_id": str(student.id)},
    )

    resp = await app_client.patch(f"/api/v1/library/books/copies/{copy_id}", json={"status": "RETIRED"})
    assert resp.status_code == 422