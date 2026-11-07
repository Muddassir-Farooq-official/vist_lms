"""Ending a scholarship withdraws its discount from later unpaid installments (paid ones keep it); a running scholarship
can be changed, and a change that gives the student more waits for another administrator."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_approvals_and_rules import _as_two, _second_admin, _waiting
from tests.integration.test_fees import _create_accounts_officer, _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio
SC = "/api/v1/fees/scholarships"
AP = "/api/v1/approvals"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _setup(client, cnic):
    """A student with one fee of 4 monthly installments of Rs 2,000 (due 1 Sep, 1 Oct, 1 Nov, 1 Dec 2026) and 50% off."""
    sid, _ = await _create_and_activate_student(client, cnic=cnic)
    await _create_active_fee_structure(client, total_amount=8000, installment_count=4)
    await _login_admin(client)
    aid = next(a["id"] for a in (await client.get("/api/v1/fees/assignments")).json() if a["payer_id"] == sid)
    r = await client.post(SC, json={"student_id": sid, "name": "Merit 50", "kind": "PERCENT", "value": 50, "valid_from": "2026-01-01",
                                    "reason": "Merit list position 2"})
    assert r.status_code == 201, r.text
    return sid, aid, r.json()["id"]


async def _fee(client, aid):
    return (await client.get(f"/api/v1/fees/assignments/{aid}")).json()


async def _listed(client, sid, scid):
    return next(s for s in (await client.get(SC, params={"student_id": sid})).json() if s["id"] == scid)


async def test_ending_withdraws_the_discount_from_later_unpaid_installments_only(app_client, admin_user):
    sid, aid, scid = await _setup(app_client, "74001-0000001-1")
    assert (await _fee(app_client, aid))["total_concession"] == 4000.0
    # pay installments 1-3 at the discounted Rs 1,000 each; installment 3 (due 1 Nov) is then paid in advance
    pay = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 3000, "description": "Counter", "method": "CASH"})
    assert pay.status_code == 201, pay.text

    future = await app_client.post(f"{SC}/{scid}/end", json={"end_date": "2099-01-01"})
    assert future.status_code == 422                                                   # a future end is an edit of "Until"
    r = await app_client.post(f"{SC}/{scid}/end", json={"end_date": "2026-10-01", "reason": "Grade fell below the requirement"})
    assert r.status_code == 200, r.text
    assert r.json()["withdrawn"] == 1000.0 and r.json()["fees_updated"] == 1           # only installment 4: unpaid and due after 1 Oct
    fee = await _fee(app_client, aid)
    assert fee["total_concession"] == 3000.0                                          # installments 1-3 keep theirs (3 was paid in full)
    assert fee["total_payable"] == 5000.0 and fee["total_paid"] == 3000.0
    listed = await _listed(app_client, sid, scid)
    assert listed["status"] == "ENDED" and listed["valid_until"] == "2026-10-01" and listed["given_so_far"] == 3000.0
    lines = (await app_client.get(f"/api/v1/fees/assignments/{aid}/statement")).json()["lines"]
    assert [x["amount"] for x in lines if x["what"] == "Discount withdrawn"] == [1000.0]
    assert (await app_client.post(f"{SC}/{scid}/end", json={})).status_code == 422      # already ended


async def test_ending_today_by_default_still_works_without_a_body(app_client, admin_user):
    sid, aid, scid = await _setup(app_client, "74001-0000002-2")
    r = await app_client.post(f"{SC}/{scid}/end")
    assert r.status_code == 200, r.text
    assert (await _listed(app_client, sid, scid))["status"] == "ENDED"


async def test_lowering_a_scholarship_applies_now_to_unpaid_installments(app_client, admin_user, db_session):
    sid, aid, scid = await _setup(app_client, "74001-0000003-3")
    await _second_admin(db_session)                                                   # someone else could approve, yet lowering needs nobody
    await _login_admin(app_client)
    pay = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 1000, "description": "Counter", "method": "CASH"})
    assert pay.status_code == 201
    no_note = await app_client.patch(f"{SC}/{scid}", json={"value": 25})
    assert no_note.status_code == 422
    r = await app_client.patch(f"{SC}/{scid}", json={"value": 25, "note": "Merit position dropped to 9"})
    assert r.status_code == 200, r.text
    assert r.json()["withdrawn"] == 3000.0 and r.json()["applied"] == 1500.0
    fee = await _fee(app_client, aid)
    assert fee["total_concession"] == 2500.0                                          # 1,000 kept on the paid installment + 3 x 500
    listed = await _listed(app_client, sid, scid)
    assert listed["value"] == 25.0 and listed["status"] == "ACTIVE" and listed["given_so_far"] == 2500.0
    renamed = await app_client.patch(f"{SC}/{scid}", json={"name": "Merit 25", "note": "Name to match the new rate"})
    assert renamed.status_code == 200 and (await _listed(app_client, sid, scid))["name"] == "Merit 25"
    assert (await _fee(app_client, aid))["total_concession"] == 2500.0                # a rename changes no money
    same = await app_client.patch(f"{SC}/{scid}", json={"name": "Merit 25", "note": "Nothing really"})
    assert same.status_code == 422


async def test_raising_a_scholarship_waits_for_another_administrator(app_client, admin_user, db_session):
    sid, aid, scid = await _setup(app_client, "74001-0000004-4")
    await _second_admin(db_session)                                                   # from now on another administrator can approve
    await _login_admin(app_client)
    r = await app_client.patch(f"{SC}/{scid}", json={"value": 75, "valid_until": "2026-12-31", "note": "Moved to merit position 1"})
    assert r.status_code == 202 and r.json()["pending_approval"]
    assert (await _fee(app_client, aid))["total_concession"] == 4000.0                # nothing changed yet
    req = next(x for x in await _waiting(app_client) if x["kind"] == "SCHOLARSHIP_EDIT")
    await _as_two(app_client)
    ok = await app_client.post(f"{AP}/{req['id']}/decide", json={"approve": True})
    assert ok.status_code == 200, ok.text
    assert (await _fee(app_client, aid))["total_concession"] == 6000.0
    listed = await _listed(app_client, sid, scid)
    assert listed["value"] == 75.0 and listed["valid_until"] == "2026-12-31"


async def test_the_accounts_office_only_proposes_changes(app_client, admin_user, db_session):
    sid, aid, scid = await _setup(app_client, "74001-0000005-5")
    await _create_accounts_officer(app_client, db_session)                            # logs in as the accounts officer
    r = await app_client.patch(f"{SC}/{scid}", json={"value": 10, "note": "Lower as per committee"})
    assert r.status_code == 202 and r.json()["pending_approval"]                      # even a lowering waits for an administrator
    assert (await app_client.post(f"{SC}/{scid}/end")).status_code == 403             # ending is for administrators
    await _login_admin(app_client)
    assert (await _fee(app_client, aid))["total_concession"] == 4000.0


async def test_a_scholarship_ended_the_old_way_can_be_finished(app_client, admin_user, db_session):
    """Before this release, ending only changed the status and later installments kept the discount: End on it now removes that."""
    import uuid
    from datetime import date

    from app.fees.models import Scholarship, ScholarshipStatus

    sid, aid, scid = await _setup(app_client, "74001-0000006-6")
    s = await db_session.get(Scholarship, uuid.UUID(scid))
    s.status, s.valid_until = ScholarshipStatus.ENDED, date(2026, 10, 1)              # what the old End left behind
    await db_session.commit()
    assert (await _fee(app_client, aid))["total_concession"] == 4000.0
    r = await app_client.post(f"{SC}/{scid}/end", json={})
    assert r.status_code == 200, r.text
    assert r.json()["withdrawn"] == 2000.0                                             # installments due 1 Nov and 1 Dec
    assert (await _fee(app_client, aid))["total_concession"] == 2000.0
    assert (await app_client.post(f"{SC}/{scid}/end", json={})).status_code == 422      # nothing left to remove


async def test_reset_all_removes_discounts_from_unpaid_installments_and_allows_a_clean_regrant(app_client, admin_user, db_session):
    """The one-off reset (scripts/reset_scholarships.py): every discount comes off installments not yet paid in full,
    paid ones keep theirs, every scholarship ends and stays on record; new tuition-only discounts can then be given."""
    from app.fees.scholarship_service import ScholarshipService

    sid_b, _ = await _create_and_activate_student(app_client, cnic="74001-0000008-8")  # enrolled first, so the fee reaches B too
    sid_a, aid_a, sc_a = await _setup(app_client, "74001-0000007-7")
    pay = await app_client.post(f"/api/v1/fees/assignments/{aid_a}/payments", json={"amount": 3000, "description": "Counter", "method": "CASH"})
    assert pay.status_code == 201                                                    # installments 1-3 paid at the discounted amount
    aid_b = next(a["id"] for a in (await app_client.get("/api/v1/fees/assignments")).json() if a["payer_id"] == sid_b)
    r = await app_client.post(SC, json={"student_id": sid_b, "name": "Sibling", "kind": "FIXED", "value": 2000, "valid_from": "2026-01-01",
                                        "reason": "Brother also enrolled"})
    assert r.status_code == 201, r.text
    sc_b = r.json()["id"]
    assert (await _fee(app_client, aid_b))["total_concession"] == 2000.0

    rows = await ScholarshipService(db_session).reset_all(actor_id=admin_user.id, note="test reset")
    await db_session.commit()
    by = {x["scholarship"]: x for x in rows}
    assert by["Merit 50"]["removed_from_unpaid"] == 1000.0 and by["Merit 50"]["kept_on_paid_installments"] == 3000.0
    assert by["Sibling"]["removed_from_unpaid"] == 2000.0 and by["Sibling"]["kept_on_paid_installments"] == 0.0
    assert (await _fee(app_client, aid_a))["total_concession"] == 3000.0
    assert (await _fee(app_client, aid_b))["total_concession"] == 0.0
    assert (await _listed(app_client, sid_a, sc_a))["status"] == "ENDED" and (await _listed(app_client, sid_b, sc_b))["status"] == "ENDED"

    again = await ScholarshipService(db_session).reset_all(actor_id=admin_user.id, note="test reset again")
    await db_session.commit()
    assert sum(x["removed_from_unpaid"] for x in again) == 0.0                         # running it twice changes nothing

    types = {t["code"]: t["id"] for t in (await app_client.get("/api/v1/fees/types")).json()}
    r = await app_client.post(SC, json={"student_id": sid_b, "name": "Tuition 50%", "kind": "PERCENT", "value": 50, "fee_type_id": types["TUI"],
                                        "valid_from": "2026-01-01", "reason": "Re-granted on tuition only"})
    assert r.status_code == 201, r.text
    assert (await _fee(app_client, aid_b))["total_concession"] == 4000.0


async def test_remove_all_discounts_from_the_page_previews_first_then_needs_reset(app_client, admin_user, db_session):
    sid, aid, scid = await _setup(app_client, "74001-0000009-9")
    p = await app_client.get(f"{SC}/reset-preview")
    assert p.status_code == 200, p.text
    assert p.json()["running"] == 1 and p.json()["removed_from_unpaid"] == 4000.0 and p.json()["students"] == 1
    assert (await _fee(app_client, aid))["total_concession"] == 4000.0                # the preview saved nothing
    assert (await _listed(app_client, sid, scid))["status"] == "ACTIVE"
    bad = await app_client.post(f"{SC}/reset", json={"confirm": "yes", "note": "Clean start for tuition only"})
    assert bad.status_code == 422
    ok = await app_client.post(f"{SC}/reset", json={"confirm": "RESET", "note": "Clean start for tuition only"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["removed_from_unpaid"] == 4000.0
    assert (await _fee(app_client, aid))["total_concession"] == 0.0
    assert (await _listed(app_client, sid, scid))["status"] == "ENDED"
    await _create_accounts_officer(app_client, db_session, code="ACC9")
    assert (await app_client.post(f"{SC}/reset", json={"confirm": "RESET", "note": "Not allowed here"})).status_code == 403


async def _with_student_card(client, cnic):
    """Tuition (4 x Rs 2,000) plus a one-off Student Card fee of Rs 500 for the same student."""
    sid, _ = await _create_and_activate_student(client, cnic=cnic)
    await _create_active_fee_structure(client, total_amount=8000, installment_count=4)
    card_type = await client.post("/api/v1/fees/types", json={"code": "CARD", "name": "Student Card"})
    assert card_type.status_code == 201, card_type.text
    card = await client.post("/api/v1/fees/structures", json={"fee_type_id": card_type.json()["id"], "name": "Student Card", "term_label": "Fall 2026",
                                                             "total_amount": 500, "installment_count": 1, "effective_from": "2026-09-01"})
    assert card.status_code == 201, card.text
    assert (await client.post(f"/api/v1/fees/structures/{card.json()['id']}/activate")).status_code == 200
    mine = [a for a in (await client.get("/api/v1/fees/assignments")).json() if a["payer_id"] == sid]
    tuition = next(a["id"] for a in mine if a["fee_structure_id"] != card.json()["id"])
    card_fee = next(a["id"] for a in mine if a["fee_structure_id"] == card.json()["id"])
    types = {t["code"]: t["id"] for t in (await client.get("/api/v1/fees/types")).json()}
    return sid, tuition, card_fee, types


@pytest.mark.parametrize("kind,value,tuition_off", [("FIXED", 2000, 2000.0), ("PERCENT", 100, 8000.0), ("PERCENT", 50, 4000.0)])
async def test_a_discount_given_on_every_fee_by_mistake_can_be_narrowed_to_tuition(app_client, admin_user, db_session, kind, value, tuition_off):
    """The accounts office meant tuition only but chose every fee, so the Student Card fee was discounted too. Changing
    "Applies to" to Tuition puts the card back to its full Rs 500 and leaves the tuition discount as it was. Narrowing
    gives the student less, so it does not wait for a second administrator."""
    cnic = {"FIXED": "74001-0000011-1", 100: "74001-0000012-2", 50: "74001-0000013-3"}[kind if kind == "FIXED" else value]
    sid, tuition, card, types = await _with_student_card(app_client, cnic)
    r = await app_client.post(SC, json={"student_id": sid, "name": "Need based", "kind": kind, "value": value, "valid_from": "2026-01-01",
                                        "reason": "Given on every fee by mistake"})
    assert r.status_code == 201, r.text
    scid = r.json()["id"]
    card_off = 500.0 if kind == "FIXED" or value == 100 else 250.0
    assert (await _fee(app_client, card))["total_concession"] == card_off
    assert (await _fee(app_client, tuition))["total_concession"] == tuition_off

    await _second_admin(db_session)                                                   # someone else could approve, yet narrowing needs nobody
    await _login_admin(app_client)
    r = await app_client.patch(f"{SC}/{scid}", json={"fee_type_id": types["TUI"], "note": "Meant for tuition only"})
    assert r.status_code == 200, r.text
    assert (await _fee(app_client, card))["total_concession"] == 0.0
    assert (await _fee(app_client, card))["total_payable"] == 500.0
    assert (await _fee(app_client, tuition))["total_concession"] == tuition_off
    lines = (await app_client.get(f"/api/v1/fees/assignments/{card}/statement")).json()["lines"]
    assert [x["amount"] for x in lines if x["what"] == "Discount withdrawn"] == [card_off]   # on record, nothing deleted


async def test_widening_a_tuition_discount_to_every_fee_still_waits_for_approval(app_client, admin_user, db_session):
    sid, tuition, card, types = await _with_student_card(app_client, "74001-0000014-4")
    r = await app_client.post(SC, json={"student_id": sid, "name": "Tuition 50", "kind": "PERCENT", "value": 50, "fee_type_id": types["TUI"],
                                        "valid_from": "2026-01-01", "reason": "Tuition only"})
    assert r.status_code == 201, r.text
    await _second_admin(db_session)
    await _login_admin(app_client)
    r2 = await app_client.patch(f"{SC}/{r.json()['id']}", json={"fee_type_id": None, "note": "Every fee please"})
    assert r2.status_code == 202, r2.text
    assert (await _fee(app_client, card))["total_concession"] == 0.0
