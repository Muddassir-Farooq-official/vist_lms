"""The Accounts screen exists for the accounts office and the administration, and is wired to the right endpoints."""
from __future__ import annotations

import pytest

from app.main import ACCOUNTS_NAV_ITEMS, ADMIN_NAV_ITEMS

pytestmark = pytest.mark.asyncio


async def test_both_portals_have_an_accounts_page_in_their_menu(app_client):
    admin = next(i for i in ADMIN_NAV_ITEMS if i["slug"] == "finance")
    office = next(i for i in ACCOUNTS_NAV_ITEMS if i["slug"] == "finance")
    assert admin["href"] == "/dashboard/admin/finance" and admin["group"] == "Finance" and office["href"] == "/dashboard/accounts/finance"
    for href in (admin["href"], office["href"]):
        html = (await app_client.get(href)).text
        for tab in ("overview", "book", "expenses", "purchasing", "income", "vendors", "categories"):
            assert f'data-tab="{tab}"' in html, f"{href}: {tab}"
        for needle in ('data-allowed-roles="ADMIN,ACCOUNTS_OFFICER"', 'id="ov-period"', 'id="ov-tiles"', 'id="ex-modal"', 'id="ex-files"', 'id="ed-modal"', 'id="in-modal"', 'id="vn-modal"', 'id="fn-ask"', "finance.js", "charts.js"):
            assert needle in html, f"{href}: {needle}"


async def test_the_accounts_script_talks_to_the_right_endpoints(app_client):
    js = (await app_client.get("/static/js/finance.js")).text
    for needle in ("/summary", "/book", "/expenses", "/income", "/vendors", "/categories", 'decide("approve"', 'decide("reject"', 'decide("void"', "/income/", "/expenses/attachments/", "account.approve", "financial_year_start_month", "This financial year", "Waiting for approval"):
        assert needle in js, needle
    assert "FINANCE:" in (await app_client.get("/static/js/admin-common.js")).text


async def test_the_accounts_settings_have_sensible_defaults():
    from app.core.config import settings
    assert settings.expense_approval_limit == 50000.0 and settings.financial_year_start_month == 7