"""The accounts office sees (and can search by) the student's father, or the main guardian when no father is on record,
wherever it picks or lists a student and on challans, receipts and statements; and it has a read-only Student lookup
that links to the fee account. Other staff keep the plain directory."""
from __future__ import annotations

import io

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.students.family import label_for
from app.students.models import Student, StudentGuardian
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login_admin, _student_payload
from tests.integration.test_fees import _create_active_fee_structure

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _kid(client, n, *, gender="Male", guardian="Ahmed Raza Khan", gcnic="35202-1111111-1", relation="Father"):
    r = await client.post("/api/v1/students", json=_student_payload(first_name="Ali", last_name="Khan", gender=gender, cnic=f"35202-81000{n:02d}-1",
                                                                    guardian_name=guardian, guardian_cnic=gcnic, relation_with_guardian=relation))
    assert r.status_code == 201, r.text
    return r.json()


async def _family(client):
    await _login_admin(client)
    a = await _kid(client, 1)                                                          # two "Ali Khan"s with different fathers
    b = await _kid(client, 2, guardian="Bashir Ahmed", gcnic="35202-2222222-2")
    c = await _kid(client, 3, gender="Female", guardian="Sara Bibi", gcnic="35202-3333333-3", relation="Mother")
    return a, b, c


async def test_labels_for_father_mother_and_none():
    def g(name, rel, primary=True):
        return StudentGuardian(name=name, relation=rel, cnic="35202-0000000-0", is_primary=primary)
    assert label_for([g("Ahmed Khan", "Father")], "Male")["label"] == "s/o Ahmed Khan"
    assert label_for([g("Ahmed Khan", "father")], "Female")["label"] == "d/o Ahmed Khan"
    assert label_for([g("Sara Bibi", "Mother")], "Female")["label"] == "Guardian: Sara Bibi (Mother)"
    assert label_for([g("Imran", "uncle", True), g("Ahmed", "Father", False)], "Male")["label"] == "s/o Ahmed"     # the father first
    assert label_for([g("Rashid", "Grandfather")], "Male")["label"] == "Guardian: Rashid (Grandfather)"
    assert label_for([], "Male") is None


async def test_the_accounts_office_sees_and_searches_by_the_father(app_client, admin_user, db_session):
    a, b, c = await _family(app_client)

    # the administration: "Ali Khan" twice, told apart by the father
    both = (await app_client.get("/api/v1/students", params={"q": "Ali Khan"})).json()["items"]
    labels = {s["id"]: s["guardian_label"] for s in both}
    assert labels[a["id"]] == "s/o Ahmed Raza Khan" and labels[b["id"]] == "s/o Bashir Ahmed" and labels[c["id"]] == "Guardian: Sara Bibi (Mother)"
    # found by the father's name (any words) or his CNIC, with or without dashes
    assert [s["id"] for s in (await app_client.get("/api/v1/students", params={"q": "raza ahmed"})).json()["items"]] == [a["id"]]
    for q in ("35202-2222222-2", "3520222222222"):
        assert [s["id"] for s in (await app_client.get("/api/v1/students", params={"q": q})).json()["items"]] == [b["id"]]

    # the accounts office: the same
    user, pw = await _staff(app_client, db_session, "ACF", "ACCOUNTS_OFFICER", "35202-7100094-3")
    await _first_login(app_client, user, pw)
    rows = (await app_client.get("/api/v1/students", params={"q": "Bashir"})).json()["items"]
    assert [(s["id"], s["guardian_label"]) for s in rows] == [(b["id"], "s/o Bashir Ahmed")]

    # other staff (exam office): no guardian in the list, and the father's name finds nothing
    await _login_admin(app_client)
    user2, pw2 = await _staff(app_client, db_session, "EXF", "EXAM_CONTROLLER", "35202-7100095-3")
    await _first_login(app_client, user2, pw2)
    plain = (await app_client.get("/api/v1/students", params={"q": "Ali Khan"})).json()["items"]
    assert plain and all(s.get("guardian_label") is None and s.get("guardian_name") is None for s in plain)
    assert (await app_client.get("/api/v1/students", params={"q": "Bashir"})).json()["items"] == []


async def test_fee_screens_prints_and_the_lookup_show_the_father(app_client, admin_user, db_session):
    a, b, c = await _family(app_client)
    await _create_active_fee_structure(app_client, total_amount=30000, installment_count=1)

    # the fee list: search by the father, each row says whose child it is
    rows = (await app_client.get("/api/v1/fees/assignments", params={"q": "Bashir", "page": 1})).json()
    assert [(r["payer_id"], r["payer_guardian"]) for r in rows] == [(b["id"], "s/o Bashir Ahmed")]
    by_cnic = (await app_client.get("/api/v1/fees/assignments", params={"q": "3520211111111", "page": 1})).json()
    assert [r["payer_id"] for r in by_cnic] == [a["id"]]
    mine = (await app_client.get(f"/api/v1/fees/assignments/{rows[0]['id']}")).json()
    assert mine["payer_guardian"] == "s/o Bashir Ahmed"

    # the student's fee account and statement
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{c['id']}")).json()
    assert acct["payer"]["guardian_label"] == "Guardian: Sara Bibi (Mother)" and acct["payer"]["guardian_relation"] == "Mother"

    # the challan and the receipt print the father's name
    from pypdf import PdfReader

    fee = acct["assignments"][0]
    inst = fee["installments"][0]
    ch = inst.get("challan") or {}
    if not ch:
        made = await app_client.post(f"/api/v1/fees/assignments/{fee['id']}/installments/{inst['ledger_entry_id']}/challan")
        ch = made.json()
    pdf = await app_client.get(f"/api/v1/fees/challans/{ch['id']}/pdf")
    assert pdf.status_code == 200
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "guardian (mother)" in text.lower() and "Sara Bibi" in text
    paid = await app_client.post(f"/api/v1/fees/challans/{ch['id']}/mark-paid", json={"method": "CASH"})
    assert paid.status_code == 200, paid.text
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{c['id']}")).json()
    receipt = next(p for p in acct["payments"] if p["receipt_number"])
    rp = await app_client.get(f"/api/v1/fees/receipts/{receipt['id']}/pdf")
    assert rp.status_code == 200
    rtext = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(rp.content)).pages)
    assert "Guardian (Mother)" in rtext and "Sara Bibi" in rtext

    # the accounts office's Student lookup
    page = (await app_client.get("/dashboard/accounts/students", headers={"accept": "text/html"})).text
    assert 'data-fee-link="/dashboard/accounts/fees"' in page and "father&rsquo;s name" in page
    principal = (await app_client.get("/dashboard/principal/students", headers={"accept": "text/html"})).text
    assert "data-fee-link" not in principal
    home = (await app_client.get("/dashboard/accounts", headers={"accept": "text/html"})).text
    assert "/dashboard/accounts/students" in home

    # a student whose guardian record is gone: nothing breaks, no label
    await db_session.execute(update(StudentGuardian).where(StudentGuardian.student_id == Student.id, Student.cnic == "35202-8100002-1").values(name=" "))
    await db_session.commit()
    again = (await app_client.get("/api/v1/fees/assignments", params={"q": "Ali Khan", "page": 1})).json()
    assert {r["payer_id"]: r["payer_guardian"] for r in again}[b["id"]] is None
