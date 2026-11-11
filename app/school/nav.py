"""School mode wording in every portal's menu (templates/_nav.html applies `portal_nav`)."""
from __future__ import annotations

import re

from app.school.service import school_mode

WORDS = [("Courses", "Subjects"), ("Course", "Subject"), ("Programs", "Classes"), ("Program", "Class"), ("Semesters", "Classes"),
         ("Semester", "Class")]
HIDE = {"supplementary"}                         # university-only pages
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
