"""The smart-timetable solver: pure Python, no database, so it can be tested on its own.

What it is given (built from the database by app/timetabling/service.py):
  * the week: for each day the class periods in order (breaks, assembly and self-study periods are left out);
  * units: one per course section, or one per COMBINED class (several sections taught together, placed once);
    each needs `need` meetings a week, each meeting `span` periods long (2 = a double period / lab); a section with
    some labs (theory + lab) has span 1 and `labs` = how many of its meetings are double periods in a lab room;
  * teachers: unavailable cells, at most N classes a day, at most M periods in a row;
  * rooms: name, kind (CLASS / LAB / HALL) and seats;
  * fixed: what is already busy and not being planned (other terms' classes at the same time of year,
    pinned classes): teacher / student-group / room / unit cells.

Hard rules (never broken): a student group, a teacher and a room are in one place at a time; a teacher's
unavailable times, classes-a-day and periods-in-a-row limits; one meeting of a course a day; a double period
is two back-to-back periods; a room is big enough (and a lab for a lab course, when labs are listed).

Soft rules (as well as it can): meetings spread over the week (not on neighbouring days), the same period
each day, compact days for students (few empty periods between classes), the section's home room.

How: the hardest units first (combined classes, labs, busy teachers, few possible times), each meeting in the
cheapest possible cell; a meeting that does not fit may push ONE other meeting somewhere else (repair); the
whole thing is repeated with small random changes for a few seconds and the best week is kept: first the
fewest meetings left out, then the lowest penalty. The same input always gives the same result (fixed seed).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

DAY_GAP_OK = 1          # meetings on neighbouring days are penalised; more than this many days apart is ideal
MAX_GAP_MIN = 15        # two periods are "back to back" when the second starts within this many minutes


@dataclass
class Slot:                       # one class period on one day
    pid: object                   # period id
    start: int                    # minutes from midnight
    end: int


@dataclass
class Unit:
    key: object                   # lead offering id (a combined class: its first section)
    label: str                    # "PHY101 A" for messages
    need: int                     # meetings a week
    span: int = 1                 # periods per meeting (2 = double period)
    teacher: object | None = None
    groups: frozenset = frozenset()       # student groups (cohort keys): two units sharing one cannot overlap
    avoid: frozenset = frozenset()        # other unit keys that share enrolled students
    students: int = 0
    lab: bool = False
    labs: int = 0                 # span 1 units only: this many of the `need` meetings are double periods (labs)
    home_room: str | None = None
    pinned: list = field(default_factory=list)   # [(day, period index, room[, span])] kept exactly as they are

    def spans(self) -> list[int]:
        """The length of each weekly meeting, labs first."""
        if self.span > 1:
            return [self.span] * self.need
        n = max(0, min(self.labs, self.need))
        return [2] * n + [1] * (self.need - n)

    def periods(self) -> int:
        return sum(self.spans())

    def has_double(self) -> bool:
        return self.span > 1 or self.labs > 0


@dataclass
class Teacher:
    blocked: set = field(default_factory=set)     # {(day, pid)}
    max_per_day: int | None = None
    max_in_row: int | None = None
    name: str = ""


@dataclass
class Room:
    name: str
    key: str
    kind: str = "CLASS"
    seats: int | None = None


@dataclass
class Placement:
    unit: object
    day: str
    idx: int                      # index of the first period of the meeting in that day's list
    span: int
    room: str
    pinned: bool = False


@dataclass
class Result:
    placements: list
    unplaced: list                # [{"unit": key, "label": str, "missing": n, "reason": str}]
    penalty: float
    rounds: int
    seconds: float


class Solver:
    # weights of the soft rules (penalty points)
    W_SPREAD = 6.0          # two meetings of a course on neighbouring days
    W_SAME = 1.5            # two meetings of a course in different periods
    W_GAP = 2.0             # an empty period between a student group's classes
    W_TGAP = 0.5            # ... between a teacher's classes
    W_HOME = 1.0            # not in the section's home room

    def __init__(self, *, days: list[str], periods: dict[str, list[Slot]], units: list[Unit], teachers: dict,
                 rooms: list[Room], fixed_teacher=(), fixed_group=(), fixed_room=(), fixed_unit=(),
                 default_max_per_day: int | None = None, default_max_in_row: int | None = None, seed: int = 7) -> None:
        self.days = list(days)
        self.periods = {d: sorted(periods.get(d, []), key=lambda s: s.start) for d in self.days}
        self.units = {u.key: u for u in units}
        self.teachers = teachers
        self.rooms = sorted(rooms, key=lambda r: (r.seats is None, r.seats or 0, r.name.lower()))
        self.labs_listed = any(r.kind == "LAB" for r in self.rooms)
        self.fixed_teacher = set(fixed_teacher)     # {(teacher, day, idx)}
        self.fixed_group = set(fixed_group)         # {(group, day, idx)}
        self.fixed_room = set(fixed_room)           # {(room key, day, idx)}
        self.fixed_unit = set(fixed_unit)           # {(unit key, day, idx)}: e.g. its students are busy elsewhere
        self.default_max_per_day = default_max_per_day
        self.default_max_in_row = default_max_in_row
        self.seed = seed
        self.day_pos = {d: i for i, d in enumerate(self.days)}
        # back-to-back check, per day
        self.joined = {d: [i + 1 < len(ps) and ps[i + 1].start - ps[i].end <= MAX_GAP_MIN for i in range(len(ps))]
                       for d, ps in self.periods.items()}

    # ------------------------------------------------------------------ state
    def _reset(self) -> None:
        self.t_occ: dict = {}            # (teacher, day, idx) -> unit
        self.g_occ: dict = {}            # (group, day, idx) -> unit
        self.r_occ: dict = {}            # (room key, day, idx) -> unit
        self.u_occ: dict = {}            # (unit, day, idx) -> True
        self.unit_days: dict = {k: {} for k in self.units}      # unit -> {day: Placement}
        self.t_day: dict = {}            # (teacher, day) -> set(idx)
        self.g_day: dict = {}            # (group, day) -> set(idx)
        self.placed: list[Placement] = []

    def _cells(self, day: str, idx: int, span: int):
        return range(idx, idx + span)

    def _max_day(self, t):
        x = self.teachers.get(t)
        return (x.max_per_day if x and x.max_per_day else None) or self.default_max_per_day

    def _max_row(self, t):
        x = self.teachers.get(t)
        return (x.max_in_row if x and x.max_in_row else None) or self.default_max_in_row

    def _run_after(self, have: set, add: range, day: str) -> int:
        cells = sorted(have | set(add))
        best = run = 0
        prev = None
        for c in cells:
            if prev is not None and c == prev + 1 and self.joined[day][prev]:
                run += 1
            else:
                run = 1
            best = max(best, run)
            prev = c
        return best

    @staticmethod
    def _is_lab(u: Unit, span: int) -> bool:
        return span > 1 or (u.lab and u.labs == 0)

    def _room_fits(self, u: Unit, r: Room, span: int | None = None) -> bool:
        span = u.span if span is None else span
        if r.seats is not None and u.students > r.seats:
            return False
        if self.labs_listed:
            lab = self._is_lab(u, span)
            return (r.kind == "LAB") == lab or (not lab and r.kind == "HALL")
        return True

    def next_span(self, u: Unit) -> int:
        """The length of the next meeting to place: the labs first, then the single periods."""
        if u.span > 1:
            return u.span
        doubles = sum(1 for p in self.unit_days[u.key].values() if p.span > 1)
        return 2 if doubles < min(u.labs, u.need) else 1

    def _free_room(self, u: Unit, day: str, idx: int, prefer: str | None = None, span: int | None = None):
        span = u.span if span is None else span
        cells = self._cells(day, idx, span)
        order = self.rooms
        if prefer and not (u.labs and span > 1):          # a theory + lab section's home room is for its theory classes
            order = sorted(self.rooms, key=lambda r: r.key != prefer)
        for r in order:
            if not self._room_fits(u, r, span):
                continue
            if all((r.key, day, c) not in self.r_occ and (r.key, day, c) not in self.fixed_room for c in cells):
                return r
        return None

    def blockers(self, u: Unit, day: str, idx: int, span: int | None = None) -> set | None:
        """None when the meeting can never go there (no such periods, unavailable, already a meeting that day by
        itself...). Otherwise the set of OTHER units in the way (empty = free), ignoring rooms."""
        span = u.span if span is None else span
        ps = self.periods[day]
        if idx + span > len(ps) or any(not self.joined[day][c] for c in range(idx, idx + span - 1)):
            return None
        cells = self._cells(day, idx, span)
        t = u.teacher
        out: set = set()
        for c in cells:
            if (u.key, day, c) in self.fixed_unit:
                return None
            if t is not None:
                if (t, day, c) in self.fixed_teacher:
                    return None
                tb = self.teachers.get(t)
                if tb and (day, ps[c].pid) in tb.blocked:
                    return None
                if (t, day, c) in self.t_occ:
                    out.add(self.t_occ[(t, day, c)])
            for g in u.groups:
                if (g, day, c) in self.fixed_group:
                    return None
                if (g, day, c) in self.g_occ:
                    out.add(self.g_occ[(g, day, c)])
            for o in u.avoid:
                if (o, day, c) in self.u_occ:
                    out.add(o)
        out.discard(u.key)
        return out

    def feasible(self, u: Unit, day: str, idx: int, span: int | None = None):
        """The room it would get, or None."""
        span = u.span if span is None else span
        if day in self.unit_days[u.key]:
            return None
        b = self.blockers(u, day, idx, span)
        if b is None or b:
            return None
        t = u.teacher
        if t is not None:
            have = self.t_day.get((t, day), set())
            m = self._max_day(t)
            if m and len(have) + span > m:
                return None
            r = self._max_row(t)
            if r and self._run_after(have, self._cells(day, idx, span), day) > r:
                return None
        return self._free_room(u, day, idx, u.home_room, span)

    def _place(self, u: Unit, day: str, idx: int, room: Room | str, pinned: bool = False, span: int | None = None) -> Placement:
        span = u.span if span is None else span
        name = room.name if isinstance(room, Room) else room
        rkey = room.key if isinstance(room, Room) else "".join(room.split()).lower()
        p = Placement(u.key, day, idx, span, name, pinned)
        for c in self._cells(day, idx, span):
            if u.teacher is not None:
                self.t_occ[(u.teacher, day, c)] = u.key
                self.t_day.setdefault((u.teacher, day), set()).add(c)
            for g in u.groups:
                self.g_occ[(g, day, c)] = u.key
                self.g_day.setdefault((g, day), set()).add(c)
            self.r_occ[(rkey, day, c)] = u.key
            self.u_occ[(u.key, day, c)] = True
        self.unit_days[u.key][day] = p
        self.placed.append(p)
        return p

    def _remove(self, p: Placement) -> None:
        u = self.units[p.unit]
        rkey = "".join(p.room.split()).lower()
        for c in self._cells(p.day, p.idx, p.span):
            if u.teacher is not None:
                self.t_occ.pop((u.teacher, p.day, c), None)
                self.t_day.get((u.teacher, p.day), set()).discard(c)
            for g in u.groups:
                self.g_occ.pop((g, p.day, c), None)
                self.g_day.get((g, p.day), set()).discard(c)
            self.r_occ.pop((rkey, p.day, c), None)
            self.u_occ.pop((u.key, p.day, c), None)
        self.unit_days[u.key].pop(p.day, None)
        self.placed.remove(p)

    # ------------------------------------------------------------------ cost of a placement (soft rules)
    def cost(self, u: Unit, day: str, idx: int, room: Room, span: int | None = None) -> float:
        span = u.span if span is None else span
        c = 0.0
        mine = self.unit_days[u.key]
        pos = self.day_pos[day]
        for d2, p2 in mine.items():
            apart = abs(self.day_pos[d2] - pos)
            if apart <= DAY_GAP_OK:
                c += self.W_SPREAD                                # neighbouring days
            if p2.idx != idx and p2.span == span:
                c += self.W_SAME                                  # not the same period as its other days (labs aside)
        if not mine and len(self.days) and u.need >= 2:
            c += 0.2 * pos                                       # first meeting early in the week leaves room to spread
        cells = set(self._cells(day, idx, span))
        for g in u.groups:
            have = self.g_day.get((g, day), set())
            c += self.W_GAP * (self._gaps(have | cells) - self._gaps(have))  # empty periods between a group's classes
        if u.teacher is not None:
            have = self.t_day.get((u.teacher, day), set())
            c += self.W_TGAP * (self._gaps(have | cells) - self._gaps(have))
        c += 0.05 * idx                                          # a little earlier is better
        if u.home_room and room.key != u.home_room and not (u.labs and span > 1):
            c += self.W_HOME
        return c

    @staticmethod
    def _gaps(cells: set) -> int:
        if len(cells) < 2:
            return 0
        return (max(cells) - min(cells) + 1) - len(cells)

    def total_penalty(self) -> float:
        total = 0.0
        for k, days in self.unit_days.items():
            u = self.units[k]
            ps = list(days.values())
            for i, a in enumerate(ps):
                for b in ps[i + 1:]:
                    if abs(self.day_pos[a.day] - self.day_pos[b.day]) <= DAY_GAP_OK:
                        total += self.W_SPREAD
                    if a.idx != b.idx and a.span == b.span:
                        total += self.W_SAME
                if u.home_room and "".join(a.room.split()).lower() != u.home_room and not (u.labs and a.span > 1):
                    total += self.W_HOME
        for cells in self.g_day.values():
            total += self.W_GAP * self._gaps(cells)
        for cells in self.t_day.values():
            total += self.W_TGAP * self._gaps(cells)
        return round(total, 2)

    # ------------------------------------------------------------------ search
    def _options(self, u: Unit, span: int | None = None):
        span = self.next_span(u) if span is None else span
        out = []
        for day in self.days:
            for idx in range(len(self.periods[day])):
                room = self.feasible(u, day, idx, span)
                if room is not None:
                    out.append((self.cost(u, day, idx, room, span), day, idx, room, span))
        out.sort(key=lambda x: (x[0], self.day_pos[x[1]], x[2]))
        return out

    def remaining(self, u: Unit) -> list[int]:
        """The lengths of the meetings still to place (labs first)."""
        left = list(u.spans())
        for p in self.unit_days[u.key].values():
            if p.span in left:
                left.remove(p.span)
            elif left:
                left.pop()
        return left

    def _place_span(self, u: Unit, span: int, rnd: random.Random, noise: float) -> bool:
        opts = self._options(u, span)
        if not opts:
            return False
        pick = opts[rnd.randrange(min(3, len(opts)))] if noise and len(opts) > 1 and rnd.random() < noise else opts[0]
        self._place(u, pick[1], pick[2], pick[3], span=pick[4])
        return True

    def _place_best(self, u: Unit, rnd: random.Random, noise: float) -> bool:
        opts = self._options(u)
        if not opts:
            return False
        if noise and len(opts) > 1 and rnd.random() < noise:
            pick = opts[rnd.randrange(min(3, len(opts)))]
        else:
            pick = opts[0]
        self._place(u, pick[1], pick[2], pick[3], span=pick[4])
        return True

    def _repair(self, u: Unit, rnd: random.Random, span: int | None = None) -> bool:
        """Put one meeting of u in a cell where exactly ONE other (not pinned) meeting is in the way, then find that
        meeting another place. Undone when the other meeting cannot be placed again."""
        span = self.next_span(u) if span is None else span
        cands = []
        for day in self.days:
            if day in self.unit_days[u.key]:
                continue
            for idx in range(len(self.periods[day])):
                b = self.blockers(u, day, idx, span)
                if b is None or len(b) != 1:
                    continue
                other = next(iter(b))
                cells = set(self._cells(day, idx, span))
                hit = [p for p in self.unit_days[other].values() if p.day == day and cells & set(self._cells(p.day, p.idx, p.span))]
                if len(hit) != 1 or hit[0].pinned:
                    continue
                cands.append((day, idx, hit[0]))
        rnd.shuffle(cands)
        for day, idx, victim in cands[:12]:
            self._remove(victim)
            vu = self.units[victim.unit]
            room = self.feasible(u, day, idx, span)
            if room is None:
                self._place(vu, victim.day, victim.idx, victim.room, span=victim.span)
                continue
            mine = self._place(u, day, idx, room, span=span)
            opts = self._options(vu, victim.span)
            if opts:
                self._place(vu, opts[0][1], opts[0][2], opts[0][3], span=victim.span)
                return True
            self._remove(mine)
            self._place(vu, victim.day, victim.idx, victim.room, span=victim.span)
        return False

    def _difficulty(self, u: Unit) -> float:
        free = 0
        for day in self.days:
            for idx in range(len(self.periods[day])):
                if self.blockers(u, day, idx, max(u.spans() or [1])) is not None:
                    free += 1
        load = 0
        if u.teacher is not None:
            load = sum(x.periods() for x in self.units.values() if x.teacher == u.teacher)
        return u.periods() / max(1, free) * 10 + len(u.groups) + (3 if u.has_double() else 0) + load / 20

    def _one_round(self, rnd: random.Random, noise: float) -> tuple[list, float]:
        self._reset()
        for u in self.units.values():
            for pin in u.pinned:
                self._place(u, pin[0], pin[1], pin[2], pinned=True, span=pin[3] if len(pin) > 3 else None)
        order = sorted(self.units.values(), key=lambda u: -(self.base_difficulty[u.key] + (rnd.random() * 2 if noise else 0)))
        short = []
        for u in order:
            for span in self.remaining(u):
                if not self._place_span(u, span, rnd, noise) and not self._repair(u, rnd, span):
                    short.append(u.key)
        self._improve()
        return short, self.total_penalty()

    def _improve(self, passes: int = 3) -> None:
        """Hill climbing: take each meeting out and put it back in the cheapest place (often where it was)."""
        for _ in range(passes):
            moved = 0
            for p in list(self.placed):
                if p.pinned or p not in self.placed:
                    continue
                u = self.units[p.unit]
                self._remove(p)
                room_back = next((r for r in self.rooms if r.key == "".join(p.room.split()).lower()), None)
                here = self.cost(u, p.day, p.idx, room_back, p.span) if room_back else None
                opts = self._options(u, p.span)
                if opts and (here is None or opts[0][0] < here - 0.01):
                    self._place(u, opts[0][1], opts[0][2], opts[0][3], span=p.span)
                    moved += 1
                else:
                    self._place(u, p.day, p.idx, p.room, span=p.span)
            if not moved:
                break

    def solve(self, *, seconds: float = 8.0, max_rounds: int = 400) -> Result:
        t0 = time.monotonic()
        rnd = random.Random(self.seed)
        self._reset()
        self.base_difficulty = {u.key: self._difficulty(u) for u in self.units.values()}
        best = None
        rounds = since_better = 0
        while rounds < max_rounds:
            short, pen = self._one_round(rnd, 0.0 if rounds == 0 else 0.25)
            rounds += 1
            score = (len(short), pen)
            if best is None or score < best[0]:
                best = (score, list(self.placed), short)
                since_better = 0
            else:
                since_better += 1
            if score == (0, 0.0) or since_better >= 60 or time.monotonic() - t0 > seconds:
                break
        _score, placed, short = best
        # put the best week back, so explain() sees it
        self._reset()
        for p in placed:
            self._place(self.units[p.unit], p.day, p.idx, p.room, pinned=p.pinned, span=p.span)
        unplaced = []
        for key in dict.fromkeys(short):
            u = self.units[key]
            missing = u.need - len(self.unit_days[key])
            if missing > 0:
                unplaced.append({"unit": key, "label": u.label, "missing": missing, "reason": self.explain(u)})
        return Result(placements=sorted(placed, key=lambda p: (self.day_pos[p.day], p.idx, str(p.unit))), unplaced=unplaced,
                      penalty=self.total_penalty(), rounds=rounds, seconds=round(time.monotonic() - t0, 2))

    # ------------------------------------------------------------------ why something did not fit
    def explain(self, u: Unit) -> str:
        for sp in sorted(set(u.spans()), reverse=True):
            if not any(self._room_fits(u, r, sp) for r in self.rooms):
                if u.students and all(r.seats is not None and r.seats < u.students for r in self.rooms):
                    return f"No room has {u.students} seats."
                return "No lab is listed for this lab course." if self._is_lab(u, sp) else "No suitable room is listed."
        if u.need > len(self.days):
            return f"It needs {u.need} classes a week but only {len(self.days)} days are chosen (one class of a course a day)."
        week = sum(len(ps) for ps in self.periods.values())
        if u.teacher is not None:
            tb = self.teachers.get(u.teacher)
            load = sum(x.periods() for x in self.units.values() if x.teacher == u.teacher)
            free = week - len(tb.blocked if tb else ())
            m = self._max_day(u.teacher)
            if m:
                free = min(free, m * len(self.days))
            if load > free:
                who = tb.name if tb and tb.name else "The teacher"
                return f"{who} has {load} periods to teach but only {free} free periods a week (unavailable times / classes-a-day limit)."
        for g in u.groups:
            load = sum(x.periods() for x in self.units.values() if g in x.groups)
            if load > week:
                return f"These students have {load} periods of classes but the week has only {week} class periods."
        if u.has_double() and not any(any(self.joined[d][:-1] or [False]) for d in self.days):
            return "No two class periods are back to back, so a double period cannot be placed."
        return "No time is free for the teacher, the students and a room together. Try fewer classes a week, another day, or ease the teacher's limits."
