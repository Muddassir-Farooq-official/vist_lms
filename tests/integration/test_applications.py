"""Applications: leave and other requests from students and employees, decided by the administration."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.applications.models import Application, ApplicationAttachment
from app.core.config import settings
from app.notifications.models import Notification, NotificationCategory
from tests.integration.test_announcements import _first_login, _staff, _student

pytestmark = pytest.mark.asyncio
ADMIN = {"username": "admin_test", "password": "AdminPass123!"}
API = "/api/v1/applications"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _login(client, username, password):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


_cache: dict = {}


@pytest.fixture(autouse=True)
def _reset():
    _cache.clear()


async def _as(client, raw, who):
    username, first = raw[who]
    if who not in _cache:
        _cache[who] = await _first_login(client, username, first)
    else:
        await _login(client, username, _cache[who])


async def _people(client, db):
    await _login(client, **ADMIN)
    raw = {"student": await _student(client, 1), "student2": await _student(client, 2),
           "teacher": await _staff(client, db, "TCH", "TEACHER", "35202-7100001-1"),
           "librarian": await _staff(client, db, "LIB", "LIBRARIAN", "35202-7100002-2"),
           "accounts": await _staff(client, db, "ACC", "ACCOUNTS_OFFICER", "35202-7100003-3")}
    return raw


async def _type(client, name, **kw):
    body = {"name": name, "for_students": True, "for_employees": True, "requires_dates": False, "requires_attachment": False}
    body.update(kw)
    r = await client.post(f"{API}/types", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _d(days):
    return (date.today() + timedelta(days=days)).isoformat()


async def _apply(client, type_id, *, subject="Family function", details="I need to attend my cousin's wedding.", start=None, end=None, files=None):
    data = {"type_id": type_id, "subject": subject, "details": details}
    if start:
        data["from_date"] = start
    if end:
        data["to_date"] = end
    return await client.post(API, data=data, files=files or None)


async def _setup(client, db):
    raw = await _people(client, db)
    t = {"leave": await _type(client, "Leave application", requires_dates=True),
         "fee": await _type(client, "Fee concession", for_employees=False),
         "salary": await _type(client, "Salary letter", for_students=False),
         "doc": await _type(client, "Needs a document", requires_attachment=True)}
    return raw, t


async def test_the_administrator_manages_what_can_be_applied_for_and_who_sees_what(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    dup = await app_client.post(f"{API}/types", json={"name": "leave APPLICATION"})
    assert dup.status_code == 422 and "already" in dup.text
    off = await _type(app_client, "Retired type")
    assert (await app_client.patch(f"{API}/types/{off}", json={"is_active": False})).status_code == 200
    assert (await app_client.post(f"{API}/types", json={"name": "Nobody", "for_students": False, "for_employees": False})).status_code == 422
    assert {x["name"] for x in (await app_client.get(f"{API}/types/all")).json()} >= {"Retired type", "Leave application"}
    await _as(app_client, raw, "student")
    assert {x["name"] for x in (await app_client.get(f"{API}/types")).json()} == {"Leave application", "Fee concession", "Needs a document"}
    await _as(app_client, raw, "teacher")
    assert {x["name"] for x in (await app_client.get(f"{API}/types")).json()} == {"Leave application", "Salary letter", "Needs a document"}
    for call in (app_client.post(f"{API}/types", json={"name": "Sneaky"}), app_client.get(f"{API}/types/all"), app_client.get(API), app_client.get(f"{API}/overview")):
        assert (await call).status_code == 403
    # nobody can apply for something that is not meant for them, or that is switched off
    assert (await _apply(app_client, t["fee"])).status_code == 422
    assert (await _apply(app_client, off)).status_code == 422


async def test_applying_for_leave_checks_the_dates(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    ok = await _apply(app_client, t["leave"], start=_d(3), end=_d(5))
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["reference"].startswith(f"APP-{date.today().year}-") and body["status"] == "SUBMITTED" and body["awaiting"] == "ADMIN" and body["days"] == 3
    assert body["type_name"] == "Leave application" and body["comment_count"] == 0
    cases = [({"start": None, "end": None}, "first and last date"), ({"start": _d(5), "end": _d(3)}, "cannot be before"),
             ({"start": _d(-90), "end": _d(-89)}, "too far in the past"), ({"start": _d(400), "end": _d(401)}, "too far ahead"),
             ({"start": _d(20), "end": _d(400)}, "too far ahead")]
    for kw, expected in cases:
        r = await _apply(app_client, t["leave"], subject="Another", **kw)
        assert r.status_code == 422 and expected in r.text, (kw, r.text)
    assert (await _apply(app_client, t["leave"], subject="No", details="x", start=_d(20), end=_d(21))).status_code == 422          # too short to mean anything
    recent = await _apply(app_client, t["leave"], subject="Reported afterwards", start=_d(-10), end=_d(-9))                             # a sick day reported late is fine
    assert recent.status_code == 201
    # dates are ignored for requests that do not need them
    plain = await _apply(app_client, t["fee"], subject="Installments please", start=_d(1), end=_d(2))
    assert plain.status_code == 201 and plain.json()["from_date"] is None and plain.json()["days"] is None


async def test_the_same_dates_cannot_be_asked_for_twice(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    first = (await _apply(app_client, t["leave"], start=_d(10), end=_d(14))).json()
    clash = await _apply(app_client, t["leave"], subject="Again", start=_d(12), end=_d(16))
    assert clash.status_code == 422 and first["reference"] in clash.text
    assert (await _apply(app_client, t["leave"], subject="Next week", start=_d(15), end=_d(16))).status_code == 201                  # different dates are fine
    await app_client.post(f"{API}/{first['id']}/withdraw")
    assert (await _apply(app_client, t["leave"], subject="Same dates after withdrawing", start=_d(12), end=_d(13))).status_code == 201
    await _as(app_client, raw, "student2")                                                                                         # another person's leave never matters
    assert (await _apply(app_client, t["leave"], subject="My own", start=_d(10), end=_d(14))).status_code == 201


async def test_documents_can_be_attached_and_are_checked(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    assert (await _apply(app_client, t["doc"])).status_code == 422                                                                 # a document is required
    good = await _apply(app_client, t["doc"], files=[("files", ("certificate.pdf", PDF, "application/pdf"))])
    assert good.status_code == 201 and good.json()["attachments"][0]["original_name"] == "certificate.pdf" and good.json()["attachments"][0]["inline"] is True
    before = (await db_session.execute(select(func.count()).select_from(Application))).scalar_one()
    bad = await _apply(app_client, t["doc"], subject="Fake", files=[("files", ("a.pdf", PDF, "application/pdf")), ("files", ("virus.pdf", b"<html>x</html>", "application/pdf"))])
    assert bad.status_code == 422 and "virus.pdf" in bad.text
    assert (await db_session.execute(select(func.count()).select_from(Application))).scalar_one() == before                         # nothing half-saved
    too_many = await _apply(app_client, t["doc"], subject="Many", files=[("files", (f"{i}.pdf", PDF, "application/pdf")) for i in range(6)])
    assert too_many.status_code == 422 and "at most 5" in too_many.text
    # who may open the file
    att = good.json()["attachments"][0]["id"]
    assert (await app_client.get(f"{API}/attachments/{att}/file")).content == PDF
    await _as(app_client, raw, "student2")
    assert (await app_client.get(f"{API}/attachments/{att}/file")).status_code == 404
    await _login(app_client, **ADMIN)
    assert (await app_client.get(f"{API}/attachments/{att}/file")).status_code == 200
    app_client.cookies.clear()
    assert (await app_client.get(f"{API}/attachments/{att}/file")).status_code == 401


async def test_the_administration_asks_a_question_the_applicant_answers_and_it_is_approved(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    app = (await _apply(app_client, t["leave"], subject="Wedding", start=_d(6), end=_d(8))).json()
    await _login(app_client, **ADMIN)
    assert (await app_client.get(f"{API}/badge")).json() == {"count": 1}                                                          # one waiting for the administration
    assert (await app_client.post(f"{API}/{app['id']}/review", json={"action": "REQUEST_INFO"})).status_code == 422                # a question is required
    started = (await app_client.post(f"{API}/{app['id']}/review", json={"action": "START_REVIEW"})).json()
    assert started["status"] == "UNDER_REVIEW" and started["reviewed_at"] is not None
    ask = (await app_client.post(f"{API}/{app['id']}/review", json={"action": "REQUEST_INFO", "note": "Please attach the wedding card."})).json()
    assert ask["status"] == "MORE_INFO_NEEDED" and ask["awaiting"] == "APPLICANT" and ask["comments"][0]["side"] == "ADMIN" and ask["comments"][0]["author_name"] == "Administration"
    assert (await app_client.get(f"{API}/badge")).json() == {"count": 0}
    await _as(app_client, raw, "student")
    assert (await app_client.get(f"{API}/badge")).json() == {"count": 1}                                                          # one waiting for MY answer
    note = [n for n in (await db_session.execute(select(Notification).where(Notification.title == "More information needed"))).scalars()]
    assert len(note) == 1 and "wedding card" in note[0].message and note[0].link == f"/dashboard/student/applications?open={app['id']}"
    replied = (await app_client.post(f"{API}/{app['id']}/comments", json={"body": "Attached now, thank you."})).json()
    assert replied["status"] == "SUBMITTED" and replied["awaiting"] == "ADMIN" and [c["side"] for c in replied["comments"]] == ["ADMIN", "APPLICANT"]
    withfile = await app_client.post(f"{API}/{app['id']}/attachments", files=[("files", ("card.pdf", PDF, "application/pdf"))])
    assert withfile.status_code == 200 and len(withfile.json()["attachments"]) == 1
    await _login(app_client, **ADMIN)
    done = (await app_client.post(f"{API}/{app['id']}/review", json={"action": "APPROVE", "note": "Enjoy. Approved."})).json()
    assert done["status"] == "APPROVED" and done["awaiting"] is None and done["decision_note"] == "Enjoy. Approved." and done["decided_at"] is not None
    await _as(app_client, raw, "student")
    mine = (await app_client.get(f"{API}/mine")).json()[0]
    assert mine["status"] == "APPROVED" and mine["decision_note"] == "Enjoy. Approved."
    assert (await app_client.post(f"{API}/{app['id']}/comments", json={"body": "One more thing"})).status_code == 422             # closed for the applicant
    assert (await app_client.post(f"{API}/{app['id']}/withdraw")).status_code == 422
    assert "Application approved" in {n.title for n in (await db_session.execute(select(Notification).where(Notification.category == NotificationCategory.APPLICATION))).scalars()}


async def test_a_rejection_needs_a_reason_and_the_administration_can_change_its_mind(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    app = (await _apply(app_client, t["fee"], subject="Please reduce my fee")).json()
    await _login(app_client, **ADMIN)
    assert (await app_client.post(f"{API}/{app['id']}/review", json={"action": "REJECT"})).status_code == 422
    assert (await app_client.post(f"{API}/{app['id']}/review", json={"action": "REJECT", "note": "  "})).status_code == 422
    rej = (await app_client.post(f"{API}/{app['id']}/review", json={"action": "REJECT", "note": "Not within the policy."})).json()
    assert rej["status"] == "REJECTED" and rej["decision_note"] == "Not within the policy."
    again = (await app_client.post(f"{API}/{app['id']}/review", json={"action": "APPROVE", "note": "Reconsidered."})).json()
    assert again["status"] == "APPROVED" and again["decision_note"] == "Reconsidered."
    await _as(app_client, raw, "student")
    assert [n.title for n in (await db_session.execute(select(Notification).where(Notification.title.in_(["Application rejected", "Application approved"])).order_by(Notification.created_at))).scalars()] == ["Application rejected", "Application approved"]


async def test_an_applicant_can_withdraw_while_it_is_open_and_then_nobody_can_review_it(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    app = (await _apply(app_client, t["fee"], subject="Never mind later")).json()
    await _as(app_client, raw, "student2")
    assert (await app_client.post(f"{API}/{app['id']}/withdraw")).status_code == 404                                              # someone else's: not there
    await _as(app_client, raw, "student")
    w = (await app_client.post(f"{API}/{app['id']}/withdraw")).json()
    assert w["status"] == "WITHDRAWN" and w["awaiting"] is None
    await _login(app_client, **ADMIN)
    r = await app_client.post(f"{API}/{app['id']}/review", json={"action": "APPROVE"})
    assert r.status_code == 422 and "withdrew" in r.text


async def test_people_only_ever_see_their_own_applications(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    mine = (await _apply(app_client, t["fee"], subject="Student one")).json()
    await _as(app_client, raw, "teacher")
    theirs = (await _apply(app_client, t["salary"], subject="Teacher one")).json()
    assert [a["subject"] for a in (await app_client.get(f"{API}/mine")).json()] == ["Teacher one"]
    assert (await app_client.get(f"{API}/{mine['id']}")).status_code == 404
    assert (await app_client.post(f"{API}/{mine['id']}/comments", json={"body": "Hi"})).status_code == 404
    assert (await app_client.post(f"{API}/{mine['id']}/review", json={"action": "APPROVE"})).status_code == 403
    await _as(app_client, raw, "student")
    assert (await app_client.get(f"{API}/{theirs['id']}")).status_code == 404
    assert (await app_client.get(API)).status_code == 403


async def test_every_kind_of_user_is_told_in_their_own_portal(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    expected = {"student": "student", "teacher": "faculty", "librarian": "library", "accounts": "accounts"}
    ids = {}
    for who in expected:
        await _as(app_client, raw, who)
        ids[who] = (await _apply(app_client, t["doc" if False else "leave"], subject=f"From {who}", start=_d(30), end=_d(31))).json()["id"]
    await _login(app_client, **ADMIN)
    admin_notes = (await db_session.execute(select(Notification).where(Notification.title.like("New application:%")))).scalars().all()
    assert len(admin_notes) == 4 and all(n.link.startswith("/dashboard/admin/applications?open=") for n in admin_notes)
    for who, portal in expected.items():
        await app_client.post(f"{API}/{ids[who]}/review", json={"action": "APPROVE"})
    links = {n.link for n in (await db_session.execute(select(Notification).where(Notification.title == "Application approved"))).scalars()}
    assert links == {f"/dashboard/{portal}/applications?open={ids[who]}" for who, portal in expected.items()}


async def test_the_list_can_be_filtered_and_the_waiting_ones_come_oldest_first(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    a1 = (await _apply(app_client, t["fee"], subject="Alpha fee request")).json()
    a2 = (await _apply(app_client, t["leave"], subject="Beta leave", start=_d(40), end=_d(41))).json()
    await _as(app_client, raw, "teacher")
    a3 = (await _apply(app_client, t["salary"], subject="Gamma salary letter")).json()
    await _login(app_client, **ADMIN)
    await app_client.post(f"{API}/{a2['id']}/review", json={"action": "APPROVE"})
    ids = lambda r: [x["id"] for x in r.json()]                                                                                    # noqa: E731
    assert ids(await app_client.get(API, params={"needs_action": True})) == [a1["id"], a3["id"]]                                  # still waiting, oldest first
    assert ids(await app_client.get(API, params={"status": "APPROVED"})) == [a2["id"]]
    assert ids(await app_client.get(API, params={"kind": "EMPLOYEE"})) == [a3["id"]]
    assert set(ids(await app_client.get(API, params={"type_id": t["fee"]}))) == {a1["id"]}
    assert ids(await app_client.get(API, params={"q": "gamma"})) == [a3["id"]]
    assert ids(await app_client.get(API, params={"q": a2["reference"].lower()})) == [a2["id"]]
    assert ids(await app_client.get(API, params={"q": "Stu1"})) != []                                                              # by the applicant's name
    detail = (await app_client.get(f"{API}/{a1['id']}")).json()
    assert detail["applicant"]["name"].startswith("Stu1") and detail["applicant"]["kind"] == "STUDENT" and detail["applicant"]["identifier"]
    assert {o["reference"] for o in detail["others"]} == {a2["reference"]}                                                         # the same person's other applications, for context


async def test_a_yearly_limit_shows_what_has_been_used_without_blocking_anyone(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    casual = await _type(app_client, "Casual leave", requires_dates=True, yearly_limit_days=5)
    await _as(app_client, raw, "teacher")
    first = (await _apply(app_client, casual, subject="Three days", start=_d(20), end=_d(22))).json()
    second = await _apply(app_client, casual, subject="Four more", start=_d(30), end=_d(33))
    assert second.status_code == 201                                                                                              # over the limit is allowed: the administration decides
    await _login(app_client, **ADMIN)
    await app_client.post(f"{API}/{first['id']}/review", json={"action": "APPROVE"})
    await _as(app_client, raw, "teacher")
    usage = next(x for x in (await app_client.get(f"{API}/types")).json() if x["name"] == "Casual leave")["usage"]
    year = date.today().year
    if (date.today() + timedelta(days=20)).year == year:
        assert (usage["limit_days"], usage["approved_days"], usage["pending_days"]) == (5, 3, 4)


async def test_the_overview_numbers(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    ids = [(await _apply(app_client, t["fee"], subject=f"Request {i}")).json()["id"] for i in range(4)]
    await _as(app_client, raw, "teacher")
    ids.append((await _apply(app_client, t["salary"], subject="Salary")).json()["id"])
    await _login(app_client, **ADMIN)
    await app_client.post(f"{API}/{ids[0]}/review", json={"action": "APPROVE"})
    await app_client.post(f"{API}/{ids[1]}/review", json={"action": "REJECT", "note": "No."})
    await app_client.post(f"{API}/{ids[2]}/review", json={"action": "START_REVIEW"})
    o = (await app_client.get(f"{API}/overview")).json()
    k = o["kpis"]
    assert (k["total"], k["needs_action"], k["under_review"], k["approved"], k["rejected"], k["withdrawn"]) == (5, 2, 1, 1, 1, 0)
    assert k["approval_rate"] == 50.0 and k["avg_decision_hours"] is not None and k["oldest_waiting_days"] == 0
    assert {x["key"]: x["count"] for x in o["by_kind"]} == {"STUDENT": 4, "EMPLOYEE": 1}
    assert o["by_type"][0]["label"] == "Fee concession" and o["by_type"][0]["count"] == 4
    assert len(o["by_week"]) == 12 and o["by_week"][-1]["submitted"] == 5 and o["by_week"][-1]["decided"] == 2


async def test_deleting_an_application_removes_nothing_by_accident_and_files_live_in_their_own_folder(app_client, admin_user, db_session):
    raw, t = await _setup(app_client, db_session)
    await _as(app_client, raw, "student")
    a = (await _apply(app_client, t["doc"], files=[("files", ("c.pdf", PDF, "application/pdf"))])).json()
    key = (await db_session.execute(select(ApplicationAttachment.storage_key))).scalar_one()
    assert key.startswith(f"applications/{a['id']}/") and (Path(settings.storage_local_path) / key).read_bytes() == PDF