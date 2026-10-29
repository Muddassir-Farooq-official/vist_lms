"""People can see all their own details and ask (with approval) to change more of them, including bank details;
staff upload their own documents; the office sees the certificate PDF people uploaded, can download everything as one
PDF, and sees how complete each profile is."""
from __future__ import annotations

import io

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.core.config import settings
from tests.integration.test_fees import _create_and_activate_student, _login, _login_admin
from tests.integration.test_staff_role import _create_staff

pytestmark = pytest.mark.asyncio
PC = "/api/v1/profile-changes"
RC = "/api/v1/records"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def _pdf(pages: int, text: str = "page") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(pages):
        c.drawString(100, 750, f"{text} {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


async def _pending(client):
    return (await client.get(f"{PC}/me")).json()["pending"]


async def test_staff_ask_to_change_bank_and_marital_status_and_it_goes_live_only_when_approved(app_client, admin_user, db_session):
    emp_id, username = await _create_staff(app_client, db_session, cnic="81001-0000001-1")
    me = (await app_client.get(f"{PC}/me")).json()
    keys = {f["key"]: f for f in me["fields"]}
    assert {"bank_name", "bank_account_title", "bank_iban", "marital_status", "blood_group", "province"} <= set(keys)
    assert keys["marital_status"]["choices"] == ["Single", "Married", "Divorced", "Widowed"]
    bad = await app_client.post(f"{PC}/me", json={"values": {"bank_iban": "PK12"}})
    assert bad.status_code == 422
    bad2 = await app_client.post(f"{PC}/me", json={"values": {"marital_status": "Engaged"}})
    assert bad2.status_code == 422
    r = await app_client.post(f"{PC}/me", json={"values": {"bank_name": "Meezan Bank", "bank_account_title": "Staff Member",
                                                            "bank_iban": "pk36 mezn 0001 2345 6789 0123", "marital_status": "married"}})
    assert r.status_code == 200, r.text
    pending = await _pending(app_client)
    assert {c["field"]: c["new"] for c in pending["changes"]}["bank_iban"] == "PK36MEZN0001234567890123"
    assert (await app_client.get("/api/v1/employees/me")).json()["bank_iban"] is None      # nothing live yet

    await _login_admin(app_client)
    ok = await app_client.post(f"{PC}/{pending['id']}/approve")
    assert ok.status_code == 200, ok.text
    await _login(app_client, username, "StaffReal123!")
    mine = (await app_client.get("/api/v1/employees/me")).json()
    assert mine["bank_name"] == "Meezan Bank" and mine["bank_iban"] == "PK36MEZN0001234567890123" and mine["marital_status"] == "Married"
    assert (await app_client.post(f"{PC}/me", json={"values": {"cnic": "11111-1111111-1"}})).status_code == 422   # identity stays office-only


async def test_students_can_ask_to_change_marital_status_blood_group_and_district(app_client, admin_user):
    sid, username = await _create_and_activate_student(app_client, cnic="81001-0000002-2")
    await _login(app_client, username, "StudentPass123!")
    r = await app_client.post(f"{PC}/me", json={"values": {"marital_status": "Married", "blood_group": "o+", "district": "Kasur"}})
    assert r.status_code == 200, r.text
    pending = await _pending(app_client)
    await _login_admin(app_client)
    assert (await app_client.post(f"{PC}/{pending['id']}/approve")).status_code == 200
    await _login(app_client, username, "StudentPass123!")
    vals = (await app_client.get(f"{PC}/me")).json()["values"]
    assert vals["marital_status"] == "Married" and vals["blood_group"] == "O+" and vals["district"] == "Kasur"


async def test_staff_upload_their_own_documents_and_the_office_gets_everything_in_one_pdf(app_client, admin_user, db_session):
    emp_id, username = await _create_staff(app_client, db_session, cnic="81001-0000003-3")
    before = (await app_client.get(f"{RC}/me/completeness")).json()
    assert "CNIC copy" in before["missing"] and "Degree / certificates" in before["missing"]

    up = await app_client.post(f"{RC}/me/documents", data={"kind": "CNIC", "title": "My CNIC"}, files={"file": ("cnic.png", PNG, "image/png")})
    assert up.status_code == 201, up.text
    up2 = await app_client.post(f"{RC}/me/documents", data={"kind": "EXPERIENCE"}, files={"file": ("exp.pdf", _pdf(2, "experience"), "application/pdf")})
    assert up2.status_code == 201                                                             # experience letters from earlier jobs
    blocked = await app_client.post(f"{RC}/me/documents", data={"kind": "CONTRACT"}, files={"file": ("c.pdf", _pdf(1), "application/pdf")})
    assert blocked.status_code == 403                                                         # the office issues contracts
    cert = await app_client.post("/api/v1/employees/me/certificate", files={"file": ("all.pdf", _pdf(3, "certificate"), "application/pdf")})
    assert cert.status_code == 200, cert.text
    mine = (await app_client.get(f"{RC}/me/documents")).json()
    assert {d["kind"] for d in mine} == {"CNIC", "EXPERIENCE"}
    assert (await app_client.get(f"{RC}/documents/{mine[0]['id']}/file")).status_code == 200  # their own file opens
    after = (await app_client.get(f"{RC}/me/completeness")).json()
    assert after["percent"] > before["percent"] and "CNIC copy" not in after["missing"] and "Degree / certificates" not in after["missing"]

    await _login_admin(app_client)
    files = (await app_client.get(f"{RC}/employees/{emp_id}/profile-files")).json()
    assert files["certificate"]["url"].endswith(f"/employees/{emp_id}/certificate") and files["photo"] is None
    pdf = await app_client.get(f"{RC}/employees/{emp_id}/documents/all.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    reader = PdfReader(io.BytesIO(pdf.content))
    assert len(reader.pages) == 1 + 3 + 1 + 2                                                 # cover, certificate PDF, CNIC scan, experience letter
    cover = reader.pages[0].extract_text()
    assert "Certificates (combined PDF" in cover and "CNIC / B-Form: My CNIC" in cover and "Experience letter" in cover
    many = (await app_client.post(f"{RC}/completeness", json={"type": "employees", "ids": [emp_id]})).json()
    assert many[emp_id]["percent"] == after["percent"]


async def test_another_person_cannot_open_your_document(app_client, admin_user, db_session):
    emp_id, username = await _create_staff(app_client, db_session, cnic="81001-0000005-5")
    up = await app_client.post(f"{RC}/me/documents", data={"kind": "CNIC"}, files={"file": ("cnic.png", PNG, "image/png")})
    doc_id = up.json()["id"]
    sid, student = await _create_and_activate_student(app_client, cnic="81001-0000006-6")
    await _login(app_client, student, "StudentPass123!")
    assert (await app_client.get(f"{RC}/documents/{doc_id}/file")).status_code == 403
    assert (await app_client.get(f"{RC}/employees/{emp_id}/documents/all.pdf")).status_code == 403
    assert (await app_client.post(f"{RC}/completeness", json={"type": "employees", "ids": [emp_id]})).status_code == 403