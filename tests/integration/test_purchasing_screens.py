"""Staff can ask for purchases from their own portal; the accounts office and administration run the whole flow from the Accounts page."""
from __future__ import annotations

import pytest

from app.main import FACULTY_NAV_ITEMS, GATE_NAV_ITEMS, LIBRARY_NAV_ITEMS

pytestmark = pytest.mark.asyncio


async def test_staff_portals_have_a_purchase_requests_page(app_client):
    for nav, portal in ((FACULTY_NAV_ITEMS, "faculty"), (LIBRARY_NAV_ITEMS, "library"), (GATE_NAV_ITEMS, "gate")):
        entry = next(i for i in nav if i["slug"] == "purchases")
        assert entry["href"] == f"/dashboard/{portal}/purchases" and entry["label"] == "Purchase requests"
        html = (await app_client.get(entry["href"])).text
        for needle in ('id="pr-new"', 'id="pr-list"', 'id="pr-filter"', 'id="pr-form"', 'id="pr-items"', 'id="pd-modal"', "purchases-staff.js", "Send the request"):
            assert needle in html, f"{portal}: {needle}"
    js = (await app_client.get("/static/js/purchases-staff.js")).text
    for needle in ("/api/v1/purchases", "/mine", "/cancel", "po-steps", "Collect it from the accounts office"):
        assert needle in js, needle


async def test_the_accounts_page_runs_the_whole_purchasing_flow(app_client):
    for path in ("/dashboard/admin/finance", "/dashboard/accounts/finance"):
        html = (await app_client.get(path)).text
        for needle in ('data-tab="purchasing"', 'id="po-tiles"', 'id="po-status"', 'id="po-modal"', 'id="po-order"', 'id="po-recv"', 'id="po-pay"', 'id="pr-new"', "purchases-staff.js"):
            assert needle in html, f"{path}: {needle}"
    js = (await app_client.get("/static/js/finance.js")).text
    for needle in ("/api/v1/purchases", '"/order"', '"/receive"', '"/pay"', "reverse-payment", "Owed to vendors", "has("):
        assert needle.replace('"/order"', "/order").replace('"/receive"', "/receive").replace('"/pay"', "/pay") in js, needle