"""Tasks between staff: the 3-hour minimum, accept / reject with a reason, flags for the administration, and progress tracking."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.notifications.models import Notification, NotificationCategory
from app.tasks.models import Task
from tests.integration.test_announcements import _first_login, _staff, _student

pytestmark = pytest.mark.asyncio
ADMIN = {"username": "admin_test", "password": "AdminPass123!"}
API = "/api/v1/tasks"
_cache: dict = {}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    _cache.clear()


async def _login(client, username, password):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


async def _as(client, raw, who):
    username, first = raw[who]
    if who not in _cache:
        _cache[who] = await _first_login(client, username, first)
    else:
        await _login(client, username, _cache[who])


async def _team(client, db):
    await _login(client, **ADMIN)
    return {"anna": await _staff(client, db, "TCA", "TEACHER", "35202-7200001-1"), "bilal": await _staff(client, db, "TCB", "TEACHER", "35202-7200002-2"),
            "carla": await _staff(client, db, "LIB", "LIBRARIAN", "35202-7200003-3"), "dev": await _staff(client, db, "ACC", "ACCOUNTS_OFFICER", "35202-7200004-4"),
            "student": await _student(client, 1)}


async def _ids(client, raw, db):
    """user id for each person (read straight from the database)."""
    from app.auth.models import User

    out = {}
    for who, (username, _) in raw.items():
        out[who] = str((await db.execute(select(User.id).where(User.username == username))).scalar_one())
    out["admin"] = str((await db.execute(select(User.id).where(User.username == ADMIN["username"]))).scalar_one())
    return out


def _in(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def _assign(client, to, *, title="Prepare the exam hall", description="Arrange 60 desks and check the lights.", hours=24, **kw):
    body = {"assignee_user_id": to, "title": title, "description": description, "deadline": _in(hours)}
    body.update(kw)
    return await client.post(API, json=body)


async def _task(client, to, **kw):
    r = await _assign(client, to, **kw)
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_deadline_less_than_three_hours_away_is_refused(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    for hours in (-5, 0, 1, 2.9):
        r = await _assign(app_client, ids["bilal"], hours=hours)
        assert r.status_code == 422 and "at least 3 hours" in r.json()["error"]["message"], hours
    assert (await _assign(app_client, ids["bilal"], hours=3.05)).status_code == 201
    far = await _assign(app_client, ids["bilal"], hours=24 * 400)
    assert far.status_code == 422 and "more than a year" in far.text


async def test_who_can_assign_to_whom(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    assert (await _assign(app_client, ids["carla"])).status_code == 201                                   # teacher -> librarian
    assert (await _assign(app_client, ids["admin"], title="For the administrator")).status_code == 201      # staff can give the administrator a task too
    me = await _assign(app_client, ids["anna"])
    assert me.status_code == 422 and "yourself" in me.text
    stu = await _assign(app_client, ids["student"])
    assert stu.status_code == 422 and "not a member of staff" in stu.text
    names = {p["name"] for p in (await app_client.get(f"{API}/staff")).json()}
    assert not any(n.startswith("Stu") for n in names) and "Administrator" in names and "TCB X" in names and "TCA X" not in names       # no students, not me
    await _login(app_client, **ADMIN)
    assert (await _assign(app_client, ids["anna"], title="From the administrator")).status_code == 201     # and the administrator can assign
    await _as(app_client, raw, "student")
    for call in (_assign(app_client, ids["anna"]), app_client.get(f"{API}/staff"), app_client.get(f"{API}/inbox"), app_client.get(API)):
        assert (await call).status_code == 403
    app_client.cookies.clear()
    assert (await app_client.get(f"{API}/inbox")).status_code == 401


async def test_accepting_and_completing_a_task(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    t = await _task(app_client, ids["bilal"], priority="HIGH")
    assert t["status"] == "PENDING" and t["reference"].startswith("TASK-") and t["can"]["edit"] == "ALL" and t["can"]["accept"] is False and t["my_role"] == "ASSIGNER"
    await _as(app_client, raw, "bilal")
    inbox = (await app_client.get(f"{API}/inbox")).json()
    assert [x["title"] for x in inbox] == ["Prepare the exam hall"] and inbox[0]["can"]["accept"] is True and inbox[0]["can"]["reject"] is True and inbox[0]["my_role"] == "ASSIGNEE"
    early = await app_client.post(f"{API}/{t['id']}/complete", json={})
    assert early.status_code == 422 and "Accept the task first" in early.text
    acc = (await app_client.post(f"{API}/{t['id']}/respond", json={"action": "ACCEPT"})).json()
    assert acc["status"] == "ACCEPTED" and acc["accepted_at"] and acc["responded_at"] and acc["can"]["complete"] is True and acc["can"]["accept"] is False
    done = (await app_client.post(f"{API}/{t['id']}/complete", json={"note": "Done, 60 desks in place."})).json()
    assert done["status"] == "COMPLETED" and done["timeliness"] == "ON_TIME" and done["late_by_hours"] is None and done["completion_note"].startswith("Done")
    await _as(app_client, raw, "anna")
    titles = [n.title for n in (await db_session.execute(select(Notification).where(Notification.category == NotificationCategory.TASK).order_by(Notification.created_at))).scalars()]
    assert titles == ["New task from TCA X", "Task accepted", "Task completed"]


async def test_a_rejection_needs_a_reason_and_a_real_explanation(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    t = await _task(app_client, ids["bilal"], hours=5)
    assert (await app_client.post(f"{API}/{t['id']}/respond", json={"action": "REJECT", "reason": "NO_CAPACITY", "explanation": "x" * 20})).status_code == 403       # only the receiver answers
    await _as(app_client, raw, "bilal")
    for body in ({"action": "REJECT"}, {"action": "REJECT", "reason": "NO_CAPACITY"}, {"action": "REJECT", "explanation": "I am far too busy this week."},
                 {"action": "REJECT", "reason": "NO_CAPACITY", "explanation": "too busy"}, {"action": "REJECT", "reason": "MADE_UP", "explanation": "I am far too busy this week."}):
        assert (await app_client.post(f"{API}/{t['id']}/respond", json=body)).status_code == 422, body
    rej = (await app_client.post(f"{API}/{t['id']}/respond", json={"action": "REJECT", "reason": "DEADLINE_TOO_SHORT", "explanation": "Five hours is not enough to arrange sixty desks."})).json()
    assert rej["status"] == "REJECTED" and rej["reject_reason"] == "DEADLINE_TOO_SHORT" and rej["reject_reason_label"] == "The deadline is too short" and "sixty desks" in rej["reject_explanation"]
    assert (await app_client.post(f"{API}/{t['id']}/respond", json={"action": "ACCEPT"})).status_code == 422                                                         # answered once
    note = (await db_session.execute(select(Notification).where(Notification.title == "Task rejected"))).scalars().one()
    assert "The deadline is too short" in note.message and "sixty desks" in note.message


async def test_late_and_overdue_work_is_recognised(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    late, ignored = await _task(app_client, ids["bilal"], title="Finishes late"), await _task(app_client, ids["bilal"], title="Never answered")
    await _as(app_client, raw, "bilal")
    await app_client.post(f"{API}/{late['id']}/respond", json={"action": "ACCEPT"})
    # time passes: both deadlines are now 5 and 2 hours ago
    await db_session.execute(update(Task).where(Task.id == late["id"]).values(deadline=datetime.now(timezone.utc) - timedelta(hours=5)))
    await db_session.execute(update(Task).where(Task.id == ignored["id"]).values(deadline=datetime.now(timezone.utc) - timedelta(hours=2)))
    await db_session.commit()
    rows = {r["title"]: r for r in (await app_client.get(f"{API}/inbox")).json()}
    assert rows["Finishes late"]["is_overdue"] is True and rows["Never answered"]["is_overdue"] is True
    s = (await app_client.get(f"{API}/summary")).json()
    assert (s["needs_response"], s["overdue"], s["open"]) == (1, 2, 1)
    done = (await app_client.post(f"{API}/{late['id']}/complete", json={})).json()
    assert done["timeliness"] == "LATE" and 4.9 < done["late_by_hours"] < 5.2 and done["is_overdue"] is False


async def test_editing_a_task(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    t = await _task(app_client, ids["bilal"], hours=10)
    sooner = await app_client.patch(f"{API}/{t['id']}", json={"deadline": _in(2)})
    assert sooner.status_code == 422 and "at least 3 hours" in sooner.text                                                  # the same rule when it is changed
    edit = (await app_client.patch(f"{API}/{t['id']}", json={"title": "Prepare Hall B", "priority": "URGENT", "deadline": _in(30)})).json()
    assert edit["title"] == "Prepare Hall B" and edit["priority"] == "URGENT" and edit["extension_count"] == 1 and edit["original_deadline"] != edit["deadline"]
    await _as(app_client, raw, "bilal")
    assert (await app_client.patch(f"{API}/{t['id']}", json={"title": "Mine now"})).status_code == 403
    await app_client.post(f"{API}/{t['id']}/respond", json={"action": "ACCEPT"})
    await _as(app_client, raw, "anna")
    assert (await app_client.patch(f"{API}/{t['id']}", json={"title": "Changed after accepting"})).status_code == 422             # only the deadline, once accepted
    ext = (await app_client.patch(f"{API}/{t['id']}", json={"deadline": _in(60)})).json()
    assert ext["extension_count"] == 2 and ext["can"]["edit"] == "DEADLINE"
    await _as(app_client, raw, "bilal")
    await app_client.post(f"{API}/{t['id']}/complete", json={})
    await _as(app_client, raw, "anna")
    assert (await app_client.patch(f"{API}/{t['id']}", json={"deadline": _in(90)})).status_code == 422                               # finished


async def test_cancelling(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    t, u = await _task(app_client, ids["bilal"]), await _task(app_client, ids["bilal"], title="Second")
    await _as(app_client, raw, "bilal")
    assert (await app_client.post(f"{API}/{t['id']}/cancel", json={"reason": "I do not want this"})).status_code == 403                 # the receiver cannot cancel it
    await _as(app_client, raw, "anna")
    assert (await app_client.post(f"{API}/{t['id']}/cancel", json={})).status_code == 422                                                # a reason is needed
    c = (await app_client.post(f"{API}/{t['id']}/cancel", json={"reason": "No longer needed"})).json()
    assert c["status"] == "CANCELLED" and c["cancel_reason"] == "No longer needed" and c["can"]["cancel"] is False
    assert (await app_client.post(f"{API}/{t['id']}/cancel", json={"reason": "again"})).status_code == 422
    await _login(app_client, **ADMIN)
    assert (await app_client.post(f"{API}/{u['id']}/cancel", json={"reason": "Administration decision"})).json()["status"] == "CANCELLED"       # the administration can cancel any


async def test_flagging_a_task_sends_it_to_the_administration(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    t = await _task(app_client, ids["bilal"], hours=48)
    await _as(app_client, raw, "bilal")
    await app_client.post(f"{API}/{t['id']}/respond", json={"action": "ACCEPT"})
    assert (await app_client.post(f"{API}/{t['id']}/flag", json={"reason": "too short"})).status_code == 422
    flagged = (await app_client.post(f"{API}/{t['id']}/flag", json={"reason": "This was agreed for Hall A but the details now say Hall B and C as well."})).json()
    assert flagged["is_flagged"] is True and flagged["flags"][0]["status"] == "OPEN" and flagged["can"]["flag"] is False
    assert (await app_client.post(f"{API}/{t['id']}/flag", json={"reason": "Flagging it a second time"})).status_code == 422
    admin_notes = (await db_session.execute(select(Notification).where(Notification.title.like("Task flagged:%")))).scalars().all()
    other_note = (await db_session.execute(select(Notification).where(Notification.title.like("%was flagged for review")))).scalars().all()
    assert len(admin_notes) >= 1 and len(other_note) == 1                                                                             # the administration and the other person
    await _as(app_client, raw, "carla")
    assert (await app_client.post(f"{API}/{t['id']}/flag", json={"reason": "I am not involved at all, really."})).status_code == 403
    assert (await app_client.get(f"{API}/{t['id']}")).status_code == 404
    # the administration decides
    await _login(app_client, **ADMIN)
    queue = (await app_client.get(API, params={"flagged": True})).json()
    assert [x["id"] for x in queue] == [t["id"]] and queue[0]["can"]["resolve_flag"] is True
    assert (await app_client.get(f"{API}/summary")).json()["open_flags"] == 1
    assert (await app_client.post(f"{API}/{t['id']}/resolve", json={"outcome": "DEADLINE_EXTENDED", "note": "More time for the extra halls."})).status_code == 422     # needs a date
    assert (await app_client.post(f"{API}/{t['id']}/resolve", json={"outcome": "DEADLINE_EXTENDED", "note": "More time.", "new_deadline": _in(1)})).status_code == 422   # same 3-hour rule
    res = (await app_client.post(f"{API}/{t['id']}/resolve", json={"outcome": "DEADLINE_EXTENDED", "note": "More time for the extra halls.", "new_deadline": _in(96)})).json()
    assert res["is_flagged"] is False and res["flags"][0]["status"] == "RESOLVED" and res["flags"][0]["outcome"] == "DEADLINE_EXTENDED" and res["extension_count"] == 1
    assert res["flags"][0]["resolved_by_name"] and res["flags"][0]["resolution_note"].startswith("More time")
    assert (await app_client.post(f"{API}/{t['id']}/resolve", json={"outcome": "NO_ACTION", "note": "nothing open"})).status_code == 422            # nothing left to resolve
    await _as(app_client, raw, "anna")
    assert (await app_client.post(f"{API}/{t['id']}/resolve", json={"outcome": "NO_ACTION", "note": "I decide"})).status_code == 403          # not for ordinary staff
    assert (await app_client.get(API)).status_code == 403
    # a flag can end with the task cancelled
    await _as(app_client, raw, "anna")
    t2 = await _task(app_client, ids["bilal"], title="Another")
    await app_client.post(f"{API}/{t2['id']}/flag", json={"reason": "I no longer know who should do this."})
    await _login(app_client, **ADMIN)
    out = (await app_client.post(f"{API}/{t2['id']}/resolve", json={"outcome": "TASK_CANCELLED", "note": "Duplicate work."})).json()
    assert out["status"] == "CANCELLED" and "Cancelled by the administration" in out["cancel_reason"]


async def test_only_the_two_people_and_the_administration_see_a_task_and_its_conversation(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    t = await _task(app_client, ids["bilal"])
    c = (await app_client.post(f"{API}/{t['id']}/comments", json={"body": "Please start with the north wing."})).json()
    assert c["comment_count"] == 1 and c["comments"][0]["body"].startswith("Please start") and c["comments"][0]["author_name"] == "TCA X"
    await _as(app_client, raw, "carla")
    assert (await app_client.get(f"{API}/{t['id']}")).status_code == 404 and (await app_client.post(f"{API}/{t['id']}/comments", json={"body": "hi"})).status_code == 404
    assert (await app_client.get(f"{API}/inbox")).json() == [] and (await app_client.get(f"{API}/sent")).json() == []
    await _as(app_client, raw, "bilal")
    assert (await app_client.post(f"{API}/{t['id']}/comments", json={"body": "Will do."})).status_code == 200
    await _login(app_client, **ADMIN)
    d = (await app_client.get(f"{API}/{t['id']}")).json()
    assert [x["body"] for x in d["comments"]] == ["Please start with the north wing.", "Will do."] and d["my_role"] == "ADMIN"
    notes = (await db_session.execute(select(Notification).where(Notification.title.like("Message on%")))).scalars().all()
    assert len(notes) == 2                                                                                                              # one to each OTHER person


async def test_a_rejected_task_can_be_given_to_someone_else_and_stays_linked(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    await _as(app_client, raw, "anna")
    t = await _task(app_client, ids["bilal"])
    assert (await _assign(app_client, ids["carla"], follow_up_of_id=t["id"])).status_code == 422                       # not rejected yet
    await _as(app_client, raw, "bilal")
    await app_client.post(f"{API}/{t['id']}/respond", json={"action": "REJECT", "reason": "NOT_MY_RESPONSIBILITY", "explanation": "This belongs to the library team."})
    await _as(app_client, raw, "anna")
    again = (await app_client.get(f"{API}/{t['id']}")).json()
    assert again["can"]["assign_again"] is True
    fresh = await _assign(app_client, ids["carla"], follow_up_of_id=t["id"])
    assert fresh.status_code == 201 and fresh.json()["follow_up_of"]["reference"] == t["reference"]
    await _as(app_client, raw, "carla")
    assert (await _assign(app_client, ids["dev"], follow_up_of_id=t["id"])).status_code == 422                          # someone else's rejected task


async def test_progress_over_30_60_90_days_and_all_time(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    ids = await _ids(app_client, raw, db_session)
    made = {}
    await _as(app_client, raw, "anna")
    for name in ("done_on_time", "done_late", "rejected_old", "never_answered", "pending_fresh", "accepted_open"):
        made[name] = await _task(app_client, ids["bilal"], title=name)
    await _as(app_client, raw, "bilal")
    for name in ("done_on_time", "done_late", "accepted_open"):
        await app_client.post(f"{API}/{made[name]['id']}/respond", json={"action": "ACCEPT"})
    await app_client.post(f"{API}/{made['done_on_time']['id']}/complete", json={})
    await app_client.post(f"{API}/{made['done_late']['id']}/complete", json={})
    await app_client.post(f"{API}/{made['rejected_old']['id']}/respond", json={"action": "REJECT", "reason": "NO_CAPACITY", "explanation": "Exam week, I have no free hours."})
    # shape the history: done_late missed its deadline; never_answered is long past; rejected_old is 45 days old; done_on_time is 100 days old
    now = datetime.now(timezone.utc)
    await db_session.execute(update(Task).where(Task.id == made["done_late"]["id"]).values(deadline=now - timedelta(hours=30), completed_at=now - timedelta(hours=1)))
    await db_session.execute(update(Task).where(Task.id == made["never_answered"]["id"]).values(deadline=now - timedelta(days=3)))
    await db_session.execute(update(Task).where(Task.id == made["rejected_old"]["id"]).values(created_at=now - timedelta(days=45), responded_at=now - timedelta(days=44)))
    await db_session.execute(update(Task).where(Task.id == made["done_on_time"]["id"]).values(created_at=now - timedelta(days=100), accepted_at=now - timedelta(days=100) + timedelta(hours=2),
                                                                                              completed_at=now - timedelta(days=99), deadline=now - timedelta(days=98)))
    await db_session.commit()
    await _login(app_client, **ADMIN)
    stats = {}
    for days in (30, 60, 90, 0):
        r = (await app_client.get(f"{API}/reports/staff", params={"days": days})).json()
        stats[days] = {row["name"]: row["receiving"] for row in r["rows"]}["TCB X"]
        assert r["days"] == days
    s30, s60, s90, sall = stats[30], stats[60], stats[90], stats[0]
    assert (s30["received"], s60["received"], s90["received"], sall["received"]) == (4, 5, 5, 6)                  # the 45-day-old one joins at 60; the 100-day-old only in "all"
    assert (s30["rejected"], s60["rejected"]) == (0, 1)
    assert (s30["completed_late"], s30["overdue"], s30["still_open"], s30["pending_response"]) == (1, 1, 1, 1)       # late one / never answered / accepted-and-open / fresh and waiting
    assert sall["completed_on_time"] == 1 and s90["completed_on_time"] == 0
    assert s30["on_time_rate"] == 0.0 and sall["on_time_rate"] == 33.3                                              # 1 on time out of (2 completed + 1 overdue)
    assert s60["acceptance_rate"] == 66.7 and s60["avg_response_hours"] is not None
    # the detail for one person
    rep = (await app_client.get(f"{API}/reports/person/{ids['bilal']}", params={"days": 60})).json()
    assert rep["person"]["name"] == "TCB X" and [t["title"] for t in rep["rejected"]] == ["rejected_old"]
    assert rep["rejected"][0]["reject_reason_label"] == "I do not have the time or capacity" and "Exam week" in rep["rejected"][0]["reject_explanation"]
    assert {t["title"] for t in rep["late"]} == {"done_late", "never_answered"}
    assert rep["reasons"] == [{"key": "NO_CAPACITY", "label": "I do not have the time or capacity", "count": 1}] and len(rep["weeks"]) == 12
    anna = (await app_client.get(f"{API}/reports/person/{ids['anna']}", params={"days": 0})).json()["assigning"]
    assert (anna["assigned"], anna["rejected"], anna["completed"], anna["overdue"]) == (6, 1, 2, 1)             # the same history, from the giver's side
    # who may look
    await _as(app_client, raw, "bilal")
    assert (await app_client.get(f"{API}/reports/me", params={"days": 30})).json()["receiving"]["received"] == 4
    assert (await app_client.get(f"{API}/reports/person/{ids['bilal']}")).status_code == 200
    assert (await app_client.get(f"{API}/reports/person/{ids['anna']}")).status_code == 403                     # nobody else's
    assert (await app_client.get(f"{API}/reports/staff")).status_code == 403
    assert (await app_client.get(f"{API}/reports/me", params={"days": -1})).status_code == 422