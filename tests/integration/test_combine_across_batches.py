"""The same course in two batch terms taught together (LB101 for BSN and for AHS 1st: one teacher, one room, one time).
Adding the second batch's class where the first already is no longer just says "teacher busy / room booked": it says
the same course is there and offers to combine (nothing is merged unasked). The Timetable wizard suggests such
sections, shows who is combined with whom, and the draft notes same-teacher sections that are not combined."""
from __future__ import annotations

import pytest

from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_smart_timetable import _q as _q  # noqa: F401  (rate limits off)
from tests.integration.test_smart_timetable import END, START, A, S, TERM, _setup

pytestmark = pytest.mark.asyncio
OTHER = "Smart Nursing 1st"
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]


async def _twin_sections(client, teacher):
    """LB101 in TERM and in OTHER (overlapping dates), both with the same teacher."""
    course = (await client.post(f"{A}/courses", json={"code": "LB101", "title": "Library Lesson", "credit_hours": 1})).json()["id"]
    ids = {}
    for term in (TERM, OTHER):
        r = await client.post(f"{A}/offerings", json={"course_id": course, "term_label": term, "section_label": "A",
                                                     "term_start_date": str(START), "term_end_date": str(END)})
        assert r.status_code == 201, r.text
        ids[term] = r.json()["id"]
        assert (await client.patch(f"{A}/offerings/{ids[term]}", json={"assigned_teacher_id": teacher})).status_code == 200
    return ids


async def test_the_clash_offers_to_combine_and_combining_takes_the_class(app_client, admin_user, db_session):
    _offs, teachers, _l, periods = await _setup(app_client, db_session)
    ids = await _twin_sections(app_client, teachers["STC"])
    slot = {"day_of_week": "THURSDAY", "period_id": periods[5], "room": "SR2"}
    first = await app_client.post(f"{A}/offerings/{ids[TERM]}/timetable-slots", json=slot)
    assert first.status_code == 201, first.text

    r = await app_client.post(f"{A}/offerings/{ids[OTHER]}/timetable-slots", json=slot)
    assert r.status_code == 422
    err = r.json()["error"]
    assert "LB101" in err["message"] and TERM in err["message"] and "combine" in err["message"].lower()
    c = err["details"]["combine"]
    assert c["offering_id"] == ids[OTHER] and c["with_offering_id"] == ids[TERM] and c["with_term"] == TERM

    # what the Combine button does
    done = await app_client.post(f"{A}/offerings/{c['with_offering_id']}/combine", json={"offering_ids": [c["offering_id"]]})
    assert done.status_code == 200, done.text
    other_slots = (await app_client.get(f"{A}/offerings/{ids[OTHER]}/timetable-slots")).json()
    assert len(other_slots) == 1 and other_slots[0]["day_of_week"] == "THURSDAY" and other_slots[0]["room"] == "SR2"
    # the other batch's section is named with its term
    assert (await app_client.get(f"{A}/offerings/{ids[TERM]}")).json()["combined_with"] == [f"A ({OTHER})"]


async def test_a_clash_with_a_different_course_is_a_plain_clash(app_client, admin_user, db_session):
    offs, teachers, _l, periods = await _setup(app_client, db_session)
    ids = await _twin_sections(app_client, teachers["STA"])
    slot = {"day_of_week": "FRIDAY", "period_id": periods[0], "room": "SR1"}
    assert (await app_client.post(f"{A}/offerings/{offs['101A']}/timetable-slots", json=slot)).status_code == 201
    r = await app_client.post(f"{A}/offerings/{ids[OTHER]}/timetable-slots", json=slot)
    assert r.status_code == 422 and not (r.json()["error"].get("details") or {}).get("combine")


async def test_the_wizard_suggests_combining_and_the_draft_notes_it(app_client, admin_user, db_session):
    _offs, teachers, _l, _p = await _setup(app_client, db_session)
    ids = await _twin_sections(app_client, teachers["STC"])
    terms = f"{TERM}||{OTHER}"

    rd = (await app_client.get(f"{S}/readiness", params={"terms": terms})).json()
    sug = [s for s in rd["combine"] if s["course"] == "LB101"]
    assert len(sug) == 1 and sug[0]["same_teacher"] and {x["id"] for x in sug[0]["sections"]} == set(ids.values())
    rows = {x["id"]: x for x in (await app_client.get(f"{S}/sections", params={"terms": terms})).json()}
    assert rows[ids[OTHER]]["can_combine"] and rows[ids[OTHER]]["can_combine"]["with"] == [f"{TERM} (A)"]
    # only one of the batches chosen: nothing to suggest
    assert not [s for s in (await app_client.get(f"{S}/readiness", params={"terms": TERM})).json()["combine"] if s["course"] == "LB101"]

    g = await app_client.post(f"{S}/drafts", json={"terms": [TERM, OTHER], "days": DAYS})
    assert g.status_code == 200, g.text
    notes = [p for p in g.json()["problems"] if p.get("warning") and p["label"] == "LB101"]
    assert notes and "combine" in notes[0]["reason"]

    s = sug[0]
    assert (await app_client.post(f"{A}/offerings/{s['lead_id']}/combine", json={"offering_ids": s["join_ids"]})).status_code == 200
    rd = (await app_client.get(f"{S}/readiness", params={"terms": terms})).json()
    assert not [x for x in rd["combine"] if x["course"] == "LB101"]
    rows = {x["id"]: x for x in (await app_client.get(f"{S}/sections", params={"terms": terms})).json()}
    assert rows[ids[TERM]]["combined"] and rows[ids[TERM]]["combined_with"] == [f"{OTHER} (A)"] and rows[ids[TERM]]["can_combine"] is None


async def test_different_teachers_need_a_choice_and_the_exam_department_can_combine(app_client, admin_user, db_session):
    _offs, teachers, _l, _p = await _setup(app_client, db_session)
    ids = await _twin_sections(app_client, teachers["STA"])
    assert (await app_client.patch(f"{A}/offerings/{ids[OTHER]}", json={"assigned_teacher_id": teachers["STB"]})).status_code == 200
    rd = (await app_client.get(f"{S}/readiness", params={"terms": f"{TERM}||{OTHER}"})).json()
    s = next(x for x in rd["combine"] if x["course"] == "LB101")
    assert not s["same_teacher"] and len(s["teachers"]) == 2

    exam, first = await _staff(app_client, db_session, "EXCB", "EXAM_CONTROLLER", "35202-8810404-4")
    await _first_login(app_client, exam, first)
    no_choice = await app_client.post(f"{A}/offerings/{s['lead_id']}/combine", json={"offering_ids": s["join_ids"]})
    assert no_choice.status_code == 422 and "teacher" in no_choice.text.lower()
    ok = await app_client.post(f"{A}/offerings/{s['lead_id']}/combine", json={"offering_ids": s["join_ids"], "teacher_id": teachers["STA"]})
    assert ok.status_code == 200, ok.text
    assert (await app_client.post(f"{A}/offerings/{ids[OTHER]}/separate")).status_code == 200


async def test_sections_of_one_batch_are_not_suggested(app_client, admin_user, db_session):
    await _setup(app_client, db_session)          # SM101 A and B, SM102 A and B: all in one term
    rd = (await app_client.get(f"{S}/readiness", params={"terms": TERM})).json()
    assert rd["combine"] == []
