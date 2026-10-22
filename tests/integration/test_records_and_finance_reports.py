"""Records (certificates, ID cards, documents on file, degree audit, public check page), finance reports for the accounts office,
and the pages that carry them."""
from __future__ import annotations

import io

import pytest

from tests.integration.test_fees import _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


async def test_certificate_is_numbered_checkable_and_can_be_cancelled(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="79001-0000001-1")
    await _login_admin(app_client)
    made = await app_client.post(f"/api/v1/records/students/{sid}/certificates/bonafide")
    assert made.status_code == 200, made.text
    assert made.headers["content-type"] == "application/pdf" and made.content.startswith(b"%PDF")

    issued = (await app_client.get(f"/api/v1/records/students/{sid}/issued")).json()
    assert len(issued) == 1 and issued[0]["kind"] == "BONAFIDE" and not issued[0]["revoked"]
    code = issued[0]["code"]

    anon = app_client            # the check page is public: it must work with no special access
    ok = await anon.get(f"/api/v1/records/verify/{code}")
    assert ok.status_code == 200 and ok.json()["owner_name"] and ok.json()["revoked"] is False
    page = await anon.get(f"/verify/{code}")
    assert page.status_code == 200 and "genuine" in page.text
    assert (await anon.get("/api/v1/records/verify/NOSUCHCODE")).status_code == 404

    assert (await app_client.post(f"/api/v1/records/issued/{issued[0]['id']}/revoke", json={"reason": "Issued by mistake"})).status_code == 200
    assert (await anon.get(f"/api/v1/records/verify/{code}")).json()["revoked"] is True
    assert "cancelled" in (await anon.get(f"/verify/{code}")).text


async def test_clearance_blocks_a_character_certificate_until_a_reason_is_given(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="79001-0000002-2")
    await _login_admin(app_client)
    fine = await app_client.post("/api/v1/fines", json={"payer_type": "STUDENT", "payer_id": sid, "amount": 500, "reason": "Library damage fee"})
    assert fine.status_code in (200, 201), fine.text
    clearance = (await app_client.get(f"/api/v1/records/students/{sid}/clearance")).json()
    assert clearance["clear"] is False and any("fine" in p.lower() for p in clearance["problems"])
    blocked = await app_client.post(f"/api/v1/records/students/{sid}/certificates/character")
    assert blocked.status_code == 422 and "give a reason" in blocked.text
    allowed = await app_client.post(f"/api/v1/records/students/{sid}/certificates/character", data={"override_reason": "Principal allowed it in writing"})
    assert allowed.status_code == 200 and allowed.content.startswith(b"%PDF")


async def test_id_cards_degree_audit_and_documents_on_file(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="79001-0000003-3")
    await _login_admin(app_client)
    cards = await app_client.post("/api/v1/records/id-cards", json={"student_ids": [sid]})
    assert cards.status_code == 200 and cards.content.startswith(b"%PDF")
    assert (await app_client.post("/api/v1/records/id-cards", json={})).status_code == 422

    audit = (await app_client.get(f"/api/v1/records/students/{sid}/degree-audit")).json()
    assert set(audit) >= {"credits_passed", "credits_required", "cgpa", "min_cgpa", "missing", "problems", "clear"}

    up = await app_client.post(f"/api/v1/records/students/{sid}/documents", data={"kind": "CNIC", "title": "B-Form"}, files={"file": ("bform.pdf", io.BytesIO(PDF), "application/pdf")})
    assert up.status_code == 201, up.text
    doc = up.json()
    assert doc["verified"] is False and doc["kind_label"]
    assert (await app_client.get(f"/api/v1/records/documents/{doc['id']}/file")).status_code == 200
    assert (await app_client.post(f"/api/v1/records/documents/{doc['id']}/verify")).json()["verified"] is True
    assert (await app_client.post(f"/api/v1/records/documents/{doc['id']}/archive", json={"reason": "Replaced by a clearer scan"})).json()["archived"] is True
    assert (await app_client.get(f"/api/v1/records/students/{sid}/documents")).json() == []
    assert len((await app_client.get(f"/api/v1/records/students/{sid}/documents", params={"include_archived": "true"})).json()) == 1
    bad = await app_client.post(f"/api/v1/records/students/{sid}/documents", data={"kind": "CNIC"}, files={"file": ("x.exe", io.BytesIO(b"MZ..."), "application/octet-stream")})
    assert bad.status_code == 422


async def test_finance_reports_answer_and_export(app_client, admin_user):
    await _login_admin(app_client)
    for path in ("/api/v1/finance-reports/defaulters", "/api/v1/finance-reports/collection", "/api/v1/finance-reports/payables"):
        r = await app_client.get(path)
        assert r.status_code == 200, (path, r.text)
    for path in ("/api/v1/finance-reports/defaulters.csv", "/api/v1/finance-reports/collection.csv", "/api/v1/finance-reports/book.csv", "/api/v1/finance-reports/deductions.csv"):
        r = await app_client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv"), (path, r.text)
    stmt = b"Date,Description,Amount\n2026-01-05,HBL 9988 deposit,5000\n2026-01-06,Cheque 11,-250\n"
    rec = await app_client.post("/api/v1/finance-reports/reconcile", files={"file": ("stmt.csv", io.BytesIO(stmt), "text/csv")})
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body["bank_lines"] == 2 and len(body["unmatched_bank"]) == 2 and body["matched"] == []
    assert (await app_client.post("/api/v1/finance-reports/reconcile", files={"file": ("s.csv", io.BytesIO(b"nothing,here\n1,2\n"), "text/csv")})).status_code == 422


async def test_new_pages_open_for_the_office(app_client, admin_user):
    await _login_admin(app_client)
    expect = {"/dashboard/admin/calendar": "cal-month", "/dashboard/admin/hostel": "h-rooms", "/dashboard/admin/finance-reports": "df-table",
              "/dashboard/admin/students": "identity-modal", "/dashboard/admin/employees": "ee-iban", "/dashboard/admin/result-sheets": "rs-outstanding",
              "/dashboard/admin/payroll": "wh-table"}
    for path, marker in expect.items():
        r = await app_client.get(path)
        assert r.status_code == 200 and marker in r.text, (path, r.status_code)
    assert (await app_client.get("/forgot-password")).status_code == 200


async def test_a_recorded_payment_can_be_reversed_and_its_receipt_prints_void(app_client, admin_user):
    from tests.integration.test_finance_controls import FA, _student_with_fee

    _sid, _user, aid = await _student_with_fee(app_client, "79002-0000001-1", total=10000, n=2)
    paid = await app_client.post(f"{FA}/{aid}/payments", json={"amount": 2500, "method": "CASH"})
    assert paid.status_code == 201, paid.text
    lines = (await app_client.get(f"{FA}/{aid}/statement")).json()["lines"]
    pay = next(l for l in lines if l["type"] == "CREDIT")
    assert pay["reversed"] is False and pay["receipt_number"]
    assert (await app_client.post(f"{FA}/payments/{pay['id']}/reverse", json={"reason": "no"})).status_code == 422
    done = await app_client.post(f"{FA}/payments/{pay['id']}/reverse", json={"reason": "Entered by mistake"})
    assert done.status_code == 200 and done.json()["reversed"] is True
    after = (await app_client.get(f"{FA}/{aid}/statement")).json()
    assert next(l for l in after["lines"] if l["id"] == pay["id"])["reversed"] is True and after["balance"] == 10000.0
    assert (await app_client.post(f"{FA}/payments/{pay['id']}/reverse", json={"reason": "Entered by mistake"})).status_code == 422
    rec = await app_client.get(f"/api/v1/fees/receipts/{pay['id']}/pdf")
    assert rec.status_code == 200 and rec.content.startswith(b"%PDF")


async def test_bulk_sms_counts_first_then_queues_once_per_number(app_client, admin_user):
    from tests.integration.test_attendance_and_grading import _create_and_activate_student as _make

    await _make(app_client, cnic="79003-0000001-1", contact_no="0300-1234567")
    await _make(app_client, cnic="79003-0000002-2", contact_no="0300-1234567")      # the same phone: counted once
    await _login_admin(app_client)
    body = {"audience": "STUDENTS", "message": "School is closed on Monday.", "dry_run": True}
    dry = await app_client.post("/api/v1/sms/bulk", json=body)
    assert dry.status_code == 200 and dry.json()["queued"] == 0 and dry.json()["recipients"] >= 1
    sent = await app_client.post("/api/v1/sms/bulk", json={**body, "dry_run": False})
    assert sent.status_code == 200 and sent.json()["queued"] == dry.json()["recipients"]
    ov = (await app_client.get("/api/v1/sms")).json()
    assert any(m["event"] == "BULK" for m in ov["messages"])
    assert (await app_client.post("/api/v1/sms/bulk", json={"audience": "STUDENTS", "message": "hi"})).status_code == 422


async def test_a_held_payslip_is_released_with_the_month_or_paid_on_its_own(app_client, admin_user, db_session):
    from datetime import date

    from sqlalchemy import select

    from app.accounts.models import Expense
    from tests.integration.test_payroll import PR, TODAY, _as, _prepare, _slip, _team

    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    run = await _prepare(app_client)
    a, b = _slip(run, team, "a"), _slip(run, team, "b")
    for s in (a, b):
        assert (await app_client.patch(f"{PR}/runs/{run['id']}/payslips/{s['id']}", json={"held": True, "held_reason": "Documents pending"})).status_code == 200
    await _login_admin(app_client)
    assert (await app_client.post(f"{PR}/runs/{run['id']}/approve")).status_code == 200
    body = {"payment_method": "CASH", "paid_date": str(TODAY), "reference_no": None}
    same = await app_client.post(f"{PR}/runs/{run['id']}/payslips/{a['id']}/release", json=body)       # month not paid yet: simply goes back in
    assert same.status_code == 200 and same.json() == {"released": True, "paid_separately": False}
    assert (await app_client.post(f"{PR}/runs/{run['id']}/payslips/{a['id']}/release", json=body)).status_code == 422       # no longer held
    paid = await app_client.post(f"{PR}/runs/{run['id']}/pay", json=body)
    assert paid.status_code == 200, paid.text
    alone = await app_client.post(f"{PR}/runs/{run['id']}/payslips/{b['id']}/release", json=body)       # month already paid: paid on its own now
    assert alone.status_code == 200 and alone.json()["paid_separately"] is True and alone.json()["expense_reference"].startswith("EXP-")
    exp = (await db_session.execute(select(Expense).where(Expense.title.like("Salary (held, paid now)%")))).scalars().one()
    assert float(exp.amount) == b["net"]
