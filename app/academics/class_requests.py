"""Make-up and extra classes, booked by the teacher in a free slot and approved by an administrator.

  * Free slots: for one of the teacher's courses, every period on the coming days (inside the course's term dates, on
    the institute's working days, not on a holiday) when the teacher, the class's own students and at least one room
    are all free. Busy means: a scheduled class, an exam paper, or a make-up / extra class still waiting for approval
    (a request holds its slot, so two teachers cannot book the same room or students).
  * A make-up class replaces a missed one: a past class that was cancelled or whose attendance was never taken.
  * An extra (emergency) class is for a course that is behind; it needs a reason.
  * The request goes to the Approvals inbox (kind CLASS_REQUEST). On approval the slot is checked again, the class is
    added to the timetable and attendance like any other class, the missed class it replaces (if still untaken) is
    marked "made up", and the students and teacher are told (app/notifications/changes.py: class_session.extra_added).
No new tables: a request is an approval_requests row whose payload says what to book.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering, TimetableSlot
from app.approvals.models import ApprovalRequest
from app.approvals.service import executor, gate, pending_response
from app.audit.service import AuditService
from app.core.clock import institute_now, institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

KIND = "CLASS_REQUEST"
MAX_DAYS = 21                      # how far one free-slot search looks
DAY_NAMES = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def _room_key(room: str | None) -> str:
    return "".join((room or "").split()).lower()


def _overlap(a1: time, a2: time, b1: time, b2: time) -> bool:
    return a1 < b2 and b1 < a2


@dataclass
class _Busy:
    """Anything that takes people or a room at a time on a date: a class, an exam paper, a request waiting for approval."""
    start_time: time
    end_time: time
    room: str
    offering: CourseOffering
    teacher_id: uuid.UUID | None
    what: str
    request_id: uuid.UUID | None = None


class ClassRequestService:
    def __init__(self, session: AsyncSession) -> None:
        from app.academics.service import TimetableService

        self.session = session
        self.tt = TimetableService(session)

    # ------------------------------------------------------------------ what is busy
    async def _busy_on(self, day: date, *, skip_request: uuid.UUID | None = None) -> list[_Busy]:
        from app.exams.models import ExamPaper

        out: list[_Busy] = []
        sessions = (await self.session.execute(select(ClassSession).where(
            ClassSession.session_date == day, ClassSession.status == ClassSessionStatus.SCHEDULED))).scalars().all()
        for cs in sessions:
            o = await self.tt._offering(cs.course_offering_id)
            if o is not None:
                out.append(_Busy(cs.start_time, cs.end_time, cs.room, o, o.assigned_teacher_id, "class"))
        for p in (await self.session.execute(select(ExamPaper).where(ExamPaper.exam_date == day))).scalars().all():
            o = await self.tt._offering(p.course_offering_id)
            if o is not None:
                out.append(_Busy(p.start_time, p.end_time, p.room, o, p.invigilator_employee_id, "exam"))
        for r in await self._waiting():
            pl = r.payload or {}
            if r.id == skip_request or pl.get("session_date") != day.isoformat():
                continue
            o = await self.tt._offering(uuid.UUID(pl["course_offering_id"]))
            if o is not None:
                out.append(_Busy(time.fromisoformat(pl["start_time"]), time.fromisoformat(pl["end_time"]), pl.get("room") or "", o,
                                 o.assigned_teacher_id, "request", r.id))
        return out

    async def _waiting(self) -> list[ApprovalRequest]:
        if not hasattr(self, "_waiting_memo"):
            self._waiting_memo = list((await self.session.execute(select(ApprovalRequest).where(
                ApprovalRequest.kind == KIND, ApprovalRequest.status == "REQUESTED"))).scalars().all())
        return self._waiting_memo

    async def _rooms(self, offering: CourseOffering | None = None) -> list[str]:
        """The institute's rooms: the room list (Timetable -> Rooms; active ones with enough seats for this class) or,
        while that list is empty, every room used on the weekly timetable."""
        from app.academics.rooms import RoomService

        listed = await RoomService(self.session).listing(active_only=True)
        if listed:
            need = len(await self.tt._enrolled_for(offering.id)) if offering is not None else 0
            return [r.name for r in listed if r.seats is None or r.seats >= need]
        rows = (await self.session.execute(select(TimetableSlot.room).distinct())).scalars().all()
        seen: dict[str, str] = {}
        for r in rows:
            if r and r.strip() and _room_key(r) not in seen:
                seen[_room_key(r)] = r.strip()
        return sorted(seen.values(), key=str.lower)

    async def _working_days(self) -> set[str]:
        rows = (await self.session.execute(select(TimetableSlot.day_of_week).distinct())).scalars().all()
        days = {getattr(d, "value", d) for d in rows}
        return days or set(DAY_NAMES[:6])

    async def _holidays(self, start: date, end: date) -> set[date]:
        from app.calendar.models import CalendarEvent

        out: set[date] = set()
        rows = (await self.session.execute(select(CalendarEvent).where(
            CalendarEvent.kind == "HOLIDAY", CalendarEvent.for_students.is_(True),
            CalendarEvent.start_date <= end, CalendarEvent.end_date >= start))).scalars().all()
        for e in rows:
            d = max(e.start_date, start)
            while d <= min(e.end_date, end):
                out.add(d)
                d += timedelta(days=1)
        return out

    async def _leave_days(self, teacher_id: uuid.UUID | None, start: date, end: date) -> set[date]:
        """Days the teacher is on APPROVED leave (no classes can be booked for them then)."""
        from app.timekeeping.models import LeaveRequest

        if teacher_id is None:
            return set()
        out: set[date] = set()
        rows = (await self.session.execute(select(LeaveRequest).where(
            LeaveRequest.employee_id == teacher_id, LeaveRequest.status == "APPROVED",
            LeaveRequest.first_day <= end, LeaveRequest.last_day >= start))).scalars().all()
        for lv in rows:
            d = max(lv.first_day, start)
            while d <= min(lv.last_day, end):
                out.add(d)
                d += timedelta(days=1)
        return out

    async def _problem(self, offering: CourseOffering, day: date, start: time, end: time, busy: list[_Busy]) -> str | None:
        """Why the teacher or the class's students cannot have a class then (None = they are free)."""
        from app.academics.combined import same_group

        here = [b for b in busy if _overlap(start, end, b.start_time, b.end_time)]
        for b in here:
            if b.offering.id == offering.id:
                return "This class already has something at that time."
            if offering.assigned_teacher_id and b.teacher_id == offering.assigned_teacher_id and not same_group(offering, b.offering):
                return "The teacher is busy then (" + ("exam duty" if b.what == "exam" else "another class") + ")."
        when = f"{day:%d %b} {{}}–{{}}"
        return await self.tt._people_conflict(offering, [(b, b.offering) for b in here], when)

    def _free_rooms(self, offering: CourseOffering, start: time, end: time, busy: list[_Busy], rooms: list[str]) -> list[str]:
        from app.academics.combined import same_group

        taken = {_room_key(b.room) for b in busy if _overlap(start, end, b.start_time, b.end_time) and not same_group(offering, b.offering)}
        return [r for r in rooms if _room_key(r) not in taken]

    # ------------------------------------------------------------------ for the teacher
    async def free_slots(self, offering: CourseOffering, *, start: date, days: int) -> list[dict]:
        today = institute_today()
        now = institute_now().time()
        days = max(1, min(days, MAX_DAYS))
        start = max(start, today)
        end = start + timedelta(days=days - 1)
        if offering.term_start_date:
            start = max(start, offering.term_start_date)
        if offering.term_end_date:
            end = min(end, offering.term_end_date)
        periods = sorted(await self.tt.period_repo.list_all(), key=lambda p: p.period_number)
        rooms = await self._rooms(offering)
        if not periods or not rooms or end < start:
            return []
        working, holidays = await self._working_days(), await self._holidays(start, end)
        holidays |= await self._leave_days(offering.assigned_teacher_id, start, end)
        out = []
        d = start
        while d <= end:
            if DAY_NAMES[d.weekday()] in working and d not in holidays:
                busy = await self._busy_on(d)
                for p in periods:
                    if d == today and p.start_time <= now:
                        continue
                    if await self._problem(offering, d, p.start_time, p.end_time, busy):
                        continue
                    free = self._free_rooms(offering, p.start_time, p.end_time, busy, rooms)
                    if free:
                        out.append({"date": d, "day": DAY_NAMES[d.weekday()].title(), "period_id": p.id, "period_number": p.period_number,
                                    "start_time": p.start_time.strftime("%H:%M"), "end_time": p.end_time.strftime("%H:%M"), "rooms": free})
            d += timedelta(days=1)
        return out

    async def missed(self, offering: CourseOffering) -> list[dict]:
        """Past classes of this course that were cancelled or never had attendance taken, not already made up / asked for."""
        today, now = institute_today(), institute_now().time()
        rows = (await self.session.execute(select(ClassSession).where(
            ClassSession.course_offering_id == offering.id, ClassSession.session_date <= today,
        ).order_by(ClassSession.session_date, ClassSession.start_time))).scalars().all()
        claimed = await self._claimed_sessions()
        out = []
        for cs in rows:
            if cs.session_date == today and cs.end_time > now:
                continue
            if cs.id in claimed or (cs.cancelled_reason or "").startswith("Made up on"):
                continue
            if cs.status == ClassSessionStatus.CANCELLED:
                why = "Cancelled" + (f": {cs.cancelled_reason}" if cs.cancelled_reason else "")
            elif cs.attendance_taken_at is None:
                why = "Attendance not taken"
            else:
                continue
            out.append({"id": cs.id, "date": cs.session_date, "start_time": cs.start_time.strftime("%H:%M"),
                        "end_time": cs.end_time.strftime("%H:%M"), "room": cs.room, "why": why})
        return out

    async def _claimed_sessions(self) -> set[uuid.UUID]:
        rows = (await self.session.execute(select(ApprovalRequest.payload).where(
            ApprovalRequest.kind == KIND, ApprovalRequest.status.in_(("REQUESTED", "APPROVED"))))).scalars().all()
        return {uuid.UUID(p["replaces_session_id"]) for p in rows if p and p.get("replaces_session_id")}

    async def request(self, offering: CourseOffering, *, kind: str, session_date: date, period_id: uuid.UUID, room: str,
                      reason: str | None, replaces_session_id: uuid.UUID | None, actor_id: uuid.UUID) -> dict:
        kind = (kind or "").upper()
        if kind not in ("MAKEUP", "EXTRA"):
            raise ValidationAppError("Choose a make-up class or an extra class.")
        reason = " ".join((reason or "").split())
        period = await self.tt.period_repo.get_by_id(period_id)
        if period is None:
            raise ResourceNotFoundError("Period not found.")
        replaced = None
        if kind == "MAKEUP":
            if replaces_session_id is None:
                raise ValidationAppError("Choose the missed class this one makes up for.")
            if replaces_session_id not in {m["id"] for m in await self.missed(offering)}:
                raise BusinessRuleViolationError("That class is not a missed class of this course, or it is already made up or asked for.")
            replaced = await self.session.get(ClassSession, replaces_session_id)
        elif len(reason) < 5:
            raise ValidationAppError("Say why the extra class is needed (for example: two topics behind before the mid-term).")
        free = [s for s in await self.free_slots(offering, start=session_date, days=1)
                if s["date"] == session_date and s["period_id"] == period_id]
        if not free:
            raise BusinessRuleViolationError("That period is not free any more for this class (or it is outside the term, a holiday or already past). Pick another.")
        rooms = {_room_key(r): r for r in free[0]["rooms"]}
        if _room_key(room) not in rooms:
            raise BusinessRuleViolationError(f"Room {room} is taken then. Free rooms: {', '.join(free[0]['rooms'])}.")
        room = rooms[_room_key(room)]
        course = await self.session.get(Course, offering.course_id)
        label = f"{course.code if course else ''} {offering.section_label or ''}".strip()
        what = "Make-up" if kind == "MAKEUP" else "Extra"
        summary = (f"{what} class: {label} on {session_date:%a %d %b %Y} {period.start_time:%H:%M}–{period.end_time:%H:%M}, room {room}"
                   + (f" (for the class of {replaced.session_date:%d %b})" if replaced else ""))
        payload = {"course_offering_id": str(offering.id), "kind": kind, "session_date": session_date.isoformat(),
                   "start_time": period.start_time.isoformat(), "end_time": period.end_time.isoformat(), "period_id": str(period.id),
                   "room": room, "replaces_session_id": str(replaces_session_id) if replaces_session_id else None, "label": label}
        r = await gate(self.session, kind=KIND, actor_id=actor_id, target_id=replaces_session_id, payload=payload, summary=summary,
                       reason=reason or None)
        if r is None:                      # nobody else can approve: booked straight away (logged as sole approver)
            fake = ApprovalRequest(kind=KIND, payload=payload, summary=summary, reason=reason or None, requested_by=actor_id)
            result = await book(self.session, fake, actor_id)
            return {"pending_approval": False, "message": f"Booked: {summary}.", **result}
        return pending_response(r)

    async def mine(self, actor_id: uuid.UUID) -> list[dict]:
        rows = (await self.session.execute(select(ApprovalRequest).where(
            ApprovalRequest.kind == KIND, ApprovalRequest.requested_by == actor_id).order_by(ApprovalRequest.created_at.desc()).limit(100))).scalars().all()
        return [{"id": r.id, "summary": r.summary, "reason": r.reason, "status": r.status, "requested_at": r.created_at,
                 "decision_note": r.decision_note, "kind": (r.payload or {}).get("kind")} for r in rows]


@executor(KIND)
async def book(session: AsyncSession, r: ApprovalRequest, actor_id) -> dict:
    """Carry out an approved request: check the slot again, add the class, mark the missed class made up."""
    pl = r.payload or {}
    svc = ClassRequestService(session)
    offering = await session.get(CourseOffering, uuid.UUID(pl["course_offering_id"]))
    if offering is None:
        raise BusinessRuleViolationError("The course of this request no longer exists.")
    day, start, end = date.fromisoformat(pl["session_date"]), time.fromisoformat(pl["start_time"]), time.fromisoformat(pl["end_time"])
    if datetime.combine(day, start) <= datetime.combine(institute_today(), institute_now().time()):
        raise BusinessRuleViolationError("This class would start in the past. Reject it and ask the teacher to book another slot.")
    if day in await svc._leave_days(offering.assigned_teacher_id, day, day):
        raise BusinessRuleViolationError("The teacher is on approved leave that day. Reject it and ask the teacher to book another day.")
    busy = await svc._busy_on(day, skip_request=r.id)
    problem = await svc._problem(offering, day, start, end, busy)
    if problem:
        raise BusinessRuleViolationError(f"The slot is no longer free: {problem} Reject it and ask the teacher to book another slot.")
    if _room_key(pl.get("room")) not in {_room_key(x) for x in svc._free_rooms(offering, start, end, busy, [pl.get("room") or ""])}:
        raise BusinessRuleViolationError(f"Room {pl.get('room')} has been taken since. Reject it and ask the teacher to book another slot.")
    what = "Make-up class" if pl.get("kind") == "MAKEUP" else "Extra class"
    replaced = None
    if pl.get("replaces_session_id"):
        replaced = await session.get(ClassSession, uuid.UUID(pl["replaces_session_id"]))
    cs = ClassSession(course_offering_id=offering.id, timetable_slot_id=None, session_date=day, start_time=start, end_time=end,
                      room=pl.get("room") or "", status=ClassSessionStatus.SCHEDULED,
                      title=what + (f" (for {replaced.session_date:%d %b})" if replaced else ""),
                      description=(r.reason or None))
    session.add(cs)
    await session.flush()
    if replaced is not None and replaced.attendance_taken_at is None:
        replaced.status = ClassSessionStatus.CANCELLED
        replaced.cancelled_reason = f"Made up on {day:%d %b %Y}"[:255]
    await session.flush()
    await AuditService(session).log(
        actor_user_id=actor_id, action="class_session.extra_added", entity_type="class_session", entity_id=cs.id,
        after={"course_offering_id": str(offering.id), "kind": pl.get("kind"), "session_date": day.isoformat(),
               "start_time": start.isoformat(), "end_time": end.isoformat(), "room": cs.room,
               "replaces": str(replaced.id) if replaced else None},
        metadata={"requested_by": str(r.requested_by) if r.requested_by else None, "reason": r.reason})
    return {"class_session_id": str(cs.id)}


# ====================================================================== API
from fastapi import APIRouter, Depends, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.auth.permissions import Permissions  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.dependencies import CurrentUser, get_current_user  # noqa: E402
from app.core.exceptions import AuthorizationError  # noqa: E402

router = APIRouter(prefix="/academics/class-requests", tags=["make-up and extra classes"])


async def _own_offering(db, offering_id: uuid.UUID, user: CurrentUser) -> CourseOffering:
    from app.employees.repository import EmployeeRepository

    offering = await db.get(CourseOffering, offering_id)
    if offering is None:
        raise ResourceNotFoundError("Course offering not found.")
    if Permissions.LECTURE_MANAGE in user.permissions:
        return offering
    employee = await EmployeeRepository(db).get_by_user_id(user.id)
    if employee is None or offering.assigned_teacher_id != employee.id:
        raise AuthorizationError("Only the teacher of this course (or the admin) can book its classes.")
    return offering


@router.get("/my-courses", summary="The courses I teach this term, with how far behind each one is")
async def my_courses(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.academics.repository import CourseOfferingRepository
    from app.academics.service import CourseProgressService
    from app.employees.repository import EmployeeRepository

    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if employee is None:
        return []
    today = institute_today()
    out = []
    svc = ClassRequestService(db)
    for o in await CourseOfferingRepository(db).list_by_teacher(employee.id):
        if o.term_end_date is not None and o.term_end_date < today:
            continue
        course = await db.get(Course, o.course_id)
        progress = await CourseProgressService(db).compute_for_offering(o.id)
        out.append({"id": o.id, "label": f"{course.code} {o.section_label or ''}".strip() if course else str(o.id),
                    "title": course.title if course else "", "term_label": o.term_label,
                    "term_start_date": o.term_start_date, "term_end_date": o.term_end_date,
                    "sessions_missing": progress.get("sessions_missing", 0), "status": progress.get("status"),
                    "missed_classes": len(await svc.missed(o))})
    return out


@router.get("/offerings/{offering_id}/free-slots", summary="Periods when the teacher, the class and a room are all free")
async def free_slots(offering_id: uuid.UUID, start: date | None = None, days: int = Query(default=7, ge=1, le=MAX_DAYS),
                     current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    offering = await _own_offering(db, offering_id, current_user)
    return await ClassRequestService(db).free_slots(offering, start=start or institute_today(), days=days)


@router.get("/offerings/{offering_id}/missed", summary="Past classes that were cancelled or had no attendance, not yet made up")
async def missed(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    offering = await _own_offering(db, offering_id, current_user)
    return await ClassRequestService(db).missed(offering)


class ClassRequestIn(BaseModel):
    kind: str = Field(pattern="^(MAKEUP|EXTRA)$")
    session_date: date
    period_id: uuid.UUID
    room: str = Field(min_length=1, max_length=50)
    reason: str | None = Field(default=None, max_length=300)
    replaces_session_id: uuid.UUID | None = None


@router.post("/offerings/{offering_id}", summary="Ask for a make-up or extra class in a free slot (an administrator approves it)")
async def request_class(offering_id: uuid.UUID, payload: ClassRequestIn, current_user: CurrentUser = Depends(get_current_user),
                        db=Depends(get_db)) -> dict:
    offering = await _own_offering(db, offering_id, current_user)
    return await ClassRequestService(db).request(offering, kind=payload.kind, session_date=payload.session_date, period_id=payload.period_id,
                                                 room=payload.room, reason=payload.reason, replaces_session_id=payload.replaces_session_id,
                                                 actor_id=current_user.id)


@router.get("/mine", summary="My make-up / extra class requests")
async def mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await ClassRequestService(db).mine(current_user.id)
