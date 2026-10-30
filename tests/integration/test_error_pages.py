"""Addresses opened in a browser get a real "Page not found" / "Something went wrong" page; the API keeps its JSON."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio
HTML = {"Accept": "text/html,application/xhtml+xml"}


async def test_an_unknown_page_shows_page_not_found(app_client):
    for path in ("/old-page", "/dashboard/admin/some-old-link", "/dashboard/zzz/calendar"):
        r = await app_client.get(path, headers=HTML)
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/html"), path
        assert "Page not found" in r.text and "Go to my portal" in r.text and "{&quot;detail" not in r.text
        assert "<script>" not in r.text                                   # the page follows the no-inline-script rule


async def test_the_api_and_files_still_answer_in_json(app_client):
    for path in ("/api/v1/nope", "/static/nope.js"):
        r = await app_client.get(path, headers=HTML)
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json"), path
    assert (await app_client.post("/old-page", headers=HTML)).headers["content-type"].startswith("application/json")
    r = await app_client.get("/api/v1/students/00000000-0000-0000-0000-000000000000")
    assert r.headers["content-type"].startswith("application/json")


async def test_a_crash_on_a_page_shows_something_went_wrong_with_a_reference():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async def boom():
        raise RuntimeError("database fell over")

    app.add_api_route("/dashboard/__boom_for_test", boom, methods=["GET"], include_in_schema=False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
            r = await c.get("/dashboard/__boom_for_test", headers=HTML)
            assert r.status_code == 500 and "Something went wrong" in r.text and "Reference:" in r.text
            assert "database fell over" not in r.text                     # no internal details on the page
            j = await c.get("/dashboard/__boom_for_test", headers={"Accept": "application/json"})
            assert j.status_code == 500 and j.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    finally:
        app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/dashboard/__boom_for_test"]