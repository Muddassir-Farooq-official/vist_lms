"""Admissions: rounds with a merit formula, applications checked like student records, verification,
merit lists that fill the free seats in order, expiry and the next list, and admitting -> a real student."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.admissions.models import Applicant
from app.core.clock import institute_today
from app.core.config import settings
from tests.integration.test_fees import _login_admin, _student_payload

pytestmark = pytest.mark.asyncio
A = "/api/v1/admissions"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _round(client, seats=2, test_weight=50):
    await _login_admin(client)
    prog = (await client.post("/api/v1/programs", json={"code": "ADM", "name": "Admissions BS", "duration_years": 4, "total_semesters": 8})).json()["id"]
    r = await client.post(f"{A}/rounds", json={"term_label": "Fall 2027", "program_id": prog, "seats": seats, "weight_matric": 10,
                                                 "weight_inter": 100 - 10 - test_weight, "weight_test": test_weight, "min_inter_percent": 45})
    assert r.status_code == 201, r.text
    return r.json()["id"], prog


async def _apply(client, rid, n, matric, inter, test=None):
    body = {**_student_payload(first_name=f"App{n}", cnic=f"79001-000000{n}-{n % 10}", guardian_cnic=f"79002-000000{n}-{n % 10}"),
            "matric_obtained": matric, "matric_total": 1100, "inter_obtained": inter, "inter_total": 1100}
    if test is not None:
        body.update(test_obtained=test, test_total=100)
    return await client.post(f"{A}/rounds/{rid}/applicants", json=body)


async def test_aggregate_ranking_merit_lists_and_admission(app_client, admin_user, db_session):
    rid, prog = await _round(app_client, seats=2)
    ids = {}
    # aggregate = 10% matric + 40% inter + 50% test
    for n, (m, i, t) in enumerate([(990, 880, 60), (880, 990, 80), (770, 660, 90), (1000, 1000, 40)], start=1):
        r = await _apply(app_client, rid, n, m, i, t)
        assert r.status_code == 201, r.text
        ids[n] = r.json()
        assert ids[n]["application_no"].startswith("APP-")
    assert ids[1]["aggregate"] == pytest.approx(9 + 32 + 30, abs=0.01)                 # 90% x .1 + 80% x .4 + 60 x .5
    for n in ids:
        assert (await app_client.post(f"{A}/applicants/{ids[n]['id']}/status", json={"status": "VERIFIED"})).status_code == 200
    # merit list 1: the top two by aggregate
    m1 = (await app_client.post(f"{A}/rounds/{rid}/merit-lists", json={"offer_days": 7})).json()
    assert m1["merit_list_no"] == 1 and m1["offered"] == 2 and m1["still_waiting"] == 2
    offered = [a for a in (await app_client.get(f"{A}/rounds/{rid}/applicants", params={"status": "OFFERED"})).json()]
    ranked = sorted(ids.values(), key=lambda a: -a["aggregate"])
    assert [a["application_no"] for a in offered] == [a["application_no"] for a in ranked[:2]]
    assert [a["merit_rank"] for a in offered] == [1, 2]
    full = await app_client.post(f"{A}/rounds/{rid}/merit-lists", json={"offer_days": 7})
    assert full.status_code == 422 and "No free seats" in full.text
    pdf = await app_client.get(f"{A}/rounds/{rid}/merit-lists/1.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    # the first admits -> a real student in the program, semester 1
    first = offered[0]
    adm = (await app_client.post(f"{A}/applicants/{first['id']}/admit")).json()
    assert adm["roll_number"] and adm["username"]
    st = (await app_client.get(f"/api/v1/students/{adm['student_id']}")).json()
    assert st["program_id"] == prog and st["current_semester"] == 1 and st["admission_session"] == "Fall 2027"
    again = await app_client.post(f"{A}/applicants/{first['id']}/admit")
    assert again.status_code == 422

    # the second does not take up the offer: it expires and merit list 2 offers the seat to the next in line
    second = (await db_session.execute(select(Applicant).where(Applicant.application_no == offered[1]["application_no"]))).scalar_one()
    second.offer_expires_on = institute_today() - timedelta(days=1)
    await db_session.flush()
    assert (await app_client.post(f"{A}/rounds/{rid}/expire-offers")).json() == {"expired": 1}
    late = await app_client.post(f"{A}/applicants/{offered[1]['id']}/admit")
    assert late.status_code == 422
    m2 = (await app_client.post(f"{A}/rounds/{rid}/merit-lists", json={"offer_days": 5})).json()
    assert m2["merit_list_no"] == 2 and m2["offered"] == 1
    nxt = (await app_client.get(f"{A}/rounds/{rid}/applicants", params={"status": "OFFERED"})).json()
    assert [a["application_no"] for a in nxt] == [ranked[2]["application_no"]]
    summary = next(r for r in (await app_client.get(f"{A}/rounds")).json() if r["id"] == rid)
    assert summary["seats_free"] == 0 and summary["counts"]["ADMITTED"] == 1 and summary["counts"]["EXPIRED"] == 1


async def test_applications_are_checked_like_student_records(app_client, admin_user):
    rid, _prog = await _round(app_client)
    bad_cnic = await app_client.post(f"{A}/rounds/{rid}/applicants", json={**_student_payload(cnic="12345"), "matric_obtained": 900, "matric_total": 1100,
                                                                        "inter_obtained": 900, "inter_total": 1100, "test_obtained": 70, "test_total": 100})
    assert bad_cnic.status_code == 422 and "cnic" in bad_cnic.text.lower()
    no_test = await _apply(app_client, rid, 1, 900, 900)
    assert no_test.status_code == 422 and "entry test" in no_test.text
    over = await _apply(app_client, rid, 2, 1200, 900, 70)
    assert over.status_code == 422
    ok = await _apply(app_client, rid, 3, 900, 900, 70)
    assert ok.status_code == 201
    dup = await _apply(app_client, rid, 3, 900, 900, 70)
    assert dup.status_code == 409
    low = (await _apply(app_client, rid, 4, 900, 400, 70)).json()                      # 36% intermediate < 45%
    assert low["status"] == "REJECTED" and "45" in low["remarks"]
    assert (await app_client.post(f"{A}/applicants/{low['id']}/status", json={"status": "VERIFIED"})).status_code == 422
    rej = await app_client.post(f"{A}/applicants/{ok.json()['id']}/status", json={"status": "REJECTED"})
    assert rej.status_code == 422 and "reason" in rej.text


async def test_round_rules_and_existing_students(app_client, admin_user):
    await _login_admin(app_client)
    prog = (await app_client.post("/api/v1/programs", json={"code": "AD2", "name": "Two", "duration_years": 4, "total_semesters": 8})).json()["id"]
    bad = await app_client.post(f"{A}/rounds", json={"term_label": "Fall 2027", "program_id": prog, "seats": 10, "weight_matric": 10,
                                                      "weight_inter": 40, "weight_test": 40})
    assert bad.status_code == 422 and "add up to 100" in bad.text
    rid, _ = await _round(app_client, test_weight=0)                                     # no entry test: 10% matric + 90% inter
    existing = await app_client.post("/api/v1/students", json=_student_payload(cnic="79009-0000009-9"))
    assert existing.status_code == 201
    again = await app_client.post(f"{A}/rounds/{rid}/applicants", json={**_student_payload(cnic="79009-0000009-9"), "matric_obtained": 900,
                                                                     "matric_total": 1100, "inter_obtained": 900, "inter_total": 1100})
    assert again.status_code == 409 and "already exists" in again.text
    closed = await app_client.post(f"{A}/rounds/{rid}/close")
    assert closed.json()["status"] == "CLOSED"
    assert (await _apply(app_client, rid, 5, 900, 900)).status_code == 422


async def test_only_administration_runs_admissions(app_client, admin_user):
    from tests.integration.test_fees import _create_and_activate_student, _login

    _sid, user = await _create_and_activate_student(app_client, cnic="79008-0000008-8")
    await _login(app_client, user, "StudentPass123!")
    assert (await app_client.get(f"{A}/rounds")).status_code == 403


async def test_csv_import_checks_each_row(app_client, admin_user):
    rid, _ = await _round(app_client)
    tpl = (await app_client.get(f"{A}/applications-template.csv")).text
    header, sample = tpl.strip().split("\r\n")
    bad = sample.replace("35202-1234567-1", "123").replace("Ali", "Bad")
    second = sample.replace("35202-1234567-1", "35202-1234567-2").replace("Ali", "Sara")
    csv_text = "\r\n".join([header, sample, bad, second]) + "\r\n"
    files = {"file": ("apps.csv", csv_text.encode(), "text/csv")}
    check = (await app_client.post(f"{A}/rounds/{rid}/applicants/import", params={"dry_run": "true"}, files=files)).json()
    assert check["ok_rows"] == 2 and [e["row"] for e in check["errors"]] == [3]
    assert (await app_client.get(f"{A}/rounds/{rid}/applicants")).json() == []                 # dry run saved nothing
    done = (await app_client.post(f"{A}/rounds/{rid}/applicants/import", params={"dry_run": "false"}, files=files)).json()
    assert done["ok_rows"] == 2
    assert sorted(a["name"] for a in (await app_client.get(f"{A}/rounds/{rid}/applicants")).json()) == ["Ali Khan", "Sara Khan"]


async def test_admissions_page_loads(app_client, admin_user):
    await _login_admin(app_client)
    page = await app_client.get("/dashboard/admin/admissions")
    assert page.status_code == 200 and "admissions.js" in page.text