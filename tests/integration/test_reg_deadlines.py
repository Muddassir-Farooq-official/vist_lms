"""University Reg No deadlines (app/students/regdeadline.py): a batch term's last day for Reg Nos (affiliated programs
only), reminders before it, the admit card held after it unless the administration exempts the student, the internal
award list's warning, and a deadline for a whole session at once."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select

from app.academics.terms import AcademicSession, AcademicTerm
from app.core.clock import institute_today
from app.core.config import settings
from app.exams.service import ExamScheduleService
from app.notifications.email_models import ServiceHeartbeat
from app.notifications.models import Notification
from app.sms.models import SmsMessage
from app.students.models import Student
from app.students.regdeadline import MARK, due_today, regno_reminder_round
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_grading_tables import _table
from tests.integration.test_result_sheets import _class

pytestmark = pytest.mark.asyncio
R = "/api/v1/results"
A = "/api/v1/academics"
D = "/api/v1/students/reg-numbers/deadlines"
X = "/api/v1/students/reg-numbers/exemptions"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def test_reminder_days():
    deadline = date(2026, 12, 21)                                    # a Monday
    assert due_today(deadline, date(2026, 12, 14))                   # 7 days left: daily
    assert due_today(deadline, date(2026, 12, 18))                   # Friday, 3 days left: daily
    assert due_today(deadline, date(2026, 12, 21))                   # the last day
    assert not due_today(deadline, date(2026, 12, 11))               # Friday, 10 days left: weekly only
    assert due_today(deadline, date(2026, 12, 7))                    # Monday, 14 days left
    assert due_today(deadline, date(2026, 12, 28)) and not due_today(deadline, date(2026, 12, 23))   # after it: Mondays


async def _held(db, student_id) -> list[str]:
    db.expire_all()
    return [w for w in await ExamScheduleService(db).withheld(student_id) if "Reg No" in w]


async def test_reg_no_deadline_reminds_holds_and_exempts(app_client, admin_user, db_session):
    offering, students, _assessments, _teacher = await _class(app_client, db_session, code="RGD", n=3, with_teacher=False)
    await _login_admin(app_client)
    s1, s2, s3 = [await db_session.get(Student, uuid.UUID(s[0])) for s in students]
    program_id = s1.program_id
    ids = (s1.id, s2.id, s3.id)
    s1_id, s2_id, s3_id = ids              # the objects expire when the test re-reads the database
    s1_roll = s1.roll_number
    table = (await app_client.post(f"{R}/grading-tables", json=_table("RGD body", body="Test University"))).json()
    assert (await app_client.put(f"{R}/programs/{program_id}/grading-table", json={"scheme_id": table["id"]})).status_code == 200
    assert (await app_client.put(f"{R}/programs/{program_id}/exam-body", json={"exam_body": "AFFILIATED", "university_parts": ["FINAL"]})).status_code == 200
    assert (await app_client.put(f"/api/v1/students/{s1_id}/reg-number", json={"reg_number": "TU-RGD-001"})).status_code == 200

    today = institute_today()
    term = (await app_client.post(f"{A}/academic-terms", json={"label": "RGD 1st Fall 2026", "start_date": str(today - timedelta(days=30)),
                                                               "end_date": str(today + timedelta(days=90)), "program_id": str(program_id),
                                                               "semester": 1})).json()["id"]
    # a program whose exams the institute holds itself needs no Reg No
    other = (await app_client.post("/api/v1/programs", json={"code": "RGX", "name": "RGX", "duration_years": 2, "total_semesters": 4})).json()["id"]
    other_term = (await app_client.post(f"{A}/academic-terms", json={"label": "RGX 1st Fall 2026", "start_date": str(today - timedelta(days=30)),
                                                                     "end_date": str(today + timedelta(days=90)), "program_id": other, "semester": 1})).json()["id"]
    refused = await app_client.put(D, json={"term_id": other_term, "reg_deadline": str(today + timedelta(days=5))})
    assert refused.status_code == 422 and "needs no university Reg No" in refused.text
    assert (await app_client.put(D, json={"reg_deadline": str(today)})).status_code == 422          # neither a term nor a session

    # before the deadline: reminders, no hold
    r = await app_client.put(D, json={"term_id": term, "reg_deadline": str(today + timedelta(days=5))})
    assert r.status_code == 200 and r.json()["changed"] == ["RGD 1st Fall 2026"]
    row = next(t for t in (await app_client.get(D)).json()["terms"] if t["term_id"] == term)
    assert row["students"] == 3 and row["missing"] == 2 and row["days_left"] == 5 and s1_roll not in row["missing_roll_numbers"]
    assert await _held(db_session, s2_id) == []

    await db_session.execute(delete(ServiceHeartbeat).where(ServiceHeartbeat.name == MARK))
    early = datetime.combine(today, time(8, 0))
    assert await regno_reminder_round(db_session, now=early) == 0                     # before 10:00: nothing yet
    now = datetime.combine(today, time(11, 0))
    assert await regno_reminder_round(db_session, now=now) > 0
    staff = (await db_session.execute(select(Notification).where(Notification.title == "Reg Nos missing", Notification.user_id == admin_user.id))).scalars().all()
    assert len(staff) == 1 and "RGD 1st Fall 2026: 2 student(s)" in staff[0].message and "5 day(s) left" in staff[0].message
    sms = (await db_session.execute(select(SmsMessage).where(SmsMessage.event == "REG_NO", SmsMessage.student_id.in_(ids)))).scalars().all()
    assert {m.student_id for m in sms} == {s2_id, s3_id} and {m.recipient for m in sms} == {"STUDENT"}           # never the parents
    assert all("admit card will be held" in m.body for m in sms)
    assert await regno_reminder_round(db_session, now=now) == 0                       # once a day

    # after the deadline: the admit card is held
    assert (await app_client.put(D, json={"term_id": term, "reg_deadline": str(today - timedelta(days=1))})).status_code == 200
    assert len(await _held(db_session, s2_id)) == 1 and len(await _held(db_session, s3_id)) == 1
    assert await _held(db_session, s1_id) == []                                     # has a Reg No
    check = (await app_client.get(f"{R}/sheets/{offering}/reg-check")).json()
    assert check["affiliated"] is True and check["missing"] == 2 and check["reg_deadline"] == str(today - timedelta(days=1))
    pdf = await app_client.get(f"{R}/sheets/{offering}/internal-award-list.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # the administration exempts one student, with a reason; it is logged and can be revoked
    assert (await app_client.post(X, json={"student_id": str(s2_id), "reason": "x"})).status_code == 422             # too short
    past = await app_client.post(X, json={"student_id": str(s2_id), "reason": "University has not issued it yet", "until": str(today - timedelta(days=1))})
    assert past.status_code == 422
    ex = await app_client.post(X, json={"student_id": str(s2_id), "reason": "University has not issued it yet", "until": str(today + timedelta(days=10))})
    assert ex.status_code == 201, ex.text
    assert await _held(db_session, s2_id) == [] and len(await _held(db_session, s3_id)) == 1
    again = await app_client.post(X, json={"student_id": str(s2_id), "reason": "Second time"})
    assert again.status_code == 422 and "already exempted" in again.text
    listed = [e for e in (await app_client.get(X)).json() if e["student_id"] in {str(i) for i in ids}]
    assert [e["student_id"] for e in listed] == [str(s2_id)] and listed[0]["created_by"] and listed[0]["active"] is True
    row = next(t for t in (await app_client.get(D)).json()["terms"] if t["term_id"] == term)
    assert row["missing"] == 2 and row["missing_exempt"] == 1
    assert (await app_client.post(f"{X}/{ex.json()['id']}/revoke")).status_code == 200
    assert (await app_client.post(f"{X}/{ex.json()['id']}/revoke")).status_code == 422
    assert len(await _held(db_session, s2_id)) == 1
    old = [e for e in (await app_client.get(X, params={"include_old": "true"})).json() if e["student_id"] == str(s2_id)]
    assert len(old) == 1 and old[0]["active"] is False and old[0]["revoked_at"]
    assert all(e["student_id"] != str(s2_id) for e in (await app_client.get(X)).json())

    # entering the Reg No lifts the hold
    assert (await app_client.put(f"/api/v1/students/{s3_id}/reg-number", json={"reg_number": "TU-RGD-003"})).status_code == 200
    assert await _held(db_session, s3_id) == []

    # a whole session at once: only open terms of affiliated programs; then clearing it lifts every hold
    sess = AcademicSession(name="RGD Fall 2026")
    db_session.add(sess)
    await db_session.flush()
    sess_id = sess.id
    for tid in (term, other_term):
        (await db_session.get(AcademicTerm, uuid.UUID(tid))).session_id = sess_id
    await db_session.flush()
    whole = await app_client.put(D, json={"session_id": str(sess_id), "reg_deadline": str(today - timedelta(days=2))})
    assert whole.status_code == 200 and whole.json()["changed"] == ["RGD 1st Fall 2026"] and whole.json()["skipped"] == ["RGX 1st Fall 2026"]
    assert len(await _held(db_session, s2_id)) == 1
    cleared = await app_client.put(D, json={"session_id": str(sess_id), "reg_deadline": None})
    assert cleared.status_code == 200 and cleared.json()["changed"] == ["RGD 1st Fall 2026"]
    assert await _held(db_session, s2_id) == []
