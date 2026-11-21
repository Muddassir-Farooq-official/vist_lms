"""Teacher workload and free times.

Load = credit hours a week of the sections a teacher teaches that run at the same time (term dates overlap; a section
without dates counts with its own term only). A combined class (several sections taught together) counts ONCE.
Each teacher should teach between the institute's range (Settings -> Teaching load, default 12-18 credit hours) or
their own range (TeacherAvailability.load_min / load_max):
  * above the most: giving them another section is refused, unless an administrator gives a reason (activity log);
  * below the least: shown as under-loaded (never blocked).
Not applied in School mode (a school's subjects are not counted in credit hours).

Also here: who is the best teacher for a section (candidates), proposals for every section without a teacher
(balance), and what is free when (rooms, teachers, a class's free periods), from the live timetable.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import Course, CourseOffering, Period, TimetableSlot
from app.audit.service import AuditService
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.core.timefmt import hm12
from app.timetabling.models import TeacherAvailability

WEEK = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def _school() -> bool:
    from app.school.service import school_mode

    return school_mode()


def _unit(o: CourseOffering):
    return o.combined_group_id or o.id


def _overlaps(o: CourseOffering, start: date | None, end: date | None, labels: set[str]) -> bool:
    if o.term_start_date is None or o.term_end_date is None or start is None or end is None:
        return (o.term_label or "").lower() in labels
    return o.term_start_date <= end and start <= o.term_end_date


def _minutes(t) -> int:
    return t.hour * 60 + t.minute


class WorkloadService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ basics
    def default_range(self) -> tuple[int, int]:
        return int(settings.teacher_load_min_credit_hours), int(settings.teacher_load_max_credit_hours)

    async def ranges(self, teacher_ids) -> dict:
        lo, hi = self.default_range()
        ids = [t for t in teacher_ids if t]
        rows = {a.employee_id: a for a in (await self.session.execute(select(TeacherAvailability).where(
            TeacherAvailability.employee_id.in_(ids)))).scalars()} if ids else {}
        out = {}
        for t in ids:
            a = rows.get(t)
            out[t] = (a.load_min if a and a.load_min is not None else lo, a.load_max if a and a.load_max is not None else hi,
                      bool(a and (a.load_min is not None or a.load_max is not None)))
        return out

    async def teachers(self) -> list:
        """Active teachers: job role TEACHER, plus anyone teaching a section that has not ended."""
        from app.core.clock import institute_today
        from app.employees.models import Employee, EmployeeStatus, JobRole

        assigned = select(CourseOffering.assigned_teacher_id).where(
            CourseOffering.assigned_teacher_id.is_not(None),
            or_(CourseOffering.term_end_date.is_(None), CourseOffering.term_end_date >= institute_today()))
        return list((await self.session.execute(select(Employee).join(JobRole, JobRole.id == Employee.job_role_id).where(
            Employee.left_on.is_(None), Employee.status != EmployeeStatus.TERMINATED,
            or_(JobRole.default_role_code == "TEACHER", Employee.id.in_(assigned)))
            .order_by(Employee.first_name, Employee.last_name))).scalars().all())

    async def _window(self, terms: list[str]) -> tuple[date | None, date | None, set[str]]:
        labels = {" ".join(t.split()).lower() for t in terms if t and t.strip()}
        if not labels:
            raise ValidationAppError("Choose at least one term.")
        row = (await self.session.execute(select(func.min(CourseOffering.term_start_date), func.max(CourseOffering.term_end_date)).where(
            func.lower(CourseOffering.term_label).in_(labels)))).one()
        return row[0], row[1], labels

    async def _running(self, start, end, labels) -> list[CourseOffering]:
        q = select(CourseOffering)
        if start is not None and end is not None:
            q = q.where(or_(func.lower(CourseOffering.term_label).in_(labels),
                            (CourseOffering.term_start_date <= end) & (CourseOffering.term_end_date >= start)))
        else:
            q = q.where(func.lower(CourseOffering.term_label).in_(labels))
        return [o for o in (await self.session.execute(q)).scalars() if _overlaps(o, start, end, labels)]

    async def _credits(self, offerings) -> dict:
        ids = {o.course_id for o in offerings}
        return {c.id: c for c in (await self.session.execute(select(Course).where(Course.id.in_(ids))))
                .scalars()} if ids else {}

    async def load_around(self, offering: CourseOffering, teacher_id: uuid.UUID, *, without_units=()) -> tuple[int, list[str]]:
        """Credit hours `teacher_id` already teaches at the same time as `offering` (not counting the classes `without_units`)."""
        labels = {(offering.term_label or "").lower()}
        skip = set(without_units)
        running = [o for o in await self._running(offering.term_start_date, offering.term_end_date, labels)
                   if o.assigned_teacher_id == teacher_id and _unit(o) not in skip and o.id not in skip]
        courses = await self._credits(running)
        seen, total, names = set(), 0, []
        for o in running:
            if _unit(o) in seen:
                continue
            seen.add(_unit(o))
            c = courses.get(o.course_id)
            total += (c.credit_hours if c else 0) or 0
            names.append(f"{c.code if c else '?'} {o.section_label}")
        return total, names

    # ------------------------------------------------------------------ the rule, when a teacher is given a section
    async def check_assign(self, offering: CourseOffering, teacher_id: uuid.UUID | None, *, override_reason: str | None,
                           actor_id: uuid.UUID | None, together=()) -> None:
        """`together`: other sections that become one class with this one (combining): they count once."""
        if teacher_id is None or _school():
            return
        if offering.assigned_teacher_id == teacher_id and not together:
            return
        course = await self.session.get(Course, offering.course_id)
        adds = (course.credit_hours if course else 0) or 0
        skip = {_unit(offering), offering.id} | {_unit(o) for o in together} | {o.id for o in together}
        have, names = await self.load_around(offering, teacher_id, without_units=skip)
        lo, hi, _own = (await self.ranges([teacher_id]))[teacher_id]
        after = have + adds
        if after <= hi:
            return
        from app.employees.models import Employee

        e = await self.session.get(Employee, teacher_id)
        who = f"{e.first_name} {e.last_name}".strip() if e else "This teacher"
        reason = " ".join((override_reason or "").split())
        if len(reason) >= 5:
            await self.audit.log(actor_user_id=actor_id, action="teacher_load.override", entity_type="course_offering", entity_id=offering.id,
                                 after={"teacher_id": str(teacher_id), "load_before": have, "load_after": after, "max": hi, "reason": reason[:300]})
            return
        raise BusinessRuleViolationError(
            f"{who} already teaches {have} credit hours at this time ({', '.join(names) or 'nothing else'}); this section adds {adds}, "
            f"making {after}, above the most of {hi}. Choose a less busy teacher, or give a reason to allow it.",
            details={"kind": "TEACHER_LOAD", "teacher_id": str(teacher_id), "load": have, "adds": adds, "after": after, "max": hi, "min": lo,
                     "override": "Send load_override_reason (at least 5 letters) to allow it."})

    # ------------------------------------------------------------------ workload report
    async def report(self, terms: list[str]) -> dict:
        start, end, labels = await self._window(terms)
        running = await self._running(start, end, labels)
        courses = await self._credits(running)
        teachers = await self.teachers()
        ranges = await self.ranges([t.id for t in teachers] + [o.assigned_teacher_id for o in running])
        ids = [o.id for o in running]
        slots = list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.in_(ids)))).scalars()) if ids else []
        by_off = defaultdict(list)
        for s in slots:
            by_off[s.course_offering_id].append(s)
        load = defaultdict(int)
        secs = defaultdict(list)
        periods = defaultdict(set)
        seen = set()
        unassigned, unassigned_ch = [], 0
        for o in sorted(running, key=lambda x: (x.term_label, x.section_label)):
            c = courses.get(o.course_id)
            ch = (c.credit_hours if c else 0) or 0
            if o.assigned_teacher_id is None:
                if _unit(o) not in seen:
                    seen.add(_unit(o))
                    unassigned.append({"offering_id": o.id, "code": c.code if c else "?", "title": c.title if c else "", "section": o.section_label,
                                       "term": o.term_label, "credit_hours": ch})
                    unassigned_ch += ch
                continue
            t = o.assigned_teacher_id
            for s in by_off[o.id]:
                periods[t].add((s.day_of_week.value, s.start_time))
            if (t, _unit(o)) in seen:
                continue
            seen.add((t, _unit(o)))
            load[t] += ch
            secs[t].append({"offering_id": o.id, "code": c.code if c else "?", "section": o.section_label, "term": o.term_label,
                            "credit_hours": ch, "combined": o.combined_group_id is not None})
        known = {t.id: t for t in teachers}
        extra = [t for t in load if t not in known]
        if extra:
            from app.employees.models import Employee

            for e in (await self.session.execute(select(Employee).where(Employee.id.in_(extra)))).scalars():
                known[e.id] = e
        rows = []
        for tid, e in known.items():
            lo, hi, own = ranges.get(tid, (*self.default_range(), False))
            n = load.get(tid, 0)
            status = "OVER" if n > hi else "UNDER" if n < lo else "OK"
            rows.append({"id": tid, "name": f"{e.first_name} {e.last_name}".strip(), "code": e.employee_code, "department": e.department,
                         "credit_hours": n, "periods_a_week": len(periods.get(tid, ())), "min": lo, "max": hi, "own_range": own,
                         "status": status, "sections": secs.get(tid, []), "room_left": max(0, hi - n)})
        rows.sort(key=lambda r: (-r["credit_hours"] / max(1, r["max"]), r["name"]))
        lo, hi = self.default_range()
        return {"terms": sorted(labels), "from": start, "to": end, "default_min": lo, "default_max": hi, "school_mode": _school(),
                "teachers": rows, "unassigned": unassigned, "unassigned_credit_hours": unassigned_ch,
                "summary": {"over": sum(r["status"] == "OVER" for r in rows), "under": sum(r["status"] == "UNDER" for r in rows),
                            "ok": sum(r["status"] == "OK" for r in rows), "teachers": len(rows),
                            "capacity_left": sum(r["room_left"] for r in rows)}}

    async def set_range(self, teacher_id: uuid.UUID, *, load_min: int | None, load_max: int | None, actor_id: uuid.UUID) -> dict:
        from app.employees.models import Employee

        if await self.session.get(Employee, teacher_id) is None:
            raise ResourceNotFoundError("Teacher not found.")
        for v in (load_min, load_max):
            if v is not None and not 0 <= v <= 60:
                raise ValidationAppError("Credit hours must be between 0 and 60.")
        lo, hi = self.default_range()
        if (load_min if load_min is not None else lo) > (load_max if load_max is not None else hi):
            raise ValidationAppError("The least cannot be more than the most.")
        a = (await self.session.execute(select(TeacherAvailability).where(TeacherAvailability.employee_id == teacher_id))).scalar_one_or_none()
        before = {"load_min": a.load_min, "load_max": a.load_max} if a else None
        if a is None:
            a = TeacherAvailability(employee_id=teacher_id, blocked=[])
            self.session.add(a)
        a.load_min, a.load_max, a.updated_by = load_min, load_max, actor_id
        await self.audit.log(actor_user_id=actor_id, action="teacher_load.range_set", entity_type="employee", entity_id=teacher_id,
                             before=before, after={"load_min": load_min, "load_max": load_max})
        await self.session.flush()
        return {"load_min": load_min, "load_max": load_max}

    # ------------------------------------------------------------------ who should teach a section
    async def _history(self, teacher_ids) -> tuple[dict, dict]:
        """(course ids each teacher has taught, program ids each teacher teaches in)."""
        from app.academics.models import CourseProgram

        taught, progs = defaultdict(set), defaultdict(set)
        if not teacher_ids:
            return taught, progs
        for tid, cid in (await self.session.execute(select(CourseOffering.assigned_teacher_id, CourseOffering.course_id).where(
                CourseOffering.assigned_teacher_id.in_(list(teacher_ids))).distinct())).all():
            taught[tid].add(cid)
        all_courses = {c for s in taught.values() for c in s}
        links = defaultdict(set)
        if all_courses:
            for cid, pid in (await self.session.execute(select(CourseProgram.course_id, CourseProgram.program_id).where(
                    CourseProgram.course_id.in_(all_courses)))).all():
                links[cid].add(pid)
        for tid, cs in taught.items():
            for c in cs:
                progs[tid] |= links[c]
        return taught, progs

    async def candidates(self, offering_id: uuid.UUID) -> dict:
        from app.academics.models import CourseProgram
        from app.academics.service import TimetableService

        o = await self.session.get(CourseOffering, offering_id)
        if o is None:
            raise ResourceNotFoundError("Course section not found.")
        course = await self.session.get(Course, o.course_id)
        adds = (course.credit_hours if course else 0) or 0
        teachers = await self.teachers()
        ranges = await self.ranges([t.id for t in teachers])
        taught, progs = await self._history({t.id for t in teachers})
        my_progs = set((await self.session.execute(select(CourseProgram.program_id).where(CourseProgram.course_id == o.course_id))).scalars())
        avail = {a.employee_id: a for a in (await self.session.execute(select(TeacherAvailability).where(
            TeacherAvailability.employee_id.in_([t.id for t in teachers])))).scalars()} if teachers else {}
        my_slots = list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == o.id))).scalars())
        tt = TimetableService(self.session)
        out = []
        for t in teachers:
            have, _names = await self.load_around(o, t.id, without_units={_unit(o), o.id})
            lo, hi, _own = ranges[t.id]
            clash = None
            if my_slots and t.id != o.assigned_teacher_id:
                try:
                    await tt.assert_teacher_free_for_offering(o, t.id)
                except BusinessRuleViolationError as e:
                    clash = e.message
                if clash is None and t.id in avail:
                    blocked = set(avail[t.id].blocked or [])
                    if any(f"{s.day_of_week.value}|{s.period_id}" in blocked for s in my_slots):
                        clash = "Marked unavailable at some of this course's times."
            after = have + (0 if t.id == o.assigned_teacher_id else adds)
            out.append({"id": t.id, "name": f"{t.first_name} {t.last_name}".strip(), "code": t.employee_code, "department": t.department,
                        "credit_hours": have, "after": after, "min": lo, "max": hi, "over": after > hi, "under": after < lo,
                        "taught_before": o.course_id in taught.get(t.id, set()), "same_program": bool(my_progs & progs.get(t.id, set())),
                        "clash": clash, "current": t.id == o.assigned_teacher_id})

        def rank(c):
            return (c["over"] or bool(c["clash"]), not c["taught_before"], not c["same_program"],
                    0 if c["credit_hours"] < c["min"] else 1, c["credit_hours"] / max(1, c["max"]), c["name"])
        out.sort(key=rank)
        best = next((c for c in out if not c["over"] and not c["clash"] and not c["current"]), None)
        return {"offering_id": o.id, "code": course.code if course else "?", "credit_hours": adds, "school_mode": _school(),
                "suggested": best["id"] if best else None, "teachers": out}

    # ------------------------------------------------------------------ balance: proposals for sections without a teacher
    async def propose(self, terms: list[str]) -> dict:
        from app.academics.models import CourseProgram

        rep = await self.report(terms)
        teachers = await self.teachers()
        taught, progs = await self._history({t.id for t in teachers})
        load = {r["id"]: r["credit_hours"] for r in rep["teachers"]}
        rng = {r["id"]: (r["min"], r["max"]) for r in rep["teachers"]}
        names = {r["id"]: r["name"] for r in rep["teachers"]}
        avail = {a.employee_id: set(a.blocked or []) for a in (await self.session.execute(select(TeacherAvailability).where(
            TeacherAvailability.employee_id.in_([t.id for t in teachers])))).scalars()} if teachers else {}
        busy = defaultdict(list)                     # teacher -> [(day, start, end)] of their live classes and of proposals
        start, end, labels = await self._window(terms)
        running = await self._running(start, end, labels)
        ids = [o.id for o in running]
        off_by_id = {o.id: o for o in running}
        slots = list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.in_(ids)))).scalars()) if ids else []
        slots_of = defaultdict(list)
        for s in slots:
            slots_of[s.course_offering_id].append(s)
            t = off_by_id[s.course_offering_id].assigned_teacher_id
            if t:
                busy[t].append((s.day_of_week.value, _minutes(s.start_time), _minutes(s.end_time)))
        items = sorted(rep["unassigned"], key=lambda u: -u["credit_hours"])
        out, left = [], []
        for u in items:
            o = off_by_id.get(u["offering_id"])
            mine = slots_of.get(u["offering_id"], [])
            my_progs = set((await self.session.execute(select(CourseProgram.program_id).where(CourseProgram.course_id == o.course_id))).scalars())
            best = None
            for tid in load:
                lo, hi = rng[tid]
                if load[tid] + u["credit_hours"] > hi:
                    continue
                if any(f"{s.day_of_week.value}|{s.period_id}" in avail.get(tid, set()) for s in mine):
                    continue
                if any(d == s.day_of_week.value and a < _minutes(s.end_time) and _minutes(s.start_time) < b
                       for s in mine for d, a, b in busy[tid]):
                    continue
                key = (o.course_id not in taught.get(tid, set()), not (my_progs & progs.get(tid, set())),
                       0 if load[tid] < lo else 1, load[tid] / max(1, hi), names[tid])
                if best is None or key < best[0]:
                    best = (key, tid)
            if best is None:
                left.append({**u, "reason": "No teacher has room for it within their most, and is free at its times."})
                continue
            tid = best[1]
            load[tid] += u["credit_hours"]
            for s in mine:
                busy[tid].append((s.day_of_week.value, _minutes(s.start_time), _minutes(s.end_time)))
            out.append({**u, "teacher_id": tid, "teacher": names[tid], "teacher_after": load[tid], "teacher_max": rng[tid][1],
                        "taught_before": o.course_id in taught.get(tid, set())})
        return {"proposals": out, "left": left}

    async def apply(self, items: list[tuple[uuid.UUID, uuid.UUID]], *, actor_id: uuid.UUID) -> int:
        """Assign teachers to sections, all or nothing, with every normal check (clashes, the most)."""
        from app.academics.service import CourseOfferingService

        svc = CourseOfferingService(self.session)
        async with self.session.begin_nested():          # one refusal undoes the whole batch
            for offering_id, teacher_id in items:
                await svc.update(offering_id, assigned_teacher_id=teacher_id, actor_id=actor_id)
            await self.session.flush()
        return len(items)

    # ------------------------------------------------------------------ what is free when
    async def free_map(self, terms: list[str], days: list[str] | None = None) -> dict:
        from app.academics.rooms import Room, room_key
        from app.timetabling.service import SmartTimetableService

        start, end, labels = await self._window(terms)
        running = await self._running(start, end, labels)
        ids = [o.id for o in running]
        off = {o.id: o for o in running}
        slots = list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.in_(ids)))).scalars()) if ids else []
        used_days = {s.day_of_week.value for s in slots}
        days = [d for d in WEEK if d in (set(days) if days else set(WEEK[:5]) | used_days)]
        periods = [p for p in (await self.session.execute(select(Period).order_by(Period.start_time, Period.period_number))).scalars() if p.kind == "CLASS"]
        rooms = list((await self.session.execute(select(Room).where(Room.is_active.is_(True)).order_by(Room.name))).scalars())
        teachers = await self.teachers()
        rep = await self.report(terms)
        loadby = {r["id"]: r for r in rep["teachers"]}
        avail = {a.employee_id: set(a.blocked or []) for a in (await self.session.execute(select(TeacherAvailability).where(
            TeacherAvailability.employee_id.in_([t.id for t in teachers])))).scalars()} if teachers else {}
        courses = await self._credits(running)
        smart = SmartTimetableService(self.session)
        cohorts_of = {}
        from app.programs.models import Program

        for o in running:
            cohorts_of[o.id] = await smart._cohorts(o)
        prog_names = {p.id: p.name for p in (await self.session.execute(select(Program))).scalars()}

        def cohort_label(c):
            p, s, sec = c
            return f"{prog_names.get(p, '?')} · Sem {s} · {sec.upper()}"

        cells = {}
        class_busy = defaultdict(set)
        for day in days:
            for p in periods:
                if not p.applies_on(day):
                    continue
                here = [s for s in slots if s.day_of_week.value == day and s.start_time < p.end_time and p.start_time < s.end_time]
                room_busy = {room_key(s.room) for s in here}
                teacher_busy = {off[s.course_offering_id].assigned_teacher_id for s in here if off[s.course_offering_id].assigned_teacher_id}
                for s in here:
                    for c in cohorts_of.get(s.course_offering_id, ()):
                        class_busy[cohort_label(c)].add(f"{day}|{p.id}")
                free_rooms = [{"name": r.name, "kind": r.kind, "seats": r.seats} for r in rooms if r.key not in room_busy]
                free_teachers = []
                for t in teachers:
                    if t.id in teacher_busy or f"{day}|{p.id}" in avail.get(t.id, set()):
                        continue
                    r = loadby.get(t.id)
                    free_teachers.append({"id": t.id, "name": f"{t.first_name} {t.last_name}".strip(),
                                          "credit_hours": r["credit_hours"] if r else 0, "max": r["max"] if r else self.default_range()[1]})
                free_teachers.sort(key=lambda x: (x["credit_hours"] / max(1, x["max"]), x["name"]))
                classes_here = sorted({f"{courses[off[s.course_offering_id].course_id].code if off[s.course_offering_id].course_id in courses else '?'} "
                                       f"{off[s.course_offering_id].section_label} · {s.room}" for s in here})
                cells[f"{day}|{p.id}"] = {"free_rooms": free_rooms, "free_teachers": free_teachers, "classes": classes_here,
                                          "rooms_total": len(rooms), "teachers_total": len(teachers)}
        all_classes = sorted({cohort_label(c) for cs in cohorts_of.values() for c in cs})
        return {"terms": sorted(labels), "days": days,
                "periods": [{"id": p.id, "number": p.period_number, "start": hm12(p.start_time), "end": hm12(p.end_time),
                             "days": p.day_list} for p in periods],
                "cells": cells, "classes": all_classes, "class_busy": {k: sorted(v) for k, v in class_busy.items()}}
