"""Expenses, vendors, other income, the day book and the income-versus-expense report."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.accounts.models import Expense
from app.core.clock import institute_today
from app.core.config import settings
from app.notifications.models import Notification, NotificationCategory
from tests.integration.test_fees import _create_accounts_officer, _create_active_fee_structure, _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
A = "/api/v1/accounts"
OFFICER_PW = "OfficerReal123!"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "expense_approval_limit", 50000.0)


async def _officer(client, db):
    return await _create_accounts_officer(client, db)


async def _as_officer(client, username):
    await _login(client, username, OFFICER_PW)


async def _cat(client, kind, name_part):
    rows = (await client.get(f"{A}/categories", params={"kind": kind})).json()
    return next(c for c in rows if name_part.lower() in c["name"].lower())


def _form(category_id, **kw):
    base = {"category_id": category_id, "title": "Generator diesel", "amount": "12000", "expense_date": str(institute_today()), "payment_method": "CASH"}
    base.update({k: str(v) for k, v in kw.items() if v is not None})
    return base


async def _spend(client, category_id, files=None, **kw):
    return await client.post(f"{A}/expenses", data=_form(category_id, **kw), files=files or None)


async def _income(client, cat_id, **kw):
    body = {"category_id": cat_id, "received_from": "Mr. Ahmed", "amount": 5000, "income_date": str(institute_today()), "payment_method": "CASH"}
    body.update(kw)
    return await client.post(f"{A}/income", json=body)


# ====================================================================== categories and vendors
async def test_the_standard_categories_exist_and_system_ones_are_protected(app_client, admin_user, db_session):
    await _login_admin(app_client)
    exp = (await app_client.get(f"{A}/categories", params={"kind": "EXPENSE"})).json()
    inc = (await app_client.get(f"{A}/categories", params={"kind": "INCOME"})).json()
    assert len(exp) == 14 and len(inc) == 6 and {c["name"] for c in exp if c["is_system"]} == {"Salaries and wages", "Purchases"}
    sal = next(c for c in exp if c["is_system"] and c["name"].startswith("Salaries"))
    assert (await app_client.patch(f"{A}/categories/{sal['id']}", json={"name": "Wages"})).status_code == 422
    assert (await app_client.patch(f"{A}/categories/{sal['id']}", json={"is_active": False})).status_code == 422
    new = await app_client.post(f"{A}/categories", json={"kind": "EXPENSE", "name": "Security services"})
    assert new.status_code == 201 and new.json()["is_system"] is False
    assert (await app_client.post(f"{A}/categories", json={"kind": "EXPENSE", "name": "security SERVICES"})).status_code == 422       # no duplicates, any capitals
    assert (await app_client.post(f"{A}/categories", json={"kind": "INCOME", "name": "Security services"})).status_code == 201         # the same name is fine for income
    off = (await app_client.patch(f"{A}/categories/{new.json()['id']}", json={"is_active": False})).json()
    assert off["is_active"] is False and "Security services" not in [c["name"] for c in (await app_client.get(f"{A}/categories", params={"kind": "EXPENSE", "active_only": True})).json()]
    officer = await _officer(app_client, db_session)
    assert (await app_client.post(f"{A}/categories", json={"kind": "EXPENSE", "name": "Not allowed"})).status_code == 403              # only the administration


async def test_vendors_are_managed_by_the_accounts_office_and_show_what_was_spent(app_client, admin_user, db_session):
    officer = await _officer(app_client, db_session)
    v = await app_client.post(f"{A}/vendors", json={"name": "Al-Noor Stationers", "phone": "0300-1234567", "contact_person": "Bilal"})
    assert v.status_code == 201 and v.json()["is_active"] is True
    assert (await app_client.post(f"{A}/vendors", json={"name": "al-noor stationers"})).status_code == 422                                # no duplicates
    cat = await _cat(app_client, "EXPENSE", "Stationery")
    assert (await _spend(app_client, cat["id"], vendor_id=v.json()["id"], amount="2500")).status_code == 201
    assert (await _spend(app_client, cat["id"], vendor_id=v.json()["id"], amount="1500")).status_code == 201
    listed = (await app_client.get(f"{A}/vendors", params={"q": "noor"})).json()
    assert len(listed) == 1 and listed[0]["total_spent"] == 4000.0
    edited = (await app_client.patch(f"{A}/vendors/{v.json()['id']}", json={"phone": "0311-7654321"})).json()
    assert edited["phone"] == "0311-7654321" and edited["total_spent"] == 4000.0
    await app_client.patch(f"{A}/vendors/{v.json()['id']}", json={"is_active": False})
    refused = await _spend(app_client, cat["id"], vendor_id=v.json()["id"])
    assert refused.status_code == 422 and "active vendor" in refused.text


# ====================================================================== expenses and the approval limit
async def test_a_small_expense_counts_immediately_and_a_large_one_waits_for_the_administration(app_client, admin_user, db_session):
    officer = await _officer(app_client, db_session)
    cat = await _cat(app_client, "EXPENSE", "Repairs")
    small = (await _spend(app_client, cat["id"], amount="49999.50", title="Roof repair")).json()
    assert small["status"] == "APPROVED" and small["reference"].startswith("EXP-") and small["recorded_by_name"] and small["decided_by_name"] is None
    big = (await _spend(app_client, cat["id"], amount="80000", title="New generator")).json()
    assert big["status"] == "PENDING_APPROVAL"
    s = (await app_client.get(f"{A}/summary")).json()
    assert s["expenses"] == 49999.5 and s["pending_expenses"] == {"count": 1, "amount": 80000.0}                                     # pending money is not counted
    note = (await db_session.execute(select(Notification).where(Notification.category == NotificationCategory.FINANCE, Notification.title.like("Expense waiting%")))).scalars().one()
    assert "80,000" in note.title and note.link == "/dashboard/admin/finance?open=expenses"
    assert (await app_client.post(f"{A}/expenses/{big['id']}/approve")).status_code == 403                                          # the officer cannot approve their own
    await _login_admin(app_client)
    assert (await app_client.post(f"{A}/expenses/{big['id']}/reject", json={"reason": "no"})).status_code == 422                       # a reason is needed
    ok = (await app_client.post(f"{A}/expenses/{big['id']}/approve")).json()
    assert ok["status"] == "APPROVED" and ok["decided_by_name"] and ok["decided_at"]
    assert (await app_client.get(f"{A}/summary")).json()["expenses"] == 129999.5
    assert (await app_client.post(f"{A}/expenses/{big['id']}/approve")).status_code == 422                                           # decided once
    told = (await db_session.execute(select(Notification).where(Notification.title.like("Expense approved%")))).scalars().one()
    assert told.message.startswith("New generator")
    # the administration's own expenses never wait for anyone
    assert (await _spend(app_client, cat["id"], amount="90000", title="Admin purchase")).json()["status"] == "APPROVED"


async def test_a_rejected_expense_stays_on_record_but_never_counts(app_client, admin_user, db_session):
    officer = await _officer(app_client, db_session)
    cat = await _cat(app_client, "EXPENSE", "Equipment")
    big = (await _spend(app_client, cat["id"], amount="75000", title="Projectors")).json()
    await _login_admin(app_client)
    rej = (await app_client.post(f"{A}/expenses/{big['id']}/reject", json={"reason": "Get three quotations first"})).json()
    assert rej["status"] == "REJECTED" and rej["decision_note"] == "Get three quotations first"
    assert (await app_client.get(f"{A}/summary")).json()["expenses"] == 0.0 and (await app_client.get(f"{A}/book")).json() == []
    assert [e["status"] for e in (await app_client.get(f"{A}/expenses", params={"status": "REJECTED"})).json()] == ["REJECTED"]
    assert (await app_client.post(f"{A}/expenses/{big['id']}/approve")).status_code == 422                                          # a rejected one is closed
    told = (await db_session.execute(select(Notification).where(Notification.title.like("Expense rejected%")))).scalars().one()
    assert "three quotations" in told.message


async def test_expense_rules(app_client, admin_user, db_session):
    officer = await _officer(app_client, db_session)
    cat = await _cat(app_client, "EXPENSE", "Utilities")
    income_cat = await _cat(app_client, "INCOME", "Donations")
    system = await _cat(app_client, "EXPENSE", "Salaries")
    today = institute_today()
    cases = [({"expense_date": today + timedelta(days=1)}, "future"), ({"expense_date": today - timedelta(days=400)}, "more than a year"), ({"amount": "0"}, "more than 0"), ({"amount": "-50"}, "more than 0"),
             ({"amount": "999999999"}, "at most"), ({"title": "   "}, "title"), ({"payment_method": "CHEQUE"}, "cheque number"), ({"payment_method": "BANK_TRANSFER"}, "reference")]
    for change, word in cases:
        r = await _spend(app_client, cat["id"], **change)
        assert r.status_code == 422 and word in r.text, (change, r.text)
    assert (await _spend(app_client, income_cat["id"])).status_code == 422                                                          # an income category cannot be spent on
    sys_r = await _spend(app_client, system["id"])
    assert sys_r.status_code == 422 and "filled in automatically" in sys_r.text
    assert (await _spend(app_client, cat["id"], payment_method="CHEQUE", reference_no="CHQ-004512")).status_code == 201
    await _login_admin(app_client)
    assert (await db_session.execute(select(func.count()).select_from(Expense))).scalar_one() == 1                                   # the failures created nothing


async def test_receipts_are_checked_stored_and_served(app_client, admin_user, db_session):
    officer = await _officer(app_client, db_session)
    cat = await _cat(app_client, "EXPENSE", "Stationery")
    ok = await _spend(app_client, cat["id"], files=[("files", ("receipt.png", PNG, "image/png")), ("files", ("bill.pdf", b"%PDF-1.4\n" + b"0" * 40, "application/pdf"))])
    assert ok.status_code == 201, ok.text
    atts = ok.json()["attachments"]
    assert [a["original_name"] for a in atts] == ["receipt.png", "bill.pdf"] and atts[0]["is_image"] is True and atts[0]["inline"] is True
    got = await app_client.get(f"{A}/expenses/attachments/{atts[0]['id']}/file")
    assert got.status_code == 200 and got.content == PNG and got.headers["x-content-type-options"] == "nosniff" and got.headers["content-disposition"].startswith("inline")
    for bad_files, word in (([("files", ("evil.exe", b"MZ" + b"0" * 40, "application/octet-stream"))], "evil.exe"), ([("files", ("fake.png", b"this is not a png at all", "image/png"))], "fake.png"),
                            ([("files", (f"r{i}.png", PNG, "image/png")) for i in range(6)], "at most 5")):
        r = await _spend(app_client, cat["id"], files=bad_files)
        assert r.status_code == 422 and word in r.text, (word, r.text)
    assert (await db_session.execute(select(func.count()).select_from(Expense))).scalar_one() == 1                                   # a bad file leaves nothing half-created
    await _login(app_client, (await _create_and_activate_student(app_client, cnic="55555-5555555-5"))[1], "StudentPass123!")
    assert (await app_client.get(f"{A}/expenses/attachments/{atts[0]['id']}/file")).status_code == 403                               # receipts are not for students


async def test_only_the_administration_voids_an_expense_and_it_stops_counting(app_client, admin_user, db_session):
    officer = await _officer(app_client, db_session)
    cat = await _cat(app_client, "EXPENSE", "Rent")
    e = (await _spend(app_client, cat["id"], amount="20000", title="October rent")).json()
    assert (await app_client.post(f"{A}/expenses/{e['id']}/void", json={"reason": "Entered twice"})).status_code == 403
    await _login_admin(app_client)
    assert (await app_client.post(f"{A}/expenses/{e['id']}/void", json={"reason": "x"})).status_code == 422
    v = (await app_client.post(f"{A}/expenses/{e['id']}/void", json={"reason": "Entered twice by mistake"})).json()
    assert v["status"] == "VOIDED" and v["void_reason"] == "Entered twice by mistake" and v["voided_at"]
    assert (await app_client.get(f"{A}/summary")).json()["expenses"] == 0.0 and (await app_client.get(f"{A}/book")).json() == []
    assert (await app_client.post(f"{A}/expenses/{e['id']}/void", json={"reason": "again please"})).status_code == 422                 # only an approved one can be voided
    assert (await app_client.get(f"{A}/expenses/{e['id']}")).json()["status"] == "VOIDED"                                              # still on record


# ====================================================================== other income
async def test_other_income_is_recorded_checked_and_voided(app_client, admin_user, db_session):
    officer = await _officer(app_client, db_session)
    cat = await _cat(app_client, "INCOME", "Donations")
    expense_cat = await _cat(app_client, "EXPENSE", "Rent")
    ok = await _income(app_client, cat["id"], amount=25000, received_from="Haji Bashir", description="Annual donation")
    assert ok.status_code == 201 and ok.json()["reference"].startswith("INC-") and ok.json()["category_name"].startswith("Donations")
    for change, word in (({"income_date": str(institute_today() + timedelta(days=2))}, "future"), ({"amount": 0}, None), ({"category_id": expense_cat["id"]}, "income category"),
                         ({"payment_method": "CHEQUE"}, "cheque number"), ({"received_from": "x"}, None)):
        r = await _income(app_client, cat["id"], **change)
        assert r.status_code == 422 and (word is None or word in r.text), (change, r.text)
    assert (await _income(app_client, cat["id"], payment_method="CHEQUE", reference_no="CHQ-77")).status_code == 201
    iid = ok.json()["id"]
    assert (await app_client.post(f"{A}/income/{iid}/void", json={"reason": "Entered twice"})).status_code == 403
    await _login_admin(app_client)
    v = (await app_client.post(f"{A}/income/{iid}/void", json={"reason": "Entered twice by mistake"})).json()
    assert v["voided_at"] and v["void_reason"].startswith("Entered twice")
    assert (await app_client.post(f"{A}/income/{iid}/void", json={"reason": "again please"})).status_code == 422
    assert len((await app_client.get(f"{A}/income")).json()) == 1 and len((await app_client.get(f"{A}/income", params={"include_voided": True})).json()) == 2
    assert (await app_client.get(f"{A}/summary")).json()["other_income"] == 5000.0                                                  # the voided Rs. 25,000 is not counted


# ====================================================================== the day book and the report
async def test_the_day_book_lists_what_counts_newest_first(app_client, admin_user, db_session):
    officer = await _officer(app_client, db_session)
    today = institute_today()
    inc, exp = await _cat(app_client, "INCOME", "Canteen"), await _cat(app_client, "EXPENSE", "Utilities")
    await _income(app_client, inc["id"], amount=3000, received_from="Canteen contractor", income_date=str(today - timedelta(days=3)))
    await _spend(app_client, exp["id"], amount="8000", title="Electricity bill", expense_date=str(today - timedelta(days=1)))
    await _spend(app_client, exp["id"], amount="70000", title="Pending big one")                                                   # waiting: must not appear
    book = (await app_client.get(f"{A}/book")).json()
    assert [(r["kind"], r["title"], r["amount"]) for r in book] == [("EXPENSE", "Electricity bill", 8000.0), ("INCOME", "Canteen contractor", 3000.0)]
    assert [r["kind"] for r in (await app_client.get(f"{A}/book", params={"kind": "INCOME"})).json()] == ["INCOME"]
    assert [r["title"] for r in (await app_client.get(f"{A}/book", params={"q": "electric"})).json()] == ["Electricity bill"]
    assert [r["title"] for r in (await app_client.get(f"{A}/book", params={"from_date": str(today - timedelta(days=2))})).json()] == ["Electricity bill"]


async def test_the_report_adds_up_fees_other_income_and_expenses(app_client, admin_user, db_session):
    student_id, student_user = await _create_and_activate_student(app_client, cnic="55555-5555556-6")
    officer = await _officer(app_client, db_session)
    await _create_active_fee_structure(app_client, total_amount=3000, installment_count=1)
    await _login(app_client, student_user, "StudentPass123!")
    challan = (await app_client.get("/api/v1/fees/accounts/me")).json()["assignments"][0]["installments"][0]["challan"]["id"]
    await _login_admin(app_client)
    assert (await app_client.post(f"/api/v1/fees/challans/{challan}/mark-paid", json={"method": "BANK_DEPOSIT", "reference": "HBL-0001"})).status_code == 200                         # Rs. 3,000 of fees collected
    await _as_officer(app_client, officer)
    vendor = (await app_client.post(f"{A}/vendors", json={"name": "Spark Electricals"})).json()
    don, can = await _cat(app_client, "INCOME", "Donations"), await _cat(app_client, "INCOME", "Canteen")
    util, rep = await _cat(app_client, "EXPENSE", "Utilities"), await _cat(app_client, "EXPENSE", "Repairs")
    await _income(app_client, don["id"], amount=1000)
    await _income(app_client, can["id"], amount=500)
    await _spend(app_client, util["id"], amount="800", vendor_id=vendor["id"])
    await _spend(app_client, rep["id"], amount="200")
    s = (await app_client.get(f"{A}/summary")).json()
    assert (s["fees_collected"], s["other_income"], s["total_income"], s["expenses"], s["net"]) == (3000.0, 1500.0, 4500.0, 1000.0, 3500.0)
    assert [(c["category"][:9], c["amount"]) for c in s["income_by_category"]] == [("Donations", 1000.0), ("Canteen a", 500.0)]
    assert [(c["category"][:9], c["amount"], c["count"]) for c in s["expenses_by_category"]] == [("Utilities", 800.0, 1), ("Repairs a", 200.0, 1)]
    assert s["top_vendors"] == [{"vendor": "Spark Electricals", "amount": 800.0, "count": 1}] and s["pending_expenses"] == {"count": 0, "amount": 0.0}
    assert len(s["monthly"]) == 12 and s["monthly"][-1]["month"] == str(institute_today())[:7]
    last = s["monthly"][-1]
    assert (last["fees"], last["other_income"], last["income"], last["expenses"]) == (3000.0, 1500.0, 4500.0, 1000.0) and sum(m["income"] for m in s["monthly"][:-1]) == 0
    # a period that excludes today shows nothing
    old = (await app_client.get(f"{A}/summary", params={"to_date": str(institute_today() - timedelta(days=60))})).json()
    assert (old["fees_collected"], old["other_income"], old["expenses"], old["net"]) == (0.0, 0.0, 0.0, 0.0)
    assert (await app_client.get(f"{A}/config")).json()["expense_approval_limit"] == 50000.0


async def test_only_the_accounts_office_and_administration_can_see_the_accounts(app_client, admin_user, db_session):
    await _login_admin(app_client)
    cat = await _cat(app_client, "EXPENSE", "Rent")
    student_id, student_user = await _create_and_activate_student(app_client, cnic="55555-5555557-7")
    await _login(app_client, student_user, "StudentPass123!")
    for path in ("config", "categories", "vendors", "expenses", "income", "book", "summary"):
        assert (await app_client.get(f"{A}/{path}")).status_code == 403, path
    assert (await _spend(app_client, cat["id"])).status_code == 403 and (await _income(app_client, cat["id"])).status_code == 403
    app_client.cookies.clear()
    assert (await app_client.get(f"{A}/summary")).status_code == 401