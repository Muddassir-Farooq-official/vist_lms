"""The internship year (models.py)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.internship.models import DutyLog, InternEvaluation, InternLeave, InternPlacement, Internship

SHIFTS = {"MORNING": "Morning", "EVENING": "Evening", "NIGHT": "Night"}
CRITERIA = ("clinical_skills", "professionalism", "communication", "punctuality", "patient_care")
CRITERIA_NAMES = {"clinical_skills": "Clinical skills", "professionalism": "Professionalism", "communication": "Communication",
                  "punctuality": "Punctuality", "patient_care": "Patient care"}


def _now():
    return datetime.now(timezone.utc)


class InternshipService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ the administration
    async def start(self, *, student_ids: list[uuid.UUID], start_date: date, end_date: date, required_hours: int, actor_id: uuid.UUID) -> dict:
        from app.students.models import Student, StudentStatus

        if end_date <= start_date:
            raise ValidationAppError("The internship must end after it starts.")
        if not (1 <= required_hours <= 5000):
            raise ValidationAppError("Required hours 1 to 5000.")
        made, skipped = 0, []
        for s in (await self.session.execute(select(Student).where(Student.id.in_(student_ids)).order_by(Student.roll_number))).scalars():
            if s.status != StudentStatus.ACTIVE:
                skipped.append(f"{s.roll_number}: not a current student")
                continue
            if (await self.session.execute(select(Internship.id).where(Internship.student_id == s.id, Internship.status.in_(("ONGOING", "COMPLETED"))))).first():
                skipped.append(f"{s.roll_number}: already has an internship")
                continue
            self.session.add(Internship(student_id=s.id, start_date=start_date, end_date=end_date, required_hours=required_hours, created_by=actor_id))
            made += 1
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="internship.started", entity_type="internship", entity_id=None,
                                             metadata={"interns": made, "from": str(start_date), "to": str(end_date), "hours": required_hours})
        return {"started": made, "skipped": skipped}

    async def _hours(self, ids: list) -> dict:
        rows = (await self.session.execute(select(DutyLog.internship_id, DutyLog.status, func.sum(DutyLog.hours)).where(DutyLog.internship_id.in_(ids or [uuid.uuid4()]))
                                           .group_by(DutyLog.internship_id, DutyLog.status))).all()
        out: dict = {}
        for iid, st, h in rows:
            out.setdefault(iid, {"VERIFIED": 0.0, "SUBMITTED": 0.0, "REJECTED": 0.0})[st] = float(h or 0)
        return out

    async def interns(self, *, status: str | None = "ONGOING") -> list[dict]:
        from app.programs.models import Program
        from app.students.models import Student

        q = (select(Internship, Student, Program.code).join(Student, Student.id == Internship.student_id).outerjoin(Program, Program.id == Student.program_id)
             .order_by(Student.roll_number))
        if status:
            q = q.where(Internship.status == status)
        rows = (await self.session.execute(q.limit(2000))).all()
        ids = [i.id for i, _s, _c in rows]
        hours = await self._hours(ids)
        evals = dict((await self.session.execute(select(InternEvaluation.internship_id, func.count()).where(InternEvaluation.internship_id.in_(ids or [uuid.uuid4()]))
                                                  .group_by(InternEvaluation.internship_id))).all())
        leaves = dict((await self.session.execute(select(InternLeave.internship_id, func.count()).where(InternLeave.internship_id.in_(ids or [uuid.uuid4()]),
                                                                                                       InternLeave.status == "REQUESTED").group_by(InternLeave.internship_id))).all())
        today = institute_today()
        current = {}
        for p, unit_name, site_name in (await self.session.execute(self._placement_q().where(InternPlacement.internship_id.in_(ids or [uuid.uuid4()]),
                                                                                              InternPlacement.start_date <= today, InternPlacement.end_date >= today))).all():
            current[p.internship_id] = f"{site_name} — {unit_name}"
        return [{"id": i.id, "student_id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}", "program": code, "status": i.status,
                 "start_date": i.start_date, "end_date": i.end_date, "required_hours": i.required_hours,
                 "verified_hours": hours.get(i.id, {}).get("VERIFIED", 0.0), "waiting_hours": hours.get(i.id, {}).get("SUBMITTED", 0.0),
                 "evaluations": int(evals.get(i.id, 0)), "leave_requests": int(leaves.get(i.id, 0)), "placed_at": current.get(i.id),
                 "completed_on": i.completed_on} for i, s, code in rows]

    @staticmethod
    def _placement_q():
        from app.clinical.models import ClinicalSite, ClinicalUnit

        return (select(InternPlacement, ClinicalUnit.name, ClinicalSite.name).join(ClinicalUnit, ClinicalUnit.id == InternPlacement.unit_id)
                .join(ClinicalSite, ClinicalSite.id == ClinicalUnit.site_id))

    async def _get(self, internship_id: uuid.UUID) -> Internship:
        i = await self.session.get(Internship, internship_id)
        if i is None:
            raise ResourceNotFoundError("Internship not found.")
        return i

    async def place(self, internship_id: uuid.UUID, *, unit_id: uuid.UUID, supervisor_id: uuid.UUID | None, start_date: date, end_date: date) -> dict:
        from app.clinical.models import ClinicalSupervisor, ClinicalUnit

        i = await self._get(internship_id)
        if i.status != "ONGOING":
            raise BusinessRuleViolationError("This internship is not ongoing.")
        if end_date < start_date or start_date < i.start_date or end_date > i.end_date:
            raise ValidationAppError(f"The placement must be within the internship ({i.start_date:%d %b %Y} to {i.end_date:%d %b %Y}).")
        if await self.session.get(ClinicalUnit, unit_id) is None:
            raise ResourceNotFoundError("Ward not found.")
        if supervisor_id and await self.session.get(ClinicalSupervisor, supervisor_id) is None:
            raise ResourceNotFoundError("Supervisor not found.")
        clash = (await self.session.execute(select(InternPlacement).where(InternPlacement.internship_id == i.id, InternPlacement.start_date <= end_date,
                                                                          InternPlacement.end_date >= start_date))).scalars().first()
        if clash:
            raise BusinessRuleViolationError(f"The intern is already placed from {clash.start_date:%d %b} to {clash.end_date:%d %b}.")
        p = InternPlacement(internship_id=i.id, unit_id=unit_id, supervisor_id=supervisor_id, start_date=start_date, end_date=end_date)
        self.session.add(p)
        await self.session.flush()
        return {"id": p.id}

    async def detail(self, internship_id: uuid.UUID) -> dict:
        from app.clinical.models import ClinicalSupervisor
        from app.students.models import Student

        i = await self._get(internship_id)
        s = await self.session.get(Student, i.student_id)
        sups = {x.id: x.name for x in (await self.session.execute(select(ClinicalSupervisor))).scalars()}
        placements = [{"id": p.id, "site": site, "unit": unit, "supervisor": sups.get(p.supervisor_id), "start_date": p.start_date, "end_date": p.end_date}
                      for p, unit, site in (await self.session.execute(self._placement_q().where(InternPlacement.internship_id == i.id)
                                                                       .order_by(InternPlacement.start_date))).all()]
        logs = (await self.session.execute(select(DutyLog).where(DutyLog.internship_id == i.id).order_by(DutyLog.day.desc(), DutyLog.shift).limit(400))).scalars().all()
        leaves = (await self.session.execute(select(InternLeave).where(InternLeave.internship_id == i.id).order_by(InternLeave.from_date.desc()))).scalars().all()
        evals = (await self.session.execute(select(InternEvaluation).where(InternEvaluation.internship_id == i.id).order_by(InternEvaluation.created_at.desc()))).scalars().all()
        hours = (await self._hours([i.id])).get(i.id, {"VERIFIED": 0.0, "SUBMITTED": 0.0})
        leave_days = sum((lv.to_date - lv.from_date).days + 1 for lv in leaves if lv.status == "APPROVED")
        return {"id": i.id, "student_id": s.id, "name": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number, "status": i.status,
                "start_date": i.start_date, "end_date": i.end_date, "required_hours": i.required_hours, "verified_hours": hours.get("VERIFIED", 0.0),
                "waiting_hours": hours.get("SUBMITTED", 0.0), "leave_days": leave_days, "completed_on": i.completed_on, "note": i.note,
                "placements": placements, "criteria": [{"key": k, "name": CRITERIA_NAMES[k]} for k in CRITERIA],
                "logs": [self._log(x) for x in logs], "leaves": [self._leave(x) for x in leaves],
                "evaluations": [{"id": e.id, "scores": e.scores, "average": float(e.average), "comment": e.comment, "created_at": e.created_at} for e in evals]}

    @staticmethod
    def _log(x: DutyLog) -> dict:
        return {"id": x.id, "day": x.day, "shift": x.shift, "shift_name": SHIFTS[x.shift], "hours": float(x.hours), "note": x.note, "status": x.status,
                "reject_note": x.reject_note}

    @staticmethod
    def _leave(x: InternLeave) -> dict:
        return {"id": x.id, "from_date": x.from_date, "to_date": x.to_date, "days": (x.to_date - x.from_date).days + 1, "reason": x.reason, "status": x.status,
                "decision_note": x.decision_note}

    async def decide_leave(self, leave_id: uuid.UUID, *, approve: bool, note: str | None, actor_id: uuid.UUID) -> dict:
        lv = await self.session.get(InternLeave, leave_id)
        if lv is None:
            raise ResourceNotFoundError("Leave not found.")
        if lv.status != "REQUESTED":
            raise BusinessRuleViolationError("This leave was already decided.")
        if not approve and len((note or "").strip()) < 3:
            raise ValidationAppError("Say why it is not approved.")
        lv.status, lv.decided_by, lv.decided_at, lv.decision_note = ("APPROVED" if approve else "REJECTED"), actor_id, _now(), (note or "").strip()[:300] or None
        await self.session.flush()
        await self._tell_intern(lv.internship_id, f"Your internship leave was {'approved' if approve else 'not approved'}", lv.decision_note or "")
        return self._leave(lv)

    async def complete(self, internship_id: uuid.UUID, *, withdraw: bool, note: str | None, actor_id: uuid.UUID) -> dict:
        """Complete (or withdraw) an internship. Completing with fewer verified hours than required, or no evaluation,
        needs a reason (recorded)."""
        i = await self._get(internship_id)
        if i.status != "ONGOING":
            raise BusinessRuleViolationError("This internship is already decided.")
        d = await self.detail(i.id)
        short = []
        if not withdraw:
            if d["verified_hours"] < i.required_hours:
                short.append(f"{d['verified_hours']:g} of {i.required_hours} hours verified")
            if not d["evaluations"]:
                short.append("no evaluation")
        if (short or withdraw) and len((note or "").strip()) < 10:
            raise ValidationAppError(("Not complete: " + ", ".join(short) + ". " if short else "") + "Give a reason of at least 10 letters.")
        i.status, i.completed_on, i.decided_by = ("WITHDRAWN" if withdraw else "COMPLETED"), institute_today(), actor_id
        i.note = (note or "").strip()[:500] or None
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="internship." + ("withdrawn" if withdraw else "completed"), entity_type="student",
                                             entity_id=i.student_id, metadata={"verified_hours": d["verified_hours"], "required": i.required_hours, "note": i.note})
        if not withdraw:
            await self._tell_intern(i.id, "Your internship is complete", "Congratulations.")
        return {"id": i.id, "status": i.status}

    async def _tell_intern(self, internship_id, title: str, message: str) -> None:
        try:
            async with self.session.begin_nested():
                from app.notifications.models import NotificationCategory
                from app.notifications.service import create_notification
                from app.students.models import Student

                i = await self.session.get(Internship, internship_id)
                s = await self.session.get(Student, i.student_id)
                if s and s.user_id:
                    await create_notification(self.session, user_id=s.user_id, category=NotificationCategory.GENERAL, title=title, message=message,
                                              link="/dashboard/student/clinical#internship")
        except Exception:                          # noqa: BLE001
            pass

    # ------------------------------------------------------------------ verifying hours and evaluating (supervisor or administration)
    async def _may_supervise(self, placement_id: uuid.UUID, user, *, admin: bool) -> InternPlacement:
        from app.clinical.service import ClinicalService

        p = await self.session.get(InternPlacement, placement_id)
        if p is None:
            raise ResourceNotFoundError("Placement not found.")
        if admin:
            return p
        sup = await ClinicalService(self.session).supervisor_of(user)
        if sup is None or p.supervisor_id != sup.id:
            raise AuthorizationError("This intern is supervised by someone else.")
        return p

    async def supervised(self, user) -> list[dict]:
        """Interns placed with this supervisor (current and last 30 days), with duty logs waiting for verification."""
        from app.clinical.service import ClinicalService
        from app.students.models import Student

        sup = await ClinicalService(self.session).supervisor_of(user)
        if sup is None:
            return []
        today = institute_today()
        rows = (await self.session.execute(self._placement_q().add_columns(Student).join(Internship, Internship.id == InternPlacement.internship_id)
                                           .join(Student, Student.id == Internship.student_id)
                                           .where(InternPlacement.supervisor_id == sup.id, InternPlacement.end_date >= today - timedelta(days=30),
                                                  Internship.status == "ONGOING").order_by(InternPlacement.start_date))).all()
        out = []
        for p, unit, site, s in rows:
            logs = (await self.session.execute(select(DutyLog).where(DutyLog.placement_id == p.id).order_by(DutyLog.status != "SUBMITTED", DutyLog.day.desc())
                                               .limit(60))).scalars().all()
            out.append({"placement_id": p.id, "internship_id": p.internship_id, "name": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number,
                        "where": f"{site} — {unit}", "start_date": p.start_date, "end_date": p.end_date, "logs": [self._log(x) for x in logs],
                        "criteria": [{"key": k, "name": CRITERIA_NAMES[k]} for k in CRITERIA]})
        return out

    async def verify(self, log_id: uuid.UUID, *, approve: bool, note: str | None, user, admin: bool) -> dict:
        x = await self.session.get(DutyLog, log_id)
        if x is None:
            raise ResourceNotFoundError("Duty not found.")
        await self._may_supervise(x.placement_id, user, admin=admin)
        if x.status != "SUBMITTED":
            raise BusinessRuleViolationError("This duty was already decided.")
        if not approve and len((note or "").strip()) < 3:
            raise ValidationAppError("Say why (the intern sees this).")
        x.status, x.decided_by, x.decided_at = ("VERIFIED" if approve else "REJECTED"), user.id, _now()
        x.reject_note = None if approve else note.strip()[:300]
        await self.session.flush()
        return self._log(x)

    async def evaluate(self, placement_id: uuid.UUID, *, scores: dict, comment: str | None, user, admin: bool) -> dict:
        p = await self._may_supervise(placement_id, user, admin=admin)
        clean = {}
        for k in CRITERIA:
            v = scores.get(k)
            if not isinstance(v, int) or not (1 <= v <= 5):
                raise ValidationAppError(f"{CRITERIA_NAMES[k]}: 1 to 5.")
            clean[k] = v
        avg = Decimal(sum(clean.values())) / len(clean)
        e = InternEvaluation(internship_id=p.internship_id, placement_id=p.id, scores=clean, average=avg.quantize(Decimal("0.01")),
                             comment=(comment or "").strip()[:1000] or None, evaluated_by=user.id)
        self.session.add(e)
        await self.session.flush()
        return {"id": e.id, "average": float(e.average)}

    # ------------------------------------------------------------------ the intern
    async def mine(self, student) -> dict | None:
        i = (await self.session.execute(select(Internship).where(Internship.student_id == student.id).order_by(Internship.created_at.desc()))).scalars().first()
        if i is None:
            return None
        d = await self.detail(i.id)
        d.pop("note", None)
        return d

    async def log_duty(self, student, *, day: date, shift: str, hours: float, note: str | None) -> dict:
        i = (await self.session.execute(select(Internship).where(Internship.student_id == student.id, Internship.status == "ONGOING"))).scalar_one_or_none()
        if i is None:
            raise BusinessRuleViolationError("You have no ongoing internship.")
        if shift not in SHIFTS:
            raise ValidationAppError("Morning, evening or night.")
        if not (0.5 <= hours <= 16):
            raise ValidationAppError("Hours 0.5 to 16.")
        today = institute_today()
        if day > today or (today - day).days > 14:
            raise ValidationAppError("Log duties within two weeks, not ahead.")
        p = (await self.session.execute(select(InternPlacement).where(InternPlacement.internship_id == i.id, InternPlacement.start_date <= day,
                                                                      InternPlacement.end_date >= day))).scalars().first()
        if p is None:
            raise BusinessRuleViolationError("You had no placement on that day.")
        if (await self.session.execute(select(InternLeave.id).where(InternLeave.internship_id == i.id, InternLeave.status == "APPROVED",
                                                                    InternLeave.from_date <= day, InternLeave.to_date >= day))).first():
            raise BusinessRuleViolationError("You were on leave that day.")
        if (await self.session.execute(select(DutyLog.id).where(DutyLog.internship_id == i.id, DutyLog.day == day, DutyLog.shift == shift))).first():
            raise BusinessRuleViolationError("That shift is already logged.")
        x = DutyLog(internship_id=i.id, placement_id=p.id, day=day, shift=shift, hours=Decimal(str(hours)), note=(note or "").strip()[:300] or None)
        self.session.add(x)
        await self.session.flush()
        return self._log(x)

    async def ask_leave(self, student, *, from_date: date, to_date: date, reason: str) -> dict:
        i = (await self.session.execute(select(Internship).where(Internship.student_id == student.id, Internship.status == "ONGOING"))).scalar_one_or_none()
        if i is None:
            raise BusinessRuleViolationError("You have no ongoing internship.")
        if to_date < from_date or (to_date - from_date).days > 60:
            raise ValidationAppError("Choose up to 60 days.")
        if len((reason or "").strip()) < 5:
            raise ValidationAppError("Give the reason.")
        lv = InternLeave(internship_id=i.id, from_date=from_date, to_date=to_date, reason=reason.strip()[:500])
        self.session.add(lv)
        await self.session.flush()
        return self._leave(lv)

    async def withdraw_log(self, student, log_id: uuid.UUID) -> None:
        x = await self.session.get(DutyLog, log_id)
        i = await self.session.get(Internship, x.internship_id) if x else None
        if x is None or i is None or i.student_id != student.id:
            raise ResourceNotFoundError("Duty not found.")
        if x.status != "SUBMITTED":
            raise BusinessRuleViolationError("A verified or rejected duty stays on record.")
        await self.session.delete(x)
        await self.session.flush()


async def graduation_problem(session: AsyncSession, student_id: uuid.UUID) -> str | None:
    """For the graduation list: an internship that is ongoing (not yet complete) keeps the student from being clear.
    Students who never had an internship are not affected."""
    i = (await session.execute(select(Internship).where(Internship.student_id == student_id).order_by(Internship.created_at.desc()))).scalars().first()
    if i is not None and i.status == "ONGOING":
        return "Internship not complete."
    return None
