"""The payroll screens exist for the accounts office and administration, and every employee has a My payslips page."""
from __future__ import annotations

import pytest

from app.main import ACCOUNTS_NAV_ITEMS, ADMIN_NAV_ITEMS, FACULTY_NAV_ITEMS, GATE_NAV_ITEMS, LIBRARY_NAV_ITEMS

pytestmark = pytest.mark.asyncio


async def test_the_accounts_office_and_administration_have_a_payroll_page(app_client):
    for nav, portal in ((ADMIN_NAV_ITEMS, "admin"), (ACCOUNTS_NAV_ITEMS, "accounts")):
        entry = next(i for i in nav if i["slug"] == "payroll")
        assert entry["href"] == f"/dashboard/{portal}/payroll"
        html = (await app_client.get(entry["href"])).text
        for tab in ("runs", "items", "advances"):
            assert f'data-tab="{tab}"' in html, f"{portal}: {tab}"
        for needle in ('data-allowed-roles="ADMIN,ACCOUNTS_OFFICER"', 'id="pr-prepare"', 'id="rd-modal"', 'id="sl-modal"', 'id="pp-modal"', 'id="it-form"', 'id="ad-form"', "payroll.js"):
            assert needle in html, f"{portal}: {needle}"
    js = (await app_client.get("/static/js/payroll.js")).text
    for needle in ("/api/v1/payroll", "/runs", "/payslips/", "/items", "/advances", "recalculate", "reverse-payment", "payroll.approve", "Pay the salaries"):
        assert needle in js, needle


async def test_every_employee_portal_has_a_my_payslips_page(app_client):
    for nav, portal in ((FACULTY_NAV_ITEMS, "faculty"), (LIBRARY_NAV_ITEMS, "library"), (GATE_NAV_ITEMS, "gate"), (ACCOUNTS_NAV_ITEMS, "accounts")):
        entry = next(i for i in nav if i["slug"] == "payslips")
        assert entry["href"] == f"/dashboard/{portal}/payslips" and entry["label"] == "My payslips"
        html = (await app_client.get(entry["href"])).text
        for needle in ('id="ps-list"', 'id="pv-modal"', 'id="pv-pdf"', "payslips-staff.js"):
            assert needle in html, f"{portal}: {needle}"
    js = (await app_client.get("/static/js/payslips-staff.js")).text
    for needle in ("/api/v1/payroll", "/me", "/pdf", "Net pay"):
        assert needle in js, needle