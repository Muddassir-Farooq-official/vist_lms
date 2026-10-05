"""Library: who may borrow, how many books, and the automatic late fee."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from app.core.clock import institute_today
from sqlalchemy import select, update

from app.core.config import settings
from app.library.models import BookBorrowRequest, BorrowerType, BorrowRequestStatus, BookIssue
from app.students.models import StudentStatus
from tests.integration.test_module3_sections_allocation import _admin, _program, _student, _teacher

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(settings, "library_max_loans_student", 3)
    monkeypatch.setattr(settings, "library_max_loans_employee", 5)
    monkeypatch.setattr(settings, "library_block_if_unpaid_fines", True)
    monkeypatch.setattr(settings, "library_block_if_overdue", True)
    monkeypatch.setattr(settings, "library_late_fee_per_day", 10.0)
    monkeypatch.setattr(settings, "library_late_fee_max", 0.0)


async def _book(client, n=8, isbn="9780000000010", title="Rules Book"):
    r = await client.post("/api/v1/library/books", json={"title": title, "author": "A", "isbn": isbn, "category": "T", "initial_copies": n})
    assert r.status_code == 201, r.text
    data = (await client.get(f"/api/v1/library/books/{r.json()['id']}/copies")).json()
    return r.json()["id"], (data if isinstance(data, list) else data.get("items", data))


async def _issue(client, copy, borrower_id, kind="STUDENT"):
    return await client.post("/api/v1/library/issues", json={"book_copy_id": copy["id"], "borrower_type": kind, "borrower_id": str(borrower_id)})


async def _backdate(db, issue_id, days):
    await db.execute(update(BookIssue).where(BookIssue.id == issue_id).values(due_date=institute_today() - timedelta(days=days)))
    await db.commit()


def _items(resp):
    data = resp.json()
    return data["items"] if isinstance(data, dict) else data


async def test_only_current_members_can_borrow(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    _b, copies = await _book(app_client)
    active = await _student(db_session, 1, prog)
    assert (await _issue(app_client, copies[0], active.id)).status_code == 201
    for n, status in ((2, StudentStatus.DROPPED), (3, StudentStatus.SUSPENDED), (4, StudentStatus.GRADUATED)):
        st = await _student(db_session, n, prog, status=status)
        r = await _issue(app_client, copies[n], st.id)
        assert r.status_code == 422 and status.value.title() in r.json()["error"]["message"], r.text
    tid = await _teacher(app_client, db_session, "LBT", "35202-9800001-1")
    assert (await _issue(app_client, copies[5], tid, "EMPLOYEE")).status_code == 201
    assert (await app_client.post(f"/api/v1/employees/{tid}/status", json={"new_status": "TERMINATED", "reason": "left"})).status_code == 200
    gone = await _issue(app_client, copies[6], tid, "EMPLOYEE")
    assert gone.status_code == 422 and "left the institute" in gone.text


async def test_loan_limit_is_enforced_and_visible_before_trying(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    _b, copies = await _book(app_client)
    st = await _student(db_session, 1, prog)
    issued = []
    for i in range(3):
        r = await _issue(app_client, copies[i], st.id)
        assert r.status_code == 201, r.text
        issued.append(r.json()["id"])
    over = await _issue(app_client, copies[3], st.id)
    assert over.status_code == 422 and "limit reached (3 books)" in over.text
    found = (await app_client.get("/api/v1/library/borrowers/search", params={"q": "S1"})).json()
    mine = next(b for b in found if b["borrower_id"] == str(st.id))
    assert (mine["can_borrow"], mine["active_loans"], mine["loan_limit"]) == (False, 3, 3) and "limit" in mine["blocked_reason"]
    assert (await app_client.post(f"/api/v1/library/issues/{issued[0]}/return", json={})).status_code == 200
    assert (await _issue(app_client, copies[3], st.id)).status_code == 201             # a returned book frees a place
    other = next(b for b in (await app_client.get("/api/v1/library/borrowers/search", params={"q": "S1"})).json() if b["borrower_id"] == str(st.id))
    assert other["active_loans"] == 3


async def test_overdue_books_and_unpaid_fines_block_borrowing(app_client, admin_user, db_session, monkeypatch):
    await _admin(app_client)
    prog = await _program(db_session)
    _b, copies = await _book(app_client)
    st = await _student(db_session, 1, prog)
    first = await _issue(app_client, copies[0], st.id)
    await _backdate(db_session, first.json()["id"], 3)
    blocked = await _issue(app_client, copies[1], st.id)
    assert blocked.status_code == 422 and "1 overdue book must be returned first" in blocked.text
    monkeypatch.setattr(settings, "library_block_if_overdue", False)                     # the rule can be switched off
    assert (await _issue(app_client, copies[1], st.id)).status_code == 201
    monkeypatch.setattr(settings, "library_block_if_overdue", True)

    other = await _student(db_session, 2, prog)
    fine = await app_client.post("/api/v1/fines", json={"payer_type": "STUDENT", "payer_id": str(other.id), "amount": 500, "reason": "test", "source": "OTHER"})
    assert fine.status_code == 201, fine.text
    no = await _issue(app_client, copies[2], other.id)
    assert no.status_code == 422 and "Rs. 500" in no.text
    paid = await app_client.post(f"/api/v1/fines/{fine.json()['id']}/mark-paid", json={})
    assert paid.status_code == 200, paid.text
    assert (await _issue(app_client, copies[2], other.id)).status_code == 201             # paying clears the block


async def test_borrow_requests_from_blocked_people_are_refused_early(app_client, admin_user, db_session):
    from app.core.exceptions import BusinessRuleViolationError
    from app.library.request_service import BookBorrowRequestService

    await _admin(app_client)
    prog = await _program(db_session)
    book_id, _c = await _book(app_client, n=1)
    dropped = await _student(db_session, 1, prog, status=StudentStatus.DROPPED)
    with pytest.raises(BusinessRuleViolationError, match="Dropped"):
        await BookBorrowRequestService(db_session).submit(
            book_id=book_id if not isinstance(book_id, str) else __import__("uuid").UUID(book_id),
            borrower_type=BorrowerType.STUDENT, borrower_id=dropped.id, requested_by=None, note=None)


async def test_waiting_list_skips_someone_who_is_blocked_and_keeps_their_place(app_client, admin_user, db_session):
    import uuid

    await _admin(app_client)
    prog = await _program(db_session)
    book_id, copies = await _book(app_client, n=1)
    holder, blocked, ok = (await _student(db_session, n, prog) for n in (1, 2, 3)) if False else [await _student(db_session, n, prog) for n in (1, 2, 3)]
    out = await _issue(app_client, copies[0], holder.id)
    now = datetime.now(timezone.utc)
    for offset, st in ((0, blocked), (1, ok)):                                           # blocked person queued FIRST
        db_session.add(BookBorrowRequest(book_id=uuid.UUID(book_id), borrower_type=BorrowerType.STUDENT, borrower_id=st.id,
                                         status=BorrowRequestStatus.WAITLISTED, created_at=now + timedelta(seconds=offset)))
    await db_session.commit()
    fine = await app_client.post("/api/v1/fines", json={"payer_type": "STUDENT", "payer_id": str(blocked.id), "amount": 100, "reason": "x", "source": "OTHER"})
    assert fine.status_code == 201
    assert (await app_client.post(f"/api/v1/library/issues/{out.json()['id']}/return", json={})).status_code == 200
    rows = {r.borrower_id: r for r in (await db_session.execute(select(BookBorrowRequest))).scalars()}
    await db_session.refresh(rows[blocked.id]); await db_session.refresh(rows[ok.id])
    assert rows[ok.id].status == BorrowRequestStatus.APPROVED                            # the copy went to the next eligible person
    assert rows[blocked.id].status == BorrowRequestStatus.WAITLISTED                      # the blocked person keeps their place


async def test_late_fee_is_worked_out_automatically(app_client, admin_user, db_session, monkeypatch):
    await _admin(app_client)
    prog = await _program(db_session)
    _b, copies = await _book(app_client)
    monkeypatch.setattr(settings, "library_block_if_overdue", False)

    async def borrow_late(n, days):
        st = await _student(db_session, n, prog)
        r = await _issue(app_client, copies[n], st.id)
        await _backdate(db_session, r.json()["id"], days)
        return st, r.json()["id"]

    async def fines_of(st):
        return [(f["amount"], f["source"]) for f in _items(await app_client.get("/api/v1/fines", params={"payer_type": "STUDENT", "payer_id": str(st.id)}))]

    st, iid = await borrow_late(1, 7)
    prev = (await app_client.get(f"/api/v1/library/issues/{iid}/return-preview")).json()
    assert (prev["days_overdue"], prev["suggested_fine"], prev["late_fee_per_day"]) == (7, 70.0, 10.0) and prev["book_title"]
    done = await app_client.post(f"/api/v1/library/issues/{iid}/return")                  # no body at all: standard fee
    assert done.status_code == 200, done.text
    assert await fines_of(st) == [(70.0, "LIBRARY")]

    st2, iid2 = await borrow_late(2, 7)
    assert (await app_client.post(f"/api/v1/library/issues/{iid2}/return", json={"fine_amount": 0})).status_code == 200
    assert await fines_of(st2) == []                                                       # 0 = librarian waived it
    st3, iid3 = await borrow_late(3, 7)
    assert (await app_client.post(f"/api/v1/library/issues/{iid3}/return", json={"fine_amount": 25})).status_code == 200
    assert await fines_of(st3) == [(25.0, "LIBRARY")]                                      # librarian's own amount wins

    monkeypatch.setattr(settings, "library_late_fee_max", 50.0)
    st4, iid4 = await borrow_late(4, 30)
    assert (await app_client.get(f"/api/v1/library/issues/{iid4}/return-preview")).json()["suggested_fine"] == 50.0    # capped
    assert (await app_client.post(f"/api/v1/library/issues/{iid4}/return", json={})).status_code == 200
    assert await fines_of(st4) == [(50.0, "LIBRARY")]

    on_time = await _student(db_session, 5, prog)
    r = await _issue(app_client, copies[5], on_time.id)
    assert (await app_client.get(f"/api/v1/library/issues/{r.json()['id']}/return-preview")).json()["suggested_fine"] == 0
    assert (await app_client.post(f"/api/v1/library/issues/{r.json()['id']}/return", json={})).status_code == 200
    assert await fines_of(on_time) == []                                                   # on time: nothing

    monkeypatch.setattr(settings, "library_late_fee_per_day", 0.0)                         # 0/day switches the automatic fee off
    st6, iid6 = await borrow_late(6, 9)
    assert (await app_client.post(f"/api/v1/library/issues/{iid6}/return", json={})).status_code == 200
    assert await fines_of(st6) == []


async def test_librarian_screen_shows_why_and_the_fee(app_client):
    js = (await app_client.get("/static/js/library-issue-return.js")).text
    for needle in ("cannot borrow", "blocked_reason", "return-preview", "Standard late fee", "can_borrow"):
        assert needle in js, needle
    assert ".chip-blocked" in (await app_client.get("/static/css/dashboard.css")).text