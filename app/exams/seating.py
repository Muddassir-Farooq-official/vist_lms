"""Exam seating and room invigilation.

Rooms: an exam room has rows x columns of seats (rooms.exam_rows / exam_cols, set on the Seating page). Seat labels are
"R<row>-C<col>".

Seating, per datesheet and time slot (papers that start at the same date and time):
  * every student enrolled in each paper's class (all sections of a combined class) gets one seat;
  * rooms are filled column by column; neighbouring columns get different papers wherever more than one paper sits in
    the slot (so neighbours left and right never write the same paper); one paper alone fills in roll-number order;
  * made again any time before the slot starts (the old seats are replaced); the paper's room is set to the rooms used.

Invigilation per room and slot (exam_room_duties): the system proposes 1 per N students (Settings ->
exam_students_per_invigilator, at least 1), choosing free teachers (no class or other duty then), never the teacher of a
paper seated in that room, fewest duties in this datesheet first; the office edits and then tells the teachers. The
teachers on a room's duty can mark the exam roll of the papers seated in that room (app/exams/duty.py _access).
Existing per-paper duties stay as they are.
"""
from __future__ import annotations

import io
import math
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timezone

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Time, UniqueConstraint, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.database import Base
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.exams.models import DutyRole


class ExamSeat(Base):
    __tablename__ = "exam_seats"
    __table_args__ = (UniqueConstraint("paper_id", "student_id", name="uq_exam_seats_paper_student"),
                      UniqueConstraint("exam_date", "start_time", "room_id", "seat_row", "seat_col", name="uq_exam_seats_place"),
                      Index("ix_exam_seats_schedule_slot", "schedule_id", "exam_date", "start_time"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_schedules.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_papers.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False)
    exam_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    seat_row: Mapped[int] = mapped_column(Integer, nullable=False)
    seat_col: Mapped[int] = mapped_column(Integer, nullable=False)


class ExamRoomDuty(Base):
    """A teacher on duty in one room for one time slot (every paper seated there)."""
    __tablename__ = "exam_room_duties"
    __table_args__ = (UniqueConstraint("exam_date", "start_time", "room_id", "employee_id", name="uq_exam_room_duties_slot_room_employee"),
                      Index("ix_exam_room_duties_employee", "employee_id"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False)
    exam_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[DutyRole] = mapped_column(Enum(DutyRole, name="exam_duty_role"), nullable=False, default=DutyRole.ASSISTANT)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    told_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def seat_label(row: int, col: int) -> str:
    return f"R{row}-C{col}"


def _hm(t: time) -> str:
    return t.strftime("%H:%M")


def arrange(rooms: list[tuple], groups: list[tuple]) -> list[tuple]:
    """Pure seating: rooms [(room_id, rows, cols)], groups [(paper_id, [student_id ... in roll order])] ->
    [(paper_id, student_id, room_id, row, col)]. Column by column; a column takes the paper with the most students left
    that differs from the previous column's paper (the same paper only when it is the only one left)."""
    left = {pid: list(students) for pid, students in groups}
    order = [pid for pid, _ in groups]
    out = []
    prev = None
    for room_id, rows, cols in rooms:
        for col in range(1, cols + 1):
            live = [p for p in order if left[p]]
            if not live:
                return out
            others = [p for p in live if p != prev] or live
            pid = max(others, key=lambda p: (len(left[p]), -order.index(p)))
            for row in range(1, rows + 1):
                if not left[pid]:
                    break
                out.append((pid, left[pid].pop(0), room_id, row, col))
            prev = pid
        prev = None                                   # a new room: no neighbour from the last one
    return out


class SeatingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ rooms
    async def rooms(self) -> list[dict]:
        from app.academics.rooms import Room

        rows = (await self.session.execute(select(Room).where(Room.is_active.is_(True)).order_by(func.lower(Room.name)))).scalars().all()
        return [{"id": r.id, "name": r.name, "kind": r.kind, "seats": r.seats, "exam_rows": r.exam_rows, "exam_cols": r.exam_cols,
                 "exam_seats": (r.exam_rows or 0) * (r.exam_cols or 0)} for r in rows]

    async def set_room(self, room_id: uuid.UUID, *, rows: int | None, cols: int | None, actor_id: uuid.UUID) -> dict:
        from app.academics.rooms import Room
        from app.audit.service import AuditService

        r = await self.session.get(Room, room_id)
        if r is None:
            raise ResourceNotFoundError("Room not found.")
        if (rows is None) != (cols is None):
            raise ValidationAppError("Give both rows and columns (or clear both).")
        if rows is not None and not (1 <= rows <= 60 and 1 <= cols <= 30):
            raise ValidationAppError("Rows 1-60 and columns 1-30.")
        before = {"rows": r.exam_rows, "cols": r.exam_cols}
        r.exam_rows, r.exam_cols = rows, cols
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="room.exam_layout_set", entity_type="room", entity_id=r.id,
                                             before=before, after={"rows": rows, "cols": cols})
        return {"id": r.id, "exam_rows": rows, "exam_cols": cols}

    # ------------------------------------------------------------------ slots
    async def _schedule(self, schedule_id: uuid.UUID):
        from app.exams.models import ExamSchedule

        s = await self.session.get(ExamSchedule, schedule_id)
        if s is None:
            raise ResourceNotFoundError("Datesheet not found.")
        return s

    async def _slot_papers(self, schedule_id: uuid.UUID, exam_date: date, start_time: time) -> list:
        from app.exams.models import ExamPaper

        return list((await self.session.execute(select(ExamPaper).where(ExamPaper.schedule_id == schedule_id, ExamPaper.exam_date == exam_date,
                                                                        ExamPaper.start_time == start_time))).scalars())

    async def _sitting(self, paper) -> list[uuid.UUID]:
        from app.academics.combined import member_ids
        from app.academics.models import Enrollment, EnrollmentStatus
        from app.students.models import Student

        group = await member_ids(self.session, paper.course_offering_id)
        # a suspended (or expelled) student does not sit the paper: left out of the seating plan
        from app.students.standing import LOCKED

        return list((await self.session.execute(select(Student.id).join(Enrollment, Enrollment.student_id == Student.id).where(
            Enrollment.course_offering_id.in_(group), Enrollment.status == EnrollmentStatus.ENROLLED, Student.status.not_in(list(LOCKED)))
            .order_by(Student.roll_number))).scalars())

    async def _labels(self, papers) -> dict:
        from app.academics.models import Course, CourseOffering

        rows = (await self.session.execute(select(CourseOffering.id, Course.code, CourseOffering.section_label).join(Course, Course.id == CourseOffering.course_id)
                                           .where(CourseOffering.id.in_([p.course_offering_id for p in papers] or [uuid.uuid4()])))).all()
        by = {oid: (code if not sec or sec == "A" else f"{code}-{sec}") for oid, code, sec in rows}
        return {p.id: by.get(p.course_offering_id, "?") for p in papers}

    async def slots(self, schedule_id: uuid.UUID) -> dict:
        """Every time slot of the datesheet: papers, students, seated, rooms used, invigilators."""
        from app.academics.rooms import Room
        from app.exams.models import ExamPaper

        sched = await self._schedule(schedule_id)
        papers = list((await self.session.execute(select(ExamPaper).where(ExamPaper.schedule_id == schedule_id)
                                                  .order_by(ExamPaper.exam_date, ExamPaper.start_time))).scalars())
        labels = await self._labels(papers)
        seated = dict((await self.session.execute(select(ExamSeat.paper_id, func.count()).where(ExamSeat.schedule_id == schedule_id)
                                                  .group_by(ExamSeat.paper_id))).all())
        room_names = {r.id: r.name for r in (await self.session.execute(select(Room))).scalars()}
        used = defaultdict(set)
        for d, t, rid in (await self.session.execute(select(ExamSeat.exam_date, ExamSeat.start_time, ExamSeat.room_id).where(
                ExamSeat.schedule_id == schedule_id).distinct())).all():
            used[(d, t)].add(rid)
        duties = defaultdict(int)
        for d, t, n in (await self.session.execute(select(ExamRoomDuty.exam_date, ExamRoomDuty.start_time, func.count()).where(
                ExamRoomDuty.schedule_id == schedule_id).group_by(ExamRoomDuty.exam_date, ExamRoomDuty.start_time))).all():
            duties[(d, t)] = n
        slots: dict = {}
        for p in papers:
            key = (p.exam_date, p.start_time)
            s = slots.setdefault(key, {"exam_date": p.exam_date, "start_time": _hm(p.start_time), "end_time": _hm(p.end_time), "papers": [],
                                       "students": 0, "seated": 0})
            n = len(await self._sitting(p))
            s["papers"].append({"id": p.id, "label": labels[p.id], "students": n, "seated": int(seated.get(p.id, 0))})
            s["students"] += n
            s["seated"] += int(seated.get(p.id, 0))
            if _hm(p.end_time) > s["end_time"]:
                s["end_time"] = _hm(p.end_time)
        out = []
        for key, s in sorted(slots.items()):
            s["rooms"] = sorted(room_names.get(r, "?") for r in used[key])
            s["invigilators"] = duties[key]
            s["done"] = s["students"] > 0 and s["seated"] == s["students"]
            out.append(s)
        return {"schedule": {"id": sched.id, "name": sched.name, "term_label": sched.term_label, "status": sched.status.value},
                "slots": out, "rooms": await self.rooms(), "per_invigilator": max(1, int(getattr(settings, "exam_students_per_invigilator", 30) or 30))}

    def _started(self, exam_date: date, start_time: time) -> bool:
        from app.core.clock import institute_now

        now = institute_now()
        return (now.date(), now.time().replace(tzinfo=None)) >= (exam_date, start_time)

    async def seat(self, schedule_id: uuid.UUID, *, exam_date: date, start_time: time, room_ids: list[uuid.UUID] | None, actor_id: uuid.UUID) -> dict:
        """Make (or remake) the seating of one slot. room_ids: the rooms to use in this order (default: every exam room)."""
        from app.academics.rooms import Room
        from app.audit.service import AuditService

        sched = await self._schedule(schedule_id)
        papers = await self._slot_papers(schedule_id, exam_date, start_time)
        if not papers:
            raise ResourceNotFoundError("No paper starts at that time on this datesheet.")
        if self._started(exam_date, start_time):
            raise BusinessRuleViolationError("These papers have started: the seating can no longer change.")
        all_rooms = {r.id: r for r in (await self.session.execute(select(Room).where(Room.is_active.is_(True), Room.exam_rows.is_not(None),
                                                                                     Room.exam_cols.is_not(None)))).scalars()}
        if room_ids:
            bad = [i for i in room_ids if i not in all_rooms]
            if bad:
                raise ValidationAppError("A chosen room has no rows and columns set (or is not active).")
            rooms = [all_rooms[i] for i in dict.fromkeys(room_ids)]
        else:
            rooms = sorted(all_rooms.values(), key=lambda r: r.name.lower())
        if not rooms:
            raise BusinessRuleViolationError("Set rows and columns for the exam rooms first.")
        # rooms already used by another datesheet at an overlapping time are not free
        clash = set((await self.session.execute(select(ExamSeat.room_id).where(ExamSeat.exam_date == exam_date, ExamSeat.start_time == start_time,
                                                                               ExamSeat.schedule_id != schedule_id))).scalars())
        rooms = [r for r in rooms if r.id not in clash]
        groups = [(p.id, await self._sitting(p)) for p in papers]
        groups.sort(key=lambda g: -len(g[1]))
        need = sum(len(g[1]) for g in groups)
        cap = sum(r.exam_rows * r.exam_cols for r in rooms)
        if need > cap:
            raise BusinessRuleViolationError(f"{need} students but only {cap} seats in the chosen rooms. Add rooms (or rows/columns).")
        # one student sitting two papers at the same time cannot have two seats
        seen: dict = {}
        for pid, studs in groups:
            for s in studs:
                if s in seen:
                    raise BusinessRuleViolationError("A student sits two papers at the same time on this datesheet: fix the datesheet first.")
                seen[s] = pid
        placed = arrange([(r.id, r.exam_rows, r.exam_cols) for r in rooms], groups)
        async with self.session.begin_nested():
            await self.session.execute(delete(ExamSeat).where(ExamSeat.paper_id.in_([p.id for p in papers])))
            for pid, sid, rid, row, col in placed:
                self.session.add(ExamSeat(schedule_id=schedule_id, paper_id=pid, student_id=sid, room_id=rid, exam_date=exam_date,
                                          start_time=start_time, seat_row=row, seat_col=col))
            by_paper = defaultdict(list)
            names = {r.id: r.name for r in rooms}
            for pid, _s, rid, _r, _c in placed:
                if names[rid] not in by_paper[pid]:
                    by_paper[pid].append(names[rid])
            for p in papers:
                if by_paper[p.id]:
                    p.room = ", ".join(by_paper[p.id])[:50]
            await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="exam.seating_made", entity_type="exam_schedule", entity_id=sched.id,
                                             metadata={"date": exam_date.isoformat(), "start": _hm(start_time), "students": len(placed),
                                                       "rooms": [r.name for r in rooms if any(x[2] == r.id for x in placed)]})
        return {"seated": len(placed), "rooms": sorted({names[x[2]] for x in placed}), "papers": len(papers)}

    async def room_plan(self, schedule_id: uuid.UUID, exam_date: date, start_time: time) -> dict:
        """The grid of each room used in the slot, and its invigilators."""
        from app.academics.rooms import Room
        from app.employees.models import Employee
        from app.students.models import Student

        papers = await self._slot_papers(schedule_id, exam_date, start_time)
        labels = await self._labels(papers)
        seats = (await self.session.execute(select(ExamSeat, Student, Room).join(Student, Student.id == ExamSeat.student_id).join(Room, Room.id == ExamSeat.room_id)
                                            .where(ExamSeat.schedule_id == schedule_id, ExamSeat.exam_date == exam_date, ExamSeat.start_time == start_time)
                                            .order_by(func.lower(Room.name), ExamSeat.seat_col, ExamSeat.seat_row))).all()
        rooms: dict = {}
        for s, st, r in seats:
            room = rooms.setdefault(r.id, {"room_id": r.id, "name": r.name, "rows": r.exam_rows, "cols": r.exam_cols, "seats": [], "papers": defaultdict(int),
                                           "invigilators": []})
            room["seats"].append({"seat": seat_label(s.seat_row, s.seat_col), "row": s.seat_row, "col": s.seat_col, "paper": labels.get(s.paper_id, "?"),
                                  "student_id": st.id, "roll_number": st.roll_number, "name": f"{st.first_name} {st.last_name}"})
            room["papers"][labels.get(s.paper_id, "?")] += 1
        for d, e in (await self.session.execute(select(ExamRoomDuty, Employee).join(Employee, Employee.id == ExamRoomDuty.employee_id).where(
                ExamRoomDuty.schedule_id == schedule_id, ExamRoomDuty.exam_date == exam_date, ExamRoomDuty.start_time == start_time))).all():
            if d.room_id in rooms:
                rooms[d.room_id]["invigilators"].append({"employee_id": e.id, "name": f"{e.first_name} {e.last_name}", "role": d.role.value,
                                                         "told": d.told_at is not None})
        out = []
        for r in rooms.values():
            r["papers"] = dict(r["papers"])
            r["students"] = len(r["seats"])
            r["needed"] = self.needed(r["students"])
            r["invigilators"].sort(key=lambda x: (x["role"] != "IN_CHARGE", x["name"]))
            out.append(r)
        return {"exam_date": exam_date, "start_time": _hm(start_time), "papers": [{"id": p.id, "label": labels[p.id]} for p in papers], "rooms": out}

    # ------------------------------------------------------------------ invigilators
    @staticmethod
    def needed(students: int) -> int:
        per = max(1, int(getattr(settings, "exam_students_per_invigilator", 30) or 30))
        return max(1, math.ceil(students / per))

    async def _busy(self, exam_date: date, start: time, end: time, schedule_id: uuid.UUID) -> set[uuid.UUID]:
        """Teachers not free then: classes, per-paper duties (any datesheet), other room duties."""
        from app.exams.duty import ExamDutyService
        from app.exams.models import ExamPaper

        probe = ExamPaper(exam_date=exam_date, start_time=start, end_time=end, id=uuid.uuid4())
        busy = set((await ExamDutyService(self.session).busy(probe)).keys())
        busy |= set((await self.session.execute(select(ExamRoomDuty.employee_id).where(ExamRoomDuty.exam_date == exam_date, ExamRoomDuty.start_time < end,
                                                                                       ExamRoomDuty.end_time > start))).scalars())
        return busy

    async def auto_invigilate(self, schedule_id: uuid.UUID, *, exam_date: date, start_time: time, actor_id: uuid.UUID) -> dict:
        """Fill every room of the slot up to the number needed (rooms already full are left alone)."""
        from app.academics.models import CourseOffering
        from app.exams.duty import ExamDutyService

        papers = await self._slot_papers(schedule_id, exam_date, start_time)
        if not papers:
            raise ResourceNotFoundError("No paper starts at that time on this datesheet.")
        if self._started(exam_date, start_time):
            raise BusinessRuleViolationError("These papers have started: invigilators can no longer change.")
        end = max(p.end_time for p in papers)
        plan = await self.room_plan(schedule_id, exam_date, start_time)
        if not plan["rooms"]:
            raise BusinessRuleViolationError("Make the seating first.")
        teachers_of = {}
        for p in papers:
            off = await self.session.get(CourseOffering, p.course_offering_id)
            teachers_of[p.id] = off.assigned_teacher_id if off else None
        own_in_room = defaultdict(set)
        for rid, pid in (await self.session.execute(select(ExamSeat.room_id, ExamSeat.paper_id).where(ExamSeat.schedule_id == schedule_id,
                                                                                                         ExamSeat.exam_date == exam_date,
                                                                                                         ExamSeat.start_time == start_time).distinct())).all():
            if teachers_of.get(pid):
                own_in_room[rid].add(teachers_of[pid])
        busy = await self._busy(exam_date, start_time, end, schedule_id)
        load = dict((await self.session.execute(select(ExamRoomDuty.employee_id, func.count()).where(ExamRoomDuty.schedule_id == schedule_id)
                                                .group_by(ExamRoomDuty.employee_id))).all())
        pool = await ExamDutyService(self.session)._pool()
        added = 0
        short = []
        for room in plan["rooms"]:
            have = len(room["invigilators"])
            want = room["needed"] - have
            if want <= 0:
                continue
            cands = [e for e in pool if e.id not in busy and e.id not in own_in_room[room["room_id"]]]
            cands.sort(key=lambda e: (load.get(e.id, 0), e.first_name.lower(), e.last_name.lower()))
            for i, e in enumerate(cands[:want]):
                self.session.add(ExamRoomDuty(schedule_id=schedule_id, room_id=room["room_id"], exam_date=exam_date, start_time=start_time, end_time=end,
                                              employee_id=e.id, role=DutyRole.IN_CHARGE if have == 0 and i == 0 else DutyRole.ASSISTANT,
                                              assigned_by=actor_id))
                busy.add(e.id)
                load[e.id] = load.get(e.id, 0) + 1
                added += 1
            if len(cands) < want:
                short.append(f"{room['name']}: {want - len(cands)} more needed, no free teacher")
        await self.session.flush()
        return {"added": added, "short": short}

    async def set_invigilators(self, schedule_id: uuid.UUID, *, exam_date: date, start_time: time, room_id: uuid.UUID,
                               employee_ids: list[uuid.UUID], in_charge_id: uuid.UUID | None, actor_id: uuid.UUID) -> dict:
        from app.employees.models import Employee

        papers = await self._slot_papers(schedule_id, exam_date, start_time)
        if not papers:
            raise ResourceNotFoundError("No paper starts at that time on this datesheet.")
        if self._started(exam_date, start_time):
            raise BusinessRuleViolationError("These papers have started: invigilators can no longer change.")
        end = max(p.end_time for p in papers)
        wanted = list(dict.fromkeys(employee_ids))
        if in_charge_id and in_charge_id not in wanted:
            wanted.insert(0, in_charge_id)
        current = {d.employee_id: d for d in (await self.session.execute(select(ExamRoomDuty).where(
            ExamRoomDuty.schedule_id == schedule_id, ExamRoomDuty.exam_date == exam_date, ExamRoomDuty.start_time == start_time,
            ExamRoomDuty.room_id == room_id))).scalars()}
        others = set((await self.session.execute(select(ExamRoomDuty.employee_id).where(ExamRoomDuty.exam_date == exam_date, ExamRoomDuty.start_time < end,
                                                                                        ExamRoomDuty.end_time > start_time, ExamRoomDuty.room_id != room_id))).scalars())
        for eid in wanted:
            if eid in others:
                e = await self.session.get(Employee, eid)
                raise BusinessRuleViolationError(f"{e.first_name} {e.last_name} is already on duty in another room at that time.")
        for eid, d in current.items():
            if eid not in wanted:
                await self.session.delete(d)
        lead = in_charge_id or (wanted[0] if wanted else None)
        for eid in wanted:
            d = current.get(eid)
            role = DutyRole.IN_CHARGE if eid == lead else DutyRole.ASSISTANT
            if d is None:
                self.session.add(ExamRoomDuty(schedule_id=schedule_id, room_id=room_id, exam_date=exam_date, start_time=start_time, end_time=end,
                                              employee_id=eid, role=role, assigned_by=actor_id))
            else:
                d.role = role
        await self.session.flush()
        return {"room_id": room_id, "invigilators": len(wanted)}

    async def tell(self, schedule_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        """Tell each teacher (once per duty) where and when they invigilate."""
        from app.academics.rooms import Room
        from app.exams.duty import ExamDutyService

        sched = await self._schedule(schedule_id)
        rows = (await self.session.execute(select(ExamRoomDuty, Room.name).join(Room, Room.id == ExamRoomDuty.room_id).where(
            ExamRoomDuty.schedule_id == schedule_id, ExamRoomDuty.told_at.is_(None)).order_by(ExamRoomDuty.exam_date, ExamRoomDuty.start_time))).all()
        by = defaultdict(list)
        for d, name in rows:
            by[d.employee_id].append((d, name))
        svc = ExamDutyService(self.session)
        now = datetime.now(timezone.utc)
        for eid, items in by.items():
            text = "; ".join(f"{d.exam_date:%a %d %b} {_hm(d.start_time)} {name}" + (" (in charge)" if d.role == DutyRole.IN_CHARGE else "") for d, name in items[:6])
            more = f" and {len(items) - 6} more" if len(items) > 6 else ""
            await svc.tell(eid, f"Invigilation: {sched.name}", f"You invigilate in the {sched.name} exams: {text}{more}. See Exam duties in the portal.")
            for d, _n in items:
                d.told_at = now
        await self.session.flush()
        return {"teachers": len(by), "duties": len(rows)}

    async def duty_sheet(self, schedule_id: uuid.UUID) -> dict:
        from app.academics.rooms import Room
        from app.employees.models import Employee

        sched = await self._schedule(schedule_id)
        rows = (await self.session.execute(select(ExamRoomDuty, Employee, Room.name).join(Employee, Employee.id == ExamRoomDuty.employee_id)
                                           .join(Room, Room.id == ExamRoomDuty.room_id).where(ExamRoomDuty.schedule_id == schedule_id)
                                           .order_by(Employee.first_name, Employee.last_name, ExamRoomDuty.exam_date, ExamRoomDuty.start_time))).all()
        people: dict = {}
        for d, e, room in rows:
            p = people.setdefault(e.id, {"name": f"{e.first_name} {e.last_name}", "code": e.employee_code, "duties": []})
            p["duties"].append({"date": d.exam_date, "start": _hm(d.start_time), "end": _hm(d.end_time), "room": room, "role": d.role.value})
        return {"schedule": sched.name, "term_label": sched.term_label, "people": list(people.values())}

    async def seat_of(self, student_id: uuid.UUID, paper_ids) -> dict:
        """{paper_id: (room name, seat label)} for the admit card."""
        from app.academics.rooms import Room

        ids = list(paper_ids)
        if not ids:
            return {}
        return {pid: (name, seat_label(r, c)) for pid, name, r, c in (await self.session.execute(
            select(ExamSeat.paper_id, Room.name, ExamSeat.seat_row, ExamSeat.seat_col).join(Room, Room.id == ExamSeat.room_id).where(
                ExamSeat.student_id == student_id, ExamSeat.paper_id.in_(ids)))).all()}


# ====================================================================== PDFs
def _pdf(title: str, blocks: list, *, landscape_page: bool = False) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from app.core import brand

    size = landscape(A4) if landscape_page else A4
    buf = io.BytesIO()
    width = size[0] - 24 * mm
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm, title=title, author=brand.name())
    base = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=13, spaceAfter=2)
    sub = ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#5b6785"), spaceAfter=6)
    story = []
    for i, b in enumerate(blocks):
        if i:
            story.append(PageBreak())
        story += brand.platypus_header(width) + [Paragraph(b["title"], h), Paragraph(b.get("sub", ""), sub)]
        data = [b["head"]] + b["rows"]
        t = Table(data, colWidths=[w * mm for w in b["widths"]], repeatRows=1)
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f9")),
                               ("FONT", (0, 1), (-1, -1), "Helvetica", 9), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d0e0")),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), b.get("pad", 3)),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), b.get("pad", 3))]))
        story += [t, Spacer(1, 4)]
        if b.get("foot"):
            story.append(Paragraph(b["foot"], sub))
    doc.build(story)
    return buf.getvalue()


def door_lists_pdf(sched_name: str, plan: dict) -> bytes:
    blocks = []
    for r in plan["rooms"]:
        papers = ", ".join(f"{k}: {v}" for k, v in sorted(r["papers"].items()))
        blocks.append({"title": f"{r['name']} — door list", "sub": f"{sched_name} · {plan['exam_date']:%a %d %b %Y} · {plan['start_time']} · {r['students']} students ({papers})",
                       "head": ["Seat", "Roll no.", "Name", "Paper"], "widths": [25, 40, 85, 36],
                       "rows": [[s["seat"], s["roll_number"], s["name"], s["paper"]] for s in sorted(r["seats"], key=lambda s: (s["roll_number"] or ""))]})
    return _pdf("Door lists", blocks or [{"title": "No seating yet", "head": [""], "rows": [], "widths": [180]}])


def attendance_sheets_pdf(sched_name: str, plan: dict) -> bytes:
    blocks = []
    for r in plan["rooms"]:
        inv = ", ".join(i["name"] + (" (in charge)" if i["role"] == "IN_CHARGE" else "") for i in r["invigilators"]) or "—"
        blocks.append({"title": f"{r['name']} — attendance sheet", "sub": f"{sched_name} · {plan['exam_date']:%a %d %b %Y} · {plan['start_time']} · Invigilators: {inv}",
                       "head": ["Seat", "Roll no.", "Name", "Paper", "Answer book no.", "Signature", "P / A"], "widths": [18, 28, 62, 24, 30, 40, 14], "pad": 7,
                       "rows": [[s["seat"], s["roll_number"], s["name"], s["paper"], "", "", ""] for s in sorted(r["seats"], key=lambda s: (s["col"], s["row"]))],
                       "foot": "Present: ______   Absent: ______   Invigilator's signature: ____________________"})
    return _pdf("Room attendance sheets", blocks or [{"title": "No seating yet", "head": [""], "rows": [], "widths": [180]}])


def duty_sheet_pdf(sheet: dict) -> bytes:
    rows = []
    for p in sheet["people"]:
        for i, d in enumerate(p["duties"]):
            rows.append([p["name"] if i == 0 else "", p["code"] if i == 0 else "", f"{d['date']:%a %d %b}", f"{d['start']}–{d['end']}", d["room"],
                         "In charge" if d["role"] == "IN_CHARGE" else "Assistant"])
    return _pdf("Invigilation duties", [{"title": f"Invigilation duties — {sheet['schedule']}", "sub": sheet["term_label"],
                                          "head": ["Teacher", "Code", "Date", "Time", "Room", "Role"], "widths": [52, 22, 28, 28, 34, 22], "rows": rows}])
