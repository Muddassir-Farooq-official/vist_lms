"""Phone / browser push: message encryption a real browser can read, VAPID signing, devices, and the background round."""
from __future__ import annotations

import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from app.core.config import settings
from app.push.webpush import _hkdf, _public_bytes, b64u, encrypt, generate_vapid_keys, unb64u, vapid_headers

pytestmark = pytest.mark.asyncio


def _browser():
    """What a browser keeps for one push subscription."""
    key = ec.generate_private_key(ec.SECP256R1())
    import os

    return key, b64u(_public_bytes(key)), b64u(os.urandom(16))


def _decrypt(body: bytes, key, p256dh: str, auth: str) -> bytes:
    """RFC 8291 from the receiving side (what the browser does)."""
    salt, idlen = body[:16], body[20]
    as_public = body[21:21 + idlen]
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hkdf(unb64u(auth), shared, b"WebPush: info\x00" + unb64u(p256dh) + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_a_browser_can_read_the_message_and_trust_the_sender():
    import jwt

    key, p256dh, auth = _browser()
    body = encrypt(json.dumps({"title": "Fee due", "body": "Rs. 12,500 by 10 Nov"}).encode(), p256dh, auth)
    assert json.loads(_decrypt(body, key, p256dh, auth))["title"] == "Fee due"
    public, private = generate_vapid_keys()
    h = vapid_headers("https://fcm.googleapis.com/fcm/send/abc", public_key=public, private_key=private, subject="mailto:a@b.pk")
    token = h["Authorization"].split("t=")[1].split(",")[0]
    pub_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(public))
    claims = jwt.decode(token, pub_key, algorithms=["ES256"], audience="https://fcm.googleapis.com")
    assert claims["sub"] == "mailto:a@b.pk" and h["Authorization"].endswith("k=" + public)


@pytest.fixture
def keys(monkeypatch):
    public, private = generate_vapid_keys()
    monkeypatch.setattr(settings, "vapid_public_key", public)
    monkeypatch.setattr(settings, "vapid_private_key", private)
    monkeypatch.setattr(settings, "vapid_subject", "mailto:it@example.edu.pk")
    return public


async def test_off_until_the_server_has_keys(app_client, admin_user):
    from tests.integration.test_fees import _login_admin

    await _login_admin(app_client)
    assert (await app_client.get("/api/v1/push/key")).json()["enabled"] is False
    r = await app_client.post("/api/v1/push/subscribe", json={"endpoint": "https://push.example/x", "keys": {"p256dh": "a", "auth": "b"}})
    assert r.status_code == 422


async def test_devices_and_the_background_round(app_client, admin_user, db_session, keys, monkeypatch):
    from app.notifications.models import Notification, NotificationCategory
    from app.push.models import PushSubscription
    from app.push.service import PushService
    from tests.integration.test_fees import _login_admin

    await _login_admin(app_client)
    assert (await app_client.get("/api/v1/push/key")).json() == {"enabled": True, "public_key": keys, "devices": 0}
    bkey, p256dh, auth = _browser()
    endpoint = "https://fcm.googleapis.com/fcm/send/device-1"
    sub = await app_client.post("/api/v1/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                                headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile"})
    assert sub.status_code == 200 and sub.json()["devices"] == 1
    assert (await app_client.post("/api/v1/push/subscribe", json={"endpoint": "http://insecure", "keys": {"p256dh": p256dh, "auth": auth}})).status_code == 422
    row = (await db_session.execute(select(PushSubscription))).scalars().one()
    assert row.device == "Chrome on Android"

    delivered = []

    import app.push.service as push_service

    async def fake_deliver(client, url, content, headers):
        delivered.append((url, json.loads(_decrypt(content, bkey, p256dh, auth)), headers))
        return httpx.Response(201, request=httpx.Request("POST", url))

    monkeypatch.setattr(push_service, "deliver", fake_deliver)
    assert (await app_client.post("/api/v1/push/test")).json()["sent"] == 1

    db_session.add(Notification(user_id=admin_user.id, category=NotificationCategory.APPLICATION, title="Forwarded to Exams",
                                message="APP-2026-00001 is now with the Exams department.", link="/dashboard/admin/applications"))
    await db_session.flush()
    result = await PushService(db_session).push_new_notifications()
    assert result["pushed"] == 1 and delivered[-1][1]["title"] == "Forwarded to Exams" and delivered[-1][1]["url"] == "/dashboard/admin/applications"
    assert delivered[-1][2]["Content-Encoding"] == "aes128gcm" and delivered[-1][2]["Authorization"].startswith("vapid t=")
    assert (await PushService(db_session).push_new_notifications())["pushed"] == 0            # each notification once

    # a device that is gone is forgotten
    async def gone(client, url, content, headers):
        return httpx.Response(410, request=httpx.Request("POST", url))

    monkeypatch.setattr(push_service, "deliver", gone)
    assert (await app_client.post("/api/v1/push/test")).json()["sent"] == 0
    assert (await app_client.get("/api/v1/push/key")).json()["devices"] == 0


async def test_the_app_shell(app_client):
    m = await app_client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    body = m.json()
    assert body["display"] == "standalone" and any(i["purpose"] == "maskable" for i in body["icons"])
    icon = await app_client.get("/branding/app-icon-192.png")
    assert icon.status_code == 200 and icon.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (await app_client.get("/branding/app-icon-512.png?maskable=1")).status_code == 200
    assert (await app_client.get("/branding/app-icon-77.png")).status_code == 404
    sw = await app_client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and "showNotification" in sw.text
    login = await app_client.get("/")
    assert 'rel="manifest"' in login.text and "apple-touch-icon" in login.text