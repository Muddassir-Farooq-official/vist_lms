"""The user manual is part of every release: its version must be the app's version, and every menu item of every
portal must be explained in that portal's chapters. A release that adds a screen without documenting it fails here."""
from __future__ import annotations

import io

import pytest

from app import help as manual
from app.core.releases import current_version

SKIP = {"Dashboard", "Home", "Help & manual"}


def _portal_lists():
    import app.main as m

    return {portal: getattr(m, nav) for portal, (_base, nav, _roles) in m._CALENDAR_PAGES.items()}


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("&amp;", "&").split())


def test_the_manual_is_written_for_this_version():
    assert manual.index()["version"] == current_version(), (
        "Bring app/manual/ up to date for this release (explain what changed), then set its version in app/manual/manual.json.")


def test_every_menu_item_is_explained_in_its_portals_chapters():
    missing = {}
    for portal, items in _portal_lists().items():
        text = _norm(" ".join(manual.plain(c["text"]) for c in manual.chapters_for(portal)))
        gaps = [i["label"] for i in items if i["label"] not in SKIP and _norm(i["label"]) not in text]
        if gaps:
            missing[portal] = gaps
    assert not missing, f"Menu items missing from the manual: {missing}"


def test_markdown_is_escaped_and_rendered():
    out = manual.to_html("# T\n\nSome **bold** <script>x</script>\n\n- one\n- two\n\n1. a\n2. b", "c")
    assert "<strong>bold</strong>" in out and "&lt;script&gt;" in out and "<ul><li>one</li><li>two</li></ul>" in out and "<ol>" in out


@pytest.mark.asyncio
async def test_help_page_and_pdf(app_client, admin_user):
    from pypdf import PdfReader

    from tests.integration.test_attendance_and_grading import _login_admin

    assert (await app_client.get("/api/v1/help/manual.pdf")).status_code == 401
    await _login_admin(app_client)
    page = await app_client.get("/dashboard/admin/help")
    assert page.status_code == 200 and "Accounts office" in page.text and "What changed" in page.text
    student = await app_client.get("/dashboard/student/help")
    assert student.status_code == 200 and "My Fees" in student.text and "Accounts office</a>" not in student.text
    full = await app_client.get("/api/v1/help/manual.pdf")
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(full.content)).pages)
    assert full.status_code == 200 and f"Version {current_version()}" in text and "Administrator" in text and "Hostel warden" in text
    part = await app_client.get("/api/v1/help/manual.pdf", params={"portal": "student"})
    ptext = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(part.content)).pages)
    assert "My Fees" in ptext and "First-time setup" not in ptext
