"""The smart-timetable solver on its own (no database): hard rules are never broken, soft rules are followed."""
from __future__ import annotations

import itertools

from app.timetabling.solver import Room, Slot, Solver, Teacher, Unit

DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]


def week(n=6, start=8 * 60, length=50, gap=0, lunch_after=None):
    out, t = [], start
    for i in range(n):
        out.append(Slot(f"p{i + 1}", t, t + length))
        t += length + gap
        if lunch_after is not None and i + 1 == lunch_after:
            t += 40
    return {d: list(out) for d in DAYS}


ROOMS = [Room("R1", "r1", "CLASS", 50), Room("R2", "r2", "CLASS", 50), Room("R3", "r3", "CLASS", 50), Room("Lab", "lab", "LAB", 40)]


def check_hard(solver: Solver, res) -> None:
    units = solver.units
    by_cell = {}
    per_day = {}
    for p in res.placements:
        u = units[p.unit]
        assert (p.unit, p.day) not in per_day, f"{u.label} twice on {p.day}"
        per_day[(p.unit, p.day)] = p
        for c in range(p.idx, p.idx + p.span):
            by_cell.setdefault((p.day, c), []).append((u, p))
    for (day, c), here in by_cell.items():
        for (a, pa), (b, pb) in itertools.combinations(here, 2):
            assert not (a.teacher and a.teacher == b.teacher), f"teacher clash {a.label}/{b.label} {day} {c}"
            assert not (a.groups & b.groups), f"student clash {a.label}/{b.label} {day} {c}"
            assert b.key not in a.avoid, f"shared students {a.label}/{b.label}"
            assert pa.room != pb.room, f"room clash {pa.room} {day} {c}"
        for u, _p in here:
            t = solver.teachers.get(u.teacher)
            if t:
                assert (day, solver.periods[day][c].pid) not in t.blocked


def test_a_term_fits_and_no_rule_is_broken():
    units = []
    # 4 sections (cohorts), 5 courses each, 3 classes a week, 6 teachers shared between cohorts
    for g in range(4):
        for k in range(5):
            units.append(Unit(key=f"g{g}c{k}", label=f"G{g}-C{k}", need=3, teacher=f"t{(g + k) % 6}", groups=frozenset({f"g{g}"}), students=40))
    s = Solver(days=DAYS, periods=week(), units=units, teachers={}, rooms=ROOMS, default_max_per_day=4, default_max_in_row=3)
    res = s.solve(seconds=60, max_rounds=150)
    assert res.unplaced == []
    assert len(res.placements) == 60
    check_hard(s, res)
    # teacher limits
    per_teacher_day = {}
    for p in res.placements:
        t = s.units[p.unit].teacher
        per_teacher_day[(t, p.day)] = per_teacher_day.get((t, p.day), 0) + p.span
    assert max(per_teacher_day.values()) <= 4
    # spread: most 3-a-week courses are Mon/Wed/Fri-like (not every one can be: 3 classrooms x 6 periods = 18 rooms a day
    # for 20 classes, so a few must use Tuesday / Thursday)
    spread = 0
    for u in units:
        days = sorted(DAYS.index(p.day) for p in res.placements if p.unit == u.key)
        spread += all(b - a >= 2 for a, b in zip(days, days[1:]))
    assert spread >= 12, spread
    assert sum(s._gaps(c) for c in s.g_day.values()) <= 6        # and students' days stay compact


def test_unavailable_times_and_double_periods_in_a_lab():
    blocked = {(d, f"p{i}") for d in DAYS for i in (1, 2, 3)}       # only after 3rd period
    units = [Unit(key="lab", label="BIO-L", need=2, span=2, teacher="t1", groups=frozenset({"g"}), students=30, lab=True),
             Unit(key="th", label="BIO", need=3, teacher="t2", groups=frozenset({"g"}), students=30)]
    s = Solver(days=DAYS, periods=week(), units=units, teachers={"t1": Teacher(blocked=blocked)}, rooms=ROOMS)
    res = s.solve(seconds=2)
    assert res.unplaced == []
    check_hard(s, res)
    for p in res.placements:
        if p.unit == "lab":
            assert p.span == 2 and p.room == "Lab" and p.idx >= 3
        else:
            assert p.room != "Lab"                                  # labs are kept for lab classes


def test_double_period_never_spans_a_break():
    periods = {d: [Slot("p1", 480, 530), Slot("p2", 530, 580), Slot("p3", 620, 670), Slot("p4", 670, 720)] for d in DAYS}
    u = Unit(key="lab", label="L", need=5, span=2, teacher="t", groups=frozenset({"g"}), lab=True)
    s = Solver(days=DAYS, periods=periods, units=[u], teachers={}, rooms=ROOMS)
    res = s.solve(seconds=1)
    assert res.unplaced == [] and all(p.idx in (0, 2) for p in res.placements)


def test_what_does_not_fit_is_reported_with_a_reason():
    # one teacher, 3 courses x 5 a week = 15 periods, but at most 2 a day x 5 days = 10
    units = [Unit(key=f"c{k}", label=f"C{k}", need=5, teacher="t", groups=frozenset({f"g{k}"})) for k in range(3)]
    s = Solver(days=DAYS, periods=week(), units=units, teachers={"t": Teacher(name="Dr. Ali", max_per_day=2)}, rooms=ROOMS)
    res = s.solve(seconds=2)
    assert len(res.placements) == 10
    assert sum(x["missing"] for x in res.unplaced) == 5
    assert "Dr. Ali has 15 periods to teach but only 10" in res.unplaced[0]["reason"]
    check_hard(s, res)


def test_fixed_classes_and_pinned_ones_are_respected():
    u1 = Unit(key="a", label="A", need=2, teacher="t", groups=frozenset({"g"}), pinned=[("MONDAY", 0, "R2")])
    u2 = Unit(key="b", label="B", need=3, teacher="t2", groups=frozenset({"g"}))
    fixed_group_busy = {("b", d, 0) for d in DAYS} | {("b", d, 1) for d in DAYS}
    s = Solver(days=DAYS, periods=week(), units=[u1, u2], teachers={}, rooms=ROOMS, fixed_unit=fixed_group_busy,
               fixed_room={("r1", "TUESDAY", 0)})
    res = s.solve(seconds=1)
    assert res.unplaced == []
    pin = [p for p in res.placements if p.unit == "a" and p.pinned]
    assert len(pin) == 1 and (pin[0].day, pin[0].idx, pin[0].room) == ("MONDAY", 0, "R2")
    assert all(p.idx >= 2 for p in res.placements if p.unit == "b")
    assert not any(p.day == "TUESDAY" and p.idx == 0 and p.room == "R1" for p in res.placements)


def test_room_size_and_same_period_preference():
    big = Unit(key="big", label="BIG", need=3, teacher="t", groups=frozenset({"g"}), students=120)
    s = Solver(days=DAYS, periods=week(), units=[big], teachers={}, rooms=ROOMS + [Room("Hall", "hall", "HALL", 150)])
    res = s.solve(seconds=1)
    assert {p.room for p in res.placements} == {"Hall"}
    assert len({p.idx for p in res.placements}) == 1                 # same time every day
    no_room = Unit(key="huge", label="HUGE", need=1, teacher="t", groups=frozenset({"h"}), students=500)
    s2 = Solver(days=DAYS, periods=week(), units=[no_room], teachers={}, rooms=ROOMS)
    r2 = s2.solve(seconds=1)
    assert r2.unplaced and "No room has 500 seats" in r2.unplaced[0]["reason"]


def test_same_input_same_result():
    def run():
        units = [Unit(key=f"g{g}c{k}", label="x", need=3, teacher=f"t{(g * 3 + k) % 5}", groups=frozenset({f"g{g}"})) for g in range(3) for k in range(4)]
        s = Solver(days=DAYS, periods=week(), units=units, teachers={}, rooms=ROOMS)
        return [(p.unit, p.day, p.idx, p.room) for p in s.solve(seconds=60, max_rounds=30).placements]
    assert run() == run()
