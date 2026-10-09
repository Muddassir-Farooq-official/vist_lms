"""Importing past (pre-system) results: validation, all-or-nothing, dry run, idempotency, and that GPA/CGPA come out right."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_exam_controller import _exam_user
from tests.integration.test_fees import _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
URL = "/api/v1/results/history/import"
HEAD = "roll_number,term_label,term_start_date,course_code,course_title,credit_hours,percentage,letter_grade\n"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _students(client, n=2):
    out = []
    for i in range(n):
        sid, user = await _create_and_activate_student(client, cnic=f"74001-000000{i}-{i}")
        await _login_admin(client)
        out.append((sid, user, (await client.get(f"/api/v1/students/{sid}")).json()["roll_number"]))
    return out


async def _post(client, csv_text, dry_run):
    return await client.post(URL, params={"dry_run": str(dry_run).lower()}, files={"file": ("past.csv", csv_text.encode(), "text/csv")})


def _good(a, b):
    return HEAD + "\n".join([
        f"{a},Fall 2024,2024-09-02,CS101,,3,78,",            # existing course, percentage only  -> B+ 3.33
        f"{a},Fall 2024,2024-09-02,MA101,Calculus I,3,,F",   # new course, letter only, failed
        f"{a},Spring 2025,2025-02-03,MA101,Calculus I,3,88,A",  # retake replaces the F
        f"{a},Spring 2025,2025-02-03,EN101,English I,2,,A-",
        f"{b},Fall 2024,2024-09-02,CS101,,3,55,D+",
    ]) + "\n"


async def test_dry_run_then_import_gives_the_right_transcript_and_cgpa(app_client, admin_user):
    (sa, ua, ra), (_sb, _ub, rb) = await _students(app_client)
    await app_client.post("/api/v1/academics/courses", json={"code": "CS101", "title": "Programming Fundamentals", "credit_hours": 3})
    check = (await _post(app_client, _good(ra, rb), True)).json()
    assert check["ok"] and check["dry_run"] and check["to_import"] == 5 and check["students"] == 2
    assert check["new_courses"] == ["EN101", "MA101"] and check["terms"] == ["Fall 2024", "Spring 2025"]
    assert (await app_client.get(f"/api/v1/results/students/{sa}/transcript")).json()["terms"] == []   # dry run changed nothing

    done = (await _post(app_client, _good(ra, rb), False)).json()
    assert done["ok"] and not done["dry_run"] and done["to_import"] == 5
    t = (await app_client.get(f"/api/v1/results/students/{sa}/transcript")).json()
    assert [x["label"] for x in t["terms"]] == ["Fall 2024", "Spring 2025"]
    fall = t["terms"][0]
    assert {c["course_code"]: c["letter_grade"] for c in fall["courses"]} == {"CS101": "B+", "MA101": "F"}
    assert fall["gpa"] == 1.67
    ma = [c for term in t["terms"] for c in term["courses"] if c["course_code"] == "MA101"]
    assert [c["replaced"] for c in ma] == [True, False]
    assert t["summary"]["cgpa"] == 3.66 and t["summary"]["credits_earned"] == 8 and t["summary"]["courses_to_clear"] == 0

    # the student sees it too
    await _login(app_client, ua, "StudentPass123!")
    assert (await app_client.get("/api/v1/results/me/transcript")).json()["summary"]["cgpa"] == 3.66

    # importing the same file again doubles nothing
    await _login_admin(app_client)
    again = (await _post(app_client, _good(ra, rb), False)).json()
    assert again["ok"] and again["to_import"] == 0 and again["already_imported"] == 5


async def test_any_bad_row_rejects_the_whole_file(app_client, admin_user):
    (sa, _ua, ra), _ = await _students(app_client)
    bad = HEAD + "\n".join([
        f"{ra},Fall 2024,2024-09-02,PH101,Physics,3,80,A-",
        "99999,Fall 2024,2024-09-02,PH101,Physics,3,80,",       # unknown student
        f"{ra},Fall 2024,2024-09-02,CH101,Chemistry,3,80,C",   # 80% is A-, not C
        f"{ra},Next Term,2099-01-01,BI101,Biology,3,70,",      # future term
        f"{ra},Fall 2024,2024-09-02,GE101,,3,70,",              # new course without title
        f"{ra},Fall 2024,2024-09-02,PH101,Physics,3,80,A-",    # duplicate row
    ]) + "\n"
    r = (await _post(app_client, bad, False)).json()
    assert r["ok"] is False and r["to_import"] == 0
    fields = {(e["row"], e["field"]) for e in r["errors"]}
    assert {(3, "roll_number"), (4, "letter_grade"), (5, "term_start_date"), (6, "course_title"), (7, "course_code")} <= fields
    assert (await app_client.get(f"/api/v1/results/students/{sa}/transcript")).json()["terms"] == []


async def test_wrong_header_and_existing_course_credit_mismatch(app_client, admin_user):
    (_sa, _ua, ra), _ = await _students(app_client)
    hdr = (await _post(app_client, "roll,course\n1,2\n", True)).json()
    assert hdr["ok"] is False and "Missing column" in hdr["errors"][0]["message"]
    await app_client.post("/api/v1/academics/courses", json={"code": "CS201", "title": "Data Structures", "credit_hours": 3})
    r = (await _post(app_client, HEAD + f"{ra},Fall 2024,2024-09-02,CS201,,4,70,\n", True)).json()
    assert r["ok"] is False and "3 credit hours in the system" in r["errors"][0]["message"]


async def test_template_and_only_admin_can_import(app_client, admin_user, db_session):
    await _login_admin(app_client)
    tpl = await app_client.get("/api/v1/results/history/template.csv")
    assert tpl.status_code == 200 and tpl.text.startswith("roll_number,term_label")
    user, pw = await _exam_user(app_client, db_session)
    await _login(app_client, user, pw)
    assert (await _post(app_client, HEAD, True)).status_code == 403


async def test_imported_results_stay_off_the_result_approval_screen(app_client, admin_user):
    (_sa, _ua, ra), _ = await _students(app_client)
    assert (await _post(app_client, HEAD + f"{ra},Fall 2024,2024-09-02,ZZ101,Old Course,3,70,\n", False)).json()["ok"]
    assert (await app_client.get("/api/v1/results/sheets")).json() == []