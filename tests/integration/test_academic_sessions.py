"""Academic sessions (app/academics/sessions.py): batch terms ("BSN 6th Fall 2026") grouped into a session from their
names, batches guessed for terms without one, new terms joining their session, and starting the next session (next
batch terms, sections from the curriculum, promoted students enrolled) -- previewed first, nothing changed until then."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.academics.models import CourseOffering, Enrollment
from app.academics.terms import AcademicSession, AcademicTerm
from app.core.config import settings
from app.students.models import Student, StudentStatus
from tests.integration.test_attendance_and_grading import _login_admin, _student_payload

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"
S = "/api/v1/academics/sessions"
_N = [0]


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _term(client, label, start, end, program=None, semester=None):
    body = {"label": label, "start_date": start, "end_date": end}
    if program:
        body.update(program_id=program, semester=semester)
    r = await client.post(f"{A}/academic-terms", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _setup(client, db):
    await _login_admin(client)
    bsn = (await client.post("/api/v1/programs", json={"code": "BSN", "name": "BS Nursing", "duration_years": 4, "total_semesters": 8})).json()["id"]
    mlt = (await client.post("/api/v1/programs", json={"code": "MLT", "name": "BS MLT", "duration_years": 4, "total_semesters": 8})).json()["id"]
    courses = {}
    for code, prog, sem in (("N601", bsn, 6), ("N602", bsn, 6), ("N701", bsn, 7), ("N702", bsn, 7), ("N703", bsn, 7), ("N801", bsn, 8), ("M201", mlt, 2)):
        cid = (await client.post(f"{A}/courses", json={"code": code, "title": code, "credit_hours": 3})).json()["id"]
        assert (await client.post(f"{A}/courses/{cid}/programs", json={"program_id": prog, "semester_number": sem})).status_code in (200, 201)
        courses[code] = cid
    t = {
        "bsn6": await _term(client, "BSN 6th Fall 2026", "2026-09-01", "2027-01-15"),                 # no batch set (the mistake)
        "bsn8": await _term(client, "BSN 8th Fall 2026", "2026-09-01", "2027-01-15"),
        "mlt2": await _term(client, "MLT 2nd Fall 2026", "2026-09-07", "2027-01-20", mlt, 2),
        "old": await _term(client, "BSN 5th Spring 2026", "2026-02-01", "2026-06-30"),
        "odd": await _term(client, "Summer camp", "2026-07-01", "2026-07-31"),
        "ahs": await _term(client, "AHS Dental 4th", "2026-09-07", "2027-01-15"),      # no session in its name, Fall 2026 dates
    }
    for sec in ("A", "B"):
        for code in ("N601", "N602"):
            assert (await client.post(f"{A}/offerings", json={"course_id": courses[code], "term_label": "BSN 6th Fall 2026", "section_label": sec})).status_code == 201
    return {"bsn": bsn, "mlt": mlt}, courses, t


async def _students(client, db, program, semester, sections):
    ids = []
    for i, sec in enumerate(sections):
        _N[0] += 1
        r = await client.post("/api/v1/students", json=_student_payload(cnic=f"61101-{_N[0]:07d}-1", first_name=f"S{semester}{i}"))
        assert r.status_code == 201, r.text
        st = await db.get(Student, r.json()["id"])
        st.program_id, st.current_semester, st.section_label, st.status = program, semester, sec, StudentStatus.ACTIVE
        ids.append(st.id)
    await db.commit()
    return ids


async def test_suggest_group_and_new_terms_join(app_client, admin_user, db_session):
    progs, _c, t = await _setup(app_client, db_session)
    s = (await app_client.get(f"{S}/suggest")).json()
    names = {g["name"]: g for g in s["suggestions"]}
    assert set(names) == {"Fall 2026", "Spring 2026"}
    fall = {x["label"]: x for x in names["Fall 2026"]["terms"]}
    assert set(fall) == {"BSN 6th Fall 2026", "BSN 8th Fall 2026", "MLT 2nd Fall 2026", "AHS Dental 4th"}
    assert fall["AHS Dental 4th"]["by_dates"] and not fall["BSN 6th Fall 2026"].get("by_dates")
    assert fall["BSN 6th Fall 2026"]["batch_guess"] == {"program_id": progs["bsn"], "program": "BSN", "semester": 6}
    assert fall["MLT 2nd Fall 2026"]["batch_guess"] is None                       # already set
    assert [x["label"] for x in s["not_matched"]] == ["Summer camp"]           # its dates fall in no suggested session

    items = [{"name": g["name"], "term_ids": [x["id"] for x in g["terms"] if not x.get("by_dates")],
              "batches": {x["id"]: {"program_id": x["batch_guess"]["program_id"], "semester": x["batch_guess"]["semester"]}
                          for x in g["terms"] if x["batch_guess"]}} for g in s["suggestions"]]
    r = await app_client.post(f"{S}/suggest/save", json={"items": items})
    assert r.status_code == 200, r.text
    assert r.json() == {"sessions_created": 2, "terms_linked": 4, "batches_set": 3}
    bsn6 = await db_session.get(AcademicTerm, t["bsn6"])
    await db_session.refresh(bsn6)
    assert (str(bsn6.program_id), bsn6.semester) == (progs["bsn"], 6)

    g = (await app_client.get(S)).json()
    by = {x["name"]: x for x in g["sessions"]}
    assert by["Fall 2026"]["terms"] == ["BSN 6th Fall 2026", "BSN 8th Fall 2026", "MLT 2nd Fall 2026"]
    assert g["ungrouped"] == ["AHS Dental 4th", "Summer camp"]                   # the by-dates one was left unticked
    assert [x["name"] for x in g["sessions"]].index("Fall 2026") < [x["name"] for x in g["sessions"]].index("Spring 2026") \
        or by["Fall 2026"]["when"] == "past"

    # a new batch term joins its session by its name; nothing else about it changes
    await _term(app_client, "BSN 2nd Fall 2026", "2026-09-01", "2027-01-15")
    g = (await app_client.get(S)).json()
    assert "BSN 2nd Fall 2026" in next(x for x in g["sessions"] if x["name"] == "Fall 2026")["terms"]

    # removing a session only removes the grouping
    sid = by["Spring 2026"]["id"]
    assert (await app_client.delete(f"{S}/{sid}")).status_code == 200
    assert await db_session.get(AcademicTerm, t["old"]) is not None
    assert (await db_session.execute(select(func.count()).select_from(AcademicSession))).scalar_one() == 1


async def test_start_next_session(app_client, admin_user, db_session):
    progs, _c, t = await _setup(app_client, db_session)
    s = (await app_client.get(f"{S}/suggest")).json()
    fall = next(g for g in s["suggestions"] if g["name"] == "Fall 2026")
    await app_client.post(f"{S}/suggest/save", json={"items": [{"name": "Fall 2026", "term_ids": [x["id"] for x in fall["terms"]],
                                                                "batches": {x["id"]: x["batch_guess"] for x in fall["terms"] if x["batch_guess"]}}]})
    sid = next(x for x in (await app_client.get(S)).json()["sessions"] if x["name"] == "Fall 2026")["id"]
    promoted = await _students(app_client, db_session, progs["bsn"], 7, ["A", "A", "B"])          # already promoted to 7th
    await _students(app_client, db_session, progs["bsn"], 6, ["A"])                              # not promoted yet

    before = (await db_session.execute(select(func.count()).select_from(AcademicTerm))).scalar_one()
    pv = await app_client.post(f"{S}/{sid}/next-preview", json={"new_name": "Spring 2027"})
    assert pv.status_code == 200, pv.text
    rows = {r["label"]: r for r in pv.json()["rows"]}
    b6 = rows["BSN 6th Fall 2026"]
    assert b6["status"] == "NEW" and b6["new_label"] == "BSN 7th Spring 2027" and b6["new_semester"] == 7
    assert b6["sections"] == ["A", "B"] and b6["courses"] == ["N701", "N702", "N703"] and b6["promoted"] == 3 and b6["not_promoted"] == 1
    assert rows["BSN 8th Fall 2026"]["status"] == "LAST"
    assert rows["MLT 2nd Fall 2026"]["new_label"] == "MLT 3rd Spring 2027"
    assert (await db_session.execute(select(func.count()).select_from(AcademicTerm))).scalar_one() == before       # nothing made

    # a new name already used by another term is refused
    await _term(app_client, "Taken name", "2027-02-01", "2027-06-30")
    bad = await app_client.post(f"{S}/{sid}/next-preview", json={"new_name": "Spring 2027", "labels": {t["bsn6"]: "Taken name"}})
    assert bad.json()["blocked"]
    refused = await app_client.post(f"{S}/{sid}/start-next", json={"new_name": "Spring 2027", "labels": {t["bsn6"]: "Taken name"},
                                                                    "start_date": "2027-02-01", "end_date": "2027-06-30"})
    assert refused.status_code == 422

    go = await app_client.post(f"{S}/{sid}/start-next", json={"new_name": "Spring 2027", "start_date": "2027-02-01", "end_date": "2027-06-30"})
    assert go.status_code == 200, go.text
    out = go.json()
    assert out["terms_created"] == 2 and out["sections_opened"] == 3 * 2 + 0        # BSN 7th: 3 courses x A, B; MLT 3rd: no curriculum
    new = (await db_session.execute(select(AcademicTerm).where(AcademicTerm.label == "BSN 7th Spring 2027"))).scalar_one()
    assert (str(new.program_id), new.semester, new.start_date, new.end_date) == (progs["bsn"], 7, date(2027, 2, 1), date(2027, 6, 30))
    ses = await db_session.get(AcademicSession, new.session_id)
    assert ses.name == "Spring 2027"
    offs = (await db_session.execute(select(CourseOffering).where(CourseOffering.term_label == "BSN 7th Spring 2027"))).scalars().all()
    assert sorted((o.section_label for o in offs)) == ["A", "A", "A", "B", "B", "B"]
    # promoted students enrolled in their own section's courses only; the student still in 6th is not
    enr = (await db_session.execute(select(Enrollment.student_id, CourseOffering.section_label).join(
        CourseOffering, CourseOffering.id == Enrollment.course_offering_id).where(CourseOffering.term_label == "BSN 7th Spring 2027"))).all()
    assert len(enr) == 3 * 3 and out["enrolled"] == 9
    assert {sid_ for sid_, _ in enr} == set(promoted)
    secs = {}
    for st_id, sec in enr:
        secs.setdefault(st_id, set()).add(sec)
    assert secs[promoted[2]] == {"B"} and secs[promoted[0]] == {"A"}
    assert (await db_session.execute(select(AcademicTerm).where(AcademicTerm.label == "MLT 3rd Spring 2027"))).scalar_one().semester == 3


async def test_only_admins_change_sessions(app_client, admin_user, db_session):
    from tests.integration.test_announcements import _staff
    from tests.integration.test_attendance_and_grading import _login

    await _login_admin(app_client)
    user, pw = await _staff(app_client, db_session, "SESX", "TEACHER", "35202-6660001-1")
    await app_client.post("/api/v1/auth/logout")
    await _login(app_client, user, pw)
    await app_client.post("/api/v1/auth/change-password", json={"current_password": pw, "new_password": "Teacher#Ses2026"})
    await _login(app_client, user, "Teacher#Ses2026")
    assert (await app_client.get(S)).status_code == 200
    assert (await app_client.get(f"{S}/suggest")).status_code == 403
    assert (await app_client.post(S, json={"name": "Hack 2026"})).status_code == 403
