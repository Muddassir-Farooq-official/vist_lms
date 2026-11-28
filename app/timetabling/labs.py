"""Labs on the timetable.

The course catalog says how a course is split: credit_hours is the total and lab_credit_hours how many of them are
lab / practical / skills, so a "3+1" course has 4 credit hours with 1 lab. By default its sections get one theory
class a week per theory credit hour and one lab (a double period) per lab credit hour: 3 theory classes + 1 lab.
A section can be set differently on the timetable screen; the screen then suggests the catalog's split.

A section is taught in one of three ways:
  * no labs: every weekly class is one period;
  * all labs ("Lab (double)" ticked, CourseOffering.double_period): every class is two back-to-back periods in a lab;
  * some labs (CourseOffering.lab_classes_per_week = n, or NULL = the course's lab credit hours): n of the weekly
    classes are lab double periods and the rest single theory periods, e.g. 4 classes with 1 lab = 3 singles + 1 double.

A published lab double period is stored as two ordinary weekly slots (and two class sessions), one per period, so
attendance and lecture pay work per period as before. Which slots are labs is worked out here rather than stored:
for an all-labs section every slot is a lab; for a some-labs section it is the slots that sit back to back with
another slot of the same section on the same day (a course has one class a day, so two joined periods on one day
can only be its lab).
"""
from __future__ import annotations

from datetime import time

JOINED_MIN = 15          # the same "back to back" gap the timetable maker uses


def catalog_split(course) -> tuple[int, int]:
    """(theory classes, labs) a week that the course catalog asks for."""
    total = (getattr(course, "credit_hours", None) or 3) if course is not None else 3
    lab = min(total, max(0, getattr(course, "lab_credit_hours", 0) or 0)) if course is not None else 0
    theory = min(5, total - lab)
    if theory + lab == 0:
        theory = 1
    return theory, lab


def default_per_week(course, offering) -> int:
    """Classes a week: the section's own number, else the catalog's theory classes + labs."""
    if getattr(offering, "classes_per_week", None):
        return offering.classes_per_week
    theory, lab = catalog_split(course)
    return max(1, theory + lab)


def labs_for(offering, need: int, course=None) -> int:
    """How many of `need` weekly classes are lab double periods."""
    if getattr(offering, "double_period", False):
        return need
    n = getattr(offering, "lab_classes_per_week", None)
    if n is None:
        n = catalog_split(course)[1] if course is not None else 0
    return max(0, min(int(n), need))


def has_labs(offering, course=None) -> bool:
    if getattr(offering, "double_period", False):
        return True
    n = getattr(offering, "lab_classes_per_week", None)
    if n is None:
        n = catalog_split(course)[1] if course is not None else 0
    return n > 0


def suggestion(course, offering) -> dict | None:
    """When a course has labs in the catalog (e.g. 3+1) and this section is set differently, what to change."""
    if course is None or not (getattr(course, "lab_credit_hours", 0) or 0):
        return None
    theory, lab = catalog_split(course)
    need = default_per_week(course, offering)
    have_lab = labs_for(offering, need, course)
    have = (need - have_lab, have_lab)
    if have == (theory, lab):
        return None
    def n(x, word):
        return f"{x} {word}" + ("" if x == 1 else "s")
    total_t = (course.credit_hours or 0) - (course.lab_credit_hours or 0)
    return {"classes_per_week": theory + lab, "labs": lab, "theory": theory,
            "text": f"{course.code} is {total_t}+{course.lab_credit_hours} in the course catalog: {n(theory, 'theory class')} and "
                    f"{n(lab, 'lab')} a week. This section is set to {n(have[0], 'theory class')} and {n(have[1], 'lab')}."}


def _m(t: time) -> int:
    return t.hour * 60 + t.minute


def lab_ids(offering, items, *, day, start, end, ident, course=None) -> set:
    """The ids of `items` (weekly slots or class sessions of ONE section) that are lab periods."""
    items = list(items)
    if not items or not has_labs(offering, course):
        return set()
    if getattr(offering, "double_period", False):
        return {ident(x) for x in items}
    by_day: dict = {}
    for x in items:
        by_day.setdefault(day(x), []).append(x)
    out: set = set()
    for xs in by_day.values():
        xs.sort(key=lambda x: _m(start(x)))
        for a, b in zip(xs, xs[1:]):
            gap = _m(start(b)) - _m(end(a))
            if 0 <= gap <= JOINED_MIN:
                out.add(ident(a))
                out.add(ident(b))
    return out


def lab_slot_ids(offering, slots, course=None) -> set:
    return lab_ids(offering, slots, day=lambda s: s.day_of_week, start=lambda s: s.start_time, end=lambda s: s.end_time,
                   ident=lambda s: s.id, course=course)


def lab_session_ids(offering, sessions, course=None) -> set:
    return lab_ids(offering, sessions, day=lambda s: s.session_date, start=lambda s: s.start_time, end=lambda s: s.end_time,
                   ident=lambda s: s.id, course=course)
