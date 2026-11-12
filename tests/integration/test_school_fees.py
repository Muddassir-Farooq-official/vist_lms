"""School mode, stage 4: class fees, monthly vouchers (tuition + transport + annual charges, sibling discount), the
admission fee, transport routes, the fixed late fine, and that the general fee pages leave school fees alone."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.fees.account_router import challan_pdf_data
from app.fees.assignment_service import FeeStatusCalculator, LateRate, late_rate
from app.fees.models import FeeAssignment, FeeLedgerEntry, FeeStructure, LedgerEntryType
from app.fees.payment_models import PaymentChallan
from app.notifications.models import Notification
from app.school.models import SchoolVoucher, SchoolVoucherKind
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_parent_portal import _parent_login
from tests.integration.test_school_setup import _classes, _student_payload

pytestmark = pytest.mark.asyncio
S = "/api/v1/school"
F = "/api/v1/school/fees"
TODAY = date(2026, 12, 20)


@pytest.fixture(autouse=True)
def _school(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "school_mode", 1)
    monkeypatch.setattr("app.school.fees.institute_today", lambda: TODAY)


async def _kid(client, n, *, guardian: str, born: str):
    r = await client.post("/api/v1/students", json=_student_payload(first_name=f"Kid{n}", cnic=f"35202-73000{n:02d}-1", guardian_cnic=guardian,
                                                                    date_of_birth=born))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _setup_school(client):
    await _login_admin(client)
    r = await client.post(f"{S}/setup", json={"term_label": "2026-27", "start_date": "2026-04-01", "end_date": "2027-03-31", "groups": ["PRIMARY"],
                                              "sections": ["A", "B"]})
    assert r.status_code == 200, r.text
    classes = await _classes(client)
    return classes["C5"], classes["C3"]


async def _vouchers(db, student_id) -> list[SchoolVoucher]:
    return list((await db.execute(select(SchoolVoucher).where(SchoolVoucher.student_id == uuid.UUID(student_id)).order_by(SchoolVoucher.month)
                                  .execution_options(populate_existing=True))).scalars())


async def _challan(db, v: SchoolVoucher) -> PaymentChallan:
    return (await db.execute(select(PaymentChallan).where(PaymentChallan.fee_ledger_entry_id == v.ledger_entry_id))).scalar_one()


async def test_monthly_vouchers_siblings_transport_and_annual_charges(app_client, admin_user, db_session):
    c5, c3 = await _setup_school(app_client)
    elder = await _kid(app_client, 1, guardian="35202-7400001-2", born="2015-02-01")
    younger = await _kid(app_client, 2, guardian="35202-7400001-2", born="2017-06-01")     # same guardian CNIC: a sister in Class 3
    other = await _kid(app_client, 3, guardian="35202-7400003-2", born="2015-05-05")
    assert not (await app_client.post(f"{S}/sections/{c5['sections'][0]['id']}/students", json={"student_ids": [elder, other]})).json()["failed"]
    assert not (await app_client.post(f"{S}/sections/{c3['sections'][0]['id']}/students", json={"student_ids": [younger]})).json()["failed"]

    # no fees set for a class: nobody is charged, and the office is told why
    nothing = (await app_client.post(F + "/month", json={"month": "2027-01-01"})).json()
    assert nothing["made"] == 0 and {s["reason"] for s in nothing["skipped"]} == {"no fee plan for the class"}

    plans = (await app_client.get(F + "/plans")).json()
    assert plans["due_day"] == 10 and plans["late_fine"] == 200 and (plans["sibling_second"], plans["sibling_third"]) == (10, 20)
    saved = await app_client.put(F + "/plans", json={"plans": [
        {"program_id": c5["id"], "monthly_tuition": 5000, "admission_fee": 3000, "annual_charges": 6000},
        {"program_id": c3["id"], "monthly_tuition": 4000, "admission_fee": 3000, "annual_charges": 6000}]})
    assert saved.status_code == 200, saved.text
    assert next(c for c in saved.json()["classes"] if c["program_id"] == c5["id"])["monthly_tuition"] == 5000

    route = (await app_client.post(F + "/routes", json={"name": "Route 1 - Gulberg", "monthly_fee": 2000})).json()
    assert (await app_client.post(F + "/routes", json={"name": "route 1 - gulberg", "monthly_fee": 1})).status_code == 409
    on = (await app_client.post(f"{F}/routes/{route['id']}/students", json={"student_ids": [elder], "from_month": "2027-01-01"})).json()
    assert on["added"] == 1 and [s["name"] for s in on["students"]] == ["Kid1 Test"]

    jan = (await app_client.post(F + "/month", json={"month": "2027-01-01"})).json()
    assert jan["made"] == 3 and jan["session"] == "2026-27"
    assert (await app_client.post(F + "/month", json={"month": "2027-01-15"})).json()["made"] == 0          # safe to run again

    [ve] = await _vouchers(db_session, elder)
    assert (float(ve.tuition), float(ve.transport), float(ve.annual), float(ve.sibling_discount)) == (5000, 2000, 6000, 0)
    [vy] = await _vouchers(db_session, younger)                                     # the younger child: 10% off tuition
    assert (float(vy.tuition), float(vy.annual), float(vy.sibling_discount), vy.sibling_rank) == (4000, 6000, 400, 2)
    [vo] = await _vouchers(db_session, other)
    assert float(vo.sibling_discount) == 0 and vo.sibling_rank is None
    ch = await _challan(db_session, vy)
    assert float(ch.amount) == 9600 and ch.due_date == date(2027, 1, 10)
    debit = await db_session.get(FeeLedgerEntry, vy.ledger_entry_id)
    assert float(debit.amount) == 10000 and debit.description.startswith("School fee, January 2027: Tuition 4,000 + Annual charges 6,000")
    conc = (await db_session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.related_entry_id == debit.id,
                                                                    FeeLedgerEntry.entry_type == LedgerEntryType.CONCESSION))).scalar_one()
    assert float(conc.amount) == 400 and "Sibling discount 10%" in conc.description

    # the challan shows the items
    a = await db_session.get(FeeAssignment, ch.fee_assignment_id)
    che = await _challan(db_session, ve)
    data = await challan_pdf_data(db_session, che, await db_session.get(FeeAssignment, che.fee_assignment_id))
    assert data["installment_text"] == "January 2027" and data["items"] == [("Tuition", 5000.0), ("Transport (Route 1 - Gulberg)", 2000.0), ("Annual charges", 6000.0)]
    assert data["late_fee_once"] is True and data["late_fee_per_day"] == 200

    # families are told
    titles = list((await db_session.execute(select(Notification.title))).scalars())
    assert any(t.startswith("January 2027 fee voucher") for t in titles)

    # the fixed late fine: Rs. 200 once, however late
    structure = await db_session.get(FeeStructure, a.fee_structure_id)
    assert structure.late_fee_once and not structure.applies_to_students
    entries = list((await db_session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == a.id))).scalars())
    for day, fine in ((date(2027, 1, 10), 0), (date(2027, 1, 12), 200), (date(2027, 3, 1), 200)):
        row = FeeStatusCalculator.installments(entries, late_rate(structure), as_of=day)[0]
        assert row["late_fee"] == fine and row["remaining"] == 9600, (day, row)

    # February: no annual charges again; the discount stays
    assert (await app_client.post(F + "/month", json={"month": "2027-02-01"})).json()["made"] == 3
    vy2 = (await _vouchers(db_session, younger))[1]
    assert (float(vy2.annual), float(vy2.sibling_discount)) == (0, 400)
    view = (await app_client.get(F + "/month", params={"month": "2027-02-01"})).json()
    assert view["vouchers"] == 3 and view["students"] == 3 and view["billed"] == (5000 + 2000) + (4000 - 400) + 5000

    # a month outside the session is not charged
    assert (await app_client.post(F + "/month", json={"month": "2027-05-01"})).json()["session"] is None


async def test_late_fine_once_against_daily_late_fee():
    daily, once = LateRate(50.0), LateRate(200.0)
    once.once = True
    e = FeeLedgerEntry(id=uuid.uuid4(), entry_type=LedgerEntryType.DEBIT, amount=1000, installment_number=1, due_date=date(2027, 1, 10),
                       created_at=None)
    assert FeeStatusCalculator.installments([e], daily, as_of=date(2027, 1, 14))[0]["late_fee"] == 200      # 4 days x 50: unchanged
    assert FeeStatusCalculator.installments([e], once, as_of=date(2027, 1, 14))[0]["late_fee"] == 200
    assert FeeStatusCalculator.installments([e], once, as_of=date(2027, 2, 28))[0]["late_fee"] == 200
    assert FeeStatusCalculator.installments([e], once, as_of=date(2027, 1, 10))[0]["late_fee"] == 0


async def test_admission_fee_transport_moves_and_who_may_do_what(app_client, admin_user, db_session):
    c5, _c3 = await _setup_school(app_client)
    await app_client.put(F + "/plans", json={"plans": [{"program_id": c5["id"], "monthly_tuition": 5000, "admission_fee": 3000, "annual_charges": 0}]})
    new = await _kid(app_client, 4, guardian="35202-7400004-2", born="2015-01-01")
    sec_a, sec_b = c5["sections"][0]["id"], c5["sections"][1]["id"]
    r = await app_client.post(f"{S}/sections/{sec_a}/students", json={"student_ids": [new], "charge_admission": True})
    assert r.status_code == 200 and r.json()["admission_vouchers"] == 1
    again = await app_client.post(f"{S}/sections/{sec_b}/students", json={"student_ids": [new], "charge_admission": True})
    assert again.json()["admission_vouchers"] == 0                                    # once per student, ever
    [adm] = await _vouchers(db_session, new)
    assert adm.kind == SchoolVoucherKind.ADMISSION and float(adm.admission) == 3000
    ch = await _challan(db_session, adm)
    assert float(ch.amount) == 3000 and ch.due_date == date(2026, 12, 27)          # Settings: 7 days to pay

    # a month's voucher made after its due date (joined late): a few days to pay, no fine straight away
    dec = (await app_client.post(F + "/month", json={"month": "2026-12-01"})).json()
    assert dec["made"] == 1
    vdec = [v for v in await _vouchers(db_session, new) if v.kind == SchoolVoucherKind.MONTHLY][0]
    assert (await _challan(db_session, vdec)).due_date == date(2026, 12, 27)

    # transport: moving route once a month is billed only from the next month
    r1 = (await app_client.post(F + "/routes", json={"name": "Route A", "monthly_fee": 1500})).json()
    r2 = (await app_client.post(F + "/routes", json={"name": "Route B", "monthly_fee": 2500})).json()
    late = (await app_client.post(f"{F}/routes/{r1['id']}/students", json={"student_ids": [new], "from_month": "2026-12-01"})).json()
    assert late["added"] == 0 and "already made" in late["failed"][0]["reason"]
    assert (await app_client.post(f"{F}/routes/{r1['id']}/students", json={"student_ids": [new], "from_month": "2027-01-01"})).json()["added"] == 1
    await app_client.post(F + "/month", json={"month": "2027-01-01"})
    moved = (await app_client.post(f"{F}/routes/{r2['id']}/students", json={"student_ids": [new], "from_month": "2027-02-01"})).json()
    assert moved["added"] == 1
    ride_a = (await app_client.get(f"{F}/routes/{r1['id']}/students")).json()
    assert ride_a[0]["until_month"] == "2027-01-01"                                   # Route A ends with January
    await app_client.post(F + "/month", json={"month": "2027-02-01"})
    feb = [v for v in await _vouchers(db_session, new) if v.month == date(2027, 2, 1)][0]
    assert (feb.route_name, float(feb.transport)) == ("Route B", 2500)
    ride_b = (await app_client.get(f"{F}/routes/{r2['id']}/students")).json()[0]
    refused = await app_client.post(f"{F}/rides/{ride_b['id']}/end", json={"last_month": "2027-01-01"})
    assert refused.status_code == 422                                                 # February's voucher already has it
    assert (await app_client.post(f"{F}/rides/{ride_b['id']}/end", json={"last_month": "2027-02-01"})).status_code == 200

    # the general fee pages cannot undo or re-rate the school fee structure
    st = (await db_session.execute(select(FeeStructure).where(FeeStructure.late_fee_once.is_(True)))).scalars().first()
    undo = await app_client.post(f"/api/v1/fees/structures/{st.id}/undo-activation", json={"reason": "testing the guard"})
    assert undo.status_code == 422 and "Classes & sections" in undo.text
    assert (await app_client.post(f"/api/v1/fees/structures/{st.id}/late-fee", json={"late_fee_per_day": 50, "reason": "test"})).status_code == 422
    listed = next(s for s in (await app_client.get("/api/v1/fees/structures")).json() if s["id"] == str(st.id))
    assert listed["late_fee_once"] is True

    # the classes page has the Fees tab; a parent cannot see the school's fee set-up or other students on a route
    page = (await app_client.get("/dashboard/admin/classes", headers={"accept": "text/html"})).text
    assert 'data-tab="fees"' in page and "school-fees.js" in page
    made = (await app_client.post(f"/api/v1/parents/students/{new}")).json()
    await _parent_login(app_client, made["username"], made["temporary_password"])
    for path in ("/plans", "/routes", f"/routes/{r2['id']}/students", "/month"):
        assert (await app_client.get(F + path)).status_code == 403, path
    assert (await app_client.post(F + "/month", json={})).status_code == 403
