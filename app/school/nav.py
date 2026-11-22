"""School mode wording in every portal's menu (templates/_nav.html applies `portal_nav`)."""
from __future__ import annotations

import re

from app.school.service import school_mode

WORDS = [("Courses", "Subjects"), ("Course", "Subject"), ("Programs", "Classes"), ("Program", "Class"), ("Semesters", "Classes"),
         ("Semester", "Class")]
HIDE = {"supplementary", "obe"}                        # university-only pages
# School mode only (stage 2): hidden in a college / university copy.
SCHOOL_ONLY = {"/dashboard/faculty/my-class", "/dashboard/faculty/homework", "/dashboard/student/diary", "/dashboard/parent/diary",
               "/dashboard/parent/meetings", "/dashboard/faculty/exam-marks", "/dashboard/student/report-cards", "/dashboard/parent/report-cards"}
# Per-period subject attendance: a school takes one morning roll call instead (My class / Diary & attendance).
COLLEGE_ONLY = {"/dashboard/faculty/attendance", "/dashboard/student/attendance",
                # ... and course grading / GPA results: a school has exam marks and report cards (stage 3)
                "/dashboard/faculty/grading", "/dashboard/student/results", "/dashboard/parent/marks"}
# ... and these take the attendance item's place in the phone bar.
PHONE_IN_SCHOOL = {"/dashboard/faculty/my-class", "/dashboard/student/diary", "/dashboard/faculty/exam-marks", "/dashboard/student/report-cards",
                   "/dashboard/parent/report-cards"}
SWAP = {"/dashboard/admin/programs": {"slug": "classes", "label": "Classes & sections", "href": "/dashboard/admin/classes"}}


def school_words(text: str) -> str:
    for old, new in WORDS:
        text = re.sub(rf"\b{old}\b", new, text)
        text = re.sub(rf"\b{old.lower()}\b", new.lower(), text)
    return text


def portal_nav(items):
    if not school_mode():
        return [i for i in items if i.get("href") not in SCHOOL_ONLY]
    out = []
    for item in items:
        if item.get("slug") in HIDE or item.get("href") in COLLEGE_ONLY:
            continue
        item = {**item, **SWAP.get(item.get("href"), {})}
        if item.get("href") in PHONE_IN_SCHOOL:
            item["mobile"] = True
        if item.get("href") not in SWAP and item.get("slug") != "classes":
            item["label"] = school_words(item["label"])
            if item.get("short"):
                item["short"] = school_words(item["short"])
        out.append(item)
    return out


def nav_sections(items) -> list[tuple[str | None, list[dict]]]:
    """The menu as sections: items without a group first (Home ...), then each group in the order it first appears, with
    its items together even when the list mixes them. Setup pages marked `hub` are kept (the menu's search finds them)."""
    order: list[str | None] = [None]
    by: dict[str | None, list[dict]] = {None: []}
    for item in items:
        g = item.get("group")
        if g not in by:
            by[g] = []
            order.append(g)
        by[g].append(item)
    return [(g, by[g]) for g in order if by[g]]
