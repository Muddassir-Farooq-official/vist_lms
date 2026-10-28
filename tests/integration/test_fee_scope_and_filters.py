"""A discount limited to one KIND of fee (e.g. Tuition) applies to that kind in every semester and leaves other fees alone;
the fee assignments list can be searched, filtered and paged."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio
FA = "/api/v1/fees/assignments"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _structure(client, type_id, name, term, total):
    r = await client.post("/api/v1/fees/structures", json={"fee_type_id": type_id, "name": name, "term_label": term, "total_amount": total,
                                                           "installment_count": 1, "effective_from": "2026-09-01"})
    assert r.status_code == 201, r.text
    assert (await client.post(f"/api/v1/fees/structures/{r.json()['id']}/activate")).status_code == 200
    return r.json()["id"]


async def _mine(client, sid):
    return {a["fee_structure_name"]: a for a in (await client.get(FA)).json() if a["payer_id"] == sid}


async def test_a_tuition_only_discount_reaches_every_semesters_tuition_and_nothing_else(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="73001-0000001-1")
    await _create_active_fee_structure(app_client, total_amount=10000)                      # type TUI, "Tuition — Fall 2026"
    types = {t["code"]: t["id"] for t in (await app_client.get("/api/v1/fees/types")).json()}
    exam = (await app_client.post("/api/v1/fees/types", json={"code": "EXM", "name": "Exam Fee"})).json()["id"]
    await _structure(app_client, exam, "Exam — Fall 2026", "Fall 2026", 4000)

    both = await app_client.post("/api/v1/fees/scholarships", json={"student_id": sid, "name": "Mixed", "kind": "PERCENT", "value": 10,
                                                                    "fee_type_id": types["TUI"], "fee_structure_id": (await _mine(app_client, sid))["Exam — Fall 2026"]["fee_structure_id"],
                                                                    "reason": "Both chosen by mistake"})
    assert both.status_code == 422                                                               # one fee OR one kind of fee, not both
    r = await app_client.post("/api/v1/fees/scholarships", json={"student_id": sid, "name": "Tuition 50%", "kind": "PERCENT", "value": 50,
                                                                 "fee_type_id": types["TUI"], "valid_from": "2026-01-01", "reason": "Merit list, tuition only"})
    assert r.status_code == 201, r.text
    fees = await _mine(app_client, sid)
    assert fees["Tuition — Fall 2026"]["total_concession"] == 5000.0
    assert fees["Exam — Fall 2026"]["total_concession"] == 0.0                                   # the exam fee is left alone
    listed = next(s for s in (await app_client.get("/api/v1/fees/scholarships", params={"student_id": sid})).json() if s["name"] == "Tuition 50%")
    assert listed["fee_type_id"] == types["TUI"] and listed["fee_structure_id"] is None

    await _structure(app_client, types["TUI"], "Tuition — Spring 2027", "Spring 2027", 8000)    # next semester's tuition
    await _structure(app_client, exam, "Exam — Spring 2027", "Spring 2027", 3000)
    fees = await _mine(app_client, sid)
    assert fees["Tuition — Spring 2027"]["total_concession"] == 4000.0                           # applied by itself
    assert fees["Exam — Spring 2027"]["total_concession"] == 0.0


async def test_the_assignments_list_can_be_searched_filtered_and_paged(app_client, admin_user):
    a, _ = await _create_and_activate_student(app_client, cnic="73001-0000002-2")
    b, _ = await _create_and_activate_student(app_client, cnic="73001-0000003-3")
    await _create_active_fee_structure(app_client, total_amount=6000)
    everything = (await app_client.get(FA)).json()                                               # no parameters: the whole list, as before
    mine = [x for x in everything if x["payer_id"] in (a, b)]
    assert len(mine) == 2 and all(x["payer_identifier"] and x["term_label"] == "Fall 2026" for x in mine)

    roll = mine[0]["payer_identifier"]
    found = await app_client.get(FA, params={"q": roll, "page": 1})
    assert found.status_code == 200 and [x["payer_identifier"] for x in found.json()] == [roll] and found.headers["X-Total-Count"] == "1"

    page = await app_client.get(FA, params={"page": 1, "page_size": 1, "term_label": "Fall 2026", "payer_type": "STUDENT"})
    assert len(page.json()) == 1 and int(page.headers["X-Total-Count"]) >= 2
    assert (await app_client.get(FA, params={"term_label": "No such term", "page": 1})).json() == []

    owing = (await app_client.get(FA, params={"owing": "false", "status": "ACTIVE"})).json()
    assert all(x["total_due_now"] <= 0 for x in owing)
    await _login_admin(app_client)