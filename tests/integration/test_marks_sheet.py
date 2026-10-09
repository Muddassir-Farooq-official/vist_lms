"""Marks sheet: download the roster x components CSV, fill it, upload it. Same rules as entering marks by hand."""
from __future__ import annotations

import csv
import io

import pytest

from app.core.config import settings
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_result_sheets import _as_teacher, _class

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def _fill(text, values):
    """values: {roll: [quiz, final]} -> CSV with those cells filled ('' leaves a cell blank), whatever the column order."""
    rows = list(csv.reader(io.StringIO(text)))
    quiz = next(i for i, h in enumerate(rows[0]) if "(/20)" in h)
    final = next(i for i, h in enumerate(rows[0]) if "(/100)" in h)
    for r in rows[1:]:
        if r[0] in values:
            r[quiz], r[final] = values[r[0]]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


async def _upload(client, offering, text, dry):
    return await client.post(f"{A}/offerings/{offering}/marks/upload", params={"dry_run": str(dry).lower()},
                             files={"file": ("marks.csv", text.encode(), "text/csv")})


async def test_download_fill_check_and_upload(app_client, admin_user, db_session):
    offering, students, assessments, teacher = await _class(app_client, db_session, code="MS1", n=3, cnic_base=7100)
    await _as_teacher(app_client, teacher)
    sheet = (await app_client.get(f"{A}/offerings/{offering}/marks.csv")).text
    header = sheet.splitlines()[0]
    assert header.startswith("roll_number,student_name,") and header.index("(/20)") < header.index("(/100)")   # course order: quiz added first
    rolls = [r[0] for r in list(csv.reader(io.StringIO(sheet)))[1:]]
    filled = _fill(sheet, {rolls[0]: ["18", "70"], rolls[1]: ["25", "50"], rolls[2]: ["15", ""]})   # 25 > 20: refused; blank final kept blank
    check = (await _upload(app_client, offering, filled, True)).json()
    assert check["rows_ok"] == 2 and check["marks"] == 3 and len(check["errors"]) == 1 and "between 0 and 20" in check["errors"][0]["message"]
    assert "18" not in (await app_client.get(f"{A}/offerings/{offering}/marks.csv")).text.split("\n")[1]       # dry run saved nothing
    done = (await _upload(app_client, offering, filled, False)).json()
    assert done["marks"] == 3
    again = list(csv.reader(io.StringIO((await app_client.get(f"{A}/offerings/{offering}/marks.csv")).text)))
    qi = next(i for i, h in enumerate(again[0]) if "(/20)" in h)
    fi = next(i for i, h in enumerate(again[0]) if "(/100)" in h)
    by_roll = {r[0]: [r[qi], r[fi]] for r in again[1:]}
    assert by_roll[rolls[0]] == ["18", "70"] and by_roll[rolls[1]] == ["", ""] and by_roll[rolls[2]] == ["15", ""]


async def test_bad_sheets_and_locked_marks(app_client, admin_user, db_session):
    offering, students, assessments, teacher = await _class(app_client, db_session, code="MS2", n=1, cnic_base=7200)
    await _as_teacher(app_client, teacher)
    bad = await _upload(app_client, offering, "name,x\nA,1\n", True)
    assert bad.status_code == 422 and "roll_number" in bad.text
    sheet = (await app_client.get(f"{A}/offerings/{offering}/marks.csv")).text
    wrong_col = sheet.replace("(/20)", "").replace(sheet.split(",")[2].split(" (")[0], "Viva", 1)
    assert (await _upload(app_client, offering, wrong_col, True)).status_code == 422
    stranger = sheet + "99999,Nobody,5,5\n"
    assert "not on this course's roster" in (await _upload(app_client, offering, stranger, True)).text
    roll = list(csv.reader(io.StringIO(sheet)))[1][0]
    await _upload(app_client, offering, _fill(sheet, {roll: ["10", "60"]}), False)
    assert (await app_client.post(f"/api/v1/results/offerings/{offering}/submit")).status_code == 200
    locked = await _upload(app_client, offering, _fill(sheet, {roll: ["11", "61"]}), False)
    assert locked.status_code == 422 and "submitted or published" in locked.text


async def test_only_the_assigned_teacher(app_client, admin_user, db_session):
    offering, _s, _a, _t = await _class(app_client, db_session, code="MS3", n=1, cnic_base=7300)
    other_offering, _s2, _a2, other_teacher = await _class(app_client, db_session, code="MS4", n=1, cnic_base=7400)
    await _as_teacher(app_client, other_teacher)
    assert (await app_client.get(f"{A}/offerings/{offering}/marks.csv")).status_code == 403