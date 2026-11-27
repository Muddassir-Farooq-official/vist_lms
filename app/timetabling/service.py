"""Smart timetable: builds a whole term's timetable as a DRAFT, lets the administrator adjust it, then publishes it.

  generate()   every course section of the chosen terms -> solver (app/timetabling/solver.py) -> a draft, with the
               list of what could not be placed and why. Classes moved by hand in an earlier draft ("pinned") stay.
  move/add/remove on a draft: checked against the same rules; a hand-placed class is pinned.
  publish()    from a date: the live weekly slots of the draft's sections are taken off from that date (classes
               before it, and every class that already has attendance, a topic or files, are kept exactly as they
               are, the same rule as changing one slot in the middle of a term), then the draft's classes are created
               through the normal TimetableService.create_slot, with all its clash checks. One transaction: if
               anything does not fit any more, nothing changes. Optionally tells teachers, students and parents.

The rules the solver follows are the same ones the live timetable enforces (TimetableService._check_conflicts): a
student group, a teacher and a room in one place at a time, combined classes together. Student groups ("cohorts")
are worked out the same way: the course's program/semester links, the section, and the term's batch.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, Course, CourseOffering, DayOfWeek, Period, TimetableSlot
from app.academics.rooms import Room, room_key
from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.core.timefmt import hm12
from app.timetabling import solver as S
from app.timetabling.models import TeacherAvailability, TimetableDraft, TimetableDraftSlot

WEEK = [d.value for d in DayOfWeek]
DEFAULT_DAYS = WEEK[:5]
MAX_TERMS = 30


def _minutes(t) -> int:
    return t.hour * 60 + t.minute


def _overlap(s1, e1, s2, e2) -> bool:
    return s1 < e2 and s2 < e1


def default_per_week(course: Course | None, offering: CourseOffering) -> int:
    if offering.classes_per_week:
        return offering.classes_per_week
    return max(1, min(5, (course.credit_hours if course and course.credit_hours else 3)))


class SmartTimetableService:
    def __init__(self, session: AsyncSession) -> None:
        from app.academics.service import TimetableService

        self.session = session
        self.tt = TimetableService(session)
        self.audit = AuditService(session)

    # ================================================================== loading what is to be planned
    async def _terms(self, terms: list[str]) -> list[str]:
        out: list[str] = []
        for t in terms or []:
            t = " ".join((t or "").split())
            if t and t.lower() not in {x.lower() for x in out}:
                out.append(await self.tt.offering_repo.canonical_term_label(t))
        if not out:
            raise ValidationAppError("Choose at least one term.")
        if len(out) > MAX_TERMS:
            raise ValidationAppError(f"Choose at most {MAX_TERMS} terms at a time.")
        return out

    async def _periods(self, days: list[str]) -> tuple[dict[str, list[Period]], dict]:
        periods = list((await self.session.execute(select(Period).order_by(Period.start_time, Period.period_number))).scalars())
        per_day = {d: [p for p in periods if p.kind == "CLASS" and p.applies_on(d)] for d in days}
        if not any(per_day.values()):
            raise BusinessRuleViolationError("No class periods are defined for the chosen days. Add them on Timetable → Daily periods first.")
        return per_day, {p.id: p for p in periods}

    async def _cohorts(self, o: CourseOffering) -> set:
        """The student groups of a section: (program, semester, section) for each program/semester its course is in,
        narrowed to the term's own batch when the term belongs to one batch (as the live clash check does)."""
        links = await self.tt._links_for(o.course_id)
        batch = await self.tt._term_batch(o.term_label)
        if batch:
            links = {(p, s) for p, s in links if any(p == bp and (bs is None or bs == s) for bp, bs in batch)}
        sec = (o.section_label or "").strip().lower()
        return {(p, s, sec) for p, s in links}

    async def _people_clash(self, a: CourseOffering, b: CourseOffering) -> bool:
        """Can these two sections NOT be at the same time because of their students (not teacher / room)?"""
        from app.academics.combined import same_group

        if a.id == b.id or same_group(a, b) or not self.tt._ranges_overlap(a, b):
            return False
        if a.course_id != b.course_id and (a.section_label or "").strip().lower() == (b.section_label or "").strip().lower() \
                and not await self.tt._different_batches(a.term_label, b.term_label) \
                and (await self.tt._links_for(a.course_id)) & (await self.tt._links_for(b.course_id)):
            return True
        mine = await self.tt._enrolled_for(a.id)
        return bool(mine and mine & await self.tt._enrolled_for(b.id))

    async def _load(self, terms: list[str], days: list[str]) -> dict:
        from app.academics.combined import members
        from app.employees.models import Employee

        days = [d for d in WEEK if d in set(days or DEFAULT_DAYS)]
        if not days:
            raise ValidationAppError("Choose at least one day.")
        per_day, all_periods = await self._periods(days)
        offerings = list((await self.session.execute(select(CourseOffering).where(
            func.lower(CourseOffering.term_label).in_([t.lower() for t in terms]))
            .order_by(CourseOffering.term_label, CourseOffering.section_label))).scalars())
        if not offerings:
            raise ResourceNotFoundError("No course sections exist for the chosen terms.")
        courses = {c.id: c for c in (await self.session.execute(select(Course).where(Course.id.in_({o.course_id for o in offerings})))).scalars()}

        # units: a combined class is ONE unit with all its sections (even a section in a term that was not chosen)
        units: list[dict] = []
        seen: set = set()
        scope: dict[uuid.UUID, CourseOffering] = {}
        for o in offerings:
            gkey = o.combined_group_id or o.id
            if gkey in seen:
                continue
            seen.add(gkey)
            group = await members(self.session, o) if o.combined_group_id else [o]
            lead = o
            for m in group:
                scope[m.id] = m
                if m.course_id not in courses:
                    c = await self.session.get(Course, m.course_id)
                    if c:
                        courses[c.id] = c
            units.append({"lead": lead, "group": group})
        teacher_ids = {u["lead"].assigned_teacher_id for u in units if u["lead"].assigned_teacher_id}
        names = {e.id: f"{e.first_name} {e.last_name}".strip() for e in (await self.session.execute(select(Employee).where(
            Employee.id.in_(teacher_ids)))).scalars()} if teacher_ids else {}
        avail = {a.employee_id: a for a in (await self.session.execute(select(TeacherAvailability).where(
            TeacherAvailability.employee_id.in_(teacher_ids)))).scalars()} if teacher_ids else {}
        rooms = list((await self.session.execute(select(Room).where(Room.is_active.is_(True)).order_by(Room.name))).scalars())
        return {"terms": terms, "days": days, "per_day": per_day, "periods": all_periods, "units": units, "scope": scope,
                "courses": courses, "teachers": names, "avail": avail, "rooms": rooms}

    async def _enrolled_count(self, o: CourseOffering) -> int:
        n = len(await self.tt._enrolled_for(o.id))
        return n

    # ================================================================== building the solver's input
    async def _solver(self, d: dict, settings: dict, pinned: list[TimetableDraftSlot]) -> tuple[S.Solver, dict]:
        days, per_day = d["days"], d["per_day"]
        slots_by_day = {day: [S.Slot(p.id, _minutes(p.start_time), _minutes(p.end_time)) for p in per_day[day]] for day in days}
        idx_of = {(day, p.id): i for day in days for i, p in enumerate(per_day[day])}
        units: list[S.Unit] = []
        info: dict = {}
        problems: list[dict] = []
        for u in d["units"]:
            lead, group = u["lead"], u["group"]
            course = d["courses"].get(lead.course_id)
            label = f"{course.code if course else '?'} {lead.section_label}".strip()
            if len(group) > 1:
                label += " (combined: " + ", ".join(sorted({m.term_label for m in group})) + ")"
            elif len(d["terms"]) > 1:
                label += f" · {lead.term_label}"
            if lead.term_start_date is None or lead.term_end_date is None:
                problems.append({"offering_id": str(lead.id), "label": label, "missing": 0,
                                 "reason": "The term dates of this section are not set, so it cannot go on the timetable. Set them on the course."})
                continue
            need = default_per_week(course, lead)
            span = 2 if lead.double_period else 1
            if need > len(days):
                problems.append({"offering_id": str(lead.id), "label": label, "missing": need - len(days),
                                 "reason": f"It needs {need} classes a week but only {len(days)} days are chosen (one class of a course a day)."})
                need = len(days)
            groups: set = set()
            students = 0
            for m in group:
                groups |= await self._cohorts(m)
                students += await self._enrolled_count(m)
            if not students:
                students = sum(m.capacity or 0 for m in group)
            unit = S.Unit(key=lead.id, label=label, need=need, span=span, teacher=lead.assigned_teacher_id,
                          groups=frozenset(groups), students=students, lab=bool(lead.double_period),
                          home_room=room_key(lead.home_room) if lead.home_room else None)
            units.append(unit)
            info[lead.id] = {"lead": lead, "group": group, "course": course, "label": label}

        # students shared between units (enrolled in both, or the same cohort in ways the keys above do not cover)
        keys = list(info)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if any([await self._people_clash(x, y) for x in info[a]["group"] for y in info[b]["group"]]):
                    ua = next(x for x in units if x.key == a)
                    ub = next(x for x in units if x.key == b)
                    ua.avoid = ua.avoid | {b}
                    ub.avoid = ub.avoid | {a}

        # what is already busy and not being planned: other sections' live classes at the same time of year
        scope_ids = set(d["scope"])
        fixed_teacher, fixed_room, fixed_unit = set(), set(), set()
        all_live = list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.not_in(scope_ids)))).scalars()) \
            if scope_ids else []
        others = {o.id: o for o in (await self.session.execute(select(CourseOffering).where(
            CourseOffering.id.in_({s.course_offering_id for s in all_live})))).scalars()} if all_live else {}
        leads = [info[k]["lead"] for k in keys]
        clash_memo: dict = {}
        for s in all_live:
            other = others.get(s.course_offering_id)
            if other is None or not any(self.tt._ranges_overlap(other, ld) for ld in leads):
                continue
            day = s.day_of_week.value
            if day not in per_day:
                continue
            cells = [i for i, p in enumerate(per_day[day]) if _overlap(_minutes(s.start_time), _minutes(s.end_time), _minutes(p.start_time), _minutes(p.end_time))]
            if not cells:
                continue
            for c in cells:
                fixed_room.add((room_key(s.room), day, c))
                if other.assigned_teacher_id:
                    fixed_teacher.add((other.assigned_teacher_id, day, c))
            if other.id not in clash_memo:                   # which units share students with this section (once per section)
                clash_memo[other.id] = [k for k in keys if any([await self._people_clash(m, other) for m in info[k]["group"]])]
            for k in clash_memo[other.id]:
                for c in cells:
                    fixed_unit.add((k, day, c))

        teachers = {}
        for tid, name in d["teachers"].items():
            a = d["avail"].get(tid)
            blocked = set()
            if a:
                for cell in a.blocked or []:
                    day, _, pid = str(cell).partition("|")
                    try:
                        blocked.add((day, uuid.UUID(pid)))
                    except ValueError:
                        continue
            teachers[tid] = S.Teacher(blocked=blocked, max_per_day=a.max_per_day if a else None, max_in_row=a.max_in_row if a else None, name=name)
        rooms = [S.Room(r.name, r.key, r.kind, r.seats) for r in d["rooms"]]

        by_unit = {u.key: u for u in units}
        for ps in pinned:
            u = by_unit.get(ps.course_offering_id)
            i = idx_of.get((ps.day, ps.period_id))
            if u is not None and i is not None and len(u.pinned) < u.need:
                u.pinned.append((ps.day, i, ps.room))
        solver = S.Solver(days=days, periods=slots_by_day, units=units, teachers=teachers, rooms=rooms,
                          fixed_teacher=fixed_teacher, fixed_room=fixed_room, fixed_unit=fixed_unit,
                          default_max_per_day=settings.get("max_per_day"), default_max_in_row=settings.get("max_in_row"))
        return solver, {"info": info, "problems": problems, "idx_of": idx_of}

    # ================================================================== generate
    async def generate(self, *, terms: list[str], days: list[str], max_per_day: int | None, max_in_row: int | None,
                       actor_id: uuid.UUID, keep_from: uuid.UUID | None = None, seconds: float = 8.0) -> TimetableDraft:
        terms = await self._terms(terms)
        for name, v in (("classes a day", max_per_day), ("periods in a row", max_in_row)):
            if v is not None and not 1 <= v <= 12:
                raise ValidationAppError(f"The teachers' {name} limit must be between 1 and 12.")
        d = await self._load(terms, days)
        if not d["rooms"]:
            raise BusinessRuleViolationError("List the rooms first (Timetable → Rooms, with seats and which are labs): the smart timetable picks rooms from that list.")
        pinned: list[TimetableDraftSlot] = []
        if keep_from is not None:
            old = await self.session.get(TimetableDraft, keep_from)
            if old is not None and old.status == "DRAFT":
                pinned = list((await self.session.execute(select(TimetableDraftSlot).where(
                    TimetableDraftSlot.draft_id == old.id, TimetableDraftSlot.pinned.is_(True)))).scalars())
        settings = {"max_per_day": max_per_day, "max_in_row": max_in_row}
        solver, meta = await self._solver(d, settings, pinned)
        res = solver.solve(seconds=seconds)

        # earlier drafts of the same terms are replaced
        await self.session.execute(update(TimetableDraft).where(TimetableDraft.status == "DRAFT").values(status="DISCARDED", updated_at=datetime.now(timezone.utc)))
        problems = meta["problems"] + [{"offering_id": str(p["unit"]), "label": p["label"], "missing": p["missing"], "reason": p["reason"]}
                                       for p in res.unplaced]
        for s in await self.combine_suggestions(terms):
            if s["same_teacher"] and s["teachers"]:
                problems.append({"offering_id": str(s["lead_id"]), "label": s["course"], "missing": 0, "warning": True,
                                 "reason": f"{', '.join(x['term'] + ' (' + x['section'] + ')' for x in s['sections'])} have the same teacher but are "
                                           "separate classes, so they are placed at different times. If they are taught together, combine them "
                                           "(step 4, Classes & labs) and generate again."})
        for k, inf in meta["info"].items():
            if inf["lead"].assigned_teacher_id is None:
                problems.append({"offering_id": str(k), "label": inf["label"], "missing": 0, "warning": True,
                                 "reason": "No teacher is assigned yet, so teacher clashes and limits could not be checked for it."})
        need = sum(u.need for u in solver.units.values())
        draft = TimetableDraft(terms=terms, days=d["days"], settings=settings, status="DRAFT", problems=problems,
                               stats={"sections": len(d["scope"]), "units": len(solver.units), "classes_needed": need,
                                      "classes_placed": len(res.placements), "penalty": res.penalty, "rounds": res.rounds,
                                      "offerings": [str(i) for i in d["scope"]]},
                               seconds=res.seconds, created_by=actor_id)
        self.session.add(draft)
        await self.session.flush()
        for p in res.placements:
            self.session.add(TimetableDraftSlot(draft_id=draft.id, course_offering_id=p.unit, day=p.day,
                                                period_id=d["per_day"][p.day][p.idx].id, span=p.span, room=p.room, pinned=p.pinned))
        await self.audit.log(actor_user_id=actor_id, action="timetable_draft.generated", entity_type="timetable_draft", entity_id=draft.id,
                             after={"terms": terms, "days": d["days"], **settings, "placed": len(res.placements), "needed": need,
                                    "problems": len(problems)})
        await self.session.flush()
        return draft

    # ================================================================== reading a draft
    async def _draft(self, draft_id: uuid.UUID, *, editable: bool = False) -> TimetableDraft:
        draft = await self.session.get(TimetableDraft, draft_id)
        if draft is None:
            raise ResourceNotFoundError("Draft not found.")
        if editable and draft.status != "DRAFT":
            raise BusinessRuleViolationError("This draft was already published or replaced. Generate a new one.")
        return draft

    async def latest(self) -> TimetableDraft | None:
        return (await self.session.execute(select(TimetableDraft).where(TimetableDraft.status.in_(["DRAFT", "PUBLISHED"]))
                                           .order_by(TimetableDraft.created_at.desc()).limit(1))).scalar_one_or_none()

    async def view(self, draft_id: uuid.UUID) -> dict:
        from app.programs.models import Program

        draft = await self._draft(draft_id)
        d = await self._load(draft.terms, draft.days)
        rows = list((await self.session.execute(select(TimetableDraftSlot).where(TimetableDraftSlot.draft_id == draft.id))).scalars())
        info = {}
        for u in d["units"]:
            lead, group = u["lead"], u["group"]
            c = d["courses"].get(lead.course_id)
            cohorts: set = set()
            for m in group:
                cohorts |= await self._cohorts(m)
            info[lead.id] = {"code": c.code if c else "?", "title": c.title if c else "", "section": lead.section_label,
                             "term": lead.term_label, "teacher": d["teachers"].get(lead.assigned_teacher_id),
                             "teacher_id": lead.assigned_teacher_id,
                             "combined": sorted({f"{m.term_label} {m.section_label}" for m in group}) if len(group) > 1 else [],
                             "per_week": default_per_week(c, lead), "double": lead.double_period, "cohorts": cohorts}
        progs = {p.id: p.name for p in (await self.session.execute(select(Program).where(
            Program.id.in_({c[0] for i in info.values() for c in i["cohorts"]})))).scalars()} if info else {}

        def cohort_label(c) -> str:
            p, s, sec = c
            return f"{progs.get(p, '?')} · Sem {s} · {sec.upper()}"

        have = defaultdict(int)
        slots = []
        for r in rows:
            i = info.get(r.course_offering_id)
            if i is None:
                continue
            have[r.course_offering_id] += 1
            day_periods = d["per_day"].get(r.day, [])
            idx = next((n for n, p in enumerate(day_periods) if p.id == r.period_id), None)
            cover = [p.id for p in day_periods[idx: idx + r.span]] if idx is not None else [r.period_id]
            slots.append({"id": r.id, "offering_id": r.course_offering_id, "day": r.day, "period_id": r.period_id, "periods": cover,
                          "span": r.span, "room": r.room, "pinned": r.pinned, "code": i["code"], "title": i["title"],
                          "section": i["section"], "term": i["term"], "teacher": i["teacher"], "teacher_id": i["teacher_id"],
                          "combined": i["combined"], "classes": sorted(cohort_label(c) for c in i["cohorts"])})
        courses = [{"offering_id": k, "code": i["code"], "title": i["title"], "section": i["section"], "term": i["term"],
                    "teacher": i["teacher"], "per_week": i["per_week"], "double": i["double"], "have": have[k],
                    "missing": max(0, i["per_week"] - have[k]), "combined": i["combined"],
                    "classes": sorted(cohort_label(c) for c in i["cohorts"])}
                   for k, i in info.items()]
        courses.sort(key=lambda x: (-x["missing"], x["code"], x["section"]))
        periods = sorted({p.id: p for day in draft.days for p in d["per_day"][day]}.values(), key=lambda p: (p.start_time, p.period_number))
        return {
            "id": draft.id, "status": draft.status, "terms": draft.terms, "days": draft.days, "settings": draft.settings,
            "problems": draft.problems, "stats": draft.stats, "seconds": draft.seconds, "created_at": draft.created_at,
            "published_at": draft.published_at, "published_from": draft.published_from,
            "periods": [{"id": p.id, "number": p.period_number, "start": hm12(p.start_time), "end": hm12(p.end_time),
                         "days": [day for day in draft.days if p in d["per_day"][day]]} for p in periods],
            "slots": slots, "courses": courses,
            "classes": sorted({c for s in courses for c in s["classes"]}),
            "teachers": sorted({s["teacher"] for s in courses if s["teacher"]}),
            "rooms": [{"name": r.name, "kind": r.kind, "seats": r.seats} for r in d["rooms"]],
        }

    # ================================================================== editing a draft
    async def _check_and_pin(self, draft: TimetableDraft, *, offering_id: uuid.UUID, day: str, period_id: uuid.UUID,
                             room: str, exclude: uuid.UUID | None) -> tuple:
        d = await self._load(draft.terms, draft.days)
        rows = list((await self.session.execute(select(TimetableDraftSlot).where(TimetableDraftSlot.draft_id == draft.id))).scalars())
        others = [r for r in rows if r.id != exclude]     # the whole draft, as it is, is the background for this one check
        solver, meta = await self._solver(d, draft.settings or {}, others)
        solver._reset()
        for u in solver.units.values():
            for pday, pidx, proom in u.pinned:
                solver._place(u, pday, pidx, proom, pinned=True)
        unit = solver.units.get(offering_id)
        if unit is None:
            raise ValidationAppError("That course is not part of this draft.")
        i = meta["idx_of"].get((day, period_id))
        if day not in draft.days or i is None:
            raise ValidationAppError("That period is not a class period on that day.")
        if day in solver.unit_days[unit.key]:
            raise BusinessRuleViolationError("This course already has a class that day (one class of a course a day).")
        if len(solver.unit_days[unit.key]) >= unit.need and exclude is None:
            raise BusinessRuleViolationError(f"This course already has all its {unit.need} classes a week. Move one instead.")
        blockers = solver.blockers(unit, day, i)
        if blockers is None:
            raise BusinessRuleViolationError("It cannot go there: the teacher is unavailable, the period is not long enough for a double period, "
                                             "or the students already have another class then (outside this draft).")
        if blockers:
            names = ", ".join(sorted(solver.units[b].label for b in blockers if b in solver.units))
            raise BusinessRuleViolationError(f"It clashes with {names} (same students or the same teacher).")
        t = unit.teacher
        if t is not None:
            have = solver.t_day.get((t, day), set())
            m = solver._max_day(t)
            if m and len(have) + unit.span > m:
                raise BusinessRuleViolationError(f"The teacher would have more than {m} classes that day.")
            mr = solver._max_row(t)
            if mr and solver._run_after(have, range(i, i + unit.span), day) > mr:
                raise BusinessRuleViolationError(f"The teacher would teach more than {mr} periods in a row.")
        rk = room_key(room)
        r = next((x for x in solver.rooms if x.key == rk), None)
        if r is None:
            raise ValidationAppError(f"Room '{room}' is not on the room list.")
        if r.seats is not None and unit.students > r.seats:
            raise BusinessRuleViolationError(f"{r.name} has {r.seats} seats; this class has {unit.students} students.")
        for c in range(i, i + unit.span):
            if (rk, day, c) in solver.r_occ or (rk, day, c) in solver.fixed_room:
                raise BusinessRuleViolationError(f"{r.name} is already in use then.")
        return unit, r

    async def move(self, draft_id: uuid.UUID, slot_id: uuid.UUID, *, day: str, period_id: uuid.UUID, room: str, actor_id: uuid.UUID) -> TimetableDraftSlot:
        draft = await self._draft(draft_id, editable=True)
        row = await self.session.get(TimetableDraftSlot, slot_id)
        if row is None or row.draft_id != draft.id:
            raise ResourceNotFoundError("That class is not in this draft.")
        _unit, r = await self._check_and_pin(draft, offering_id=row.course_offering_id, day=day, period_id=period_id, room=room, exclude=row.id)
        before = {"day": row.day, "period_id": str(row.period_id), "room": row.room}
        row.day, row.period_id, row.room, row.pinned = day, period_id, r.name, True
        await self.audit.log(actor_user_id=actor_id, action="timetable_draft.class_moved", entity_type="timetable_draft", entity_id=draft.id,
                             before=before, after={"day": day, "period_id": str(period_id), "room": r.name, "offering_id": str(row.course_offering_id)})
        await self.session.flush()
        return row

    async def add(self, draft_id: uuid.UUID, *, offering_id: uuid.UUID, day: str, period_id: uuid.UUID, room: str, actor_id: uuid.UUID) -> TimetableDraftSlot:
        draft = await self._draft(draft_id, editable=True)
        unit, r = await self._check_and_pin(draft, offering_id=offering_id, day=day, period_id=period_id, room=room, exclude=None)
        row = TimetableDraftSlot(draft_id=draft.id, course_offering_id=offering_id, day=day, period_id=period_id, span=unit.span, room=r.name, pinned=True)
        self.session.add(row)
        draft.problems = [p for p in (draft.problems or []) if not (p.get("offering_id") == str(offering_id) and p.get("missing", 0) <= 1 and not p.get("warning"))] \
            + [{**p, "missing": p["missing"] - 1} for p in (draft.problems or []) if p.get("offering_id") == str(offering_id) and p.get("missing", 0) > 1]
        await self.audit.log(actor_user_id=actor_id, action="timetable_draft.class_added", entity_type="timetable_draft", entity_id=draft.id,
                             after={"day": day, "period_id": str(period_id), "room": r.name, "offering_id": str(offering_id)})
        await self.session.flush()
        return row

    async def remove(self, draft_id: uuid.UUID, slot_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        draft = await self._draft(draft_id, editable=True)
        row = await self.session.get(TimetableDraftSlot, slot_id)
        if row is None or row.draft_id != draft.id:
            raise ResourceNotFoundError("That class is not in this draft.")
        await self.session.delete(row)
        await self.audit.log(actor_user_id=actor_id, action="timetable_draft.class_removed", entity_type="timetable_draft", entity_id=draft.id,
                             before={"day": row.day, "period_id": str(row.period_id), "room": row.room, "offering_id": str(row.course_offering_id)})
        await self.session.flush()

    async def discard(self, draft_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        draft = await self._draft(draft_id, editable=True)
        draft.status = "DISCARDED"
        await self.audit.log(actor_user_id=actor_id, action="timetable_draft.discarded", entity_type="timetable_draft", entity_id=draft.id)

    # ================================================================== publish
    async def publish_preview(self, draft_id: uuid.UUID, from_date: date) -> dict:
        draft = await self._draft(draft_id, editable=True)
        ids = [uuid.UUID(x) for x in (draft.stats or {}).get("offerings", [])]
        live = list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.in_(ids))))
                    .scalars()) if ids else []
        row = (await self.session.execute(text(
            "SELECT count(*) FILTER (WHERE cs.session_date >= :d AND cs.status = 'SCHEDULED') AS upcoming,"
            f" count(*) FILTER (WHERE cs.session_date >= :d AND cs.status = 'SCHEDULED' AND {self.tt._KEEP_SQL}) AS with_work,"
            " count(*) FILTER (WHERE cs.session_date < :d AND cs.attendance_taken_at IS NOT NULL) AS held"
            " FROM class_sessions cs WHERE cs.timetable_slot_id = ANY(:slots)"), {"d": from_date, "slots": [s.id for s in live]})).mappings().one() \
            if live else {"upcoming": 0, "with_work": 0, "held": 0}
        from app.academics.combined import members

        rows = list((await self.session.execute(select(TimetableDraftSlot).where(TimetableDraftSlot.draft_id == draft.id))).scalars())
        n = len(rows)
        size: dict = {}
        per_section = 0                       # the same unit as live_slots: one per section and period (a combined class is in each section)
        for r in rows:
            if r.course_offering_id not in size:
                size[r.course_offering_id] = len(await members(self.session, r.course_offering_id)) or 1
            per_section += size[r.course_offering_id] * r.span
        return {"from_date": from_date, "live_slots": len(live), "draft_slots": per_section, "upcoming_classes_replaced": row["upcoming"],
                "upcoming_with_work_kept": row["with_work"], "classes_held_before": row["held"], "draft_classes": n,
                "unplaced": sum(p.get("missing", 0) for p in draft.problems or [] if not p.get("warning"))}

    async def _retire(self, slot: TimetableSlot, from_date: date) -> None:
        """This live slot ends before from_date: its classes from that date are deleted when empty, cancelled when they already
        have a topic / files / marks (the same as TimetableService.change_slot); earlier classes are untouched."""
        params = {"slot": slot.id, "d": from_date}
        await self.session.execute(text(
            "UPDATE class_sessions cs SET status = 'CANCELLED', cancelled_reason = 'Timetable slot removed' "
            f"WHERE cs.timetable_slot_id = :slot AND cs.session_date >= :d AND cs.status = 'SCHEDULED' AND cs.attendance_taken_at IS NULL AND {self.tt._KEEP_SQL}"),
            params)
        await self.session.execute(text(
            f"DELETE FROM class_sessions cs WHERE cs.timetable_slot_id = :slot AND cs.session_date >= :d AND NOT {self.tt._KEEP_SQL}"), params)
        await self.session.execute(update(ClassSession).where(ClassSession.timetable_slot_id == slot.id).values(timetable_slot_id=None)
                                   .execution_options(synchronize_session="fetch"))
        await self.session.delete(slot)

    async def publish(self, draft_id: uuid.UUID, *, from_date: date, notify: set[str], actor_id: uuid.UUID) -> dict:
        from app.notifications.changes import quiet

        # one publish at a time: a second administrator pressing Publish waits, then finds it already published
        locked = (await self.session.execute(select(TimetableDraft).where(TimetableDraft.id == draft_id).with_for_update())).scalar_one_or_none()
        if locked is not None:
            await self.session.refresh(locked)
        draft = await self._draft(draft_id, editable=True)
        today = institute_today()
        if from_date < today:
            raise ValidationAppError("The new timetable can start today or later. Classes that already happened are never changed.")
        ids = [uuid.UUID(x) for x in (draft.stats or {}).get("offerings", [])]
        offerings = {o.id: o for o in (await self.session.execute(select(CourseOffering).where(CourseOffering.id.in_(ids)))).scalars()} if ids else {}
        ending = [o for o in offerings.values() if o.term_end_date is not None and o.term_end_date < from_date]
        rows = list((await self.session.execute(select(TimetableDraftSlot).where(TimetableDraftSlot.draft_id == draft.id))).scalars())
        if not rows:
            raise BusinessRuleViolationError("The draft has no classes.")
        periods = {p.id: p for p in (await self.session.execute(select(Period))).scalars()}
        per_day, _ = await self._periods(draft.days)
        courses = {c.id: c for c in (await self.session.execute(select(Course).where(Course.id.in_({o.course_id for o in offerings.values()})))).scalars()}

        live = list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.in_(list(offerings)))))
                    .scalars())
        created = 0
        with quiet():                                         # one message per person at the end, not one per class
            async with self.session.begin_nested():           # all or nothing, even inside a larger transaction
                for s in live:
                    o = offerings.get(s.course_offering_id)
                    if o is not None and o in ending:
                        continue
                    await self._retire(s, from_date)
                await self.session.flush()
                self.tt._day_memo.clear()
                for r in sorted(rows, key=lambda x: (WEEK.index(x.day), periods[x.period_id].start_time if x.period_id in periods else 0)):
                    o = offerings.get(r.course_offering_id)
                    if o is None or o in ending:
                        continue
                    day_list = per_day.get(r.day, [])
                    idx = next((n for n, p in enumerate(day_list) if p.id == r.period_id), None)
                    if idx is None or idx + r.span > len(day_list):
                        raise BusinessRuleViolationError("A period used in the draft was changed or removed since. Generate the draft again. Nothing was changed.")
                    c = courses.get(o.course_id)
                    for p in day_list[idx: idx + r.span]:
                        try:
                            async with self.session.begin_nested():
                                _slot, n = await self.tt.create_slot(course_offering_id=o.id, period_id=p.id, day_of_week=DayOfWeek(r.day), room=r.room,
                                                                     actor_id=actor_id, starts_from=max(from_date, o.term_start_date or from_date), mirror=True)
                        except BusinessRuleViolationError as e:
                            raise BusinessRuleViolationError(
                                f"{c.code if c else '?'} {o.section_label}, {r.day.title()} {hm12(p.start_time)}: {e.message} "
                                "Something changed since the draft was made. Generate it again. Nothing was changed.") from None
                        created += n
        draft.status = "PUBLISHED"
        draft.published_at = datetime.now(timezone.utc)
        draft.published_from = from_date
        draft.published_by = actor_id
        told = await self._notify(offerings.values(), notify, draft, from_date) if notify else {}
        await self.audit.log(actor_user_id=actor_id, action="timetable_draft.published", entity_type="timetable_draft", entity_id=draft.id,
                             after={"terms": draft.terms, "from_date": str(from_date), "slots_replaced": len(live), "draft_classes": len(rows),
                                    "class_sessions_created": created, "told": told, "skipped_ended_sections": len(ending)})
        await self.session.flush()
        return {"published_from": from_date, "slots_replaced": len(live), "draft_classes": len(rows), "class_sessions_created": created,
                "told": told, "skipped_ended_sections": len(ending)}

    async def _notify(self, offerings, notify: set[str], draft: TimetableDraft | None, from_date: date, *, terms_text: str | None = None,
                      cleared: bool = False) -> dict:
        from app.academics.models import Enrollment, EnrollmentStatus
        from app.employees.models import Employee
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.parents.models import ParentStudentLink
        from app.students.models import Student

        ids = [o.id for o in offerings]
        terms = terms_text or ", ".join(draft.terms if draft else [])
        when = from_date.strftime("%d %b %Y")
        if cleared:
            title = "Timetable being remade"
            teacher_msg = f"The weekly timetable for {terms} is cleared from {when} while a new one is made. You will be told when it is ready."
            student_msg = f"Your weekly timetable for {terms} is being remade from {when}. You will be told when the new one is ready."
            parent_msg = f"Your child's weekly timetable for {terms} is being remade from {when}."
        else:
            title = "New timetable"
            teacher_msg = f"The timetable for {terms} has been updated from {when}. Open My Timetable to see your classes."
            student_msg = f"Your timetable for {terms} has changed from {when}. Open My Timetable to see it."
            parent_msg = f"Your child's timetable for {terms} has changed from {when}."
        out = {}
        if "teachers" in notify:
            users = set((await self.session.execute(select(Employee.user_id).where(
                Employee.id.in_({o.assigned_teacher_id for o in offerings if o.assigned_teacher_id}), Employee.user_id.is_not(None)))).scalars())
            for uid in users:
                await create_notification(self.session, user_id=uid, category=NotificationCategory.GENERAL, title=title, message=teacher_msg,
                                          link="/dashboard/faculty/timetable")
            out["teachers"] = len(users)
        student_ids = set((await self.session.execute(select(Enrollment.student_id).where(
            Enrollment.course_offering_id.in_(ids), Enrollment.status == EnrollmentStatus.ENROLLED))).scalars()) if ids else set()
        if "students" in notify and student_ids:
            users = set((await self.session.execute(select(Student.user_id).where(Student.id.in_(student_ids), Student.user_id.is_not(None)))).scalars())
            for uid in users:
                await create_notification(self.session, user_id=uid, category=NotificationCategory.GENERAL, title=title, message=student_msg,
                                          link="/dashboard/student/timetable")
            out["students"] = len(users)
        if "parents" in notify and student_ids:
            users = set((await self.session.execute(select(ParentStudentLink.user_id).where(ParentStudentLink.student_id.in_(student_ids)))).scalars())
            for uid in users:
                await create_notification(self.session, user_id=uid, category=NotificationCategory.GENERAL, title=title, message=parent_msg,
                                          link="/dashboard/parent/timetable")
            out["parents"] = len(users)
        return out

    # ================================================================== clearing the live timetable to start again
    async def _clear_plan(self, terms: list[str], from_date: date) -> dict:
        """Which sections are cleared and from which day. Every batch term has its own dates, so each section is cleared
        from the later of the chosen date and its own term start, and a section whose term has ended is left alone.
        A combined class is cleared in all its sections (it is one class), even when another section is in a term that
        was not chosen; the plan says so."""
        from app.academics.combined import members

        terms = await self._terms(terms)
        today = institute_today()
        if from_date < today:
            raise ValidationAppError("Clearing can start today or later. Classes that already happened are never changed.")
        chosen = list((await self.session.execute(select(CourseOffering).where(
            func.lower(CourseOffering.term_label).in_([t.lower() for t in terms])))).scalars())
        offerings: dict = {o.id: o for o in chosen}
        extra: dict = {}
        for o in chosen:
            for m in await members(self.session, o):
                if m.id not in offerings:
                    offerings[m.id] = m
                    extra[m.id] = o
        plan, ended = [], []
        for o in offerings.values():
            start = max(from_date, o.term_start_date or from_date)
            if o.term_end_date is not None and o.term_end_date < start:
                ended.append(o)
                continue
            plan.append((o, start))
        return {"terms": terms, "plan": plan, "ended": ended, "extra": extra}

    async def clear_preview(self, terms: list[str], from_date: date) -> dict:
        """What clearing would do, per batch term. Nothing is changed."""
        p = await self._clear_plan(terms, from_date)
        keep = self.tt._KEEP_SQL
        by_term: dict = {}
        for o, start in p["plan"]:
            slots = [x for x, in (await self.session.execute(select(TimetableSlot.id).where(TimetableSlot.course_offering_id == o.id))).all()]
            row = {"slots": len(slots), "removed": 0, "kept_attendance": 0, "kept_cancelled": 0, "before": 0}
            if slots:
                r = (await self.session.execute(text(
                    "SELECT count(*) FILTER (WHERE cs.session_date >= :d AND NOT " + keep + ") AS removed,"
                    " count(*) FILTER (WHERE cs.session_date >= :d AND cs.attendance_taken_at IS NOT NULL) AS kept_attendance,"
                    " count(*) FILTER (WHERE cs.session_date >= :d AND cs.status = 'SCHEDULED' AND cs.attendance_taken_at IS NULL AND " + keep + ") AS kept_cancelled,"
                    " count(*) FILTER (WHERE cs.session_date < :d) AS before"
                    " FROM class_sessions cs WHERE cs.timetable_slot_id = ANY(:slots)"), {"d": start, "slots": slots})).mappings().one()
                row.update({k: int(r[k]) for k in row if k != "slots"})
            t = by_term.setdefault(o.term_label, {"term": o.term_label, "from": start, "until": o.term_end_date, "sections": 0, "slots": 0, "removed": 0,
                                                  "kept_attendance": 0, "kept_cancelled": 0, "before": 0, "combined_from": set()})
            t["from"] = min(t["from"], start)
            t["sections"] += 1
            for k in ("slots", "removed", "kept_attendance", "kept_cancelled", "before"):
                t[k] += row[k]
            if o.id in p["extra"]:
                t["combined_from"].add(p["extra"][o.id].term_label)
        rows = sorted(by_term.values(), key=lambda x: x["term"])
        for r in rows:
            r["combined_from"] = sorted(r["combined_from"])
            r["chosen"] = r["term"].lower() in {t.lower() for t in p["terms"]}
        totals = {k: sum(r[k] for r in rows) for k in ("sections", "slots", "removed", "kept_attendance", "kept_cancelled", "before")}
        return {"from_date": from_date, "terms": rows, "totals": totals,
                "ended": sorted({o.term_label for o in p["ended"]}), "confirm_word": "CLEAR"}

    async def clear(self, terms: list[str], *, from_date: date, notify: set[str], actor_id: uuid.UUID) -> dict:
        """Take every weekly class pattern of the chosen terms off from the date (per section: from the later of the date
        and its term start; finished terms untouched). Kept exactly as they are: every class before that day, and every
        class that has attendance; a later class with a topic, files or marks is cancelled, never deleted; empty later
        classes are removed. One-off extra and make-up classes are not touched. All or nothing; recorded."""
        from app.notifications.changes import quiet

        preview = await self.clear_preview(terms, from_date)
        p = await self._clear_plan(terms, from_date)
        removed_slots = 0
        with quiet():
            async with self.session.begin_nested():
                for o, start in p["plan"]:
                    for slot in list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == o.id))).scalars()):
                        await self._retire(slot, start)
                        removed_slots += 1
                await self.session.flush()
                self.tt._day_memo.clear()
        offerings = [o for o, _start in p["plan"]]
        told = await self._notify(offerings, notify, None, from_date, terms_text=", ".join(p["terms"]), cleared=True) if notify and offerings else {}
        await self.audit.log(actor_user_id=actor_id, action="timetable.cleared", entity_type="timetable", entity_id=None,
                             after={"terms": p["terms"], "from_date": str(from_date), "weekly_patterns_removed": removed_slots,
                                    "classes_removed": preview["totals"]["removed"], "kept_with_attendance": preview["totals"]["kept_attendance"],
                                    "kept_cancelled": preview["totals"]["kept_cancelled"], "ended_terms_untouched": preview["ended"], "told": told})
        await self.session.flush()
        return {**preview, "weekly_patterns_removed": removed_slots, "told": told}

    # ================================================================== where the wizard stands
    async def combine_suggestions(self, terms: list[str]) -> list[dict]:
        """Sections of the SAME course in DIFFERENT chosen batch terms, running at the same time of year, that are not one
        combined class yet (e.g. LB101 for BSN and for AHS 1st). Each suggestion can be combined with one click; nothing is merged here."""
        from app.academics.combined import concurrent
        from app.employees.models import Employee

        terms = await self._terms(terms)
        rows = (await self.session.execute(select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id).where(
            func.lower(CourseOffering.term_label).in_([t.lower() for t in terms]))
            .order_by(Course.code, CourseOffering.term_label, CourseOffering.section_label))).all()
        by_course: dict[uuid.UUID, list] = {}
        for o, c in rows:
            by_course.setdefault(c.id, []).append((o, c))
        teacher_ids = {o.assigned_teacher_id for o, _c in rows if o.assigned_teacher_id}
        names = {e.id: f"{e.first_name} {e.last_name}".strip() for e in (await self.session.execute(
            select(Employee).where(Employee.id.in_(teacher_ids or {uuid.uuid4()})))).scalars()}
        out = []
        for items in by_course.values():
            units: dict = {}                               # one unit per combined class (or per section on its own)
            for o, c in items:
                units.setdefault(o.combined_group_id or o.id, []).append(o)
            if len(units) < 2:
                continue
            pending = list(units.values())
            while pending:
                lead = pending.pop(0)
                # other BATCHES only: sections A and B of one batch term are normally taught separately
                lead_terms = {(o.term_label or "").lower() for o in lead}
                cluster = [lead] + [u for u in pending if concurrent(u[0], lead[0]) and not ({(o.term_label or "").lower() for o in u} & lead_terms)]
                pending = [u for u in pending if u not in cluster]
                if len(cluster) < 2:
                    continue
                code = items[0][1].code
                teachers = {o.assigned_teacher_id for u in cluster for o in u if o.assigned_teacher_id}
                out.append({"course": code, "title": items[0][1].title, "lead_id": lead[0].id, "join_ids": [u[0].id for u in cluster[1:]],
                            "same_teacher": len(teachers) <= 1,
                            "teachers": [{"id": t, "name": names.get(t, "")} for t in sorted(teachers, key=lambda t: names.get(t, ""))],
                            "sections": [{"id": o.id, "term": o.term_label, "section": o.section_label, "combined": o.combined_group_id is not None,
                                          "teacher": names.get(o.assigned_teacher_id)} for u in cluster for o in u]})
        return out

    async def readiness(self, terms: list[str]) -> dict:
        """What is in place for the chosen terms, for the wizard's step badges. Read only."""
        terms = await self._terms(terms)
        rows = (await self.session.execute(select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id).where(
            func.lower(CourseOffering.term_label).in_([t.lower() for t in terms])))).all()
        ids = [o.id for o, _c in rows]
        periods = list((await self.session.execute(select(Period))).scalars())
        rooms = int((await self.session.execute(select(func.count()).select_from(Room).where(Room.is_active.is_(True)))).scalar_one())
        teachers = {o.assigned_teacher_id for o, _c in rows if o.assigned_teacher_id}
        avail = int((await self.session.execute(select(func.count()).select_from(TeacherAvailability).where(
            TeacherAvailability.employee_id.in_(teachers or {uuid.uuid4()})))).scalar_one())
        live = int((await self.session.execute(select(func.count()).select_from(TimetableSlot).where(
            TimetableSlot.course_offering_id.in_(ids or [uuid.uuid4()])))).scalar_one())
        with_slots = int((await self.session.execute(select(func.count(func.distinct(TimetableSlot.course_offering_id))).where(
            TimetableSlot.course_offering_id.in_(ids or [uuid.uuid4()])))).scalar_one())
        drafts = list((await self.session.execute(select(TimetableDraft).where(TimetableDraft.status != "DISCARDED")
                                                  .order_by(TimetableDraft.created_at.desc()).limit(20))).scalars())
        tl = {t.lower() for t in terms}
        draft = next((d for d in drafts if {x.lower() for x in (d.terms or [])} & tl), None)
        from app.academics.terms import AcademicTerm

        ungrouped = list((await self.session.execute(select(AcademicTerm.label).where(
            func.lower(AcademicTerm.label).in_([t.lower() for t in terms]), AcademicTerm.session_id.is_(None)))).scalars())
        return {"terms": terms, "combine": await self.combine_suggestions(terms), "ungrouped": ungrouped,
                "periods": {"class": sum(1 for x in periods if x.holds_class), "breaks": sum(1 for x in periods if not x.holds_class)},
                "rooms": rooms,
                "sections": {"total": len(rows), "without_teacher": sum(1 for o, _c in rows if not o.assigned_teacher_id),
                             "labs": sum(1 for o, _c in rows if o.double_period), "with_timetable": with_slots},
                "teachers": {"total": len(teachers), "with_times": avail},
                "live_weekly_patterns": live,
                "draft": {"id": draft.id, "status": draft.status, "terms": draft.terms, "created_at": draft.created_at,
                          "published_from": draft.published_from} if draft else None}

    # ================================================================== per-section settings
    async def sections(self, terms: list[str]) -> list[dict]:
        terms = await self._terms(terms)
        rows = (await self.session.execute(select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id).where(
            func.lower(CourseOffering.term_label).in_([t.lower() for t in terms]))
            .order_by(CourseOffering.term_label, Course.code, CourseOffering.section_label))).all()
        groups = {o.combined_group_id for o, _c in rows if o.combined_group_id}
        mates: dict = {}
        for m in (await self.session.execute(select(CourseOffering).where(CourseOffering.combined_group_id.in_(groups or {uuid.uuid4()}))
                                             .order_by(CourseOffering.term_label, CourseOffering.section_label))).scalars():
            mates.setdefault(m.combined_group_id, []).append(m)
        could = {}
        for s in await self.combine_suggestions(terms):         # sections that could be combined with others of the same course
            for x in s["sections"]:
                could[x["id"]] = s
        return [{"id": o.id, "code": c.code, "title": c.title, "section": o.section_label, "term": o.term_label,
                 "credit_hours": c.credit_hours, "classes_per_week": o.classes_per_week, "per_week": default_per_week(c, o),
                 "double_period": o.double_period, "home_room": o.home_room, "combined": o.combined_group_id is not None,
                 "combined_with": [f"{m.term_label} ({m.section_label})" for m in mates.get(o.combined_group_id, []) if m.id != o.id],
                 "can_combine": ({"lead_id": could[o.id]["lead_id"], "join_ids": could[o.id]["join_ids"], "same_teacher": could[o.id]["same_teacher"],
                                  "teachers": could[o.id]["teachers"],
                                  "with": [f"{x['term']} ({x['section']})" for x in could[o.id]["sections"] if x["id"] != o.id]}
                                 if o.id in could else None)}
                for o, c in rows]

    async def update_section(self, offering_id: uuid.UUID, *, classes_per_week: int | None, double_period: bool, home_room: str | None,
                             actor_id: uuid.UUID) -> CourseOffering:
        from app.academics.combined import members

        o = await self.session.get(CourseOffering, offering_id)
        if o is None:
            raise ResourceNotFoundError("Course section not found.")
        if classes_per_week is not None and not 1 <= classes_per_week <= 7:
            raise ValidationAppError("Classes a week must be between 1 and 7.")
        home = " ".join((home_room or "").split()) or None
        if home:
            r = (await self.session.execute(select(Room).where(Room.key == room_key(home)))).scalar_one_or_none()
            if r is None:
                raise ValidationAppError(f"Room '{home}' is not on the room list.")
            home = r.name
        group = await members(self.session, o)            # a combined class is one class: the same settings in every section
        for m in group:
            before = {"classes_per_week": m.classes_per_week, "double_period": m.double_period, "home_room": m.home_room}
            m.classes_per_week, m.double_period, m.home_room = classes_per_week, bool(double_period), home
            await self.audit.log(actor_user_id=actor_id, action="course_offering.timetable_settings", entity_type="course_offering",
                                 entity_id=m.id, before=before, after={"classes_per_week": classes_per_week, "double_period": bool(double_period), "home_room": home})
        await self.session.flush()
        return o


# ====================================================================== teachers' unavailable times
class AvailabilityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def get(self, employee_id: uuid.UUID) -> dict:
        a = (await self.session.execute(select(TeacherAvailability).where(TeacherAvailability.employee_id == employee_id))).scalar_one_or_none()
        periods = list((await self.session.execute(select(Period).order_by(Period.start_time, Period.period_number))).scalars())
        used = set((await self.session.execute(select(TimetableSlot.day_of_week).distinct())).scalars())
        days = [d for d in WEEK if d in DEFAULT_DAYS or DayOfWeek(d) in used or d == "SATURDAY"]
        return {"employee_id": employee_id, "blocked": (a.blocked if a else []), "max_per_day": a.max_per_day if a else None,
                "max_in_row": a.max_in_row if a else None, "note": a.note if a else None, "locked": bool(a and a.locked),
                "updated_at": a.updated_at if a else None, "days": days,
                "periods": [{"id": p.id, "number": p.period_number, "start": hm12(p.start_time), "end": hm12(p.end_time),
                             "kind": p.kind, "label": p.label, "days": p.day_list} for p in periods]}

    async def set(self, employee_id: uuid.UUID, *, blocked: list[str], max_per_day: int | None, max_in_row: int | None,
                  note: str | None, actor_id: uuid.UUID, by_admin: bool, locked: bool | None = None) -> dict:
        from app.employees.models import Employee

        if await self.session.get(Employee, employee_id) is None:
            raise ResourceNotFoundError("Teacher not found.")
        periods = {str(pid) for pid in (await self.session.execute(select(Period.id))).scalars()}
        clean = []
        for cell in blocked or []:
            day, _, pid = str(cell).partition("|")
            if day not in WEEK or pid not in periods:
                raise ValidationAppError("Unknown day or period in the unavailable times.")
            if f"{day}|{pid}" not in clean:
                clean.append(f"{day}|{pid}")
        for name, v in (("classes a day", max_per_day), ("periods in a row", max_in_row)):
            if v is not None and not 1 <= v <= 12:
                raise ValidationAppError(f"The {name} limit must be between 1 and 12.")
        a = (await self.session.execute(select(TeacherAvailability).where(TeacherAvailability.employee_id == employee_id))).scalar_one_or_none()
        if a is not None and a.locked and not by_admin:
            raise BusinessRuleViolationError("The administration has fixed your available times. Ask them to change it.")
        before = None
        if a is None:
            a = TeacherAvailability(employee_id=employee_id)
            self.session.add(a)
        else:
            before = {"blocked": len(a.blocked or []), "max_per_day": a.max_per_day, "max_in_row": a.max_in_row, "locked": a.locked}
        a.blocked, a.max_per_day, a.max_in_row = clean, max_per_day, max_in_row
        a.note = (note or "").strip()[:300] or None
        a.updated_by = actor_id
        if by_admin and locked is not None:
            a.locked = bool(locked)
        await self.audit.log(actor_user_id=actor_id, action="teacher_availability.updated", entity_type="employee", entity_id=employee_id,
                             before=before, after={"blocked": len(clean), "max_per_day": max_per_day, "max_in_row": max_in_row,
                                                   "locked": a.locked, "by_admin": by_admin})
        await self.session.flush()
        return await self.get(employee_id)


def next_monday(today: date | None = None) -> date:
    today = today or institute_today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)
