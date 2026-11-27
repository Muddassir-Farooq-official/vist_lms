"""Parallel timetable: several batch terms side by side, e.g. "AHS BS Dental 4th", "AHS BS Anesthesia 4th" and
"AHS BS MLT 4th", which run at the same time and share some subjects (taught as combined classes).

  * view():      one column per term + section (one group of students), one row per day and period. A cell with no
                 class is a FREE period for those students, counted per column.
  * auto_fill(): proposes classes for the free periods. Each section still short of its weekly classes (classes per
                 week = credit hours, at most 5) is placed into a period that is free for its students, its teacher
                 and a room. Combined classes go first because one placement fills a free period in every batch.
                 preview=True only shows the plan; preview=False creates the slots (normal clash checks, one nested
                 transaction per class, so one refusal never undoes the rest).

Nothing here changes existing slots; auto-fill only ADDS classes. Free periods that remain after auto-fill mean the
batch has fewer weekly class hours than periods -- the report says so, so the admin can add a tutorial/lab/library
period or shorten the day.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.combined import members, same_group
from app.academics.models import Course, CourseOffering, DayOfWeek, Period, TimetableSlot
from app.academics.rooms import Room, room_key
from app.audit.service import AuditService
from app.core.timefmt import hm12, range12
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission, require_any_permission
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.notifications.changes import silently

WEEK = [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY,
        DayOfWeek.SATURDAY, DayOfWeek.SUNDAY]
MAX_TERMS = 12


def _overlap(s1, e1, s2, e2) -> bool:
    return s1 < e2 and s2 < e1


def _track(o: CourseOffering) -> tuple[str, str]:
    """One group of students: a term and a section ("AHS BS Dental 4th", "A")."""
    return ((o.term_label or "").strip().lower(), (o.section_label or "").strip().lower())


def per_week(course: Course | None) -> int:
    return max(1, min(5, (course.credit_hours if course and course.credit_hours else 3)))


class ParallelTimetableService:
    def __init__(self, session: AsyncSession) -> None:
        from app.academics.service import TimetableService

        self.session = session
        self.tt = TimetableService(session)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ loading
    async def _load(self, term_labels: list[str]) -> dict:
        labels = []
        for t in term_labels:
            t = " ".join((t or "").split())
            if t and t.lower() not in {x.lower() for x in labels}:
                labels.append(await self.tt.offering_repo.canonical_term_label(t))
        if not labels:
            raise ValidationAppError("Choose at least one term.")
        if len(labels) > MAX_TERMS:
            raise ValidationAppError(f"Choose at most {MAX_TERMS} terms at a time.")
        from sqlalchemy import func

        offerings = list((await self.session.execute(select(CourseOffering).where(
            func.lower(CourseOffering.term_label).in_([x.lower() for x in labels]))
            .order_by(CourseOffering.term_label, CourseOffering.section_label))).scalars())
        if not offerings:
            raise ResourceNotFoundError("No course offerings exist for the chosen terms.")
        periods = list((await self.session.execute(select(Period).order_by(Period.period_number))).scalars())
        if not periods:
            raise BusinessRuleViolationError("No periods have been defined yet. Add them on Timetable → Daily periods first.")
        ids = [o.id for o in offerings]
        slots = list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.in_(ids)))).scalars())
        courses = {c.id: c for c in (await self.session.execute(select(Course).where(
            Course.id.in_({o.course_id for o in offerings})))).scalars()}
        used_days = set((await self.session.execute(select(TimetableSlot.day_of_week).distinct())).scalars())
        days = [d for d in WEEK if d in WEEK[:5] or d in used_days]
        from app.employees.models import Employee

        teachers = {e.id: f"{e.first_name} {e.last_name}".strip() for e in (await self.session.execute(select(Employee).where(
            Employee.id.in_({o.assigned_teacher_id for o in offerings if o.assigned_teacher_id})))).scalars()}
        # the other sections of combined classes (they may be in a term that was not chosen)
        group_terms: dict[uuid.UUID, list[str]] = {}
        for o in offerings:
            if o.combined_group_id and o.combined_group_id not in group_terms:
                group_terms[o.combined_group_id] = sorted({m.term_label for m in await members(self.session, o)})
        return {"labels": labels, "offerings": offerings, "periods": periods, "slots": slots, "courses": courses,
                "days": days, "teachers": teachers, "group_terms": group_terms}

    @staticmethod
    def _cells_of(slot: TimetableSlot, periods: list[Period]) -> list[Period]:
        return [p for p in periods if _overlap(slot.start_time, slot.end_time, p.start_time, p.end_time)]

    # ------------------------------------------------------------------ view
    async def view(self, term_labels: list[str]) -> dict:
        d = await self._load(term_labels)
        offerings, periods, courses = d["offerings"], d["periods"], d["courses"]
        by_id = {o.id: o for o in offerings}
        tracks: dict[tuple, dict] = {}
        for o in offerings:
            key = _track(o)
            if key not in tracks:
                tracks[key] = {"term": o.term_label, "section": o.section_label, "cells": defaultdict(list), "offerings": []}
        slot_count: dict[uuid.UUID, int] = defaultdict(int)
        for s in d["slots"]:
            o = by_id[s.course_offering_id]
            slot_count[o.id] += 1
            c = courses.get(o.course_id)
            for p in self._cells_of(s, periods):
                tracks[_track(o)]["cells"][f"{s.day_of_week.value}|{p.id}"].append({
                    "offering_id": o.id, "code": c.code if c else "?", "title": c.title if c else "",
                    "room": s.room, "teacher": d["teachers"].get(o.assigned_teacher_id),
                    "combined_with": [t for t in d["group_terms"].get(o.combined_group_id, []) if t != o.term_label],
                })
        for o in offerings:
            c = courses.get(o.course_id)
            need = per_week(c)
            tracks[_track(o)]["offerings"].append({
                "id": o.id, "code": c.code if c else "?", "title": c.title if c else "", "teacher": d["teachers"].get(o.assigned_teacher_id),
                "credit_hours": c.credit_hours if c else None, "per_week": need, "have": slot_count[o.id],
                "missing": max(0, need - slot_count[o.id]),
                "combined_with": [t for t in d["group_terms"].get(o.combined_group_id, []) if t != o.term_label],
            })
        out = []
        # only ordinary class periods on the days they apply can be "free"; breaks, study and zero periods never are
        class_cells = {f"{day.value}|{p.id}" for day in d["days"] for p in periods if p.kind == "CLASS" and p.applies_on(day)}
        total = len(class_cells)
        for key in sorted(tracks, key=lambda k: (k[0], k[1])):
            t = tracks[key]
            busy = len(class_cells & set(t["cells"]))
            missing = sum(x["missing"] for x in t["offerings"])
            out.append({"term": t["term"], "section": t["section"], "cells": dict(t["cells"]), "offerings": t["offerings"],
                        "periods_in_week": total, "busy": busy, "free": total - busy, "missing_classes": missing,
                        "clashes": sorted(k for k, v in t["cells"].items() if len({x["offering_id"] for x in v}) > 1)})
        return {"terms": d["labels"], "days": [x.value for x in d["days"]],
                "periods": [{"id": p.id, "number": p.period_number, "start": hm12(p.start_time), "end": hm12(p.end_time),
                             "kind": p.kind, "label": p.label, "days": p.day_list} for p in periods],
                "tracks": out}

    # ------------------------------------------------------------------ auto-fill
    async def _rooms_for(self, students: int, fallback: list[str]) -> list[str]:
        listed = list((await self.session.execute(select(Room).where(Room.is_active.is_(True)).order_by(Room.seats, Room.name))).scalars())
        if listed:
            return [r.name for r in listed if r.seats is None or r.seats >= students]
        return fallback

    async def _plan(self, d: dict) -> tuple[list[dict], list[dict]]:
        offerings, periods, courses = d["offerings"], d["periods"], d["courses"]
        by_id = {o.id: o for o in offerings}
        chosen_tracks = {_track(o) for o in offerings}
        busy_cells: set[tuple] = set()                                   # (track, day, period_id)
        unit_days: dict[uuid.UUID, dict] = defaultdict(lambda: defaultdict(int))
        have: dict[uuid.UUID, int] = defaultdict(int)
        for s in d["slots"]:
            o = by_id[s.course_offering_id]
            have[o.id] += 1
            for p in self._cells_of(s, periods):
                busy_cells.add((_track(o), s.day_of_week, p.id))
            unit_days[o.combined_group_id or o.id][s.day_of_week] += 1
        fallback_rooms = sorted({s.room for s in d["slots"] if s.room}) or sorted(
            set((await self.session.execute(select(TimetableSlot.room).distinct())).scalars()) - {None, ""})

        # units: a combined class is one unit (all its sections at once); any other section is a unit of its own
        units, seen = [], set()
        for o in offerings:
            key = o.combined_group_id or o.id
            if key in seen:
                continue
            seen.add(key)
            group = await members(self.session, o) if o.combined_group_id else [o]
            course = courses.get(o.course_id) or await self.session.get(Course, o.course_id)
            counts, students = [], 0
            for m in group:
                counts.append(len(await self.tt.slot_repo.list_for_offering(m.id)))
                students += len(await self.tt._enrolled_for(m.id))
            need = per_week(course) - min(counts)
            units.append({"key": key, "lead": o, "group": group, "course": course, "missing": max(0, need), "students": students,
                          "teacher": o.assigned_teacher_id})
        units.sort(key=lambda u: (-len(u["group"]), -u["missing"], u["course"].code if u["course"] else ""))

        planned, unplaced = [], []
        prop_teacher: set[tuple] = set()       # (teacher, day, period)
        prop_room: set[tuple] = set()          # (room key, day, period)
        prop_offerings: dict[tuple, list] = defaultdict(list)   # (day, period) -> offerings placed there in this plan
        day_index = {x: i for i, x in enumerate(d["days"])}
        for u in units:
            if not u["missing"]:
                continue
            code = u["course"].code if u["course"] else "?"
            if u["teacher"] is None:
                unplaced.append({"code": code, "terms": sorted({m.term_label for m in u["group"]}), "section": u["lead"].section_label,
                                 "missing": u["missing"], "reason": "No teacher is assigned yet."})
                continue
            rooms = await self._rooms_for(u["students"], fallback_rooms)
            if not rooms:
                unplaced.append({"code": code, "terms": sorted({m.term_label for m in u["group"]}), "section": u["lead"].section_label,
                                 "missing": u["missing"], "reason": "No room is big enough (add rooms on Timetable → Rooms)."})
                continue
            placed_here = 0
            last_reason = "Every free period of these students clashes with the teacher, a room or other classes."
            for _ in range(u["missing"]):
                used = unit_days[u["key"]]
                cells = sorted(((day, p) for day in d["days"] for p in periods if p.kind == "CLASS" and p.applies_on(day)),
                               key=lambda c: (used[c[0]], c[1].period_number, day_index[c[0]]))
                best = None
                for day, p in cells:
                    tracks = {_track(m) for m in u["group"]}
                    if any((t, day, p.id) in busy_cells for t in tracks if t in chosen_tracks):
                        continue                                          # not a free period for these students
                    if (u["teacher"], day, p.id) in prop_teacher:
                        continue
                    when = f"{day.value} {{}}–{{}}"
                    problem, taken = None, set()
                    for m in u["group"]:
                        neighbours = await self.tt._live_neighbours(m, day, p.start_time, p.end_time)
                        problem = await self.tt._people_conflict(m, neighbours, when)
                        if problem:
                            break
                        taken |= {room_key(sl.room) for sl, other in neighbours if not same_group(m, other)}
                        mine = await self.tt._enrolled_for(m.id)
                        for oid in prop_offerings[(day, p.id)]:
                            if mine & await self.tt._enrolled_for(oid):
                                problem = "students already have a class planned then"
                                break
                        if problem:
                            break
                    if problem:
                        last_reason = problem
                        continue
                    room = next((r for r in rooms if room_key(r) not in taken and (room_key(r), day, p.id) not in prop_room), None)
                    if room is None:
                        last_reason = "No room is free at the free periods."
                        continue
                    best = (day, p, room)
                    break
                if best is None:
                    break
                day, p, room = best
                for m in u["group"]:
                    busy_cells.add((_track(m), day, p.id))
                    prop_offerings[(day, p.id)].append(m.id)
                prop_teacher.add((u["teacher"], day, p.id))
                prop_room.add((room_key(room), day, p.id))
                unit_days[u["key"]][day] += 1
                placed_here += 1
                planned.append({"offering_id": u["lead"].id, "code": code, "title": u["course"].title if u["course"] else "",
                                "terms": sorted({m.term_label for m in u["group"]}), "section": u["lead"].section_label,
                                "day": day.value, "period_id": p.id, "period": p.period_number,
                                "time": range12(p.start_time, p.end_time), "room": room,
                                "teacher": d["teachers"].get(u["teacher"])})
            if placed_here < u["missing"]:
                unplaced.append({"code": code, "terms": sorted({m.term_label for m in u["group"]}), "section": u["lead"].section_label,
                                 "missing": u["missing"] - placed_here, "reason": last_reason})
        return planned, unplaced

    @silently                     # building a timetable in bulk: no message per class (app/notifications/changes.py)
    async def auto_fill(self, term_labels: list[str], *, preview: bool, actor_id: uuid.UUID) -> dict:
        d = await self._load(term_labels)
        planned, unplaced = await self._plan(d)
        created, failed = [], []
        if not preview:
            for item in planned:
                try:
                    async with self.session.begin_nested():
                        await self.tt.create_slot(course_offering_id=item["offering_id"], period_id=item["period_id"],
                                                  day_of_week=DayOfWeek(item["day"]), room=item["room"], actor_id=actor_id)
                    created.append(item)
                except (BusinessRuleViolationError, ResourceNotFoundError) as exc:
                    self.tt._day_memo.clear()
                    failed.append({**item, "reason": exc.message})
            await self.audit.log(
                actor_user_id=actor_id, action="timetable.parallel_auto_filled", entity_type="course_offering", entity_id=None,
                metadata={"terms": d["labels"], "created": len(created), "failed": len(failed), "unplaced": len(unplaced)},
            )
        after = await self.view(d["labels"]) if not preview else None
        return {"preview": preview, "planned": planned, "created": created, "failed": failed, "unplaced": unplaced,
                "free_after": [{"term": t["term"], "section": t["section"], "free": t["free"]} for t in after["tracks"]] if after else None}


# ---------------------------------------------------------------------- API
router = APIRouter(prefix="/academics/timetable/parallel", tags=["timetable"])
manage = require_any_permission(Permissions.TIMETABLE_MANAGE, Permissions.COURSE_OFFERING_MANAGE)


class ParallelRequest(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=MAX_TERMS)
    preview: bool = True


@router.get("", summary="Several batch terms side by side, with the free periods of each group of students -- admin")
async def parallel_view(terms: str, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await ParallelTimetableService(db).view([t for t in terms.split("||") if t.strip()])


@router.post("/auto-fill", summary="Propose (preview) or add classes in the free periods of the chosen batch terms -- admin")
async def parallel_auto_fill(payload: ParallelRequest, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await ParallelTimetableService(db).auto_fill(payload.terms, preview=payload.preview, actor_id=current_user.id)
