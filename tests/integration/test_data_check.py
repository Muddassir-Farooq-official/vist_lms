"""Data checker (app/reports/datacheck.py): finds gaps and mistakes, links each to the page that fixes it, changes nothing."""
from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from app.students.models import Student
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_result_sheets import _class

pytestmark = pytest.mark.asyncio
D = "/api/v1/data-check"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def _rows(d, key):
    return next(c for c in d["checks"] if c["key"] == key)


async def test_finds_gaps_and_links_them(app_client, admin_user, db_session):
    offering, students, _a, _t = await _class(app_client, db_session, code="DCK", n=3, with_teacher=False)
    s1, s2, s3 = [await db_session.get(Student, uuid.UUID(s[0])) for s in students]
    ids = {s1.id, s2.id, s3.id}
    s1.contact_no = "12345"                              # not a mobile number
    s3.current_semester = None                           # no semester
    rolls = {s.id: s.roll_number for s in (s1, s2, s3)}
    await db_session.flush()
    await _login_admin(app_client)
    r = await app_client.get(D)
    assert r.status_code == 200, r.text
    d = r.json()
    assert {c["area"] for c in d["checks"]} == {"Students", "Staff", "Academics", "Fees"}
    mine = lambda key: {x["id"] for x in _rows(d, key)["rows"]} & {str(i) for i in ids}  # noqa: E731
    assert mine("s_bad_phone") == {str(s1.id)}
    assert mine("s_dup_name") == {str(i) for i in ids}                             # the test data gives them one name and birthday
    assert mine("s_no_class") == {str(s3.id)}
    assert mine("f_no_fee") == {str(i) for i in ids}                                # no fee structure made yet
    bad = next(x for x in _rows(d, "s_bad_phone")["rows"] if x["id"] == str(s1.id))
    assert bad["link"] == f"/dashboard/admin/students?q={rolls[s1.id]}" and bad["detail"] == "12345"
    subject = [x for x in _rows(d, "a_no_teacher")["rows"] if x["id"] == offering]
    assert subject == [] or subject[0]["link"].endswith(offering)                   # only listed when its term is open
    assert sum(a["problems"] for a in d["areas"]) == d["problems"] and all(c["count"] >= len(c["rows"]) for c in d["checks"])
    db_session.expire_all()
    assert (await db_session.get(Student, uuid.UUID(students[0][0]))).contact_no == "12345"     # nothing changed
    page = (await app_client.get("/dashboard/admin/data-check")).text
    assert "data-check.js" in page and 'data-nav="data-check"' in page


async def test_only_administrators(app_client, admin_user, db_session):
    from tests.integration.test_fees import _create_accounts_officer

    await _login_admin(app_client)
    await _create_accounts_officer(app_client, db_session, code="DCACC")
    assert (await app_client.get(D)).status_code == 403
