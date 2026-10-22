"""Platform hardening (launch step 6d): Content-Security-Policy with no inline scripts, the visitor's real
address behind a proxy, error alerts that do not flood, PDFs built off the event loop, a pinned lock file."""
from __future__ import annotations

import pathlib
import re

import pytest

from app.core import alerts
from app.core.config import settings
from app.core.net import client_ip

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_every_page_carries_a_content_security_policy(app_client):
    r = await app_client.get("/")
    csp = r.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]


def test_no_template_has_inline_script_or_event_handler_attributes():
    bad = []
    for f in (ROOT / "templates").rglob("*.html"):
        text = f.read_text(encoding="utf-8")
        if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text) or re.search(r"\son[a-z]+\s*=\s*[\"']", text):
            bad.append(f.name)
    assert bad == []


class _Req:
    def __init__(self, peer, fwd=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": fwd} if fwd else {}


def test_forwarded_address_is_believed_only_from_a_trusted_proxy():
    assert client_ip(_Req("127.0.0.1", "203.0.113.7, 10.0.0.2")) == "203.0.113.7"
    assert client_ip(_Req("172.18.0.3", "203.0.113.7")) == "203.0.113.7"          # Docker network
    assert client_ip(_Req("198.51.100.4", "1.2.3.4")) == "198.51.100.4"           # someone on the internet faking it
    assert client_ip(_Req("127.0.0.1", "not-an-ip")) == "127.0.0.1"


def test_error_alerts_are_sent_once_per_error_not_once_per_visitor(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "error_alert_webhook", "https://hooks.example/x")
    monkeypatch.setattr(alerts, "_last_sent", {})

    class Loop:
        def create_task(self, coro):
            sent.append(coro)
            coro.close()
    monkeypatch.setattr(alerts.asyncio, "get_running_loop", lambda: Loop())
    try:
        raise ValueError("boom")
    except ValueError as exc:
        for _ in range(5):
            alerts.alert_unhandled(exc, path="/api/v1/x", request_id="r1")
        alerts.alert_unhandled(exc, path="/api/v1/other", request_id="r2")
    assert len(sent) == 2
    monkeypatch.setattr(settings, "error_alert_webhook", "")
    alerts.alert_unhandled(ValueError("x"), path="/z", request_id=None)
    assert len(sent) == 2


def test_pdf_routes_do_not_build_pdfs_on_the_event_loop():
    offenders = []
    for f in (ROOT / "app").rglob("*router*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(?<![\w.])render_\w*pdf\(", line) and "def " not in line and "import" not in line:
                offenders.append(f"{f.name}:{n}")
    assert offenders == []


def test_the_lock_file_pins_every_dependency_with_hashes():
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    for name in ("fastapi", "sqlalchemy", "asyncpg", "reportlab", "pydantic", "starlette"):
        assert re.search(rf"^{name}==[\d.]+ \\$", lock, re.M | re.I), name
    assert "--hash=sha256:" in lock
    assert "--require-hashes -r requirements.lock" in (ROOT / "Dockerfile").read_text(encoding="utf-8")