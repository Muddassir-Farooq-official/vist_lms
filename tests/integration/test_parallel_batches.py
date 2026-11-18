"""One term per batch ("AHS BS Dental 4th", "AHS BS Anesthesia 4th") running at the same time:
a shared subject can be one combined class across the two terms (no "teacher already booked"), a section in another
batch's term is not the same group of students, and the side-by-side view + auto-fill leave no student clash."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.academics.models import CourseOffering, TimetableSlot
from app.core.config import settings
from app.employees.models import Employee
from tests.integration.test_announcements import _staff
from tests.integration.test_attendance_and_grading import TERM_END, TERM_START, _get_or_create_period, _login_admin

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"
DEN, ANS = "AHS BS Dental 4th", "AHS BS Anesthesia 4th"


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _setup(client, db):
    await _login_admin(client)
    progs = {}
    for code, name in (("ANS", "BS Anesthesia"), ("DEN", "BS Dental")):
        progs[code] = (await client.post("/api/v1/programs", json={"code": code, "name": name, "duration_years": 4, "total_semesters": 8})).json()["id"]
    courses = {}
    for code, links in (("AHS401", ("ANS", "DEN")), ("DEN401", ("DEN",)), ("ANS401", ("ANS",))):
        cid = (await client.post(f"{A}/courses", json={"code": code, "title": code + " title", "credit_hours": 2})).json()["id"]
        for p in links:
            assert (await client.post(f"{A}/courses/{cid}/programs", json={"program_id": progs[p], "semester_number": 4})).status_code in (200, 201)
        courses[code] = cid
    teachers = {}
    for i, code in enumerate(("PTA", "PTD", "PTN")):
        await _staff(client, db, code, "TEACHER", f"35202-76{i}0001-1")
        teachers[code] = str((await db.execute(select(Employee).where(Employee.first_name == code))).scalar_one().id)
    offs = {}
    for key, course, term, teacher in (("shr_den", "AHS401", DEN, "PTA"), ("shr_ans", "AHS401", ANS, "PTA"),
                                       ("den", "DEN401", DEN, "PTD"), ("ans", "ANS401", ANS, "PTN")):
        r = await client.post(f"{A}/offerings", json={"course_id": courses[course], "term_label": term, "section_label": "A",
                                                     "term_start_date": str(TERM_START), "term_end_date": str(TERM_END)})
        assert r.status_code == 201, r.text
        offs[key] = r.json()["id"]
        assert (await client.patch(f"{A}/offerings/{offs[key]}", json={"assigned_teacher_id": teachers[teacher]})).status_code == 200
    for name in ("Room P1", "Room P2", "Room P3"):
        assert (await client.post(f"{A}/rooms", json={"name": name, "kind": "CLASS", "seats": 40})).status_code in (200, 201)
    return offs, teachers


async def test_shared_subject_combines_across_batch_terms_and_other_batches_are_other_students(app_client, admin_user, db_session):
    offs, _t = await _setup(app_client, db_session)
    p1 = await _get_or_create_period(app_client, "08:00:00", "09:00:00")
    p2 = await _get_or_create_period(app_client, "09:00:00", "10:00:00")
    slot = lambda off, period, room: app_client.post(f"{A}/offerings/{off}/timetable-slots", json={"day_of_week": "MONDAY", "period_id": period, "room": room})

    # the same teacher at the same time in two batch terms: refused while they are separate classes
    assert (await slot(offs["shr_den"], p1, "Room P1")).status_code == 201
    clash = await slot(offs["shr_ans"], p1, "Room P2")
    assert clash.status_code == 422 and "teacher" in clash.text.lower()

    # the other batch's section of the same course can now be combined with it (different term, same dates)
    view = (await app_client.get(f"{A}/offerings/{offs['shr_den']}/combined")).json()
    assert [(m["term_label"], m["section_label"]) for m in view["can_join"]] == [(ANS, "A")]
    ok = await app_client.post(f"{A}/offerings/{offs['shr_den']}/combine", json={"offering_ids": [offs["shr_ans"]]})
    assert ok.status_code == 200, ok.text
    mirrored = (await db_session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == offs["shr_ans"]))).scalars().all()
    assert [(s.day_of_week.value, s.start_time.hour) for s in mirrored] == [("MONDAY", 8)]

    # Dental's own subject at P2, and Anesthesia's own subject at P2 too: different batches, different students, no clash
    # (before, AHS401 being linked to both programs made the Dental term's section look like the Anesthesia students)
    assert (await slot(offs["den"], p2, "Room P2")).status_code == 201
    assert (await slot(offs["ans"], p2, "Room P3")).status_code == 201

    # a batch whose dates do not overlap cannot be combined
    other = (await app_client.post(f"{A}/offerings", json={"course_id": (await app_client.get(f"{A}/offerings/{offs['den']}")).json()["course_id"],
                                                          "term_label": "AHS BS Dental 6th", "section_label": "A",
                                                          "term_start_date": "2027-03-01", "term_end_date": "2027-06-30"})).json()["id"]
    refused = await app_client.post(f"{A}/offerings/{offs['den']}/combine", json={"offering_ids": [other]})
    assert refused.status_code == 422 and "same time of year" in refused.text


async def test_side_by_side_view_counts_free_periods_and_auto_fill_places_without_clashes(app_client, admin_user, db_session):
    offs, teachers = await _setup(app_client, db_session)
    p1 = await _get_or_create_period(app_client, "08:00:00", "09:00:00")
    await _get_or_create_period(app_client, "09:00:00", "10:00:00")
    assert (await app_client.post(f"{A}/offerings/{offs['shr_den']}/combine", json={"offering_ids": [offs["shr_ans"]]})).status_code == 200
    assert (await app_client.post(f"{A}/offerings/{offs['shr_den']}/timetable-slots",
                                  json={"day_of_week": "MONDAY", "period_id": p1, "room": "Room P1"})).status_code == 201

    v = await app_client.get(f"{A}/timetable/parallel", params={"terms": f"{DEN}||{ANS}"})
    assert v.status_code == 200, v.text
    v = v.json()
    week = len(v["days"]) * len(v["periods"])
    tracks = {t["term"]: t for t in v["tracks"]}
    assert set(tracks) == {DEN, ANS}
    for t in tracks.values():                      # the combined class fills one period in BOTH batches
        assert t["busy"] == 1 and t["free"] == week - 1 and t["missing_classes"] == 1 + 2 and not t["clashes"]
        cell = t["cells"][f"MONDAY|{p1}"][0]
        assert cell["code"] == "AHS401" and cell["combined_with"]

    preview = (await app_client.post(f"{A}/timetable/parallel/auto-fill", json={"terms": [DEN, ANS], "preview": True})).json()
    assert preview["preview"] is True and not preview["unplaced"]
    assert sorted(p["code"] for p in preview["planned"]) == ["AHS401", "ANS401", "ANS401", "DEN401", "DEN401"]
    assert not (await db_session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == offs["den"]))).scalars().all()

    done = (await app_client.post(f"{A}/timetable/parallel/auto-fill", json={"terms": [DEN, ANS], "preview": False})).json()
    assert len(done["created"]) == 5 and not done["failed"]
    after = {t["term"]: t for t in (await app_client.get(f"{A}/timetable/parallel", params={"terms": f"{DEN}||{ANS}"})).json()["tracks"]}
    for t in after.values():
        assert t["missing_classes"] == 0 and t["busy"] == 4 and t["free"] == week - 4 and not t["clashes"]

    # no teacher teaches two different classes at once, and a combined class has the same slots in both batches
    db_session.expire_all()
    slots = (await db_session.execute(select(TimetableSlot, CourseOffering).join(CourseOffering, CourseOffering.id == TimetableSlot.course_offering_id)
                                      .where(CourseOffering.term_label.in_([DEN, ANS])))).all()
    seen = {}
    for s, o in slots:
        key = (str(o.assigned_teacher_id), s.day_of_week, s.start_time)
        group = o.combined_group_id or o.id
        assert seen.setdefault(key, group) == group
    shr = lambda oid: sorted((s.day_of_week.value, s.start_time) for s, o in slots if str(o.id) == oid)
    assert shr(offs["shr_den"]) == shr(offs["shr_ans"]) and len(shr(offs["shr_den"])) == 2
    assert teachers  # used above via the offerings
