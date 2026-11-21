"""Teacher workload (app/timetabling/workload.py): credit hours a week while terms run, the institute's 12-18 range
(or a teacher's own), refusing a section above the most unless a reason is given, a combined class counting once,
suggestions and proposals for the least busy teachers, and what is free when."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.audit.models import AuditLog
from app.core.clock import institute_today
from app.core.config import settings
from app.employees.models import Employee
from tests.integration.test_announcements import _staff
from tests.integration.test_attendance_and_grading import _get_or_create_period, _login_admin

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"
S = "/api/v1/academics/timetable/smart"
TERM, LATER = "Load Fall", "Load Spring"
TODAY = institute_today()
START, END = TODAY - timedelta(days=10), TODAY + timedelta(days=80)


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "teacher_load_min_credit_hours", 12)
    monkeypatch.setattr(settings, "teacher_load_max_credit_hours", 18)


async def _setup(client, db, n_courses=8):
    await _login_admin(client)
    prog = (await client.post("/api/v1/programs", json={"code": "LDP", "name": "BS Load", "duration_years": 4, "total_semesters": 8})).json()["id"]
    courses = []
    for i in range(n_courses):
        cid = (await client.post(f"{A}/courses", json={"code": f"LD{i:02d}", "title": f"Load {i}", "credit_hours": 3})).json()["id"]
        assert (await client.post(f"{A}/courses/{cid}/programs", json={"program_id": prog, "semester_number": 1 + i % 2})).status_code in (200, 201)
        courses.append(cid)
    t = {}
    for i, code in enumerate(("LTA", "LTB", "LTC")):
        await _staff(client, db, code, "TEACHER", f"35202-77{i}0001-1")
        t[code] = str((await db.execute(select(Employee).where(Employee.first_name == code))).scalar_one().id)
    return courses, t


async def _offer(client, course, sec="A", term=TERM, start=START, end=END, teacher=None, reason=None):
    body = {"course_id": course, "term_label": term, "section_label": sec, "term_start_date": str(start), "term_end_date": str(end)}
    if teacher:
        body["assigned_teacher_id"] = teacher
    if reason:
        body["load_override_reason"] = reason
    return await client.post(f"{A}/offerings", json=body)


async def test_the_most_is_enforced_and_can_be_overridden_with_a_reason(app_client, admin_user, db_session):
    courses, t = await _setup(app_client, db_session)
    offs = []
    for i in range(6):                                                       # 6 x 3 = 18: exactly the most
        r = await _offer(app_client, courses[i], teacher=t["LTA"])
        assert r.status_code == 201, r.text
        offs.append(r.json()["id"])
    # a section in a term that does NOT run at the same time does not count
    later = await _offer(app_client, courses[6], term=LATER, start=END + timedelta(days=10), end=END + timedelta(days=100), teacher=t["LTA"])
    assert later.status_code == 201, later.text

    seventh = (await _offer(app_client, courses[6])).json()["id"]
    refused = await app_client.patch(f"{A}/offerings/{seventh}", json={"assigned_teacher_id": t["LTA"]})
    assert refused.status_code == 422
    e = refused.json()["error"]
    assert e["details"]["kind"] == "TEACHER_LOAD" and e["details"]["after"] == 21 and e["details"]["max"] == 18
    assert "18 credit hours" in e["message"] and "above the most of 18" in e["message"]
    assert (await _offer(app_client, courses[7], teacher=t["LTA"])).status_code == 422                 # creating too

    ok = await app_client.patch(f"{A}/offerings/{seventh}", json={"assigned_teacher_id": t["LTA"], "load_override_reason": "Visiting teacher on leave"})
    assert ok.status_code == 200, ok.text
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "teacher_load.override"))).scalar_one()
    assert log.after["reason"] == "Visiting teacher on leave" and log.after["load_after"] == 21

    # the report: LTA over, the others under; the one in the later term is not counted
    rep = (await app_client.get(f"{S}/workload", params={"terms": TERM})).json()
    rows = {r["id"]: r for r in rep["teachers"]}
    assert rows[t["LTA"]]["credit_hours"] == 21 and rows[t["LTA"]]["status"] == "OVER"
    assert rows[t["LTB"]]["credit_hours"] == 0 and rows[t["LTB"]]["status"] == "UNDER"
    assert rep["summary"]["over"] == 1


async def test_own_range_and_combined_class_counts_once(app_client, admin_user, db_session):
    courses, t = await _setup(app_client, db_session)
    assert (await app_client.put(f"{S}/workload/teachers/{t['LTB']}/range", json={"load_min": 3, "load_max": 6})).status_code == 200
    a = (await _offer(app_client, courses[0], "A", teacher=t["LTB"])).json()["id"]
    b = (await _offer(app_client, courses[0], "B")).json()["id"]
    # the same course's other section, combined with the first: one class, 3 credit hours, not 6
    assert (await app_client.post(f"{A}/offerings/{a}/combine", json={"offering_ids": [b]})).status_code == 200
    assert (await _offer(app_client, courses[1], teacher=t["LTB"])).status_code == 201                    # 3 + 3 = 6: fine
    r = await _offer(app_client, courses[2], teacher=t["LTB"])
    assert r.status_code == 422 and r.json()["error"]["details"]["max"] == 6                              # her own most
    rep = (await app_client.get(f"{S}/workload", params={"terms": TERM})).json()
    me = next(x for x in rep["teachers"] if x["id"] == t["LTB"])
    assert me["credit_hours"] == 6 and me["own_range"] and (me["min"], me["max"]) == (3, 6) and me["status"] == "OK"
    assert (await app_client.put(f"{S}/workload/teachers/{t['LTB']}/range", json={"load_min": 9, "load_max": 6})).status_code == 422


async def test_suggestions_proposals_and_assigning(app_client, admin_user, db_session):
    courses, t = await _setup(app_client, db_session)
    # LTA taught LD00 before (another term) and is busy now; LTC is free; LTB is nearly full
    assert (await _offer(app_client, courses[0], term="Old Term", start=TODAY - timedelta(days=400), end=TODAY - timedelta(days=300),
                         teacher=t["LTA"])).status_code == 201
    for i in range(1, 6):
        assert (await _offer(app_client, courses[i], teacher=t["LTB"])).status_code == 201              # 15
    target = (await _offer(app_client, courses[0])).json()["id"]
    c = (await app_client.get(f"{S}/workload/candidates/{target}")).json()
    by = {x["id"]: x for x in c["teachers"]}
    assert c["suggested"] == t["LTA"] and by[t["LTA"]]["taught_before"]
    assert by[t["LTB"]]["after"] == 18 and not by[t["LTB"]]["over"]

    # proposals for every section without a teacher keep everyone within their most
    extra = [(await _offer(app_client, courses[i], "B")).json()["id"] for i in range(6, 8)]
    p = (await app_client.get(f"{S}/workload/proposals", params={"terms": TERM})).json()
    assert {x["offering_id"] for x in p["proposals"]} == {target, *extra} and not p["left"]
    load = {t["LTB"]: 15}
    for x in p["proposals"]:
        load[x["teacher_id"]] = load.get(x["teacher_id"], 0) + x["credit_hours"]
        assert x["teacher_after"] <= x["teacher_max"]
    assert all(v <= 18 for v in load.values())
    done = await app_client.post(f"{S}/workload/assign", json={"items": [{"offering_id": x["offering_id"], "teacher_id": x["teacher_id"]} for x in p["proposals"]]})
    assert done.status_code == 200 and done.json()["assigned"] == 3
    rep = (await app_client.get(f"{S}/workload", params={"terms": TERM})).json()
    assert rep["unassigned"] == []

    # all or nothing: one item above the most refuses the whole batch
    more = [(await _offer(app_client, courses[i], "C")).json()["id"] for i in range(0, 2)]
    assert (await app_client.put(f"{S}/workload/teachers/{t['LTB']}/range", json={"load_max": 15})).status_code == 200
    bad = await app_client.post(f"{S}/workload/assign", json={"items": [{"offering_id": more[0], "teacher_id": t["LTC"]},
                                                                       {"offering_id": more[1], "teacher_id": t["LTB"]}]})
    assert bad.status_code == 422
    rep = (await app_client.get(f"{S}/workload", params={"terms": TERM})).json()
    assert {u["offering_id"] for u in rep["unassigned"]} == set(more), (rep["unassigned"], bad.json())


async def test_what_is_free(app_client, admin_user, db_session):
    courses, t = await _setup(app_client, db_session)
    p1 = await _get_or_create_period(app_client, "08:00:00", "08:50:00")
    await _get_or_create_period(app_client, "09:00:00", "09:50:00")
    for name in ("FR1", "FR2"):
        assert (await app_client.post(f"{A}/rooms", json={"name": name, "kind": "CLASS", "seats": 40})).status_code in (200, 201)
    o = (await _offer(app_client, courses[0], teacher=t["LTA"])).json()["id"]
    assert (await app_client.post(f"{A}/offerings/{o}/timetable-slots", json={"day_of_week": "MONDAY", "period_id": p1, "room": "FR1"})).status_code == 201
    assert (await app_client.put(f"{S}/teachers/{t['LTC']}/availability", json={"blocked": [f"MONDAY|{p1}"]})).status_code == 200
    f = await app_client.get(f"{S}/free", params={"terms": TERM})
    assert f.status_code == 200, f.text
    f = f.json()
    cell = f["cells"][f"MONDAY|{p1}"]
    assert [r["name"] for r in cell["free_rooms"]] == ["FR2"]
    free_t = {x["id"] for x in cell["free_teachers"]}
    assert t["LTA"] not in free_t and t["LTC"] not in free_t and t["LTB"] in free_t       # busy / unavailable / free
    assert cell["classes"] == ["LD00 A · FR1"]
    tue = f["cells"][f"TUESDAY|{p1}"]
    assert len(tue["free_rooms"]) == 2 and t["LTA"] in {x["id"] for x in tue["free_teachers"]}
    cls = next(c for c in f["classes"] if "Sem 1" in c)
    assert f["class_busy"][cls] == [f"MONDAY|{p1}"]


async def test_the_range_is_a_setting(app_client, admin_user, db_session):
    await _login_admin(app_client)
    r = await app_client.put("/api/v1/policy", json={"values": {"teacher_load_min_credit_hours": 20, "teacher_load_max_credit_hours": 18}})
    assert r.status_code == 422 and "cannot be more than" in r.text
    ok = await app_client.put("/api/v1/policy", json={"values": {"teacher_load_min_credit_hours": 10, "teacher_load_max_credit_hours": 16}})
    assert ok.status_code == 200
    assert (settings.teacher_load_min_credit_hours, settings.teacher_load_max_credit_hours) == (10, 16)
