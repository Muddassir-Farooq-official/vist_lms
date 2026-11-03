"""Crowded pages are split into tabs: one concern per tab, one panel visible at a time."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

TABBED = {
    "/dashboard/student/courses": ["mine", "available", "browse"],
    "/dashboard/student/attendance": ["summary", "history"],
    "/dashboard/student/profile": ["details", "quals", "certs"],
    "/dashboard/student/complaints": ["overview", "mine", "new"],
    "/dashboard/student/library": ["borrowed", "requests", "history", "catalogue", "journals", "eresources"],
    "/dashboard/faculty/profile": ["details", "quals", "certs"],
    "/dashboard/faculty/complaints": ["overview", "mine", "new"],
    "/dashboard/admin/complaints": ["overview", "all", "people"],
    "/dashboard/admin/announcements": ["all", "compose", "officials"],
    "/dashboard/student/assignments": ["todo", "done", "closed"],
    "/dashboard/admin/applications": ["queue", "all", "overview", "types"],
    "/dashboard/admin/staff-attendance": ["daily", "month", "timings", "tools"],
    "/dashboard/admin/payroll": ["runs", "items", "advances", "security"],
    "/dashboard/admin/finance": ["overview", "book", "expenses", "purchasing", "income", "vendors", "categories"],
    "/dashboard/admin/result-sheets": ["SUBMITTED", "APPROVED", "PUBLISHED", "RETURNED", "OUTSTANDING", "CARDS", "SCALE"],
    "/dashboard/faculty/tasks": ["inbox", "todo", "sent", "new", "mine"],
    "/dashboard/library/tasks": ["inbox", "todo", "sent", "new", "mine"],
    "/dashboard/accounts/tasks": ["inbox", "todo", "sent", "new", "mine"],
    "/dashboard/admin/tasks": ["inbox", "todo", "sent", "new", "mine", "flags", "staff", "all", "todo-report"],
    "/dashboard/admin/activity": ["activity", "security"],
    "/dashboard/student/applications": ["mine", "new"],
    "/dashboard/faculty/applications": ["mine", "new"],
    "/dashboard/library/applications": ["mine", "new"],
    "/dashboard/accounts/applications": ["mine", "new"],
    "/dashboard/faculty/assignments": ["list", "review", "new"],
    "/dashboard/faculty/attendance": ["take", "requests"],
    "/dashboard/faculty/library": ["borrowed", "requests", "history", "catalogue", "journals", "eresources"],
    "/dashboard/admin/attendance": ["attention", "corrections", "mark", "lock", "holiday"],
    "/dashboard/admin/imports": ["upload", "history", "past-results"],
    "/dashboard/admin/programs": ["all", "add"],
    "/dashboard/admin/courses/offering/00000000-0000-0000-0000-000000000000": ["overview", "syllabus", "timetable", "sessions", "students"],
    "/dashboard/library/issue-return": ["issue", "issued"],
    "/dashboard/library/journals": ["journals", "eres"],
    "/dashboard/admin/timetable": ["weekly", "slots", "periods"],
    "/dashboard/student/timetable": ["weekly", "upcoming"],
    "/dashboard/faculty/timetable": ["weekly", "upcoming"],
}


@pytest.mark.parametrize("path", list(TABBED))
async def test_page_is_split_into_tabs(app_client, path):
    keys = TABBED[path]
    html = (await app_client.get(path)).text
    for key in keys:
        assert f'data-tab="{key}"' in html, f"{path}: tab button {key}"
        assert f'data-tab-panel="{key}"' in html, f"{path}: panel {key}"
    assert html.count("data-tab-panel=") == len(keys)
    # only the first panel is visible on arrival
    panels = [chunk for chunk in html.split("data-tab-panel=")[1:]]
    visible = [p for p in panels if not p.split(">", 1)[0].rstrip().endswith("hidden")]
    assert len(visible) == 1, f"{path}: {len(visible)} panels visible on arrival"


async def test_the_library_no_longer_crams_loans_requests_and_history_into_one_tab(app_client):
    for path in ("/dashboard/student/library", "/dashboard/faculty/library"):
        html = (await app_client.get(path)).text
        assert "my-library" not in html and 'data-tab="borrowed"' in html


async def test_the_still_working_note_cannot_linger_on_a_page_whose_data_has_arrived(app_client):
    js = (await app_client.get("/static/js/admin-common.js")).text
    # it is driven by what is STILL pending right now, re-checked every second, not by an old timer
    assert "net.inflight.forEach" in js and "setInterval(watchSlow, 1000)" in js and "SLOW_AFTER_MS = 8000" in js
    assert "if (!oldest || age < SLOW_AFTER_MS) { note.hidden = true; return; }" in js
    assert "net-slow-close" in js and "[slow request]" in js           # dismissible, and the slow request is named in the console
    assert "VA.showTab" in (await app_client.get("/static/js/student-attendance.js")).text
    assert "showTab: showTab" in js