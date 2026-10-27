"""Academic terms: new offerings and fee structures must use a registered, open term (production default)."""
from __future__ import annotations

import pytest

from tests.integration.test_exam_controller import _exam_user
from tests.integration.test_fees import _login, _login_admin

pytestmark = [pytest.mark.asyncio, pytest.mark.launch_guards]
T = "/api/v1/academics/academic-terms"


async def _course(client, code="TM101"):
    return (await client.post("/api/v1/academics/courses", json={"code": code, "title": "Term test", "credit_hours": 3})).json()["id"]


async def test_offering_needs_a_registered_open_term_and_takes_its_dates(app_client, admin_user):
    await _login_admin(app_client)
    course = await _course(app_client)
    typo = await app_client.post("/api/v1/academics/offerings", json={"course_id": course, "term_label": "Fall 2030"})
    assert typo.status_code == 422 and "not a registered term" in typo.text
    term = await app_client.post(T, json={"label": "Fall 2030", "start_date": "2030-09-02", "end_date": "2030-12-20"})
    assert term.status_code == 201, term.text
    near = await app_client.post("/api/v1/academics/offerings", json={"course_id": course, "term_label": "fall 2030"})
    assert near.status_code == 422 and 'Did you mean "Fall 2030"' in near.json()["error"]["message"]
    ok = await app_client.post("/api/v1/academics/offerings", json={"course_id": course, "term_label": "Fall 2030"})
    assert ok.status_code == 201 and ok.json()["term_start_date"] == "2030-09-02" and ok.json()["term_end_date"] == "2030-12-20"
    assert "Fall 2030" in (await app_client.get("/api/v1/academics/terms")).json()


async def test_duplicates_bad_dates_and_closed_terms_are_refused(app_client, admin_user):
    await _login_admin(app_client)
    t = (await app_client.post(T, json={"label": "Spring 2031", "start_date": "2031-02-03", "end_date": "2031-06-20"})).json()
    dup = await app_client.post(T, json={"label": "spring  2031", "start_date": "2031-02-03", "end_date": "2031-06-20"})
    assert dup.status_code == 409
    bad = await app_client.post(T, json={"label": "Summer 2031", "start_date": "2031-07-01", "end_date": "2031-06-01"})
    assert bad.status_code == 422
    assert (await app_client.patch(f"{T}/{t['id']}", json={"status": "CLOSED"})).json()["status"] == "CLOSED"
    course = await _course(app_client, "TM102")
    closed = await app_client.post("/api/v1/academics/offerings", json={"course_id": course, "term_label": "Spring 2031"})
    assert closed.status_code == 422 and "closed" in closed.text


async def test_fee_structure_needs_a_registered_term(app_client, admin_user):
    await _login_admin(app_client)
    ft = (await app_client.post("/api/v1/fees/types", json={"code": "TUI", "name": "Tuition"})).json()["id"]
    body = {"fee_type_id": ft, "name": "Tuition", "term_label": "Fal 2030", "total_amount": 1000, "effective_from": "2030-09-01"}
    assert (await app_client.post("/api/v1/fees/structures", json=body)).status_code == 422
    await app_client.post(T, json={"label": "Fall 2030", "start_date": "2030-09-02", "end_date": "2030-12-20"})
    body["term_label"] = "Fall 2030"
    assert (await app_client.post("/api/v1/fees/structures", json=body)).status_code == 201


async def test_only_administration_manages_terms(app_client, admin_user, db_session):
    user, pw = await _exam_user(app_client, db_session)
    await _login(app_client, user, pw)
    assert (await app_client.post(T, json={"label": "X 2030", "start_date": "2030-01-01", "end_date": "2030-05-01"})).status_code == 403

async def test_a_term_can_be_edited_and_renamed_everywhere(app_client, admin_user):
    await _login_admin(app_client)
    t = (await app_client.post(T, json={"label": "Fal 2032", "start_date": "2032-09-01", "end_date": "2032-12-20"})).json()
    fixed = await app_client.patch(f"{T}/{t['id']}", json={"label": "Fall 2032", "end_date": "2032-12-24"})
    assert fixed.status_code == 200 and fixed.json()["label"] == "Fall 2032" and fixed.json()["end_date"] == "2032-12-24"
    other = (await app_client.post(T, json={"label": "Spring 2033", "start_date": "2033-02-01", "end_date": "2033-06-01"})).json()
    assert (await app_client.patch(f"{T}/{other['id']}", json={"label": "fall 2032"})).status_code == 409      # no duplicates
    assert (await app_client.patch(f"{T}/{t['id']}", json={"start_date": "2033-01-01"})).status_code == 422    # must end after it starts
    course = await _course(app_client, "TM103")
    made = await app_client.post("/api/v1/academics/offerings", json={"course_id": course, "term_label": "Fall 2032"})
    assert made.status_code == 201, made.text
    listed = next(x for x in (await app_client.get(T)).json() if x["id"] == t["id"])
    assert listed["used_by"] == {"offerings": 1}
    renamed = await app_client.patch(f"{T}/{t['id']}", json={"label": "Autumn 2032"})        # used: renamed on every record too
    assert renamed.status_code == 200 and renamed.json()["label"] == "Autumn 2032"
    offs = (await app_client.get("/api/v1/academics/offerings", params={"term_label": "Autumn 2032"})).json()
    assert len(offs) == 1 and not (await app_client.get("/api/v1/academics/offerings", params={"term_label": "Fall 2032"})).json()
    log = (await app_client.get("/api/v1/audit/activity", params={"action": "academic_term.renamed"})).json()["items"]
    detail = (await app_client.get(f"/api/v1/audit/activity/{log[0]['id']}")).json()
    assert detail["metadata"]["records_updated"]["offerings"] == 1 and detail["before"]["label"] == "Fall 2032"
    assert (await app_client.patch(f"{T}/{t['id']}", json={"start_date": "2032-08-25"})).json()["start_date"] == "2032-08-25"   # dates still can
    page = (await app_client.get("/dashboard/admin/courses")).text
    assert 'data-tab="terms"' in page and 'id="term-edit"' in page and 'id="term-add-form"' in page