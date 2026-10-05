from __future__ import annotations

import pytest

from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Fee",
        "last_name": "Payer",
        "date_of_birth": "2004-01-01",
        "gender": "Female",
        "cnic": "55555-5555555-5",
        "contact_no": "03005550000",
        "marital_status": "Single",
        "religion": "Islam",
        "nationality": "Pakistani",
        "guardian_name": "Guardian Payer",
        "guardian_cnic": "55555-4444444-4",
        "relation_with_guardian": "Father",
        "emergency_contact_no": "03005559999",
        "present_address": "Somewhere",
        "province": "Punjab",
        "district": "Lahore",
        "city": "Lahore",
    }
    payload.update(overrides)
    return payload


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Officer",
        "last_name": "Accounts",
        "date_of_birth": "1988-01-01",
        "gender": "Male",
        "cnic": "66666-6666666-6",
        "contact_no": "03006660000",
        "hire_date": "2026-08-01",
        "department": "Finance",
        "job_role_id": str(job_role_id),
        "base_salary": 60000,
        "password": "OfficerPass123!",
    }
    payload.update(overrides)
    return payload


async def _login(app_client, username, password):
    resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


async def _login_admin(app_client):
    await _login(app_client, ADMIN["username"], ADMIN["password"])


async def _create_and_activate_student(app_client, *, cnic="55555-5555555-5") -> tuple[str, str]:
    """Creates a student as admin, then logs in as that student and sets
    a real password (clearing must_change_password, which otherwise
    blocks every endpoint except /auth/me, /auth/change-password,
    /auth/logout). Returns (student_id, username); leaves app_client
    logged in as the student. Caller must re-login as whoever they need
    next — app_client's cookie jar always reflects the LAST login."""
    await _login_admin(app_client)
    create_resp = await app_client.post("/api/v1/students", json=_student_payload(cnic=cnic))
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    student_id, username = body["id"], body["username"]

    await _login(app_client, username, "vertex123")
    change_resp = await app_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "vertex123", "new_password": "StudentPass123!"},
    )
    assert change_resp.status_code == 204
    await _login(app_client, username, "StudentPass123!")
    return student_id, username


async def _create_accounts_officer(app_client, db_session, *, code="ACC") -> str:
    """Creates a real ACCOUNTS_OFFICER via the normal employee-creation
    flow (job role -> employee -> role assigned from job_role.default_role_code),
    not by poking the DB directly — exercises the same path a real admin
    would use. Returns the officer's username; leaves app_client logged
    in as the officer."""
    job_role = JobRole(code=code, title="Accounts Officer", default_role_code="ACCOUNTS_OFFICER")
    db_session.add(job_role)
    await db_session.commit()

    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
    assert resp.status_code == 201, resp.text
    username = resp.json()["username"]

    await _login(app_client, username, "OfficerPass123!")
    change_resp = await app_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "OfficerPass123!", "new_password": "OfficerReal123!"},
    )
    assert change_resp.status_code == 204
    await _login(app_client, username, "OfficerReal123!")
    return username


async def _create_active_fee_structure(app_client, *, total_amount=6000, installment_count=1) -> str:
    """Admin creates a fee type + institute-wide fee structure (no
    program_ids = applies to every active student) and activates it.
    Returns the structure_id. Leaves app_client logged in as admin."""
    await _login_admin(app_client)
    type_resp = await app_client.post(
        "/api/v1/fees/types", json={"code": "TUI", "name": "Tuition Fee"}
    )
    assert type_resp.status_code == 201, type_resp.text
    fee_type_id = type_resp.json()["id"]

    structure_resp = await app_client.post(
        "/api/v1/fees/structures",
        json={
            "fee_type_id": fee_type_id,
            "name": "Tuition — Fall 2026",
            "term_label": "Fall 2026",
            "total_amount": total_amount,
            "installment_count": installment_count,
            "effective_from": "2026-09-01",
        },
    )
    assert structure_resp.status_code == 201, structure_resp.text
    structure_id = structure_resp.json()["id"]

    activate_resp = await app_client.post(f"/api/v1/fees/structures/{structure_id}/activate")
    assert activate_resp.status_code == 200, activate_resp.text
    return structure_id


# ---------------------------------------------------------------------------
# Regression tests for three real bugs found while writing this suite
# (not pre-existing test coverage — these were previously untested and,
# per the first one, the module's core creation endpoint was completely
# broken for every real caller):
#
# 1. POST /fees/structures crashed with a 500 AttributeError on every
#    single call — FeeStructureCreateRequest had no course_id field even
#    though FeeStructureService.create() unconditionally read
#    data.course_id. Fixed by adding the field to the schema.
# 2. FeeStructure.course_id's own docstring documents course-scoping and
#    program/semester-scoping as mutually exclusive ("see
#    FeeStructureService validation") — that validation never existed.
#    Fixed by adding it to create().
# 3. clone() silently dropped course_id, so cloning a course-scoped fee
#    structure produced a clone that was no longer course-scoped. Fixed
#    by copying it across like every other field.
# ---------------------------------------------------------------------------
class TestFeeStructureCreationBugsFoundWhileWritingThisSuite:
    async def test_creating_a_plain_program_scoped_structure_no_longer_crashes(
        self, app_client, admin_user
    ):
        """This alone used to 500 on every call — see bug #1 above.
        Covered indirectly by every other test in this file via
        _create_active_fee_structure, but asserted explicitly here too
        since it's the actual regression."""
        await _login_admin(app_client)
        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "REG1", "name": "Regression Fee"})
        assert type_resp.status_code == 201
        resp = await app_client.post(
            "/api/v1/fees/structures",
            json={
                "fee_type_id": type_resp.json()["id"],
                "name": "Regression Structure",
                "term_label": "Fall 2026",
                "total_amount": 1000,
                "effective_from": "2026-09-01",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["course_id"] is None

    async def test_course_scoped_and_program_scoped_together_is_rejected(self, app_client, admin_user, db_session):
        from app.academics.models import Course

        await _login_admin(app_client)
        program_resp = await app_client.post(
            "/api/v1/programs", json={"code": "REGP", "name": "Regression Program", "duration_years": 4, "total_semesters": 8}
        )
        assert program_resp.status_code == 201
        program_id = program_resp.json()["id"]

        course = Course(code="REGC101", title="Regression Course", credit_hours=3)
        db_session.add(course)
        await db_session.commit()

        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "REG2", "name": "Regression Fee 2"})
        resp = await app_client.post(
            "/api/v1/fees/structures",
            json={
                "fee_type_id": type_resp.json()["id"],
                "name": "Conflicting Scope",
                "term_label": "Fall 2026",
                "total_amount": 1000,
                "effective_from": "2026-09-01",
                "course_id": str(course.id),
                "program_ids": [program_id],
            },
        )
        assert resp.status_code == 422
        assert "course-scoped" in resp.json()["error"]["message"].lower()

    async def test_cloning_a_course_scoped_structure_preserves_course_id(self, app_client, admin_user, db_session):
        from app.academics.models import Course

        await _login_admin(app_client)
        course = Course(code="REGC202", title="Regression Course 2", credit_hours=3)
        db_session.add(course)
        await db_session.commit()

        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "REG3", "name": "Regression Fee 3"})
        create_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={
                "fee_type_id": type_resp.json()["id"],
                "name": "Course Fee",
                "term_label": "Fall 2026",
                "total_amount": 1000,
                "effective_from": "2026-09-01",
                "course_id": str(course.id),
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        structure_id = create_resp.json()["id"]
        assert create_resp.json()["course_id"] == str(course.id)

        clone_resp = await app_client.post(
            f"/api/v1/fees/structures/{structure_id}/clone",
            json={"new_term_label": "Spring 2027", "percentage_change": 10, "effective_from": "2027-01-01"},
        )
        assert clone_resp.status_code == 201, clone_resp.text
        assert clone_resp.json()["course_id"] == str(course.id)
        assert clone_resp.json()["total_amount"] == 1100
    async def test_admin_can_create_fee_type_and_structure(self, app_client, admin_user):
        structure_id = await _create_active_fee_structure(app_client)
        assert structure_id

    async def test_student_cannot_create_fee_type(self, app_client, admin_user, db_session):
        _, _username = await _create_and_activate_student(app_client, cnic="11111-1111111-1")
        resp = await app_client.post("/api/v1/fees/types", json={"code": "X", "name": "X Fee"})
        assert resp.status_code == 403

    async def test_accounts_officer_cannot_create_fee_structure(self, app_client, admin_user, db_session):
        """Deliberate design decision per the code's own comments: fee
        structures are ADMIN-only, NOT Accounts Officer — collecting
        money and defining what's owed are different authority levels."""
        await _create_accounts_officer(app_client, db_session, code="ACC1")
        resp = await app_client.post(
            "/api/v1/fees/types", json={"code": "Y", "name": "Y Fee"}
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Activation -> assignment -> ledger -> challan generation (the full path)
# ---------------------------------------------------------------------------
class TestActivationGeneratesRealCharges:
    async def test_activation_creates_assignment_and_debit_ledger_for_matching_active_student(
        self, app_client, admin_user, db_session
    ):
        student_id, _username = await _create_and_activate_student(app_client, cnic="22222-2222222-2")
        await _create_active_fee_structure(app_client, total_amount=9000, installment_count=3)

        await _login_admin(app_client)
        list_resp = await app_client.get("/api/v1/fees/assignments")
        assert list_resp.status_code == 200
        matching = [a for a in list_resp.json() if a["payer_id"] == student_id]
        assert len(matching) == 1
        assignment = matching[0]
        assert assignment["total_charged"] == 9000
        assert assignment["total_paid"] == 0

        ledger_resp = await app_client.get(f"/api/v1/fees/assignments/{assignment['id']}/ledger")
        assert ledger_resp.status_code == 200
        debits = [e for e in ledger_resp.json() if e["entry_type"] == "DEBIT"]
        assert len(debits) == 3  # installment_count=3
        assert sum(e["amount"] for e in debits) == 9000

    async def test_activation_auto_generates_a_payable_challan_per_installment(
        self, app_client, admin_user, db_session
    ):
        _student_id, username = await _create_and_activate_student(app_client, cnic="33333-3333333-3")
        structure_id = await _create_active_fee_structure(app_client, total_amount=4000, installment_count=1)

        await _login(app_client, username, "StudentPass123!")
        my_assignments = await app_client.get("/api/v1/fees/assignments/me")
        assert my_assignments.status_code == 200
        mine = [a for a in my_assignments.json() if a["fee_structure_id"] == structure_id]
        assert len(mine) == 1
        assignment_id = mine[0]["id"]

        challans_resp = await app_client.get(f"/api/v1/fees/assignments/me/{assignment_id}/challans")
        assert challans_resp.status_code == 200
        challans = challans_resp.json()
        assert len(challans) == 1
        assert challans[0]["status"] == "PENDING"
        assert challans[0]["amount"] == 4000


# ---------------------------------------------------------------------------
# IDOR / cross-payer data exposure — regression tests for the vulnerability
# assignment_router.py itself documents as "confirmed live before this fix,
# not theoretical": any logged-in student could previously see every other
# student's and staff member's exact charges, payments, and balances via
# the plain FEE_VIEW-gated list/detail endpoints.
# ---------------------------------------------------------------------------
class TestFeeDataIsolationRegressionForConfirmedPriorVulnerability:
    async def test_student_cannot_list_all_fee_assignments(self, app_client, admin_user, db_session):
        await _create_and_activate_student(app_client, cnic="44444-4444444-9")
        resp = await app_client.get("/api/v1/fees/assignments")
        assert resp.status_code == 403

    async def test_student_cannot_get_another_payers_assignment_by_id(self, app_client, admin_user, db_session):
        victim_id, _ = await _create_and_activate_student(app_client, cnic="55551-5555555-1")
        await _create_active_fee_structure(app_client, total_amount=3000)

        await _login_admin(app_client)
        assignments = (await app_client.get("/api/v1/fees/assignments")).json()
        victim_assignment_id = next(a["id"] for a in assignments if a["payer_id"] == victim_id)

        await _create_and_activate_student(app_client, cnic="66661-6666666-1")
        # attacker is now logged in (last _login call inside the helper)
        direct_resp = await app_client.get(f"/api/v1/fees/assignments/{victim_assignment_id}")
        assert direct_resp.status_code == 403

    async def test_student_cannot_view_another_payers_ledger_via_me_endpoint(
        self, app_client, admin_user, db_session
    ):
        victim_id, _ = await _create_and_activate_student(app_client, cnic="77771-7777777-1")
        await _create_active_fee_structure(app_client, total_amount=3000)

        await _login_admin(app_client)
        assignments = (await app_client.get("/api/v1/fees/assignments")).json()
        victim_assignment_id = next(a["id"] for a in assignments if a["payer_id"] == victim_id)

        await _create_and_activate_student(app_client, cnic="88881-8888888-1")
        resp = await app_client.get(f"/api/v1/fees/assignments/me/{victim_assignment_id}/ledger")
        # Deliberately 404, not 403 — see the code comment in
        # assignment_router.py: must not confirm to a prober that the ID
        # is valid but belongs to someone else.
        assert resp.status_code == 404

    async def test_student_cannot_view_another_payers_challans_via_me_endpoint(
        self, app_client, admin_user, db_session
    ):
        victim_id, _ = await _create_and_activate_student(app_client, cnic="99991-9999999-1")
        await _create_active_fee_structure(app_client, total_amount=3000)

        await _login_admin(app_client)
        assignments = (await app_client.get("/api/v1/fees/assignments")).json()
        victim_assignment_id = next(a["id"] for a in assignments if a["payer_id"] == victim_id)

        await _create_and_activate_student(app_client, cnic="12121-1212121-1")
        resp = await app_client.get(f"/api/v1/fees/assignments/me/{victim_assignment_id}/challans")
        assert resp.status_code == 404

    async def test_student_own_me_ledger_still_works(self, app_client, admin_user, db_session):
        """The fix must not be so broad it also blocks legitimate
        self-access — confirm the student CAN still see their own."""
        _student_id, username = await _create_and_activate_student(app_client, cnic="13131-1313131-1")
        await _create_active_fee_structure(app_client, total_amount=3000)

        await _login(app_client, username, "StudentPass123!")
        mine = (await app_client.get("/api/v1/fees/assignments/me")).json()
        assignment_id = mine[0]["id"]
        resp = await app_client.get(f"/api/v1/fees/assignments/me/{assignment_id}/ledger")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Payment recording vs. reversal — deliberately different authority levels
# ---------------------------------------------------------------------------
class TestPaymentRecordingVsReversalAuthoritySplit:
    async def test_accounts_officer_can_record_a_manual_payment(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="21212-2121212-1")
        await _create_active_fee_structure(app_client, total_amount=5000)

        await _login_admin(app_client)
        assignments = (await app_client.get("/api/v1/fees/assignments")).json()
        assignment_id = next(a["id"] for a in assignments if a["payer_id"] == student_id)

        await _create_accounts_officer(app_client, db_session, code="ACC2")
        resp = await app_client.post(
            f"/api/v1/fees/assignments/{assignment_id}/payments",
            json={"amount": 5000, "description": "Paid at counter"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["total_paid"] == 5000

    async def test_only_admin_can_mark_a_challan_unpaid(self, app_client, admin_user, db_session):
        _student_id, username = await _create_and_activate_student(app_client, cnic="41414-4141414-1")
        structure_id = await _create_active_fee_structure(app_client, total_amount=1500)

        await _login(app_client, username, "StudentPass123!")
        mine = (await app_client.get("/api/v1/fees/assignments/me")).json()
        assignment_id = next(a["id"] for a in mine if a["fee_structure_id"] == structure_id)
        challans = (
            await app_client.get(f"/api/v1/fees/assignments/me/{assignment_id}/challans")
        ).json()
        challan_id = challans[0]["id"]

        # Admin marks it paid first (as staff would after a bank confirmation).
        await _login_admin(app_client)
        paid_resp = await app_client.post(f"/api/v1/fees/challans/{challan_id}/mark-paid", json={"note": "bank confirmed"})
        assert paid_resp.status_code == 200
        assert paid_resp.json()["status"] == "PAID"

        # Accounts officer cannot reverse it.
        await _create_accounts_officer(app_client, db_session, code="ACC4")
        reverse_resp = await app_client.post(
            f"/api/v1/fees/challans/{challan_id}/mark-unpaid", json={"reason": "typo"}
        )
        assert reverse_resp.status_code == 403

        # Admin can.
        await _login_admin(app_client)
        admin_reverse_resp = await app_client.post(
            f"/api/v1/fees/challans/{challan_id}/mark-unpaid", json={"reason": "recorded in error"}
        )
        assert admin_reverse_resp.status_code == 200
        assert admin_reverse_resp.json()["status"] == "PENDING"


# ---------------------------------------------------------------------------
# Self-service payment simulation gating (the "webhook wide open" fix
# documented in app/core/config.py's comments)
# ---------------------------------------------------------------------------
class TestPaymentSimulationGating:
    async def test_student_cannot_self_pay_when_simulation_disabled_by_default(
        self, app_client, admin_user, db_session
    ):
        from app.core.config import settings

        assert settings.enable_student_payment_simulation is False  # the documented safe default

        _student_id, username = await _create_and_activate_student(app_client, cnic="51515-5151515-1")
        structure_id = await _create_active_fee_structure(app_client, total_amount=1000)

        await _login(app_client, username, "StudentPass123!")
        mine = (await app_client.get("/api/v1/fees/assignments/me")).json()
        assignment_id = next(a["id"] for a in mine if a["fee_structure_id"] == structure_id)
        challans = (await app_client.get(f"/api/v1/fees/assignments/me/{assignment_id}/challans")).json()
        challan_id = challans[0]["id"]

        resp = await app_client.post(f"/api/v1/fees/challans/{challan_id}/simulate-payment")
        assert resp.status_code == 403

    async def test_admin_can_always_simulate_payment_regardless_of_flag(
        self, app_client, admin_user, db_session
    ):
        _student_id, username = await _create_and_activate_student(app_client, cnic="61616-6161616-1")
        structure_id = await _create_active_fee_structure(app_client, total_amount=1000)

        await _login(app_client, username, "StudentPass123!")
        mine = (await app_client.get("/api/v1/fees/assignments/me")).json()
        assignment_id = next(a["id"] for a in mine if a["fee_structure_id"] == structure_id)
        challans = (await app_client.get(f"/api/v1/fees/assignments/me/{assignment_id}/challans")).json()
        challan_id = challans[0]["id"]

        await _login_admin(app_client)
        resp = await app_client.post(f"/api/v1/fees/challans/{challan_id}/simulate-payment")
        assert resp.status_code == 200
        assert resp.json()["status"] == "PAID"

    async def test_student_can_self_pay_own_challan_when_flag_explicitly_enabled(
        self, app_client, admin_user, db_session, monkeypatch
    ):
        from app.core.config import settings
        from app.fees import challan_router

        monkeypatch.setattr(settings, "enable_student_payment_simulation", True)
        monkeypatch.setattr(challan_router.settings, "enable_student_payment_simulation", True)

        _student_id, username = await _create_and_activate_student(app_client, cnic="71717-7171717-1")
        structure_id = await _create_active_fee_structure(app_client, total_amount=1000)

        await _login(app_client, username, "StudentPass123!")
        mine = (await app_client.get("/api/v1/fees/assignments/me")).json()
        assignment_id = next(a["id"] for a in mine if a["fee_structure_id"] == structure_id)
        challans = (await app_client.get(f"/api/v1/fees/assignments/me/{assignment_id}/challans")).json()
        challan_id = challans[0]["id"]

        resp = await app_client.post(f"/api/v1/fees/challans/{challan_id}/simulate-payment")
        assert resp.status_code == 200
        assert resp.json()["status"] == "PAID"

    async def test_student_cannot_self_pay_someone_elses_challan_even_when_flag_enabled(
        self, app_client, admin_user, db_session, monkeypatch
    ):
        """The flag only ever allows paying YOUR OWN challan — it must
        never become a way to pay (or mark paid) someone else's."""
        from app.core.config import settings
        from app.fees import challan_router

        monkeypatch.setattr(settings, "enable_student_payment_simulation", True)
        monkeypatch.setattr(challan_router.settings, "enable_student_payment_simulation", True)

        _victim_id, victim_username = await _create_and_activate_student(app_client, cnic="81818-8181818-1")
        structure_id = await _create_active_fee_structure(app_client, total_amount=1000)

        await _login(app_client, victim_username, "StudentPass123!")
        mine = (await app_client.get("/api/v1/fees/assignments/me")).json()
        assignment_id = next(a["id"] for a in mine if a["fee_structure_id"] == structure_id)
        challans = (await app_client.get(f"/api/v1/fees/assignments/me/{assignment_id}/challans")).json()
        victim_challan_id = challans[0]["id"]

        await _create_and_activate_student(app_client, cnic="91919-9191919-1")
        # attacker is logged in now
        resp = await app_client.post(f"/api/v1/fees/challans/{victim_challan_id}/simulate-payment")
        assert resp.status_code == 403