"""Smart timetable (app/timetabling) end to end: a whole term generated as a draft with no clashes, teachers'
unavailable times and limits respected, labs as double periods in a lab, hand moves checked and pinned, and
publishing from a date that keeps every past class and its attendance, all or nothing."""
from __future__ import annotations

import itertools
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.academics.models import ClassSession, ClassSessionStatus, TimetableSlot
from app.core.clock import institute_today
from app.core.config import settings
from app.employees.models import Employee
from app.notifications.models import Notification
from app.timetabling.models import TimetableDraft
from tests.integration.test_announcements import _staff
from tests.integration.test_attendance_and_grading import _get_or_create_period, _login, _login_admin

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"
S = "/api/v1/academics/timetable/smart"
TERM = "Smart Fall"
TODAY = institute_today()
START = TODAY - timedelta(days=21)
END = TODAY + timedelta(days=70)


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _setup(client, db):
    await _login_admin(client)
    prog = (await client.post("/api/v1/programs", json={"code": "SCS", "name": "BS Smart", "duration_years": 4, "total_semesters": 8})).json()["id"]
    courses = {}
    for code, ch in (("SM101", 3), ("SM102", 3), ("SM103", 2)):
        cid = (await client.post(f"{A}/courses", json={"code": code, "title": code + " title", "credit_hours": ch})).json()["id"]
        assert (await client.post(f"{A}/courses/{cid}/programs", json={"program_id": prog, "semester_number": 1})).status_code in (200, 201)
        courses[code] = cid
    teachers, logins = {}, {}
    for i, code in enumerate(("STA", "STB", "STC")):
        logins[code] = await _staff(client, db, code, "TEACHER", f"35202-88{i}0001-1")
        teachers[code] = str((await db.execute(select(Employee).where(Employee.first_name == code))).scalar_one().id)
    offs = {}
    for key, course, sec, teacher in (("101A", "SM101", "A", "STA"), ("101B", "SM101", "B", "STA"), ("102A", "SM102", "A", "STB"),
                                      ("102B", "SM102", "B", "STB"), ("103A", "SM103", "A", "STC")):
        r = await client.post(f"{A}/offerings", json={"course_id": courses[course], "term_label": TERM, "section_label": sec,
                                                     "term_start_date": str(START), "term_end_date": str(END)})
        assert r.status_code == 201, r.text
        offs[key] = r.json()["id"]
        assert (await client.patch(f"{A}/offerings/{offs[key]}", json={"assigned_teacher_id": teachers[teacher]})).status_code == 200
    periods = []
    for h in range(8, 14):
        periods.append(await _get_or_create_period(client, f"{h:02d}:00:00", f"{h:02d}:50:00"))
    for name, kind in (("SR1", "CLASS"), ("SR2", "CLASS"), ("SLab", "LAB")):
        assert (await client.post(f"{A}/rooms", json={"name": name, "kind": kind, "seats": 40})).status_code in (200, 201)
    return offs, teachers, logins, periods


def _no_clash(view) -> None:
    teacher_of = {}
    for s in view["slots"]:
        teacher_of[s["id"]] = s["teacher"]
    cells = {}
    for s in view["slots"]:
        for pid in s["periods"]:
            cells.setdefault((s["day"], pid), []).append(s)
    for (_day, _pid), here in cells.items():
        for a, b in itertools.combinations(here, 2):
            assert a["room"] != b["room"], (a, b)
            assert a["teacher"] != b["teacher"], (a, b)
            if a["section"] == b["section"]:
                assert not set(a["classes"]) & set(b["classes"]), (a, b)
    per_day = {}
    for s in view["slots"]:
        key = (s["offering_id"], s["day"])
        assert key not in per_day, "a course twice in one day"
        per_day[key] = s


async def test_generate_edit_publish(app_client, admin_user, db_session):
    offs, teachers, logins, periods = await _setup(app_client, db_session)

    # SM103 is a lab: a double period in the lab; STB cannot teach on Mondays
    assert (await app_client.patch(f"{S}/sections/{offs['103A']}", json={"classes_per_week": 1, "double_period": True})).status_code == 200
    monday = [f"MONDAY|{p}" for p in periods]
    r = await app_client.put(f"{S}/teachers/{teachers['STB']}/availability", json={"blocked": monday, "max_per_day": 2})
    assert r.status_code == 200 and len(r.json()["blocked"]) == 6
    secs = (await app_client.get(f"{S}/sections", params={"terms": TERM})).json()
    assert {s["code"]: s["per_week"] for s in secs} == {"SM101": 3, "SM102": 3, "SM103": 1}

    # the term already has a live slot with a class held (attendance taken) two weeks ago
    live = await app_client.post(f"{A}/offerings/{offs['101A']}/timetable-slots", json={"day_of_week": "FRIDAY", "period_id": periods[5], "room": "SR1"})
    assert live.status_code == 201, live.text
    old_slot = live.json()["slot"]["id"]
    sessions = (await db_session.execute(select(ClassSession).where(ClassSession.timetable_slot_id == old_slot).order_by(ClassSession.session_date))).scalars().all()
    held = sessions[0]
    assert held.session_date < TODAY
    held.attendance_taken_at = held.created_at
    await db_session.commit()
    held_id = held.id

    g = await app_client.post(f"{S}/drafts", json={"terms": [TERM], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],
                                                   "max_per_day": 4, "max_in_row": 3})
    assert g.status_code == 200, g.text
    v = g.json()
    assert v["stats"]["classes_needed"] == 3 + 3 + 3 + 3 + 1 and v["stats"]["classes_placed"] == 13
    assert not [p for p in v["problems"] if not p.get("warning")]
    _no_clash(v)
    for s in v["slots"]:
        if s["code"] == "SM102":
            assert s["day"] != "MONDAY"                        # unavailable
        if s["code"] == "SM103":
            assert s["span"] == 2 and s["room"] == "SLab" and len(s["periods"]) == 2
        else:
            assert s["room"] != "SLab"
    stb_days = {}
    for s in v["slots"]:
        if s["code"] == "SM102":
            stb_days[s["day"]] = stb_days.get(s["day"], 0) + 1
    assert max(stb_days.values()) <= 2                         # STB's own limit
    # nothing live changed yet
    assert (await db_session.execute(select(func.count()).select_from(TimetableSlot))).scalar_one() == 1

    # a hand move into a clash is refused; a good one is pinned and survives "generate again"
    a = next(s for s in v["slots"] if s["code"] == "SM101" and s["section"] == "A")
    b = next(s for s in v["slots"] if s["code"] == "SM101" and s["section"] == "B")
    bad = await app_client.patch(f"{S}/drafts/{v['id']}/slots/{a['id']}", json={"day": b["day"], "period_id": b["period_id"], "room": "SR2"})
    assert bad.status_code == 422 and "SM101 B" in bad.json()["error"]["message"]
    busy = {(s["day"], pid) for s in v["slots"] for pid in s["periods"] if s["code"] in ("SM101", "SM103") or (s["section"] == "A")}
    a_days = {s["day"] for s in v["slots"] if s["offering_id"] == a["offering_id"] and s["id"] != a["id"]}
    target = next((d, p) for d in ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY") for p in periods
                  if d not in a_days and (d, p) not in busy)
    ok = await app_client.patch(f"{S}/drafts/{v['id']}/slots/{a['id']}", json={"day": target[0], "period_id": target[1], "room": "SR2"})
    assert ok.status_code == 200, ok.text
    moved = next(s for s in ok.json()["slots"] if s["id"] == a["id"])
    assert moved["pinned"] and (moved["day"], moved["period_id"], moved["room"]) == (target[0], target[1], "SR2")
    _no_clash(ok.json())

    again = await app_client.post(f"{S}/drafts", json={"terms": [TERM], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],
                                                       "max_per_day": 4, "max_in_row": 3, "keep_pinned_from": v["id"]})
    assert again.status_code == 200, again.text
    v2 = again.json()
    assert any(s["pinned"] and s["offering_id"] == a["offering_id"] and (s["day"], s["period_id"], s["room"]) == (target[0], target[1], "SR2") for s in v2["slots"])
    assert (await db_session.get(TimetableDraft, v["id"])).status == "DISCARDED"
    _no_clash(v2)

    # publish from next Monday: preview first, then for real, telling teachers
    start = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
    pv = (await app_client.get(f"{S}/drafts/{v2['id']}/publish-preview", params={"from_date": str(start)})).json()
    assert pv["live_slots"] == 1 and pv["draft_classes"] == 13 and pv["classes_held_before"] == 1
    assert (await app_client.post(f"{S}/drafts/{v2['id']}/publish", json={"from_date": str(TODAY - timedelta(days=1)), "notify": []})).status_code == 422
    pub = await app_client.post(f"{S}/drafts/{v2['id']}/publish", json={"from_date": str(start), "notify": ["teachers"]})
    assert pub.status_code == 200, pub.text
    assert pub.json()["told"] == {"teachers": 3}

    slots = (await db_session.execute(select(TimetableSlot))).scalars().all()
    assert len(slots) == 12 + 2                                # 12 single classes + the lab's two periods
    assert old_slot not in {str(s.id) for s in slots}
    kept = await db_session.get(ClassSession, held_id)          # the class already held is exactly as it was
    await db_session.refresh(kept)
    assert kept is not None and kept.attendance_taken_at is not None and kept.status == ClassSessionStatus.SCHEDULED
    before = (await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id == offs["101A"], ClassSession.session_date < start))).scalars().all()
    assert all(c.timetable_slot_id is None for c in before)    # earlier classes stay, detached from the removed slot
    after_new = (await db_session.execute(select(func.min(ClassSession.session_date)).where(ClassSession.timetable_slot_id.in_([s.id for s in slots])))).scalar_one()
    assert after_new >= start
    gone = (await db_session.execute(select(func.count()).select_from(ClassSession).where(
        ClassSession.course_offering_id == offs["101A"], ClassSession.session_date >= start, ClassSession.start_time == held.start_time,
        ClassSession.timetable_slot_id.is_(None), ClassSession.status == ClassSessionStatus.SCHEDULED))).scalar_one()
    assert gone == 0                                           # the old Friday classes from that date are off the timetable
    told = (await db_session.execute(select(func.count()).select_from(Notification).where(Notification.title == "New timetable"))).scalar_one()
    assert told == 3
    assert (await app_client.post(f"{S}/drafts/{v2['id']}/discard")).status_code == 422          # already published


async def test_publish_is_all_or_nothing(app_client, admin_user, db_session):
    offs, teachers, _l, periods = await _setup(app_client, db_session)
    v = (await app_client.post(f"{S}/drafts", json={"terms": [TERM], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]})).json()
    # after the draft was made, someone books a room it uses for another term at the same time of year
    first = v["slots"][0]
    other = (await app_client.post(f"{A}/courses", json={"code": "OTH1", "title": "Other", "credit_hours": 1})).json()["id"]
    o = (await app_client.post(f"{A}/offerings", json={"course_id": other, "term_label": "Other Term", "section_label": "Z",
                                                      "term_start_date": str(START), "term_end_date": str(END)})).json()["id"]
    assert (await app_client.post(f"{A}/offerings/{o}/timetable-slots", json={"day_of_week": first["day"], "period_id": first["period_id"],
                                                                             "room": first["room"]})).status_code == 201
    start = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
    r = await app_client.post(f"{S}/drafts/{v['id']}/publish", json={"from_date": str(start), "notify": ["teachers", "students", "parents"]})
    assert r.status_code == 422 and "Nothing was changed" in r.json()["error"]["message"]
    assert (await db_session.execute(select(func.count()).select_from(TimetableSlot).where(
        TimetableSlot.course_offering_id.in_(list(offs.values()))))).scalar_one() == 0
    assert (await db_session.get(TimetableDraft, v["id"])).status == "DRAFT"
    # and generating again works around the new booking
    v2 = (await app_client.post(f"{S}/drafts", json={"terms": [TERM], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]})).json()
    assert not any(s["day"] == first["day"] and first["period_id"] in s["periods"] and s["room"] == first["room"] for s in v2["slots"])
    assert (await app_client.post(f"{S}/drafts/{v2['id']}/publish", json={"from_date": str(start), "notify": []})).status_code == 200


async def test_teachers_set_their_own_times_unless_fixed(app_client, admin_user, db_session):
    _offs, teachers, logins, periods = await _setup(app_client, db_session)
    user, pw = logins["STA"]
    await app_client.post("/api/v1/auth/logout")
    await _login(app_client, user, pw)
    pw2 = "Teacher#Smart2026"
    assert (await app_client.post("/api/v1/auth/change-password", json={"current_password": pw, "new_password": pw2})).status_code == 204
    pw = pw2
    mine = (await app_client.get(f"{S}/my-availability")).json()
    assert mine["blocked"] == [] and not mine["locked"]
    r = await app_client.put(f"{S}/my-availability", json={"blocked": [f"TUESDAY|{periods[0]}"], "note": "Hospital duty"})
    assert r.status_code == 200 and r.json()["blocked"] == [f"TUESDAY|{periods[0]}"]
    assert (await app_client.get(f"{S}/teachers")).status_code == 403          # admin list is not for teachers
    await app_client.post("/api/v1/auth/logout")
    await _login_admin(app_client)
    lst = {t["id"]: t for t in (await app_client.get(f"{S}/teachers")).json()}
    assert lst[teachers["STA"]]["blocked"] == 1
    assert (await app_client.put(f"{S}/teachers/{teachers['STA']}/availability",
                                 json={"blocked": [f"TUESDAY|{periods[0]}"], "locked": True})).status_code == 200
    await app_client.post("/api/v1/auth/logout")
    await _login(app_client, user, pw)
    refused = await app_client.put(f"{S}/my-availability", json={"blocked": []})
    assert refused.status_code == 422 and "fixed" in refused.json()["error"]["message"]
    bad = await app_client.put(f"{S}/my-availability", json={"blocked": ["FUNDAY|x"]})
    assert bad.status_code == 422


async def test_combined_class_across_batch_terms_is_placed_once_and_published_in_both(app_client, admin_user, db_session):
    from tests.integration.test_parallel_batches import ANS, DEN
    from tests.integration.test_parallel_batches import _setup as batch_setup

    offs, _t = await batch_setup(app_client, db_session)
    for h in range(8, 12):
        await _get_or_create_period(app_client, f"{h:02d}:00:00", f"{h:02d}:50:00")
    assert (await app_client.post(f"{A}/offerings/{offs['shr_den']}/combine", json={"offering_ids": [offs["shr_ans"]]})).status_code == 200
    g = await app_client.post(f"{S}/drafts", json={"terms": [DEN, ANS], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]})
    assert g.status_code == 200, g.text
    v = g.json()
    shared = [s for s in v["slots"] if s["code"] == "AHS401"]
    assert len(shared) == 2 and all(len(s["combined"]) == 2 for s in shared)        # 2 credit hours, ONE class for both batches
    assert v["stats"]["units"] == 3 and v["stats"]["classes_placed"] == 6
    _no_clash(v)
    # the batches' own subjects never sit on the combined class (same students), and both batches see it
    cells = {(s["day"], s["period_id"]) for s in shared}
    assert not any((s["day"], s["period_id"]) in cells for s in v["slots"] if s["code"] != "AHS401")
    classes = {c for s in shared for c in s["classes"]}
    assert any("Dental" in c for c in classes) and any("Anesthesia" in c for c in classes)
    from app.academics.models import CourseOffering

    term = await db_session.get(CourseOffering, offs["shr_den"])
    start = max(TODAY, term.term_start_date) + timedelta(days=1)
    if term.term_end_date is not None and start > term.term_end_date:
        return                                                                       # the fixed test term is over: publish is covered above
    pub = await app_client.post(f"{S}/drafts/{v['id']}/publish", json={"from_date": str(start), "notify": []})
    assert pub.status_code == 200, pub.text


async def test_theory_and_lab_in_one_section_shows_lab_on_everyones_timetable(app_client, admin_user, db_session):
    """1.74.0: 3 classes a week with 1 lab = two single theory periods + one lab double period, and the lab periods
    carry a Lab label on the weekly timetable and in the class lists."""
    offs, teachers, logins, periods = await _setup(app_client, db_session)
    r = await app_client.patch(f"{S}/sections/{offs['101A']}", json={"classes_per_week": 3, "double_period": False, "lab_classes_per_week": 1})
    assert r.status_code == 200 and r.json()["lab_classes_per_week"] == 1, r.text
    bad = await app_client.patch(f"{S}/sections/{offs['101B']}", json={"classes_per_week": 3, "lab_classes_per_week": 3})
    assert bad.status_code == 422 and "All" in bad.json()["error"]["message"]
    # an older screen that does not send the labs field leaves them as they are
    assert (await app_client.patch(f"{S}/sections/{offs['101A']}", json={"classes_per_week": 3, "home_room": "SR1"})).json()["lab_classes_per_week"] == 1
    secs = {s["id"]: s for s in (await app_client.get(f"{S}/sections", params={"terms": TERM})).json()}
    assert secs[offs["101A"]]["lab_classes_per_week"] == 1 and not secs[offs["101A"]]["double_period"]

    g = await app_client.post(f"{S}/drafts", json={"terms": [TERM], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]})
    assert g.status_code == 200, g.text
    v = g.json()
    _no_clash(v)
    mine = [s for s in v["slots"] if s["offering_id"] == offs["101A"]]
    assert sorted(s["span"] for s in mine) == [1, 1, 2]
    for s in mine:
        assert (s["room"] == "SLab") == (s["span"] == 2), s
    assert next(c for c in v["courses"] if c["offering_id"] == offs["101A"])["labs"] == 1

    start = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
    assert (await app_client.post(f"{S}/drafts/{v['id']}/publish", json={"from_date": str(start), "notify": []})).status_code == 200
    week = (await app_client.get(f"{A}/timetable/weekly", params={"term_label": TERM})).json()
    entries = [e for c in week["cells"] for e in c["entries"] if e["offering_id"] == offs["101A"]]
    assert len(entries) == 4 and sum(e["is_lab"] for e in entries) == 2                 # the lab's two periods
    assert all(not e["is_lab"] for c in week["cells"] for e in c["entries"] if e["offering_id"] != offs["101A"])
    pdf = await app_client.get(f"{A}/timetable/weekly.pdf", params={"term_label": TERM})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    # the teacher's class list marks the lab periods too (two of every week's four classes)
    user, pw = logins["STA"]
    await app_client.post("/api/v1/auth/logout")
    await _login(app_client, user, pw)
    assert (await app_client.post("/api/v1/auth/change-password", json={"current_password": pw, "new_password": "Teacher#Smart2026"})).status_code == 204
    mine = [e for e in (await app_client.get(f"{A}/offerings/me/schedule")).json() if e["course_offering_id"] == offs["101A"]]
    first_week = [e for e in mine if start <= __import__("datetime").date.fromisoformat(e["session_date"]) < start + timedelta(days=7)]
    assert len(first_week) == 4 and sum(e["is_lab"] for e in first_week) == 2
    labs = sorted((e["session_date"], e["start_time"]) for e in first_week if e["is_lab"])
    assert labs[0][0] == labs[1][0]                                                        # the same day, back to back


async def test_catalog_three_plus_one_gives_three_theory_and_one_lab_and_suggests_when_a_section_differs(app_client, admin_user, db_session):
    """1.74.0: the course catalog says theory + lab credit hours (3+1 = 4 credit hours with 1 lab). Its sections get
    3 theory classes and 1 lab a week by default; a section set otherwise gets a suggestion and a warning."""
    offs, teachers, logins, periods = await _setup(app_client, db_session)
    bad = await app_client.post(f"{A}/courses", json={"code": "LB100", "title": "Too much lab", "credit_hours": 2, "lab_credit_hours": 3})
    assert bad.status_code == 422 and "3+1" in bad.json()["error"]["message"]
    r = await app_client.post(f"{A}/courses", json={"code": "LB101", "title": "Physiology", "credit_hours": 4, "lab_credit_hours": 1})
    assert r.status_code == 201 and r.json()["lab_credit_hours"] == 1, r.text
    cid = r.json()["id"]
    o = await app_client.post(f"{A}/offerings", json={"course_id": cid, "term_label": TERM, "section_label": "A",
                                                     "term_start_date": str(START), "term_end_date": str(END)})
    oid = o.json()["id"]
    assert (await app_client.patch(f"{A}/offerings/{oid}", json={"assigned_teacher_id": teachers["STC"]})).status_code == 200

    secs = {s["id"]: s for s in (await app_client.get(f"{S}/sections", params={"terms": TERM})).json()}
    assert secs[oid]["per_week"] == 4 and secs[oid]["labs"] == 1 and secs[oid]["suggest"] is None
    assert secs[offs["101A"]]["labs"] == 0 and secs[offs["101A"]]["suggest"] is None       # no lab in the catalog: nothing to say

    # someone sets the section to 4 classes with no labs: the screen and the draft both point at the catalog
    assert (await app_client.patch(f"{S}/sections/{oid}", json={"classes_per_week": 4, "lab_classes_per_week": 0})).status_code == 200
    sec = next(s for s in (await app_client.get(f"{S}/sections", params={"terms": TERM})).json() if s["id"] == oid)
    assert sec["labs"] == 0 and sec["suggest"]["theory"] == 3 and sec["suggest"]["labs"] == 1 and "3+1" in sec["suggest"]["text"]
    g = (await app_client.post(f"{S}/drafts", json={"terms": [TERM], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]})).json()
    assert any(p.get("warning") and p["offering_id"] == oid and "3+1" in p["reason"] for p in g["problems"])
    assert sorted(s["span"] for s in g["slots"] if s["offering_id"] == oid) == [1, 1, 1, 1]

    # one tap: as the catalog says
    ok = await app_client.patch(f"{S}/sections/{oid}", json={"classes_per_week": None, "double_period": False, "labs_as_catalog": True})
    assert ok.status_code == 200 and ok.json()["lab_classes_per_week"] is None and ok.json()["classes_per_week"] is None
    sec = next(s for s in (await app_client.get(f"{S}/sections", params={"terms": TERM})).json() if s["id"] == oid)
    assert sec["suggest"] is None and sec["labs"] == 1
    g2 = (await app_client.post(f"{S}/drafts", json={"terms": [TERM], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]})).json()
    assert sorted(s["span"] for s in g2["slots"] if s["offering_id"] == oid) == [1, 1, 1, 2]
    assert not any(p["offering_id"] == oid and "catalog" in p["reason"] for p in g2["problems"])

    # editing the split later keeps the total consistent
    e = await app_client.patch(f"{A}/courses/{cid}", json={"lab_credit_hours": 2})
    assert e.status_code == 200 and e.json()["credit_hours"] == 4 and e.json()["lab_credit_hours"] == 2
