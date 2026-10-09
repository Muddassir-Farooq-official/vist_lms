"""An offering created without a teacher (or with the wrong one) can be fixed later,
and the offerings list says which program and semester each course belongs to."""
from __future__ import annotations

import pytest

from tests.integration.test_module3_sections_allocation import _admin, _course, _offering, _program, _teacher

pytestmark = pytest.mark.asyncio


async def test_assign_change_and_remove_teacher_later(app_client, admin_user, db_session):
    await _admin(app_client)
    program = await _program(db_session, "BSXT")
    cid = await _course(app_client, "TCH-301", program_id=str(program.id), semester=3)
    oid = await _offering(app_client, cid)                     # forgot the teacher

    listing = (await app_client.get("/api/v1/academics/offerings", params={"term_label": "Fall 2026"})).json()
    row = next(o for o in listing if o["id"] == oid)
    assert row["assigned_teacher_id"] is None
    assert row["curriculum"] == ["BSXT · Semester 3"]
    one = (await app_client.get(f"/api/v1/academics/offerings/{oid}")).json()
    assert one["curriculum"] == ["BSXT · Semester 3"]

    t1 = await _teacher(app_client, db_session, "TCHA", "35202-7100001-1")
    t2 = await _teacher(app_client, db_session, "TCHB", "35202-7100002-1")
    r = await app_client.patch(f"/api/v1/academics/offerings/{oid}", json={"assigned_teacher_id": t1})
    assert r.status_code == 200 and r.json()["assigned_teacher_id"] == t1
    r = await app_client.patch(f"/api/v1/academics/offerings/{oid}", json={"assigned_teacher_id": t2})
    assert r.status_code == 200 and r.json()["teacher_name"].startswith("TCHB")
    r = await app_client.patch(f"/api/v1/academics/offerings/{oid}", json={"assigned_teacher_id": None})
    assert r.status_code == 200 and r.json()["assigned_teacher_id"] is None


async def test_course_outside_any_curriculum_has_empty_list(app_client, admin_user):
    await _admin(app_client)
    cid = await _course(app_client, "TCH-ELEC")
    oid = await _offering(app_client, cid)
    assert (await app_client.get(f"/api/v1/academics/offerings/{oid}")).json()["curriculum"] == []


async def test_courses_page_has_the_teacher_picker(app_client, admin_user):
    await _admin(app_client)
    html = (await app_client.get("/dashboard/admin/courses")).text
    assert "/static/js/teacher-picker.js" in html and 'id="only-unassigned"' in html
    js = (await app_client.get("/static/js/courses.js")).text
    assert "TeacherPicker.open" in js