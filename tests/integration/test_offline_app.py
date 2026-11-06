"""The app's offline page and the service worker's rules: never keep private data, only the design files."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_offline_page_is_public_branded_and_has_no_inline_script(app_client):
    r = await app_client.get("/offline")
    assert r.status_code == 200 and "You're offline" in r.text and "/static/js/offline.js" in r.text
    assert "<script>" not in r.text                     # the content security policy only allows the portal's own script files
    assert (await app_client.get("/static/js/offline.js")).status_code == 200


async def test_service_worker_shows_the_offline_page_and_never_keeps_api_answers(app_client):
    sw = (await app_client.get("/sw.js")).text
    assert '"/offline"' in sw and "offlinePage" in sw and "[502, 503, 504]" in sw
    assert 'url.pathname.startsWith("/static/")' in sw
    # only the offline page, its script and the icon are stored ahead; no portal page with anyone's data
    precache = sw.split("const PRECACHE = [")[1].split("]")[0]
    assert "/dashboard" not in precache and "/api" not in precache


async def test_phones_move_to_new_push_keys_by_themselves(app_client):
    js = (await app_client.get("/static/js/admin-common.js")).text
    assert "refreshPushKey" in js and "applicationServerKey" in js