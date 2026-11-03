"""The administration forwards an application to a department; the department works on it and sends it back;
the applicant follows the stage (but not the internal notes); the administration makes the final decision."""
from __future__ import annotations

import pytest

from tests.integration.test_applications import ADMIN, API, _apply, _as, _login, _people, _type

pytestmark = pytest.mark.asyncio


async def test_forward_to_a_department_and_back(app_client, admin_user, db_session):
    raw = await _people(app_client, db_session)
    t = await _type(app_client, "Exam related")
    await _as(app_client, raw, "student")
    made = await _apply(app_client, t, subject="Recheck my DS mid-term", details="I think question 4 was not marked.")
    assert made.status_code == 201, made.text
    app_id = made.json()["id"]
    assert made.json()["stage_label"].startswith("Submitted")

    await _login(app_client, **ADMIN)
    deps = {d["code"]: d for d in (await app_client.get(f"{API}/departments")).json()}
    assert deps["ACCOUNTS_OFFICER"]["people"] >= 1 and deps["EXAM_CONTROLLER"]["people"] == 0
    assert (await app_client.post(f"{API}/{app_id}/forward", json={"department": "EXAM_CONTROLLER"})).status_code == 422   # nobody holds it
    assert (await app_client.post(f"{API}/{app_id}/forward", json={"department": "NOPE"})).status_code == 422
    fw = await app_client.post(f"{API}/{app_id}/forward", json={"department": "ACCOUNTS_OFFICER", "note": "Internal: check whether the recheck fee was paid."})
    assert fw.status_code == 200, fw.text
    body = fw.json()
    assert body["status"] == "UNDER_REVIEW" and body["stage_label"] == "Forwarded to Accounts" and body["forwards"][0]["note"].startswith("Internal")
    assert (await app_client.post(f"{API}/{app_id}/forward", json={"department": "ACCOUNTS_OFFICER"})).status_code == 422       # already with them
    forward_id = body["forwards"][0]["id"]

    # the applicant sees the stage but not the internal note
    await _as(app_client, raw, "student")
    mine = (await app_client.get(f"{API}/{app_id}")).json()
    assert mine["stage_label"] == "Forwarded to Accounts" and mine["forwards"] == []
    assert all(e["note"] is None for e in mine["timeline"]) and any(e["label"] == "Forwarded to Accounts" for e in mine["timeline"])
    assert (await app_client.get(f"{API}/department/inbox")).json()["departments"] == []                       # a student has no department

    # another department cannot see or answer it
    await _as(app_client, raw, "librarian")
    assert (await app_client.get(f"{API}/department/inbox")).json()["items"] == []
    assert (await app_client.post(f"{API}/forwards/{forward_id}/respond", json={"status": "DONE", "response": "x x x"})).status_code == 404

    # Accounts works on it and sends it back
    await _as(app_client, raw, "accounts")
    box = (await app_client.get(f"{API}/department/inbox")).json()
    assert box["departments"][0]["name"] == "Accounts" and box["items"][0]["application"]["applicant"]["name"]
    assert (await app_client.get(f"{API}/badge")).json()["count"] >= 1
    assert (await app_client.post(f"{API}/forwards/{forward_id}/respond", json={"status": "IN_PROGRESS"})).status_code == 200
    await _as(app_client, raw, "student")
    assert (await app_client.get(f"{API}/{app_id}")).json()["stage_label"] == "Accounts is working on it"
    await _as(app_client, raw, "accounts")
    assert (await app_client.post(f"{API}/forwards/{forward_id}/respond", json={"status": "DONE"})).status_code == 422      # a note is needed
    assert (await app_client.post(f"{API}/forwards/{forward_id}/respond", json={"status": "DONE", "response": "Recheck fee paid on 2 Nov."})).status_code == 200
    assert (await app_client.post(f"{API}/forwards/{forward_id}/respond", json={"status": "IN_PROGRESS"})).status_code == 422  # already sent back

    await _as(app_client, raw, "student")
    assert (await app_client.get(f"{API}/{app_id}")).json()["stage_label"] == "Back with the administration"

    # the administration sees the department's note and decides
    await _login(app_client, **ADMIN)
    full = (await app_client.get(f"{API}/{app_id}")).json()
    assert full["forwards"][0]["status"] == "DONE" and full["forwards"][0]["response"] == "Recheck fee paid on 2 Nov."
    assert (await app_client.post(f"{API}/{app_id}/review", json={"action": "APPROVE", "note": "Paper rechecked."})).status_code == 200
    assert (await app_client.post(f"{API}/{app_id}/forward", json={"department": "LIBRARIAN"})).status_code == 422             # closed


async def test_the_administration_can_take_it_back(app_client, admin_user, db_session):
    raw = await _people(app_client, db_session)
    t = await _type(app_client, "Library related")
    await _as(app_client, raw, "student")
    app_id = (await _apply(app_client, t, subject="Lost library card", details="I lost my card last week.")).json()["id"]
    await _login(app_client, **ADMIN)
    fid = (await app_client.post(f"{API}/{app_id}/forward", json={"department": "LIBRARIAN"})).json()["forwards"][0]["id"]
    back = await app_client.post(f"{API}/forwards/{fid}/recall")
    assert back.status_code == 200 and back.json()["forwards"][0]["status"] == "RECALLED" and back.json()["stage_label"] == "In review"
    await _as(app_client, raw, "librarian")
    assert (await app_client.get(f"{API}/department/inbox")).json()["items"] == []
    assert (await app_client.post(f"{API}/forwards/{fid}/respond", json={"status": "IN_PROGRESS"})).status_code == 422