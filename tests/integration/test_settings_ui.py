"""Settings: every rule has a proper control (switch, dropdown, number with its unit, or limit on/off), never a bare 1 / 0."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_every_rule_has_a_real_control_section_and_plain_label(app_client, admin_user):
    from tests.integration.test_attendance_and_grading import _login_admin

    await _login_admin(app_client)
    rules = (await app_client.get("/api/v1/policy")).json()
    sections = {s["name"] for s in (await app_client.get("/api/v1/policy/sections")).json()}
    assert "Fees & fines" in sections and "SMS messages" in sections
    for r in rules:
        u = r["ui"]
        assert u["control"] in {"switch", "choice", "number", "optional"}, r["key"]
        assert u["section"] in sections, r["key"]
        for bad in ("1 = yes", "0 = no", "(0 =", "(1 ="):
            assert bad not in u["label"], (r["key"], u["label"])
        if (r["min"], r["max"]) == (0, 1):
            # an on/off rule is a switch, unless its two values are named choices (e.g. how attendance fines work)
            assert u["control"] == "switch" or (u["control"] == "choice" and len(u["choices"]) == 2), r["key"]
        if u["control"] == "choice":
            assert u["choices"] and all(isinstance(c[1], str) for c in u["choices"])
        if u["control"] == "optional":
            assert u["off"] and r["min"] == 0                                # "off" stores 0 ("no limit", "never")
    double = next(r for r in rules if r["key"] == "fee_late_fee_double_every_days")["ui"]
    assert double["control"] == "choice" and [7, "Every week"] in double["choices"] and double["custom"] == "days"


async def test_settings_page_has_the_side_menu_and_phone_dropdown(app_client, admin_user):
    from tests.integration.test_attendance_and_grading import _login_admin

    await _login_admin(app_client)
    html = (await app_client.get("/dashboard/admin/settings")).text
    assert 'id="st-nav"' in html and 'id="st-nav-select"' in html and 'id="policy-search"' in html
    for panel in ("institute", "look", "rules", "email", "account"):
        assert f'data-tab-panel="{panel}"' in html
