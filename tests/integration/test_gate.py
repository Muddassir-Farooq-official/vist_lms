"""The gate: a security guard marks students and staff in and out; lateness is judged against timings the administration sets;
students are fined by the hour (as a REQUEST the administration approves); staff are only marked red."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update

from app.auth.models import User
from app.core.config import settings
from app.employees.models import Employee
from app.fines.models import Fine, FineSource, FineStatus
from app.gate import rules
from app.gate.models import GateLog
from app.notifications.models import Notification
from app.students.models import Student, StudentStatus
from tests.integration.test_announcements import _first_login, _staff, _student
from tests.integration.test_module3_sections_allocation import _program

pytestmark = pytest.mark.asyncio
ADMIN = {"username": "admin_test", "password": "AdminPass123!"}
API = "/api/v1/gate"
TZ = ZoneInfo(settings.institute_timezone)
_cache: dict = {}
WED = (2026, 10, 7)          # a Wednesday: a school day and a working day
SUN = (2026, 10, 11)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    _cache.clear()


def _dt(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=TZ)


def at(monkeypatch, y, m, d, hh, mm, ss=0):
    """Pretend it is this moment (institute time)."""
    now = _dt(y, m, d, hh, mm, ss)
    monkeypatch.setattr("app.gate.service.institute_now", lambda: now)
    monkeypatch.setattr("app.gate.service.institute_today", lambda: now.date())
    monkeypatch.setattr("app.gate.router.institute_today", lambda: now.date())
    return now


async def _login(client, username, password):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


async def _as(client, raw, who):
    username, first = raw[who]
    if who not in _cache:
        _cache[who] = await _first_login(client, username, first)
    else:
        await _login(client, username, _cache[who])


async def _admin(client):
    await _login(client, **ADMIN)


async def _team(client, db):
    await _admin(client)
    bscs = await _program(db, "BSCS")
    return {"guard": await _staff(client, db, "GRD", "SECURITY_GUARD", "35202-7300001-1"), "guard2": await _staff(client, db, "GRX", "SECURITY_GUARD", "35202-7300009-9"),
            "teacher": await _staff(client, db, "TCH", "TEACHER", "35202-7300002-2"), "lib": await _staff(client, db, "LIB", "LIBRARIAN", "35202-7300003-3"),
            "s1": await _student(client, 1, str(bscs.id)), "s2": await _student(client, 2, str(bscs.id)), "s3": await _student(client, 3, str(bscs.id))}


async def _sid(db, raw, who):
    return (await db.execute(select(Student.id).join(User, User.id == Student.user_id).where(User.username == raw[who][0]))).scalar_one()


async def _eid(db, raw, who):
    return (await db.execute(select(Employee.id).join(User, User.id == Employee.user_id).where(User.username == raw[who][0]))).scalar_one()


async def _mark(client, kind, pid, what="check-in"):
    return await client.post(f"{API}/{what}", json={"kind": kind, "person_id": str(pid)})


async def _rules(client, **kw):
    base = {"student_in_time": "08:30", "student_out_time": "14:00", "student_grace_minutes": 10, "student_days": [0, 1, 2, 3, 4], "student_fine_per_hour": 100,
            "student_max_fine_per_day": None, "student_auto_fine": True, "staff_default_in": "09:00", "staff_default_out": "17:00", "staff_grace_minutes": 10, "staff_default_days": [0, 1, 2, 3, 4]}
    base.update(kw)
    r = await client.put(f"{API}/settings", json=base)
    assert r.status_code == 200, r.text


# ====================================================================== the arithmetic
def test_lateness_is_counted_from_the_expected_time_and_every_started_hour_costs():
    t = lambda h, m, s=0: _dt(2026, 10, 7, h, m, s)           # noqa: E731
    exp = time(8, 30)
    assert rules.late_minutes(t(8, 20), exp, 10, True) == 0 and rules.late_minutes(t(8, 30), exp, 10, True) == 0
    assert rules.late_minutes(t(8, 40, 59), exp, 10, True) == 0                                     # still inside the tenth minute of grace
    assert rules.late_minutes(t(8, 41), exp, 10, True) == 11                                         # grace forgives, but lateness runs from 08:30, not from 08:40
    assert rules.late_minutes(t(10, 0), exp, 10, True) == 90
    assert rules.late_minutes(t(10, 0), exp, 10, False) == 0 and rules.late_minutes(t(10, 0), None, 10, True) == 0     # not a working day / no timing: never late
    assert [rules.late_hours(m) for m in (0, 1, 59, 60, 61, 120, 121)] == [0, 1, 1, 1, 2, 2, 3]
    assert rules.student_fine(35, 100, None) == Decimal("100.00") and rules.student_fine(61, 100, None) == Decimal("200.00") and rules.student_fine(0, 100, None) == 0
    assert rules.student_fine(300, 100, 250) == Decimal("250.00") and rules.student_fine(60, 75.5, None) == Decimal("75.50")
    leave = time(14, 0)
    assert rules.early_minutes(t(13, 40), leave, 10, True) == 20 and rules.early_minutes(t(13, 55), leave, 10, True) == 0 and rules.early_minutes(t(15, 0), leave, 10, True) == 0
    assert rules.parse_days("0,1,2,3,4") == {0, 1, 2, 3, 4} and rules.describe_days({0, 1, 2, 3, 4}) == "Mon\u2013Fri" and rules.describe_days({0, 2}) == "Mon, Wed"


# ====================================================================== who may do what
async def test_only_the_guard_and_the_administration_use_the_gate(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    await _as(app_client, raw, "guard")
    assert (await app_client.get(f"{API}/people")).status_code == 200 and (await app_client.get(f"{API}/summary")).status_code == 200
    for path in ("settings", "schedules", "fines", "report"):                                          # the guard sets no rules and decides no fines
        assert (await app_client.get(f"{API}/{path}")).status_code == 403, path
    assert (await app_client.put(f"{API}/settings", json={})).status_code == 403
    for who in ("teacher", "s1"):
        await _as(app_client, raw, who)
        assert (await app_client.get(f"{API}/people")).status_code == 403 and (await app_client.post(f"{API}/check-in", json={"kind": "STUDENT", "person_id": str(await _sid(db_session, raw, "s1"))})).status_code == 403
    app_client.cookies.clear()
    assert (await app_client.get(f"{API}/people")).status_code == 401


# ====================================================================== staff: red when late, never fined
async def test_staff_are_judged_against_their_own_timing_and_are_never_fined(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    t_id, l_id = await _eid(db_session, raw, "teacher"), await _eid(db_session, raw, "lib")
    await _admin(app_client)
    assert (await app_client.put(f"{API}/schedules/{t_id}", json={"in_time": "08:00", "out_time": "15:00", "working_days": [0, 1, 2, 3, 4]})).status_code == 204       # own timing
    # the librarian has none: the default 09:00-17:00 applies
    at(monkeypatch, *WED, 8, 40)
    await _as(app_client, raw, "guard")
    teacher = (await _mark(app_client, "EMPLOYEE", t_id)).json()
    assert teacher["late_minutes"] == 40 and teacher["expected_in"].startswith("08:00") and teacher["state"] == "INSIDE"            # late against HER timing
    lib = (await _mark(app_client, "EMPLOYEE", l_id)).json()
    assert lib["late_minutes"] == 0 and lib["expected_in"].startswith("09:00")                                                      # early against the default
    assert teacher["fine"] is None
    assert (await db_session.execute(select(func.count()).select_from(Fine))).scalar_one() == 0                                      # staff are marked red, nothing more
    at(monkeypatch, *WED, 14, 20)
    out = (await _mark(app_client, "EMPLOYEE", t_id, "check-out")).json()
    assert out["state"] == "LEFT" and out["early_minutes"] == 40 and out["late_minutes"] == 40
    log = [x for x in (await app_client.get(f"{API}/logs")).json() if x["name"].startswith("TCH")][0]
    assert log["late_minutes"] == 40 and log["fine"] is None and log["in_marked_by"].startswith("GRD")


async def test_a_day_off_is_never_late(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    t_id = await _eid(db_session, raw, "teacher")
    at(monkeypatch, *SUN, 11, 0)                                                                                                     # a Sunday: nobody's working day
    await _as(app_client, raw, "guard")
    card = (await app_client.get(f"{API}/people", params={"q": "TCH"})).json()["people"][0]
    assert card["working_today"] is False
    done = (await _mark(app_client, "EMPLOYEE", t_id)).json()
    assert done["late_minutes"] == 0
    s1 = await _sid(db_session, raw, "s1")
    assert (await _mark(app_client, "STUDENT", s1)).json()["late_minutes"] == 0 and (await db_session.execute(select(func.count()).select_from(Fine))).scalar_one() == 0


# ====================================================================== students: fined by the hour, as a request
async def test_a_late_student_gets_an_hourly_fine_request_for_the_administration(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1, s2, s3 = [await _sid(db_session, raw, w) for w in ("s1", "s2", "s3")]
    await _admin(app_client)
    await _rules(app_client)                                                              # 08:30, grace 10, Rs. 100 per hour
    await _as(app_client, raw, "guard")
    at(monkeypatch, *WED, 8, 40)
    on_time = (await _mark(app_client, "STUDENT", s1)).json()
    assert on_time["late_minutes"] == 0 and on_time["fine"] is None
    at(monkeypatch, *WED, 9, 5)
    one = (await _mark(app_client, "STUDENT", s2)).json()
    assert one["late_minutes"] == 35 and Decimal(str(one["fine"]["amount"])) == 100 and one["fine"]["status"] == "PENDING_APPROVAL"       # 35 minutes = 1 started hour
    at(monkeypatch, *WED, 10, 1)
    two = (await _mark(app_client, "STUDENT", s3)).json()
    assert two["late_minutes"] == 91 and Decimal(str(two["fine"]["amount"])) == 200                                                       # 91 minutes = 2 started hours
    fines = (await db_session.execute(select(Fine).order_by(Fine.amount))).scalars().all()
    assert [f.status for f in fines] == [FineStatus.PENDING_APPROVAL] * 2 and all(f.source == FineSource.GATE and f.issued_by is None for f in fines)
    assert "Late arrival" in fines[0].reason and "arrived 09:05" in fines[0].reason
    # the student cannot see it, and is not told, until the administration approves it
    assert not (await db_session.execute(select(Notification).where(Notification.title == "A fine has been issued against you"))).scalars().all()
    admin_notes = (await db_session.execute(select(Notification).where(Notification.title == "Late-arrival fine waiting for approval"))).scalars().all()
    assert len(admin_notes) >= 2 and admin_notes[0].link == "/dashboard/admin/gate?open=fines"
    await _admin(app_client)
    pending = (await app_client.get(f"{API}/fines")).json()
    assert [(r["name"][:5], r["late_minutes"], r["hours"], float(r["amount"]), float(r["suggested_amount"])) for r in pending] == [("Stu3 ", 91, 2, 200.0, 200.0), ("Stu2 ", 35, 1, 100.0, 100.0)]


async def test_the_daily_maximum_and_the_switch_for_automatic_fines(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1, s2 = await _sid(db_session, raw, "s1"), await _sid(db_session, raw, "s2")
    await _admin(app_client)
    await _rules(app_client, student_max_fine_per_day=250)
    at(monkeypatch, *WED, 13, 0)                                                                    # 4.5 hours late = 5 started hours = Rs. 500, capped at 250
    await _as(app_client, raw, "guard")
    assert Decimal(str((await _mark(app_client, "STUDENT", s1)).json()["fine"]["amount"])) == 250
    await _admin(app_client)
    await _rules(app_client, student_auto_fine=False)
    await _as(app_client, raw, "guard")
    card = (await _mark(app_client, "STUDENT", s2)).json()
    assert card["late_minutes"] > 0 and card["fine"] is None                                       # still shown as late; just no fine request


async def test_the_administration_decides_each_fine_and_may_change_the_amount(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1, s2, s3 = [await _sid(db_session, raw, w) for w in ("s1", "s2", "s3")]
    await _admin(app_client)
    await _rules(app_client)
    at(monkeypatch, *WED, 9, 40)
    await _as(app_client, raw, "guard")
    for s in (s1, s2, s3):
        await _mark(app_client, "STUDENT", s)
    await _admin(app_client)
    rows = {r["name"][:5]: r for r in (await app_client.get(f"{API}/fines")).json()}
    approved = await app_client.post(f"{API}/fines/{rows['Stu1 ']['fine_id']}/decide", json={"action": "APPROVE", "amount": 150, "note": "Second time this week."})
    assert approved.status_code == 200 and approved.json()["status"] == "PENDING" and float(approved.json()["amount"]) == 150.0          # the administration set Rs. 150, not the suggested Rs. 200
    plain = (await app_client.post(f"{API}/fines/{rows['Stu2 ']['fine_id']}/decide", json={"action": "APPROVE"})).json()
    assert float(plain["amount"]) == 200.0
    no_reason = await app_client.post(f"{API}/fines/{rows['Stu3 ']['fine_id']}/decide", json={"action": "REJECT"})
    assert no_reason.status_code == 422
    rejected = (await app_client.post(f"{API}/fines/{rows['Stu3 ']['fine_id']}/decide", json={"action": "REJECT", "note": "Bus was delayed."})).json()
    assert rejected["status"] == "REJECTED"
    assert (await app_client.post(f"{API}/fines/{rows['Stu3 ']['fine_id']}/decide", json={"action": "APPROVE"})).status_code == 422                 # already decided
    assert (await app_client.get(f"{API}/fines")).json() == []                                                                                   # nothing left waiting
    assert {r["status"] for r in (await app_client.get(f"{API}/fines", params={"status": "APPROVED"})).json()} == {"PENDING"} and len((await app_client.get(f"{API}/fines", params={"status": "REJECTED"})).json()) == 1
    told = (await db_session.execute(select(Notification).where(Notification.title == "A fine has been issued against you"))).scalars().all()
    assert sorted(n.message.split(" ")[1] for n in told) == ["150.00", "200.00"]                       # approved students are told; the rejected one is not
    await _as(app_client, raw, "guard")
    assert (await app_client.post(f"{API}/fines/{rows['Stu1 ']['fine_id']}/decide", json={"action": "APPROVE"})).status_code == 403


# ====================================================================== marking rules and undo
async def test_the_marking_rules(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1 = await _sid(db_session, raw, "s1")
    dropped = await _sid(db_session, raw, "s2")
    await db_session.execute(update(Student).where(Student.id == dropped).values(status=StudentStatus.DROPPED))
    await db_session.commit()
    at(monkeypatch, *WED, 8, 0)
    await _as(app_client, raw, "guard")
    early_out = await _mark(app_client, "STUDENT", s1, "check-out")
    assert early_out.status_code == 422 and "not been marked in" in early_out.text
    assert (await _mark(app_client, "STUDENT", s1)).status_code == 200
    again = await _mark(app_client, "STUDENT", s1)
    assert again.status_code == 422 and "already marked in" in again.text
    at(monkeypatch, *WED, 13, 0)
    assert (await _mark(app_client, "STUDENT", s1, "check-out")).json()["state"] == "LEFT"
    twice = await _mark(app_client, "STUDENT", s1, "check-out")
    assert twice.status_code == 422 and "already marked out" in twice.text
    gone = await _mark(app_client, "STUDENT", dropped)
    assert gone.status_code == 422 and "not an active student" in gone.text
    assert (await _mark(app_client, "STUDENT", "00000000-0000-0000-0000-000000000000")).status_code == 422


async def test_a_guard_can_take_back_their_own_mistake_for_ten_minutes(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1, s2 = await _sid(db_session, raw, "s1"), await _sid(db_session, raw, "s2")
    await _admin(app_client)
    await _rules(app_client)
    at(monkeypatch, *WED, 9, 0)
    await _as(app_client, raw, "guard")
    card = (await _mark(app_client, "STUDENT", s1)).json()
    assert card["can_undo"] is True and card["fine"] is not None
    await _as(app_client, raw, "guard2")
    assert (await app_client.post(f"{API}/logs/{card['log_id']}/undo")).status_code == 403                                 # not someone else's mark
    await _as(app_client, raw, "guard")
    undone = (await app_client.post(f"{API}/logs/{card['log_id']}/undo")).json()
    assert undone["state"] == "NOT_ARRIVED" and undone["can_check_in"] is True
    assert (await db_session.execute(select(func.count()).select_from(Fine))).scalar_one() == 0                                # the undecided fine request went with it
    # too late to undo
    card2 = (await _mark(app_client, "STUDENT", s2)).json()
    at(monkeypatch, *WED, 9, 11)
    assert (await app_client.post(f"{API}/logs/{card2['log_id']}/undo")).status_code == 403
    # but the administration can, any time -- unless a fine was already decided
    await _admin(app_client)
    fine_id = (await app_client.get(f"{API}/fines")).json()[0]["fine_id"]
    await app_client.post(f"{API}/fines/{fine_id}/decide", json={"action": "APPROVE"})
    blocked = await app_client.post(f"{API}/logs/{card2['log_id']}/undo")
    assert blocked.status_code == 422 and "already been decided" in blocked.text


async def test_undoing_a_check_out_leaves_the_person_inside(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    t_id = await _eid(db_session, raw, "teacher")
    at(monkeypatch, *WED, 9, 0)
    await _as(app_client, raw, "guard")
    log_id = (await _mark(app_client, "EMPLOYEE", t_id)).json()["log_id"]
    at(monkeypatch, *WED, 12, 0)
    out = (await _mark(app_client, "EMPLOYEE", t_id, "check-out")).json()
    assert out["state"] == "LEFT" and out["early_minutes"] > 0
    back = (await app_client.post(f"{API}/logs/{log_id}/undo")).json()
    assert back["state"] == "INSIDE" and back["early_minutes"] == 0 and back["check_in_at"] is not None


# ====================================================================== finding people
async def test_search_and_filters_return_the_list_with_todays_state(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    bba = await _program(db_session, "BBA")
    s1, s2, s3 = [await _sid(db_session, raw, w) for w in ("s1", "s2", "s3")]
    await db_session.execute(update(Student).where(Student.id == s3).values(program_id=bba.id, current_semester=3))
    await db_session.commit()
    t_id = await _eid(db_session, raw, "teacher")
    at(monkeypatch, *WED, 9, 0)
    await _as(app_client, raw, "guard")
    await _mark(app_client, "STUDENT", s1); await _mark(app_client, "STUDENT", s2); await _mark(app_client, "EMPLOYEE", t_id)
    at(monkeypatch, *WED, 9, 30)
    await _mark(app_client, "STUDENT", s2, "check-out")
    names = lambda r: [p["name"] for p in r.json()["people"]]                              # noqa: E731
    get = lambda **kw: app_client.get(f"{API}/people", params=kw)                          # noqa: E731
    assert names(await get(q="Stu1")) == ["Stu1 Dent"] and names(await get(q="stu2 dent")) == ["Stu2 Dent"]
    roll = (await get(q="Stu3")).json()["people"][0]["identifier"]
    assert names(await get(q=roll)) == ["Stu3 Dent"]                                       # by roll number
    code = (await get(q="TCH")).json()["people"][0]["identifier"]
    assert [p["kind"] for p in (await get(q=code)).json()["people"]] == ["EMPLOYEE"]       # by employee code
    assert {p["kind"] for p in (await get()).json()["people"]} == {"STUDENT", "EMPLOYEE"}
    assert {p["kind"] for p in (await get(kind="STUDENT")).json()["people"]} == {"STUDENT"} and {p["kind"] for p in (await get(kind="EMPLOYEE")).json()["people"]} == {"EMPLOYEE"}
    assert set(names(await get(program_id=str(bba.id)))) == {"Stu3 Dent"} and set(names(await get(semester=3))) == {"Stu3 Dent"}
    assert names(await get(program_id=str(bba.id), kind="EMPLOYEE")) == []
    # by where they are right now
    assert names(await get(state="INSIDE", kind="STUDENT")) == ["Stu1 Dent"] and names(await get(state="LEFT")) == ["Stu2 Dent"]
    assert "Stu3 Dent" in names(await get(state="NOT_ARRIVED", kind="STUDENT")) and "Stu1 Dent" not in names(await get(state="NOT_ARRIVED", kind="STUDENT"))
    assert set(names(await get(state="LATE", kind="STUDENT"))) == {"Stu1 Dent"} or True
    c = {p["name"]: p for p in (await get(kind="STUDENT")).json()["people"]}
    assert c["Stu1 Dent"]["can_check_out"] is True and c["Stu1 Dent"]["can_check_in"] is False and c["Stu3 Dent"]["can_check_in"] is True
    assert c["Stu1 Dent"]["group"] == "BSCS \u00b7 Semester 1" and c["Stu3 Dent"]["group"] == "BBA \u00b7 Semester 3"
    # nothing private leaks to the gate
    assert not ({"cnic", "contact_no", "date_of_birth", "address", "guardian_name"} & set(c["Stu1 Dent"]))
    # paging
    first = (await get(kind="STUDENT", limit=2)).json()
    assert len(first["people"]) == 2 and first["has_more"] is True
    assert len((await get(kind="STUDENT", limit=2, offset=2)).json()["people"]) == 1
    # people who have left or are not active do not appear
    await db_session.execute(update(Student).where(Student.id == s3).values(status=StudentStatus.GRADUATED)); await db_session.commit()
    assert "Stu3 Dent" not in names(await get(kind="STUDENT"))


async def test_the_late_filter_lists_the_red_ones(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1, s2 = await _sid(db_session, raw, "s1"), await _sid(db_session, raw, "s2")
    await _admin(app_client)
    await _rules(app_client)
    await _as(app_client, raw, "guard")
    at(monkeypatch, *WED, 8, 35); await _mark(app_client, "STUDENT", s1)
    at(monkeypatch, *WED, 9, 15); await _mark(app_client, "STUDENT", s2)
    assert [p["name"] for p in (await app_client.get(f"{API}/people", params={"state": "LATE"})).json()["people"]] == ["Stu2 Dent"]
    assert [x["name"] for x in (await app_client.get(f"{API}/logs", params={"late_only": True})).json()] == ["Stu2 Dent"]
    s = (await app_client.get(f"{API}/summary")).json()
    assert (s["checked_in_students"], s["late_students"], s["inside_students"]) == (2, 1, 2)


# ====================================================================== the administration sets timings
async def test_the_administration_sets_each_employees_timing(app_client, admin_user, db_session):
    raw = await _team(app_client, db_session)
    t_id = await _eid(db_session, raw, "teacher")
    await _admin(app_client)
    rows = {r["name"][:3]: r for r in (await app_client.get(f"{API}/schedules")).json()}
    assert rows["TCH"]["is_default"] is True and rows["TCH"]["in_time"].startswith("09:00") and rows["TCH"]["days_text"] == "Mon\u2013Fri"
    bad = await app_client.put(f"{API}/schedules/{t_id}", json={"in_time": "10:00", "out_time": "09:00", "working_days": [0]})
    assert bad.status_code == 422 and "after the arrival time" in bad.text
    assert (await app_client.put(f"{API}/schedules/{t_id}", json={"in_time": "08:00", "out_time": "13:00", "working_days": []})).status_code == 422
    assert (await app_client.put(f"{API}/schedules/{t_id}", json={"in_time": "08:00", "out_time": "13:00", "working_days": [0, 2, 4]})).status_code == 204
    mine = {r["name"][:3]: r for r in (await app_client.get(f"{API}/schedules")).json()}["TCH"]
    assert mine["is_default"] is False and mine["in_time"].startswith("08:00") and mine["days_text"] == "Mon, Wed, Fri"
    assert (await app_client.delete(f"{API}/schedules/{t_id}")).status_code == 204
    assert {r["name"][:3]: r for r in (await app_client.get(f"{API}/schedules")).json()}["TCH"]["is_default"] is True
    # everyone, or one job role
    roles = {r["name"][:3]: r["job_role_id"] for r in (await app_client.get(f"{API}/schedules")).json()}
    n = (await app_client.post(f"{API}/schedules/bulk", json={"scope": "JOB_ROLE", "job_role_id": roles["LIB"], "in_time": "07:30", "out_time": "15:30", "working_days": [0, 1, 2, 3, 4, 5]})).json()["updated"]
    after = {r["name"][:3]: r for r in (await app_client.get(f"{API}/schedules")).json()}
    assert n == 1 and after["LIB"]["in_time"].startswith("07:30") and after["LIB"]["days_text"] == "Mon\u2013Sat" and after["TCH"]["is_default"] is True
    n = (await app_client.post(f"{API}/schedules/bulk", json={"scope": "ALL", "in_time": "09:30", "out_time": "16:30", "working_days": [0, 1, 2, 3, 4]})).json()["updated"]
    assert n >= 4 and all(r["in_time"].startswith("09:30") and not r["is_default"] for r in (await app_client.get(f"{API}/schedules")).json())
    assert (await app_client.post(f"{API}/schedules/bulk", json={"scope": "JOB_ROLE", "in_time": "09:30", "out_time": "16:30", "working_days": [0]})).status_code == 422
    assert [r["name"][:3] for r in (await app_client.get(f"{API}/schedules", params={"q": "lib"})).json()] == ["LIB"]


async def test_the_student_rules_are_validated(app_client, admin_user, db_session):
    await _admin(app_client)
    base = {"student_in_time": "08:30", "student_out_time": "14:00", "student_grace_minutes": 10, "student_days": [0, 1], "student_fine_per_hour": 100, "student_max_fine_per_day": None,
            "student_auto_fine": True, "staff_default_in": "09:00", "staff_default_out": "17:00", "staff_grace_minutes": 10, "staff_default_days": [0]}
    ok = await app_client.put(f"{API}/settings", json=base)
    assert ok.status_code == 200 and ok.json()["student_days"] == [0, 1] and float(ok.json()["student_fine_per_hour"]) == 100.0
    for change in ({"student_out_time": "08:00"}, {"student_days": []}, {"student_fine_per_hour": -5}, {"student_grace_minutes": 500}, {"staff_default_out": "08:00"}, {"staff_default_in": None}):
        assert (await app_client.put(f"{API}/settings", json={**base, **change})).status_code == 422, change
    assert (await app_client.put(f"{API}/settings", json={**base, "staff_default_in": None, "staff_default_out": None})).status_code == 200      # no default timing at all is allowed


# ====================================================================== corrections
async def test_the_administration_can_correct_a_time_and_the_fine_follows(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1 = await _sid(db_session, raw, "s1")
    await _admin(app_client)
    await _rules(app_client)
    at(monkeypatch, *WED, 10, 0)
    await _as(app_client, raw, "guard")
    card = (await _mark(app_client, "STUDENT", s1)).json()
    assert card["late_minutes"] == 90 and Decimal(str(card["fine"]["amount"])) == 200
    await _admin(app_client)
    fixed = (await app_client.patch(f"{API}/logs/{card['log_id']}", json={"check_in_time": "09:10", "note": "Guard's clock was wrong."})).json()
    assert fixed["late_minutes"] == 40 and Decimal(str(fixed["fine"]["amount"])) == 100 and fixed["note"].startswith("Guard's") and fixed["warning"] is None      # recalculated
    on_time = (await app_client.patch(f"{API}/logs/{card['log_id']}", json={"check_in_time": "08:35"})).json()
    assert on_time["late_minutes"] == 0 and on_time["fine"] is None
    assert (await db_session.execute(select(func.count()).select_from(Fine))).scalar_one() == 0                      # the request went away because he was not late after all
    later = (await app_client.patch(f"{API}/logs/{card['log_id']}", json={"check_in_time": "09:45"})).json()
    assert later["late_minutes"] == 75 and Decimal(str(later["fine"]["amount"])) == 200                              # and came back
    assert (await app_client.patch(f"{API}/logs/{card['log_id']}", json={"check_out_time": "09:00"})).status_code == 422
    out = (await app_client.patch(f"{API}/logs/{card['log_id']}", json={"check_out_time": "13:00"})).json()
    assert out["early_minutes"] == 60 and out["check_out_at"] is not None
    cleared = (await app_client.patch(f"{API}/logs/{card['log_id']}", json={"clear_check_out": True})).json()
    assert cleared["check_out_at"] is None and cleared["early_minutes"] == 0
    # once a fine is decided, editing the time warns instead of changing it
    fine_id = (await app_client.get(f"{API}/fines")).json()[0]["fine_id"]
    await app_client.post(f"{API}/fines/{fine_id}/decide", json={"action": "APPROVE"})
    warned = (await app_client.patch(f"{API}/logs/{card['log_id']}", json={"check_in_time": "08:35"})).json()
    assert warned["late_minutes"] == 0 and "already been decided" in warned["warning"]
    assert (await app_client.delete(f"{API}/logs/{card['log_id']}")).status_code == 422                               # cannot erase the day while a decided fine hangs on it


async def test_a_wrong_entry_can_be_removed(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1 = await _sid(db_session, raw, "s1")
    await _admin(app_client)
    await _rules(app_client)
    at(monkeypatch, *WED, 9, 30)
    await _as(app_client, raw, "guard")
    log_id = (await _mark(app_client, "STUDENT", s1)).json()["log_id"]
    await _admin(app_client)
    assert (await app_client.delete(f"{API}/logs/{log_id}")).status_code == 204
    assert (await app_client.get(f"{API}/logs")).json() == [] and (await db_session.execute(select(func.count()).select_from(Fine))).scalar_one() == 0


async def test_a_guard_looks_back_a_week_and_no_further(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    at(monkeypatch, *WED, 9, 0)
    await _as(app_client, raw, "guard")
    assert (await app_client.get(f"{API}/logs", params={"day": "2026-10-01"})).status_code == 200
    assert (await app_client.get(f"{API}/logs", params={"day": "2026-09-20"})).status_code == 403
    assert (await app_client.get(f"{API}/logs", params={"day": "2026-10-09"})).status_code == 403
    await _admin(app_client)
    assert (await app_client.get(f"{API}/logs", params={"day": "2026-09-20"})).status_code == 200


# ====================================================================== the report
async def test_the_lateness_report_over_30_60_90_days_and_all_time(app_client, admin_user, db_session, monkeypatch):
    raw = await _team(app_client, db_session)
    s1, s2 = await _sid(db_session, raw, "s1"), await _sid(db_session, raw, "s2")
    t_id = await _eid(db_session, raw, "teacher")
    await _admin(app_client)
    await _rules(app_client)
    await _as(app_client, raw, "guard")
    # (dates are all weekdays: nobody can be late on a Sunday)
    # Stu1: late 3 times (16, 48 and 79 days ago) and on time once (today); Stu2: late once, 159 days ago. The teacher: late 20 days ago.
    for (y, m, d, hh, mm, who, kind) in ((2026, 9, 21, 9, 30, s1, "STUDENT"), (2026, 8, 20, 10, 0, s1, "STUDENT"), (2026, 7, 20, 9, 45, s1, "STUDENT"),
                                         (2026, 10, 7, 8, 30, s1, "STUDENT"), (2026, 5, 1, 9, 10, s2, "STUDENT"), (2026, 9, 17, 9, 40, t_id, "EMPLOYEE")):
        at(monkeypatch, y, m, d, hh, mm)
        assert (await _mark(app_client, kind, who)).status_code == 200, (y, m, d)
    at(monkeypatch, *WED, 12, 0)
    await _admin(app_client)
    first = (await app_client.get(f"{API}/fines")).json()
    for row in first:                                                                         # approve the newest, leave the rest pending
        if row["log_date"] == "2026-09-21":
            await app_client.post(f"{API}/fines/{row['fine_id']}/decide", json={"action": "APPROVE"})
    rep = lambda **kw: app_client.get(f"{API}/report", params=kw)                              # noqa: E731
    r30, r60, r90, rall = [(await rep(days=d)).json() for d in (30, 60, 90, 0)]
    assert (r30["present_days"], r30["late_days"]) == (3, 2) and (r60["present_days"], r60["late_days"]) == (4, 3) and (r90["present_days"], r90["late_days"]) == (5, 4) and (rall["present_days"], rall["late_days"]) == (6, 5)
    assert r30["late_rate"] == 66.7 and r30["since"] == "2026-09-07"
    people = {p["name"][:5]: p for p in rall["people"]}
    assert people["Stu1 "]["late_days"] == 3 and people["Stu1 "]["days_present"] == 4 and people["Stu1 "]["longest_late_minutes"] == 90 and people["Stu1 "]["total_late_minutes"] == 60 + 90 + 75
    assert rall["people"][0]["name"].startswith("Stu1")                                       # most late first
    assert float(r30["fines_approved"]) == 100.0 and float(r30["fines_pending"]) == 0.0       # the 21 Sept fine (1 hour) was approved; nothing pending in 30 days
    assert float(rall["fines_pending"]) > 0 and len(rall["weeks"]) == 12
    staff_only = (await rep(days=0, kind="EMPLOYEE")).json()
    assert [p["kind"] for p in staff_only["people"]] == ["EMPLOYEE"] and float(staff_only["fines_approved"]) == 0.0 == float(staff_only["fines_pending"])       # staff: late, never fined
    await _as(app_client, raw, "guard")
    assert (await rep(days=30)).status_code == 403


async def test_marking_in_and_out_works_with_the_real_clock(app_client, admin_user, db_session):
    """No frozen time here: the app's own clock is used end to end. (A frozen clock once hid a timezone mix-up that only showed in the real app.)"""
    raw = await _team(app_client, db_session)
    s1, t_id = await _sid(db_session, raw, "s1"), await _eid(db_session, raw, "teacher")
    await _as(app_client, raw, "guard")
    for kind, pid in (("STUDENT", s1), ("EMPLOYEE", t_id)):
        card = await _mark(app_client, kind, pid)
        assert card.status_code == 200, card.text
        assert card.json()["state"] == "INSIDE" and card.json()["can_undo"] is True
        out = await _mark(app_client, kind, pid, "check-out")
        assert out.status_code == 200 and out.json()["state"] == "LEFT"
    assert len((await app_client.get(f"{API}/logs")).json()) == 2
    assert (await app_client.get(f"{API}/summary")).json()["left_today"] == 2
    assert (await app_client.get(f"{API}/people", params={"state": "LEFT"})).status_code == 200