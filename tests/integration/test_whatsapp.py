"""Batch 4: WhatsApp first, SMS as the fallback; documents (challan, receipt, result card, certificate) as signed links."""
from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.sms import whatsapp as wa
from app.sms.models import SmsMessage, SmsStatus, WhatsAppSettings
from app.sms.service import SmsService
from tests.integration.test_families import _at, _student_with_fee
from tests.integration.test_parent_portal import _login_admin

pytestmark = pytest.mark.asyncio
API = "/api/v1/sms/whatsapp"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "sms_to_guardian", 1)
    monkeypatch.setattr(settings, "sms_provider_url", "")


def test_document_tokens_are_signed_and_expire():
    t = wa.make_token("CHALLAN:" + str(uuid.UUID(int=7)))
    assert wa.read_token(t) == ("CHALLAN", uuid.UUID(int=7))
    payload, sig = t.split(".")
    forged = wa._b64(json.dumps({"a": "RECEIPT:" + str(uuid.UUID(int=7)), "e": int(time.time()) + 999}).encode())
    assert wa.read_token(f"{forged}.{sig}") is None                       # someone else's document: refused
    assert wa.read_token(t[:-2] + "xx") is None
    assert wa.read_token(t, now=time.time() + (wa.DOC_DAYS + 1) * 86400) is None
    assert wa.read_token("nonsense") is None and wa.read_token(wa.make_token("PAYSLIP:" + str(uuid.UUID(int=1)))) is None
    assert wa.wa_number("03001234567") == "923001234567" and wa.wa_number("+923001234567") == "923001234567"


async def _settings(db, **kw):
    from app.core.secretbox import seal

    row = WhatsAppSettings(id=1, mode=kw.pop("mode", "META"), meta_phone_number_id="1234567890", meta_token_enc=seal("TOKEN-abc"),
                           meta_template_text="portal_message", meta_template_document="portal_document", public_base="https://lms.example.edu.pk", **kw)
    db.add(row)
    await db.commit()
    return row


def _queue(db, *, attachment=None, number="0300-1234567", body="Hello from the portal"):
    m = SmsMessage(to_number=number, recipient="STUDENT", body=body, event="FEE_REMINDER", status=SmsStatus.QUEUED, attachment=attachment)
    db.add(m)
    return m


async def test_whatsapp_first_with_the_document_and_sms_only_when_whatsapp_fails(db_session, monkeypatch):
    await _settings(db_session)
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "graph.facebook.com":
            body = json.loads(req.content)
            if body["to"] == "923001111111":
                return httpx.Response(400, json={"error": {"message": "Recipient is not a WhatsApp user"}})
            return httpx.Response(200, json={"messages": [{"id": "wamid.OK"}]})
        return httpx.Response(200, text="OK sms")

    a = _queue(db_session, attachment="CHALLAN:" + str(uuid.UUID(int=5)))
    b = _queue(db_session, number="0300-1111111")
    await db_session.commit()
    monkeypatch.setattr(settings, "sms_provider_url", "https://sms.example.pk/send?to={to}&text={message}")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await SmsService(db_session).send_pending(client=client)
    await db_session.commit()
    assert out["sent"] == 2
    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.status == SmsStatus.SENT and a.channel == "WHATSAPP" and a.provider_reference == "wamid.OK"
    assert b.status == SmsStatus.SENT and b.channel == "SMS" and "not a WhatsApp user" in b.whatsapp_error

    first = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer TOKEN-abc" and seen[0].url.path == "/v20.0/1234567890/messages"
    assert first["template"]["name"] == "portal_document"
    doc = first["template"]["components"][0]["parameters"][0]["document"]
    assert doc["link"].startswith("https://lms.example.edu.pk/wa/d/") and doc["filename"] == "fee-challan.pdf"
    assert wa.read_token(doc["link"].rsplit("/", 1)[1]) == ("CHALLAN", uuid.UUID(int=5))
    assert first["template"]["components"][1]["parameters"][0]["text"] == "Hello from the portal"
    assert [r.url.host for r in seen] == ["graph.facebook.com", "graph.facebook.com", "sms.example.pk"]   # SMS only for the one that failed


async def test_without_sms_a_failed_whatsapp_is_retried_then_marked_failed(db_session):
    await _settings(db_session)
    m = _queue(db_session)
    await db_session.commit()
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={"error": {"message": "down"}}))
    for _ in range(3):
        async with httpx.AsyncClient(transport=transport) as client:
            await SmsService(db_session).send_pending(client=client)
        await db_session.commit()
    await db_session.refresh(m)
    assert m.status == SmsStatus.FAILED and m.attempts == 3 and m.last_error.startswith("WhatsApp: HTTP 500")
    assert await SmsService(db_session).retry_failed() == 1 and m.status == SmsStatus.QUEUED and m.channel is None


async def test_a_providers_web_link_gets_the_document_link_in_the_message_when_it_has_no_place_for_it(db_session):
    await _settings(db_session, mode="LINK", link_url="https://wa.example.pk/api?to={to}&msg={message}", link_sender="VIST")
    m = _queue(db_session, attachment="RECEIPT:" + str(uuid.UUID(int=9)))
    await db_session.commit()
    seen = []
    transport = httpx.MockTransport(lambda req: (seen.append(req), httpx.Response(200, text="queued"))[1])
    async with httpx.AsyncClient(transport=transport) as client:
        await SmsService(db_session).send_pending(client=client)
    await db_session.commit()
    await db_session.refresh(m)
    assert m.status == SmsStatus.SENT and m.channel == "WHATSAPP"
    q = dict(seen[0].url.params)
    assert q["to"] == "923001234567" and q["msg"].startswith("Hello from the portal https://lms.example.edu.pk/wa/d/")


async def test_test_sms_still_goes_by_sms_only(db_session, monkeypatch):
    await _settings(db_session)
    monkeypatch.setattr(settings, "sms_provider_url", "https://sms.example.pk/send?to={to}&text={message}")
    m = await SmsService(db_session).queue_test("0300-1234567", "hi")
    await db_session.commit()
    seen = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: (seen.append(req.url.host), httpx.Response(200, text="ok"))[1])) as client:
        await SmsService(db_session).send_pending(client=client)
    assert seen == ["sms.example.pk"] and m.channel == "SMS"


async def test_settings_screen_keeps_secrets_hidden(app_client, admin_user, db_session):
    await _login_admin(app_client)
    first = (await app_client.get(API)).json()
    assert first["mode"] == "OFF" and first["ready"] is False
    bad = await app_client.put(API, json={"mode": "META", "meta_phone_number_id": "123"})
    assert bad.status_code == 422 and "access token" in bad.text
    await db_session.rollback()
    ok = await app_client.put(API, json={"mode": "META", "meta_phone_number_id": "123", "meta_token": "SECRET-1", "meta_template_text": "portal_message"},
                              headers={"host": "lms.vist.edu.pk", "x-forwarded-proto": "https"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["ready"] and body["has_meta_token"] and "SECRET-1" not in ok.text and body["documents_address"] == "https://lms.vist.edu.pk"
    again = await app_client.put(API, json={"mode": "META", "meta_phone_number_id": "123", "meta_template_text": "portal_message"})   # token kept
    assert again.json()["has_meta_token"] is True
    row = await db_session.get(WhatsAppSettings, 1)
    await db_session.refresh(row)
    assert "SECRET-1" not in (row.meta_token_enc or "")
    assert (await app_client.post(API + "/test", json={"number": "12"})).status_code == 422
    page = await app_client.get("/dashboard/admin/sms", headers={"accept": "text/html"})
    assert page.status_code == 200 and "{{1}}" in page.text and "whatsapp-settings.js" in page.text


async def test_document_link_opens_the_challan_without_signing_in_and_payments_send_the_receipt(app_client, admin_user, db_session, monkeypatch):
    from app.fees.payment_models import PaymentChallan
    from app.notifications import family as fam

    sid, _parent, consumer = await _student_with_fee(app_client, db_session, cnic="81201-0000001-1", due_in=3)
    ch = (await db_session.execute(select(PaymentChallan).where(PaymentChallan.consumer_number == consumer))).scalar_one()

    # the fee reminder carries the challan
    _at(monkeypatch)
    assert await fam.fee_reminders(db_session) == 1
    await db_session.commit()
    sms = (await db_session.execute(select(SmsMessage).where(SmsMessage.event == "FEE_REMINDER"))).scalars().all()
    assert sms and {m.attachment for m in sms} == {f"CHALLAN:{ch.id}"}

    app_client.cookies.clear()
    link = await app_client.get(f"/wa/d/{wa.make_token(f'CHALLAN:{ch.id}')}")
    assert link.status_code == 200 and link.content[:4] == b"%PDF" and link.headers["content-type"] == "application/pdf"
    assert (await app_client.get(f"/wa/d/{wa.make_token(f'CHALLAN:{ch.id}')[:-3]}abc")).status_code == 404
    assert (await app_client.get(f"/wa/d/{wa.make_token(f'CHALLAN:{uuid.uuid4()}')}")).status_code == 404

    # payments: the first round only sets the mark; a new payment then gets a message with its receipt
    assert await fam.payment_messages(db_session) == 0
    await db_session.commit()
    await _login_admin(app_client)
    paid = await app_client.post(f"/api/v1/fees/challans/{ch.id}/simulate-payment")
    assert paid.status_code == 200, paid.text
    assert await fam.payment_messages(db_session) == 1
    await db_session.commit()
    msgs = (await db_session.execute(select(SmsMessage).where(SmsMessage.event == "FEE_PAID"))).scalars().all()
    assert msgs and all(m.attachment.startswith("RECEIPT:") and "Rs. 12,000 received" in m.body for m in msgs)
    assert await fam.payment_messages(db_session) == 0                    # never twice
    rid = uuid.UUID(msgs[0].attachment.split(":")[1])
    receipt = await app_client.get(f"/wa/d/{wa.make_token(f'RECEIPT:{rid}')}")
    assert receipt.status_code == 200 and receipt.content[:4] == b"%PDF"

    monkeypatch.setattr(settings, "sms_on_fee_paid", 0)                   # switched off: nothing, and nothing saved up
    assert await fam.payment_messages(db_session) == 0
    await db_session.execute(update(SmsMessage).values(status=SmsStatus.SENT))
    await db_session.commit()
