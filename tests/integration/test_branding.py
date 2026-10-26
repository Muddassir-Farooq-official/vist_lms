"""Settings -> Institute: the name, logo, colour, bank details, signatory and footer show on pages and documents."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.core.config import settings
from tests.integration.test_fees import _create_accounts_officer, _login_admin

pytestmark = pytest.mark.asyncio
P = "/api/v1/institute"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))


def _png(w=300, h=120, color=(11, 107, 93, 255)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(out, format="PNG")
    return out.getvalue()


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)


async def test_profile_shows_on_pages_pdfs_and_sms(app_client, admin_user):
    await _login_admin(app_client)
    p = (await app_client.get(f"{P}/profile")).json()
    assert p["name"] == settings.institute_name and p["has_logo"] is False
    r = await app_client.put(f"{P}/profile", json={"name": "Northern College of Nursing", "short_name": "NCN Portal", "city": "Gilgit",
                                                     "address": "Jutial Road", "phone": "05811-123456", "primary_color": "#0B6B5D",
                                                     "document_footer": "Fees once paid are not refundable.", "bank_name": "Bank Alfalah",
                                                     "bank_account_title": "Northern College of Nursing", "bank_account_no": "PK00ALFH0000001234567890",
                                                     "signatory_name": "Dr. Sana Ali", "signatory_title": "Principal"})
    assert r.status_code == 200, r.text
    assert r.json()["primary_color"] == "#0b6b5d" and r.json()["version"] >= 2
    login = (await app_client.get("/?reauth=1")).text
    assert "Northern College of Nursing" in login and "NCN Portal" in login and "Vertex" not in login
    page = (await app_client.get("/dashboard/admin/settings")).text
    assert "NCN Portal Admin" in page and "--blue-600:#0b6b5d" in page
    challan = await app_client.get(f"{P}/preview/challan.pdf")
    text = _pdf_text(challan.content)
    assert "Northern College of Nursing" in text and "Bank Alfalah" in text and "PK00ALFH0000001234567890" in text and "not refundable" in text
    from app.core import brand

    assert brand.short_name() == "NCN Portal" and settings.app_name == "NCN Portal"          # SMS use settings.app_name


async def test_logo_upload_checks_and_public_files(app_client, admin_user):
    await _login_admin(app_client)
    fallback = await app_client.get("/branding/logo.png")
    assert fallback.status_code == 200 and fallback.headers["content-type"].startswith("image/svg")     # lettered badge until a logo exists
    svg = await app_client.post(f"{P}/images/logo", files={"file": ("x.svg", b"<svg onload='alert(1)'></svg>", "image/svg+xml")})
    assert svg.status_code == 422 and "PNG or JPG" in svg.text
    fake = await app_client.post(f"{P}/images/logo", files={"file": ("x.png", b"\x89PNG\r\n\x1a\nnot really", "image/png")})
    assert fake.status_code == 422
    ok = await app_client.post(f"{P}/images/logo", files={"file": ("logo.png", _png(1600, 600), "image/png")})
    assert ok.status_code == 200 and ok.json()["has_logo"]
    logo = await app_client.get("/branding/logo.png")
    assert logo.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(logo.content)).size[0] <= 800                                           # resized
    fav = Image.open(io.BytesIO((await app_client.get("/branding/favicon.png")).content))
    assert fav.size == (64, 64)
    sig = await app_client.post(f"{P}/images/signature", files={"file": ("s.jpg", _jpg(), "image/jpeg")})
    assert sig.status_code == 200 and sig.json()["has_signature"]
    assert (await app_client.get(f"{P}/signature.png")).status_code == 200
    page = (await app_client.get("/dashboard/admin")).text
    assert "brand-mark has-logo" in page and "/branding/logo.png?v=" in page
    gone = await app_client.delete(f"{P}/images/logo")
    assert gone.json()["has_logo"] is False


def _jpg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (600, 200), (255, 255, 255)).save(out, format="JPEG")
    return out.getvalue()


async def test_validation_audit_and_who_can_change_it(app_client, admin_user, db_session):
    from sqlalchemy import select

    from app.audit.models import AuditLog

    await _login_admin(app_client)
    bad = await app_client.put(f"{P}/profile", json={"primary_color": "green"})
    assert bad.status_code == 422
    assert (await app_client.put(f"{P}/profile", json={"name": " "})).status_code == 422
    assert (await app_client.put(f"{P}/profile", json={"email": "not-an-email"})).status_code == 422
    assert (await app_client.put(f"{P}/profile", json={"tagline": "Knowledge is light"})).status_code == 200
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.action == "institute.profile_updated"))).scalars().all()
    assert rows and rows[-1].after == {"tagline": "Knowledge is light"}
    await _create_accounts_officer(app_client, db_session)
    assert (await app_client.put(f"{P}/profile", json={"name": "Hacked"})).status_code == 403
    assert (await app_client.get(f"{P}/signature.png")).status_code == 403


async def test_documents_carry_the_institute_header(app_client, admin_user):
    from datetime import date

    from app.academics.transcript_pdf import render_transcript_pdf  # noqa: F401  (imported to make sure it loads)
    from app.fees.receipt_pdf import render_receipt_pdf
    from app.records.pdf import render_certificate_pdf

    await _login_admin(app_client)
    await app_client.put(f"{P}/profile", json={"name": "Hunza Model College", "short_name": "HMC", "document_footer": "Verify at hmc.edu.pk",
                                               "signatory_name": "Mr. Karim Khan", "signatory_title": "Principal"})
    await app_client.post(f"{P}/images/logo", files={"file": ("logo.png", _png(), "image/png")})
    rec = render_receipt_pdf({"receipt_number": "RCPT-2026-000001", "paid_on": date.today(), "recorded_on": date.today(), "payer": {"name": "A Student"},
                              "fee_name": "Tuition", "term_label": "Fall 2026", "amount": 1000, "method": "CASH", "reference": None, "description": "x"})
    t = _pdf_text(rec)
    assert "Hunza Model College" in t and "Verify at hmc.edu.pk" in t and "PAYMENT RECEIPT" in t
    cert = render_certificate_pdf(institute=settings.institute_name, kind="BONAFIDE", reference="BON-1", code="ABC", verify_url="https://x/verify/ABC",
                                  paragraphs=["This is to certify."], issued_on=date.today())
    t = _pdf_text(cert)
    assert "HUNZA MODEL COLLEGE" in t and "Mr. Karim Khan" in t and "Principal" in t
