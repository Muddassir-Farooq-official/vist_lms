"""Version number, release notes and the /health status page."""
from __future__ import annotations

import json
import re

from app.core import releases as rel
from tests.integration.test_attendance_and_grading import _login_admin

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def test_the_release_notes_file_is_complete_and_newest_first():
    data = json.loads(rel.RELEASES_FILE.read_text(encoding="utf-8"))
    assert data, "app/releases.json must list at least one release"
    versions = [r["version"] for r in data]
    assert all(SEMVER.match(v) for v in versions), versions
    assert len(set(versions)) == len(versions), "a version is listed twice"
    assert [_key(v) for v in versions] == sorted((_key(v) for v in versions), reverse=True), "newest release must be first"
    dates = [r["date"] for r in data]
    assert dates == sorted(dates, reverse=True)
    for r in data:
        assert r["title"].strip() and r["summary"].strip(), r["version"]
        assert r["changes"], f"{r['version']} has no changes listed"
        for c in r["changes"]:
            assert c["kind"] in rel.KINDS, c
            assert c["text"].strip() and c["for"].strip(), c


def test_version_info_uses_the_newest_release_and_the_deploy_record(tmp_path, monkeypatch):
    rel.build.cache_clear()
    monkeypatch.setattr(rel, "BUILD_FILE", tmp_path / "build_info.json")
    info = rel.version_info()
    assert info["version"] == rel.releases()[0]["version"] and info["build"] is None    # on a developer machine
    (tmp_path / "build_info.json").write_text('{"commit": "0123456789abcdef", "built_at": "2026-11-08T10:30:00Z", "run": 7}')
    rel.build.cache_clear()
    info = rel.version_info()
    assert info["build"] == "0123456" and info["updated_on_text"] == "8 Nov 2026"
    rel.build.cache_clear()


async def test_health_stays_tiny_json_for_monitors_and_docker(app_client):
    for accept in (None, "*/*", "application/json"):
        r = await app_client.get("/health", headers={"accept": accept} if accept else {})
        assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert (await app_client.get("/health/live")).json() == {"status": "ok"}


async def test_health_in_a_browser_shows_the_status_page(app_client):
    r = await app_client.get("/health", headers={"accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    page = r.text
    assert "Everything is working" in page and f"v{rel.current_version()}" in page
    assert "Records (database)" in page and 'id="whats-new"' in page
    # the notes themselves are not in the public page; they are loaded only for signed-in people
    assert rel.releases()[0]["changes"][0]["text"][:40] not in page


async def test_version_is_public_but_release_notes_need_sign_in(app_client, admin_user):
    v = await app_client.get("/api/v1/system/version")
    assert v.status_code == 200 and v.json()["version"] == rel.current_version() and "releases" not in v.json()
    assert (await app_client.get("/api/v1/system/releases")).status_code == 401

    await _login_admin(app_client)
    r = await app_client.get("/api/v1/system/releases")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == rel.current_version() and body["releases"][0]["version"] == body["version"]
    assert len(body["releases"]) == len(rel.releases())
    one = (await app_client.get("/api/v1/system/releases", params={"limit": 1})).json()
    assert len(one["releases"]) == 1 and one["releases"][0]["date_text"]


async def test_staff_menus_show_the_version_and_student_menus_do_not(app_client):
    staff = await app_client.get("/dashboard/admin", headers={"accept": "text/html"})
    assert staff.status_code == 200
    assert 'id="app-version"' in staff.text and f'data-version="{rel.current_version()}"' in staff.text
    assert "/static/js/releases-ui.js" in staff.text
    student = await app_client.get("/dashboard/student", headers={"accept": "text/html"})
    assert student.status_code == 200 and 'id="app-version"' not in student.text


async def test_shared_links_show_a_preview_picture(app_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "public_url", "https://lms.example.edu.pk")
    via = {"accept": "text/html", "host": "lms.example.edu.pk", "x-forwarded-proto": "https"}   # as nginx passes it on
    login = (await app_client.get("/", headers=via)).text
    assert '<meta property="og:image" content="https://lms.example.edu.pk/share/preview.png?kind=portal' in login
    assert 'property="og:title"' in login and 'name="twitter:card" content="summary_large_image"' in login
    health = (await app_client.get("/health", headers=via)).text
    assert "share/preview.png?kind=version" in health and f'og:url" content="https://lms.example.edu.pk/health?v={rel.current_version()}"' in health
    assert "data-share-url" in health
    for kind in ("portal", "version"):
        img = await app_client.get("/share/preview.png", params={"kind": kind})
        assert img.status_code == 200 and img.headers["content-type"] == "image/png" and len(img.content) < 300_000   # WhatsApp's limit
        from PIL import Image
        import io
        assert Image.open(io.BytesIO(img.content)).size == (1200, 630)


async def test_staff_menu_has_a_share_button_for_the_version(app_client):
    page = (await app_client.get("/dashboard/faculty", headers={"accept": "text/html"})).text
    assert f'data-share-path="/health?v={rel.current_version()}"' in page and "drawer-version-top" in page


async def test_preview_uses_the_address_people_came_in_on_when_public_url_is_not_set(app_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "public_url", "")
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:8000")
    page = (await app_client.get("/", headers={"accept": "text/html", "host": "lms.vist.edu.pk", "x-forwarded-proto": "https"})).text
    assert '<meta property="og:image" content="https://lms.vist.edu.pk/share/preview.png?kind=portal' in page
    assert "localhost" not in page.split('og:image" content="')[1].split('"')[0]


async def test_previews_use_the_address_the_link_was_opened_with_and_answer_head(app_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "public_url", "")
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:8000")     # a typical leftover: must not be used
    h = {"host": "lms.vist.edu.pk", "x-forwarded-proto": "https", "user-agent": "WhatsApp/2.23.20.0 A", "accept": "text/html"}
    page = (await app_client.get("/", headers=h)).text
    assert '<meta property="og:image" content="https://lms.vist.edu.pk/share/preview.png?kind=portal' in page
    assert '<meta property="og:url" content="https://lms.vist.edu.pk/"' in page and "localhost" not in page.split("og:url")[1][:80]
    for url in ("/", "/share/preview.png?kind=portal", "/health"):
        r = await app_client.head(url, headers=h)
        assert r.status_code == 200 and r.content == b"", url
    img = await app_client.head("/share/preview.png?kind=version", headers=h)
    assert img.headers["content-type"] == "image/png" and int(img.headers["content-length"]) > 1000
