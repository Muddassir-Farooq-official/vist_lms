"""Teachers and their classes: who took attendance on time, classes missed by a teacher who was on campus, and covering
an absent teacher's classes (with the cover paid on the substitute's payslip).

Read-only over the existing timetable, class, attendance and staff check-in records; the only table it writes is
class_covers (plus notifications). Nothing here changes a student's attendance or a past class.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering, Enrollment, EnrollmentStatus
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_now, institute_today
from app.core.config import settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.employees.models import Employee, EmployeeStatus, JobRole
from app.teaching.models import ClassCover
from app.timekeeping.models import StaffAttendance

MISSED_TITLE = "Class not taken"
TEACHING_ROLES = {Roles.TEACHER, Roles.HOD}
PORTAL = {"TEACHER": "faculty", "HOD": "faculty", "LIBRARIAN": "library", "ACCOUNTS_OFFICER": "accounts", "SECURITY_GUARD": "gate",
          "EXAM_CONTROLLER": "exam", "HOSTEL_WARDEN": "warden", "STAFF": "staff", "ADMIN": "admin"}


def q2(x) -> Decimal:
    from app.payroll.rules import q2 as _q2

    return _q2(x)


def to_local(dt: datetime | None) -> datetime | None:
    """A stored moment (UTC) as the institute's wall-clock time, the same way class times are kept."""
    if dt is None:
        return None
    from zoneinfo import ZoneInfo

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(settings.institute_timezone)).replace(tzinfo=None)


def window() -> int:
    return int(settings.attendance_marking_window_minutes or 15)


def name(e: Employee | None) -> str | None:
    return f"{e.first_name} {e.last_name}".strip() if e else None


def class_state(cs: ClassSession, now: datetime) -> dict:
    """Where one class stands: not started, open for marking, taken on time / late, or missed."""
    start = datetime.combine(cs.session_date, cs.start_time)
    end_window = start + timedelta(minutes=window())
    taken = to_local(cs.attendance_taken_at)
    out = {"taken_at": taken, "minutes_after_start": None}
    if taken is not None:
        out["minutes_after_start"] = max(0, int((taken - start).total_seconds() // 60))
        on_time = taken <= end_window and not cs.late_marking_note
        out["state"] = "ON_TIME" if on_time else "LATE"
    elif now < start:
        out["state"] = "UPCOMING"
    elif now <= end_window:
        out["state"] = "OPEN"
    else:
        out["state"] = "MISSED"
    return out


class TeachingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ lookups
    async def _classes(self, first: date, last: date, *, teacher_id: uuid.UUID | None = None) -> list[tuple[ClassSession, CourseOffering, Course]]:
        stmt = (select(ClassSession, CourseOffering, Course).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
                .join(Course, Course.id == CourseOffering.course_id)
                .where(ClassSession.session_date >= first, ClassSession.session_date <= last, ClassSession.status == ClassSessionStatus.SCHEDULED))
        if teacher_id is not None:
            covered = select(ClassCover.class_session_id).where(ClassCover.status == "ACTIVE", ClassCover.substitute_employee_id == teacher_id)
            stmt = stmt.where((CourseOffering.assigned_teacher_id == teacher_id) | ClassSession.id.in_(covered))
        return [tuple(r) for r in (await self.session.execute(stmt.order_by(ClassSession.session_date, ClassSession.start_time))).all()]

    async def _covers(self, session_ids) -> dict[uuid.UUID, ClassCover]:
        if not session_ids:
            return {}
        rows = (await self.session.execute(select(ClassCover).where(ClassCover.class_session_id.in_(list(session_ids)), ClassCover.status == "ACTIVE"))).scalars()
        return {c.class_session_id: c for c in rows}

    async def _employees(self, ids) -> dict[uuid.UUID, Employee]:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        return {e.id: e for e in (await self.session.execute(select(Employee).where(Employee.id.in_(ids)))).scalars()}

    async def _days(self, ids, first: date, last: date) -> dict[tuple, StaffAttendance]:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        rows = (await self.session.execute(select(StaffAttendance).where(StaffAttendance.employee_id.in_(ids), StaffAttendance.work_date >= first,
                                                                        StaffAttendance.work_date <= last))).scalars()
        return {(r.employee_id, r.work_date): r for r in rows}

    @staticmethod
    def _presence(row: StaffAttendance | None) -> dict:
        if row is None:
            return {"presence": "NOT_IN", "presence_label": "Not checked in", "checked_in_at": None}
        if row.excused:
            return {"presence": "LEAVE", "presence_label": "On leave / duty" + (f" ({row.excuse_reason})" if row.excuse_reason else ""), "checked_in_at": None}
        if row.check_in_at is None:
            return {"presence": "NOT_IN", "presence_label": "Not checked in", "checked_in_at": None}
        t = to_local(row.check_in_at)
        left = to_local(row.check_out_at)
        return {"presence": "IN", "presence_label": f"Checked in {t:%H:%M}" + (f", left {left:%H:%M}" if left else ""), "checked_in_at": t}

    async def _teacher_ids(self) -> set[uuid.UUID]:
        rows = (await self.session.execute(select(Employee.id).join(JobRole, JobRole.id == Employee.job_role_id)
                                           .where(JobRole.default_role_code.in_(TEACHING_ROLES)))).scalars()
        return set(rows)

    # ------------------------------------------------------------------ one day, every class
    async def day(self, day: date) -> dict:
        """Every class on a day with its teacher's check-in, whether attendance was taken (on time / late / missed) and its
        cover. The classes the administration should look at come first."""
        now = institute_now()
        rows = await self._classes(day, day)
        covers = await self._covers([cs.id for cs, _, _ in rows])
        people = await self._employees({o.assigned_teacher_id for _, o, _ in rows} | {c.substitute_employee_id for c in covers.values()})
        days = await self._days(people.keys(), day, day)
        out, absent = [], {}
        for cs, off, course in rows:
            cover = covers.get(cs.id)
            teacher = people.get(off.assigned_teacher_id)
            pres = self._presence(days.get((off.assigned_teacher_id, day)))
            st = class_state(cs, now)
            who_id = cover.substitute_employee_id if cover else off.assigned_teacher_id
            who_pres = self._presence(days.get((who_id, day))) if cover else pres
            end_window = datetime.combine(cs.session_date, cs.start_time) + timedelta(minutes=window())
            # The case to highlight: the person who should take it was on campus in time, and it was not taken.
            missed_while_in = st["state"] == "MISSED" and who_pres["presence"] == "IN" and who_pres["checked_in_at"] is not None \
                and who_pres["checked_in_at"] <= end_window
            needs_cover = (cover is None and st["state"] in ("UPCOMING", "OPEN", "MISSED") and off.assigned_teacher_id is not None
                           and pres["presence"] != "IN" and cs.attendance_taken_at is None)
            if needs_cover and teacher is not None:
                absent.setdefault(teacher.id, {"employee_id": teacher.id, "name": name(teacher), "presence_label": pres["presence_label"], "classes": 0})
                absent[teacher.id]["classes"] += 1
            out.append({
                "class_session_id": cs.id, "date": cs.session_date, "start_time": cs.start_time, "end_time": cs.end_time, "room": cs.room,
                "course_code": course.code, "course_title": course.title, "section": off.section_label, "term_label": off.term_label,
                "teacher_id": off.assigned_teacher_id, "teacher_name": name(teacher), **pres,
                "cover": self._cover_out(cover, people) if cover else None,
                **st, "late_note": cs.late_marking_note, "missed_while_in": missed_while_in, "needs_cover": needs_cover,
            })
        order = {"MISSED": 0, "OPEN": 2, "UPCOMING": 3, "LATE": 4, "ON_TIME": 5}
        out.sort(key=lambda r: (0 if r["missed_while_in"] else 1 if r["needs_cover"] else 2, order.get(r["state"], 9), r["start_time"]))
        counts = defaultdict(int)
        for r in out:
            counts[r["state"]] += 1
        return {"date": day, "window_minutes": window(), "classes": out, "absent_teachers": sorted(absent.values(), key=lambda a: a["name"] or ""),
                "counts": {"total": len(out), "on_time": counts["ON_TIME"], "late": counts["LATE"], "missed": counts["MISSED"],
                           "open": counts["OPEN"], "upcoming": counts["UPCOMING"],
                           "missed_while_in": sum(1 for r in out if r["missed_while_in"]), "needs_cover": sum(1 for r in out if r["needs_cover"])}}

    @staticmethod
    def _cover_out(c: ClassCover, people: dict) -> dict:
        return {"id": c.id, "substitute_id": c.substitute_employee_id, "substitute_name": name(people.get(c.substitute_employee_id)),
                "amount": float(c.amount), "amount_note": c.amount_note, "description": c.description}

    # ------------------------------------------------------------------ how punctual each teacher is
    async def punctuality(self, first: date, last: date) -> dict:
        """Per teacher, over the classes already due in the period: taken on time (within the marking window), taken late
        (by the administration or a correction afterwards), never taken, and the average minutes after the start at which
        attendance was taken. A covered class counts for the person who covered it."""
        if last < first:
            raise ValidationAppError("The last day is before the first.")
        if (last - first).days > 370:
            raise ValidationAppError("Choose at most a year.")
        now = institute_now()
        rows = await self._classes(first, min(last, now.date()))
        covers = await self._covers([cs.id for cs, _, _ in rows])
        stats: dict = {}
        for cs, off, _course in rows:
            if datetime.combine(cs.session_date, cs.start_time) + timedelta(minutes=window()) > now:
                continue                                     # not due yet: still open or upcoming
            cover = covers.get(cs.id)
            who = cover.substitute_employee_id if cover else off.assigned_teacher_id
            if who is None:
                continue
            s = stats.setdefault(who, {"due": 0, "on_time": 0, "late": 0, "missed": 0, "minutes": [], "covered": 0})
            s["due"] += 1
            s["covered"] += 1 if cover else 0
            st = class_state(cs, now)
            if st["state"] == "ON_TIME":
                s["on_time"] += 1
                s["minutes"].append(st["minutes_after_start"])
            elif st["state"] == "LATE":
                s["late"] += 1
            else:
                s["missed"] += 1
        people = await self._employees(stats.keys())
        out = []
        for eid, s in stats.items():
            pct = round(100 * s["on_time"] / s["due"], 1) if s["due"] else None
            avg = round(sum(s["minutes"]) / len(s["minutes"]), 1) if s["minutes"] else None
            verdict = "On time" if pct is not None and pct >= 90 else "Sometimes late" if pct is not None and pct >= 70 else "Often late or missing"
            out.append({"employee_id": eid, "name": name(people.get(eid)) or "?", "code": people[eid].employee_code if eid in people else "",
                        "classes_due": s["due"], "on_time": s["on_time"], "late": s["late"], "missed": s["missed"], "covered": s["covered"],
                        "on_time_percent": pct, "average_minutes_after_start": avg, "verdict": verdict})
        out.sort(key=lambda r: (r["on_time_percent"] if r["on_time_percent"] is not None else 101, r["name"]))
        total_due = sum(r["classes_due"] for r in out)
        return {"from": first, "to": last, "window_minutes": window(), "teachers": out,
                "totals": {"classes_due": total_due, "on_time": sum(r["on_time"] for r in out), "late": sum(r["late"] for r in out),
                           "missed": sum(r["missed"] for r in out),
                           "on_time_percent": round(100 * sum(r["on_time"] for r in out) / total_due, 1) if total_due else None}}

    async def teacher_classes(self, employee_id: uuid.UUID, first: date, last: date) -> list[dict]:
        """One teacher's classes in a period, with how each one went (for the report's detail view)."""
        now = institute_now()
        rows = await self._classes(first, min(last, now.date()), teacher_id=employee_id)
        covers = await self._covers([cs.id for cs, _, _ in rows])
        days = await self._days({employee_id}, first, last)
        out = []
        for cs, off, course in rows:
            cover = covers.get(cs.id)
            if cover is not None and cover.substitute_employee_id != employee_id:
                continue                                     # someone else covered this one
            out.append({"class_session_id": cs.id, "date": cs.session_date, "start_time": cs.start_time, "course_code": course.code,
                        "course_title": course.title, "section": off.section_label, "room": cs.room, "covering": cover is not None,
                        **self._presence(days.get((employee_id, cs.session_date))), **class_state(cs, now), "late_note": cs.late_marking_note})
        return out

    # ------------------------------------------------------------------ alerts
    async def alert_missed_classes(self) -> int:
        """Today: a class whose marking window has closed with no attendance, although the person who should take it had
        checked in by then -> one notification to each administrator (once per class). Safe to call every minute."""
        from app.notifications.models import Notification, NotificationCategory

        if not int(getattr(settings, "missed_class_alerts", 1) or 0):
            return 0
        now = institute_now()
        data = await self.day(now.date())
        hits = [r for r in data["classes"] if r["missed_while_in"]]
        if not hits:
            return 0
        start = datetime.combine(now.date(), time.min).replace(tzinfo=timezone.utc) - timedelta(days=1)
        told = set((await self.session.execute(select(Notification.link).where(Notification.title == MISSED_TITLE, Notification.created_at >= start))).scalars())
        admins = (await self.session.execute(select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
                                             .where(Role.code == Roles.ADMIN, User.is_active.is_(True)))).scalars().all()
        n = 0
        for r in hits:
            link = f"/dashboard/admin/attendance?day={r['date'].isoformat()}&class={r['class_session_id']}#teachers"
            if link in told:
                continue
            who = r["cover"]["substitute_name"] if r["cover"] else r["teacher_name"]
            msg = (f"{who} checked in but did not take attendance for {r['course_code']} ({r['section']}) at {r['start_time']:%H:%M}, room {r['room']}. "
                   f"The {window()}-minute marking window has closed.")
            for uid in set(admins):
                self.session.add(Notification(user_id=uid, category=NotificationCategory.GENERAL, title=MISSED_TITLE, message=msg[:1000], link=link))
            n += 1
        await self.session.flush()
        return n

    # ------------------------------------------------------------------ cover
    async def _class(self, class_session_id: uuid.UUID) -> tuple[ClassSession, CourseOffering, Course]:
        row = (await self.session.execute(select(ClassSession, CourseOffering, Course).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
                                          .join(Course, Course.id == CourseOffering.course_id).where(ClassSession.id == class_session_id))).first()
        if row is None:
            raise ResourceNotFoundError("That class was not found.")
        return tuple(row)

    async def day_rate(self, employee_id: uuid.UUID, day: date) -> tuple[Decimal, str]:
        """The absent teacher's pay for that day, worked out exactly as payroll works out a day's pay."""
        from app.payroll import rules
        from app.payroll.service import PayrollService
        from app.timekeeping.service import TimekeepingService

        month = day.replace(day=1)
        base = (await PayrollService(self.session)._salaries([employee_id], month)).get(employee_id)
        if not base:
            return Decimal(0), "No basic salary on record for the absent teacher"
        wd = int(settings.payroll_default_working_days or 0)
        if not wd:
            emp = await self.session.get(Employee, employee_id)
            wd = (await TimekeepingService(self.session).scheduled_days(month, [emp])).get(employee_id) or rules.days_in(month)
        return q2(Decimal(base) / wd), f"basic Rs. {float(base):,.0f} / {wd} working days"

    async def suggested_amount(self, absent_id: uuid.UUID | None, day: date) -> tuple[Decimal, str]:
        if absent_id is None:
            return Decimal(0), "No teacher assigned to this class"
        rate, how = await self.day_rate(absent_id, day)
        n = (await self.session.execute(select(func.count()).select_from(ClassSession).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
                                        .where(CourseOffering.assigned_teacher_id == absent_id, ClassSession.session_date == day,
                                               ClassSession.status == ClassSessionStatus.SCHEDULED))).scalar_one() or 1
        if not rate:
            return Decimal(0), how
        return q2(rate / n), f"Day's pay Rs. {float(rate):,.0f} ({how}) shared by {n} class{'es' if n != 1 else ''} that day"

    async def suggestions(self, class_session_id: uuid.UUID) -> dict:
        """Who could take this class: active staff who are free at that time (no class of their own or another cover) and
        not on leave. Teachers come first; then those who already teach this course, are in the same department, have
        checked in, and have fewer classes that day."""
        cs, off, course = await self._class(class_session_id)
        day = cs.session_date
        absent = await self.session.get(Employee, off.assigned_teacher_id) if off.assigned_teacher_id else None
        todays = await self._classes(day, day)
        covers = await self._covers([s.id for s, _, _ in todays])
        busy: dict[uuid.UUID, list] = defaultdict(list)
        load: dict[uuid.UUID, int] = defaultdict(int)
        for s, o, c in todays:
            cov = covers.get(s.id)
            who = cov.substitute_employee_id if cov else o.assigned_teacher_id
            if who is None:
                continue
            load[who] += 1
            if s.id != cs.id and s.start_time < cs.end_time and cs.start_time < s.end_time:
                busy[who].append(f"{c.code} {s.start_time:%H:%M}")
        staff = (await self.session.execute(select(Employee, JobRole).join(JobRole, JobRole.id == Employee.job_role_id)
                                            .where(Employee.status == EmployeeStatus.ACTIVE, Employee.user_id.is_not(None)))).all()
        days = await self._days({e.id for e, _ in staff}, day, day)
        same_course = set((await self.session.execute(select(CourseOffering.assigned_teacher_id).where(
            CourseOffering.course_id == course.id, CourseOffering.assigned_teacher_id.is_not(None)))).scalars())
        current = covers.get(cs.id)
        people = []
        for e, jr in staff:
            if absent is not None and e.id == absent.id:
                continue
            pres = self._presence(days.get((e.id, day)))
            if pres["presence"] == "LEAVE" or busy.get(e.id):
                continue
            teacher = jr.default_role_code in TEACHING_ROLES
            reasons, score = [], 0
            if teacher:
                score += 40
            if e.id in same_course:
                score += 30
                reasons.append("teaches this course")
            if absent is not None and e.department and e.department == absent.department:
                score += 15
                reasons.append(f"same department ({e.department})")
            if pres["presence"] == "IN":
                score += 20
                reasons.append(pres["presence_label"].lower())
            elif day == institute_today():
                reasons.append("not checked in yet")
            score -= 3 * load.get(e.id, 0)
            reasons.append(f"{load.get(e.id, 0)} class{'es' if load.get(e.id, 0) != 1 else ''} that day")
            people.append({"employee_id": e.id, "name": name(e), "code": e.employee_code, "job": jr.title, "is_teacher": teacher,
                           "department": e.department, "classes_that_day": load.get(e.id, 0), "presence": pres["presence"],
                           "reasons": reasons, "score": score, "current": bool(current and current.substitute_employee_id == e.id)})
        people.sort(key=lambda p: (-p["score"], p["name"] or ""))
        for i, p in enumerate(people):
            p["recommended"] = i < 3 and p["is_teacher"]
        amount, note = await self.suggested_amount(off.assigned_teacher_id, day)
        return {"class": {"class_session_id": cs.id, "date": day, "start_time": cs.start_time, "end_time": cs.end_time, "room": cs.room,
                          "course_code": course.code, "course_title": course.title, "section": off.section_label,
                          "teacher_name": name(absent), "taken": cs.attendance_taken_at is not None},
                "cover": {"id": current.id, "substitute_id": current.substitute_employee_id, "amount": float(current.amount)} if current else None,
                "suggested_amount": float(amount), "amount_note": note, "people": people[:60]}

    async def _paid_in(self, cover_ids) -> dict[uuid.UUID, str]:
        """Covers already on a payslip of a payroll that is not cancelled -> that payroll's reference."""
        from app.payroll.models import PayrollRun, PayrollStatus, Payslip, PayslipLine
        from app.payroll.service import run_ref

        if not cover_ids:
            return {}
        rows = (await self.session.execute(select(PayslipLine.ref_id, PayrollRun).join(Payslip, Payslip.id == PayslipLine.payslip_id)
                                           .join(PayrollRun, PayrollRun.id == Payslip.run_id)
                                           .where(PayslipLine.source == "COVER", PayslipLine.ref_id.in_(list(cover_ids)),
                                                  PayrollRun.status != PayrollStatus.CANCELLED))).all()
        return {ref: run_ref(run) for ref, run in rows}

    async def assign(self, class_session_id: uuid.UUID, *, substitute_id: uuid.UUID, amount: Decimal | None, actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService
        from app.notifications.models import Notification, NotificationCategory

        cs, off, course = await self._class(class_session_id)
        if cs.status != ClassSessionStatus.SCHEDULED:
            raise BusinessRuleViolationError("This class is cancelled.")
        sub = await self.session.get(Employee, substitute_id)
        if sub is None or sub.status != EmployeeStatus.ACTIVE:
            raise ValidationAppError("Choose an active member of staff.")
        if sub.id == off.assigned_teacher_id:
            raise ValidationAppError("That is the class's own teacher.")
        if amount is not None and (amount < 0 or amount > 1_000_000):
            raise ValidationAppError("Enter an amount between 0 and 1,000,000.")
        old = (await self._covers([cs.id])).get(cs.id)
        if old is not None:
            if (await self._paid_in([old.id])):
                raise BusinessRuleViolationError("This cover is already on a payroll, so it cannot be changed.")
            old.status, old.cancelled_by, old.cancelled_at, old.cancel_reason = "CANCELLED", actor_id, datetime.now(timezone.utc), "Replaced"
            await self.session.flush()
        absent = await self.session.get(Employee, off.assigned_teacher_id) if off.assigned_teacher_id else None
        suggested, note = await self.suggested_amount(off.assigned_teacher_id, cs.session_date)
        final = q2(amount) if amount is not None else suggested
        desc = f"{course.code} ({off.section_label}) on {cs.session_date:%d %b %Y} at {cs.start_time:%H:%M}" + (f", for {name(absent)}" if absent else "")
        cover = ClassCover(class_session_id=cs.id, cover_date=cs.session_date, absent_employee_id=absent.id if absent else None,
                           substitute_employee_id=sub.id, description=desc[:200], amount=final,
                           amount_note=(note if amount is None or final == suggested else f"Set by the administration (suggested Rs. {float(suggested):,.0f})")[:200],
                           status="ACTIVE", assigned_by=actor_id)
        self.session.add(cover)
        await self.session.flush()
        if sub.user_id:
            portal = await self._portal_of(sub.user_id)
            self.session.add(Notification(user_id=sub.user_id, category=NotificationCategory.TASK, title="You are covering a class",
                                          message=f"{desc}, room {cs.room}. Take its attendance from your Tasks page within {window()} minutes of the start."
                                                  + (f" Cover pay: Rs. {float(final):,.0f}." if final > 0 else ""),
                                          link=f"/dashboard/{portal}/tasks#covers" if portal else None))
        await AuditService(self.session).log(actor_user_id=actor_id, action="class_cover.assigned", entity_type="class_session", entity_id=cs.id,
                                             after={"substitute": str(sub.id), "absent": str(absent.id) if absent else None, "amount": float(final),
                                                    "replaced": str(old.id) if old else None})
        return {"id": cover.id, "description": cover.description, "substitute_name": name(sub), "amount": float(cover.amount), "amount_note": cover.amount_note}

    async def _portal_of(self, user_id: uuid.UUID) -> str | None:
        codes = (await self.session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id))).scalars().all()
        return next((PORTAL[c] for c in codes if c in PORTAL and c != "ADMIN"), "admin" if "ADMIN" in codes else None)

    async def _cover(self, cover_id: uuid.UUID) -> ClassCover:
        c = await self.session.get(ClassCover, cover_id)
        if c is None:
            raise ResourceNotFoundError("That cover was not found.")
        return c

    async def cancel(self, cover_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID) -> None:
        from app.audit.service import AuditService

        c = await self._cover(cover_id)
        if c.status != "ACTIVE":
            raise BusinessRuleViolationError("This cover is already cancelled.")
        paid = await self._paid_in([c.id])
        if paid:
            raise BusinessRuleViolationError(f"This cover is on payroll {paid[c.id]}, so it cannot be cancelled. Cancel or recalculate that payroll first.")
        c.status, c.cancelled_by, c.cancelled_at, c.cancel_reason = "CANCELLED", actor_id, datetime.now(timezone.utc), (reason or "").strip()[:200] or None
        await AuditService(self.session).log(actor_user_id=actor_id, action="class_cover.cancelled", entity_type="class_session", entity_id=c.class_session_id,
                                             after={"cover": str(c.id), "reason": c.cancel_reason})

    async def set_amount(self, cover_id: uuid.UUID, *, amount: Decimal, actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService

        c = await self._cover(cover_id)
        if c.status != "ACTIVE":
            raise BusinessRuleViolationError("This cover is cancelled.")
        if amount < 0 or amount > 1_000_000:
            raise ValidationAppError("Enter an amount between 0 and 1,000,000.")
        paid = await self._paid_in([c.id])
        if paid:
            raise BusinessRuleViolationError(f"This cover is on payroll {paid[c.id]}, so its amount cannot be changed.")
        before = float(c.amount)
        c.amount, c.amount_note = q2(amount), "Set by the administration"
        await AuditService(self.session).log(actor_user_id=actor_id, action="class_cover.amount_changed", entity_type="class_session", entity_id=c.class_session_id,
                                             before={"amount": before}, after={"amount": float(c.amount)})
        return {"id": c.id, "amount": float(c.amount)}

    async def covers(self, first: date, last: date) -> list[dict]:
        rows = (await self.session.execute(select(ClassCover, ClassSession).join(ClassSession, ClassSession.id == ClassCover.class_session_id)
                                           .where(ClassCover.cover_date >= first, ClassCover.cover_date <= last)
                                           .order_by(ClassCover.cover_date.desc(), ClassSession.start_time))).all()
        people = await self._employees({c.substitute_employee_id for c, _ in rows} | {c.absent_employee_id for c, _ in rows})
        paid = await self._paid_in([c.id for c, _ in rows])
        now = institute_now()
        out = []
        for c, cs in rows:
            st = class_state(cs, now)
            status = ("Cancelled" if c.status != "ACTIVE" else "Class cancelled" if cs.status != ClassSessionStatus.SCHEDULED
                      else "Taken" if cs.attendance_taken_at else "Not taken" if st["state"] == "MISSED" else "Waiting")
            out.append({"id": c.id, "class_session_id": cs.id, "date": c.cover_date, "description": c.description,
                        "substitute_id": c.substitute_employee_id, "substitute_name": name(people.get(c.substitute_employee_id)),
                        "absent_name": name(people.get(c.absent_employee_id)), "amount": float(c.amount), "amount_note": c.amount_note,
                        "status": status, "active": c.status == "ACTIVE", "payable": c.status == "ACTIVE" and cs.attendance_taken_at is not None,
                        "payroll": paid.get(c.id), "cancel_reason": c.cancel_reason})
        return out

    # ------------------------------------------------------------------ the substitute's side
    async def _me(self, user_id: uuid.UUID) -> Employee | None:
        return (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalars().first()

    async def mine(self, user_id: uuid.UUID) -> list[dict]:
        me = await self._me(user_id)
        if me is None:
            return []
        today = institute_today()
        rows = (await self.session.execute(select(ClassCover, ClassSession).join(ClassSession, ClassSession.id == ClassCover.class_session_id)
                                           .where(ClassCover.substitute_employee_id == me.id, ClassCover.status == "ACTIVE",
                                                  ClassCover.cover_date >= today - timedelta(days=7), ClassCover.cover_date <= today + timedelta(days=30))
                                           .order_by(ClassCover.cover_date, ClassSession.start_time))).all()
        now = institute_now()
        out = []
        for c, cs in rows:
            st = class_state(cs, now)
            out.append({"id": c.id, "description": c.description, "date": cs.session_date, "start_time": cs.start_time, "end_time": cs.end_time,
                        "room": cs.room, "amount": float(c.amount), "state": st["state"], "taken_at": st["taken_at"],
                        "can_mark": cs.status == ClassSessionStatus.SCHEDULED and st["state"] in ("OPEN", "ON_TIME")
                        and now <= datetime.combine(cs.session_date, cs.start_time) + timedelta(minutes=window()),
                        "window_minutes": window()})
        return out

    async def _my_cover(self, cover_id: uuid.UUID, user_id: uuid.UUID) -> tuple[ClassCover, ClassSession]:
        c = await self._cover(cover_id)
        me = await self._me(user_id)
        if me is None or c.substitute_employee_id != me.id or c.status != "ACTIVE":
            raise AuthorizationError("This class is not one you are covering.")
        return c, await self.session.get(ClassSession, c.class_session_id)

    async def register(self, cover_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        from app.attendance.models import AttendanceRecord
        from app.students.models import Student

        c, cs = await self._my_cover(cover_id, user_id)
        students = (await self.session.execute(select(Student).join(Enrollment, Enrollment.student_id == Student.id)
                                               .where(Enrollment.course_offering_id == cs.course_offering_id, Enrollment.status == EnrollmentStatus.ENROLLED)
                                               .order_by(Student.roll_number))).scalars().all()
        marks = {r.student_id: r.status.value for r in (await self.session.execute(select(AttendanceRecord).where(AttendanceRecord.class_session_id == cs.id))).scalars()}
        return {"id": c.id, "description": c.description, "room": cs.room,
                "students": [{"id": s.id, "name": f"{s.first_name} {s.last_name}".strip(), "roll_number": s.roll_number, "status": marks.get(s.id)} for s in students]}

    async def mark(self, cover_id: uuid.UUID, user_id: uuid.UUID, present_ids: list[uuid.UUID]) -> dict:
        """The substitute takes the class's attendance: the same rules as its own teacher (from the start, within the window)."""
        from app.attendance.service import AttendanceService

        c, cs = await self._my_cover(cover_id, user_id)
        records = await AttendanceService(self.session).mark_session(cs.id, present_student_ids=present_ids, actor_id=user_id, actor_is_admin=False)
        return {"marked": len(records), "present": sum(1 for r in records if r.status.value == "PRESENT")}


async def covers_payable(session: AsyncSession, employee_id: uuid.UUID, month_end: date, run_id: uuid.UUID) -> list[ClassCover]:
    """For payroll: this person's covers up to the end of the month whose class was held, not yet on another payroll."""
    from app.payroll.models import PayrollRun, PayrollStatus, Payslip, PayslipLine

    taken = select(PayslipLine.ref_id).join(Payslip, Payslip.id == PayslipLine.payslip_id).join(PayrollRun, PayrollRun.id == Payslip.run_id).where(
        PayslipLine.source == "COVER", PayrollRun.status != PayrollStatus.CANCELLED, PayrollRun.id != run_id, PayslipLine.ref_id.is_not(None))
    return list((await session.execute(select(ClassCover).join(ClassSession, ClassSession.id == ClassCover.class_session_id).where(
        ClassCover.substitute_employee_id == employee_id, ClassCover.status == "ACTIVE", ClassCover.amount > 0, ClassCover.cover_date <= month_end,
        ClassSession.attendance_taken_at.is_not(None), ClassSession.status == ClassSessionStatus.SCHEDULED, ClassCover.id.not_in(taken))
        .order_by(ClassCover.cover_date))).scalars())