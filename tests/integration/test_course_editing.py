"""Fixing courses and curricula: move a course to another semester, take it out, fix its code, delete a mistake."""
from __future__ import annotations

import pytest

from tests.integration.test_attendance_and_grading import _login_admin, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"


async def _program(client, code, sems=8):
    r = await client.post("/api/v1/programs", json={"code": code, "name": f"{code} Program", "duration_years": 4, "total_semesters": sems})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _course(client, code, ch=3):
    r = await client.post(f"{A}/courses", json={"code": code, "title": f"{code} title", "credit_hours": ch})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_curriculum_move_and_remove(app_client, admin_user):
    await _login_admin(app_client)
    pid = await _program(app_client, "CE1")
    cid = await _course(app_client, "CE-101")
    assert (await app_client.post(f"{A}/courses/{cid}/programs", json={"program_id": pid, "semester_number": 1})).status_code == 201
    r = await app_client.patch(f"{A}/courses/{cid}/programs/{pid}", json={"semester_number": 3})
    assert r.status_code == 200 and r.json()["semester_number"] == 3
    assert (await app_client.patch(f"{A}/courses/{cid}/programs/{pid}", json={"semester_number": 9})).status_code == 422     # program has 8
    cur = (await app_client.get(f"{A}/programs/{pid}/curriculum")).json()
    assert [(c["course_code"], c["semester_number"]) for c in cur] == [("CE-101", 3)]
    assert (await app_client.delete(f"{A}/courses/{cid}/programs/{pid}")).status_code == 204
    assert (await app_client.get(f"{A}/programs/{pid}/curriculum")).json() == []


async def test_a_mistaken_course_can_be_renamed_and_deleted_until_it_is_offered(app_client, admin_user):
    await _login_admin(app_client)
    pid = await _program(app_client, "CE2")
    cid = await _course(app_client, "CE-21O")                                     # typo: letter O instead of zero
    await app_client.post(f"{A}/courses/{cid}/programs", json={"program_id": pid, "semester_number": 2})
    r = await app_client.patch(f"{A}/courses/{cid}", json={"code": "ce-210"})
    assert r.status_code == 200 and r.json()["code"] == "CE-210"
    assert (await app_client.delete(f"{A}/courses/{cid}")).status_code == 204
    assert (await app_client.get(f"{A}/courses/{cid}")).status_code == 404
    assert (await app_client.get(f"{A}/programs/{pid}/curriculum")).json() == []


async def test_an_offered_course_keeps_its_code_and_cannot_be_deleted(app_client, admin_user):
    off, _ = await _setup_offering_with_enrolled_students(app_client, code="CE3", n_students=1, cnic_base=5600)
    cid = (await app_client.get(f"{A}/offerings/{off}")).json()["course_id"]
    r = await app_client.patch(f"{A}/courses/{cid}", json={"code": "CE3-NEW"})
    assert r.status_code == 422 and "offered" in r.text
    d = await app_client.delete(f"{A}/courses/{cid}")
    assert d.status_code == 422 and "inactive" in d.text.lower()
    # the offering itself can still be edited (section, seats, enrolment)
    e = await app_client.patch(f"{A}/offerings/{off}", json={"section_label": "B", "capacity": 40, "is_open_for_enrollment": False})
    assert e.status_code == 200 and e.json()["section_label"] == "B" and e.json()["capacity"] == 40
    assert (await app_client.delete(f"{A}/offerings/{off}")).status_code == 422                     # it has a student: unenroll must be asked for


async def test_an_offering_with_students_is_deleted_by_unenrolling_them_when_nothing_was_recorded(app_client, admin_user, db_session):
    from sqlalchemy import select

    from app.academics.models import Enrollment
    from app.audit.models import AuditLog
    from app.notifications.models import Notification

    off, studs = await _setup_offering_with_enrolled_students(app_client, code="CE4", n_students=2, cnic_base=5700)
    chk = (await app_client.get(f"{A}/offerings/{off}/delete-check")).json()
    assert chk == {"can_delete": True, "enrolled": 2, "blockers": []}
    no = await app_client.delete(f"{A}/offerings/{off}")
    assert no.status_code == 422 and "unenroll" in no.text
    yes = await app_client.delete(f"{A}/offerings/{off}", params={"unenroll": "true"})
    assert yes.status_code == 204, yes.text
    assert (await app_client.get(f"{A}/offerings/{off}")).status_code == 404
    import uuid as _u

    assert (await db_session.execute(select(Enrollment).where(Enrollment.course_offering_id == _u.UUID(off)))).first() is None
    told = (await db_session.execute(select(Notification).where(Notification.title == "Removed from a course"))).scalars().all()
    assert len(told) == 2
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "course_offering.deleted"))).scalars().all()
    assert len(log[-1].before["unenrolled"]) == 2


async def test_an_offering_with_attendance_is_never_deleted(app_client, admin_user):
    from tests.integration.test_attendance_and_grading import _create_n_sessions

    off, studs = await _setup_offering_with_enrolled_students(app_client, code="CE5", n_students=1, cnic_base=5800)
    sessions = await _create_n_sessions(app_client, off, 1)
    r = await app_client.post(f"/api/v1/attendance/sessions/{sessions[0]}/mark", json={"present_student_ids": [studs[0][0]], "late_note": "test"})
    assert r.status_code == 200, r.text
    chk = (await app_client.get(f"{A}/offerings/{off}/delete-check")).json()
    assert chk["can_delete"] is False and "attendance" in chk["blockers"][0]
    d = await app_client.delete(f"{A}/offerings/{off}", params={"unenroll": "true"})
    assert d.status_code == 422 and "attendance" in d.text

async def test_unenrol_and_delete_works_with_the_live_databases_no_delete_rule(app_client, admin_user, db_session):
    """The live database refuses to delete enrolments (trigger vist_keep_history, migration a6c2d3e4f567); the test
    database is built without it, which is how "unexpected error" slipped through. Install the real rule here."""
    import uuid as _u

    from sqlalchemy import select, text

    from app.academics.models import Enrollment
    from tests.integration.test_attendance_and_grading import _create_n_sessions

    rule_sql = """
        CREATE OR REPLACE FUNCTION vist_keep_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF coalesce(current_setting('vist.allow_delete', true), '') = 'on' THEN RETURN OLD; END IF;
            RAISE EXCEPTION 'Rows in % are history and cannot be deleted (close or reverse them instead).', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$;"""
    await db_session.execute(text(rule_sql))
    await db_session.execute(text("DROP TRIGGER IF EXISTS trg_enrollments_keep_history ON enrollments"))
    await db_session.execute(text("CREATE TRIGGER trg_enrollments_keep_history BEFORE DELETE ON enrollments FOR EACH ROW EXECUTE FUNCTION vist_keep_history()"))
    await db_session.commit()
    try:
        off, studs = await _setup_offering_with_enrolled_students(app_client, code="CE9", n_students=3, cnic_base=5900)
        await _create_n_sessions(app_client, off, 4)                                  # a timetable and classes, nothing marked
        other, others = await _setup_offering_with_enrolled_students(app_client, code="CE8", n_students=1, cnic_base=5950)

        yes = await app_client.delete(f"{A}/offerings/{off}", params={"unenroll": "true"})
        assert yes.status_code == 204, yes.text
        assert (await db_session.execute(select(Enrollment).where(Enrollment.course_offering_id == _u.UUID(off)))).first() is None

        # everywhere else enrolments are still protected
        keep = (await db_session.execute(select(Enrollment).where(Enrollment.course_offering_id == _u.UUID(other)))).scalars().first()
        with pytest.raises(Exception, match="history"):
            await db_session.execute(text("DELETE FROM enrollments WHERE id = :i"), {"i": keep.id})
        await db_session.rollback()
    finally:
        await db_session.execute(text("DROP TRIGGER IF EXISTS trg_enrollments_keep_history ON enrollments"))
        await db_session.execute(text("DROP FUNCTION IF EXISTS vist_keep_history()"))
        await db_session.commit()
