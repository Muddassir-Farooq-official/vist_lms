"""School mode wording in every portal's menu (templates/_nav.html applies `portal_nav`)."""
from __future__ import annotations

import re

from app.school.service import school_mode

WORDS = [("Courses", "Subjects"), ("Course", "Subject"), ("Programs", "Classes"), ("Program", "Class"), ("Semesters", "Classes"),
         ("Semester", "Class")]
HIDE = {"supplementary"}                         # university-only pages
SWAP = {"/dashboard/admin/programs": {"slug": "classes", "label": "Classes & sections", "href": "/dashboard/admin/classes"}}


def school_words(text: str) -> str:
    for old, new in WORDS:
        text = re.sub(rf"\b{old}\b", new, text)
        text = re.sub(rf"\b{old.lower()}\b", new.lower(), text)
    return text


def portal_nav(items):
    if not school_mode():
        return items
    out = []
    for item in items:
        if item.get("slug") in HIDE:
            continue
        item = {**item, **SWAP.get(item.get("href"), {})}
        if item.get("href") not in SWAP and item.get("slug") != "classes":
            item["label"] = school_words(item["label"])
            if item.get("short"):
                item["short"] = school_words(item["short"])
        out.append(item)
    return out
