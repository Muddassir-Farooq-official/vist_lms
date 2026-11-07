"""Graded attendance fines, per subject (Settings -> Attendance -> "Per subject, by band").

  at or above the minimum to pass (75%)      no fine
  band 1  (65% up to 75%)                    Rs. 3,000 for that subject
  band 2  (55% up to 65%)                    Rs. 6,000
  band 3  (45% up to 55%)                    Rs. 10,000
  below band 3 (under 45%)                   no automatic fine or drop: listed for an administrator's decision
                                             (drop from the course, allow with a fine, or excuse)

* An administrator runs the fines for a term before the final exams (Attendance -> Attendance fines): a preview first,
  then "Charge". Locking a course's attendance at the end of term runs the same check for that course, so nobody is
  missed. Running it again never charges twice: one row per student and subject (attendance_shortfalls); if attendance
  has fallen into a worse band since, only the difference is charged.
* Paying the fine (or an administrator waiving it), or an "excuse" decision, lifts the "minimum attendance to pass"
  condition for that subject: the exam marks decide. Until then the subject cannot be passed and the admit card shows
  it as not eligible; unpaid attendance fines also hold the admit card, course registration and the result card.
* Every Monday students in a band are warned (app, and SMS if switched on) with the fine it would mean.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.core.logging import get_logger

logger = get_logger(__name__)
REVIEW = 4                                   # the "band" of a student below the lowest fine band


def graded() -> bool:
    return int(getattr(settings, "attendance_fine_mode", 1)) == 1


def band_for(pct: float) -> int:
    """0 = no fine, 1..3 = fine bands, 4 = below the lowest band (administrator decides)."""
    if pct >= settings.attendance_eligibility_threshold_percent:
        return 0
    if pct >= settings.attendance_band1_from:
        return 1
    if pct >= settings.attendance_band2_from:
        return 2
    if pct >= settings.attendance_band3_from:
        return 3
    return REVIEW


def amount_for(band: int) -> float:
    return {1: settings.attendance_band1_amount, 2: settings.attendance_band2_amount, 3: settings.attendance_band3_amount}.get(band, 0.0)


def band_label(band: int) -> str:
    elig = settings.attendance_eligibility_threshold_percent
    edges = {1: (settings.attendance_band1_from, elig), 2: (settings.attendance_band2_from, settings.attendance_band1_from),
             3: (settings.attendance_band3_from, settings.attendance_band2_from)}
    if band in edges:
        lo, hi = edges[band]
        return f"{lo:g}% to under {hi:g}%"
    return f"below {settings.attendance_band3_from:g}%" if band == REVIEW else "no fine"


def pct_of(present: int, taken: int) -> float:
    """The same formula as AttendanceService.compute_percentage (approved leave and cancelled classes left out)."""
    return 100.0 if taken == 0 else round((present / taken) * 100, 2)


async def percentages(session: AsyncSession, *, term_label: str | None = None, offering_id: uuid.UUID | None = None,
                      unlocked_only: bool = False) -> list[dict]:
    """Every ENROLLED student's attendance in every matching course, in one query."""
    from app.academics.models import ClassSession, ClassSessionStatus, CourseOffering, Enrollment, EnrollmentStatus
    from app.attendance.models import AttendanceRecord, AttendanceStatus

    counted = and_(AttendanceRecord.id.is_not(None), AttendanceRecord.status != AttendanceStatus.LEAVE)
    stmt = (select(Enrollment.student_id, Enrollment.course_offering_id, CourseOffering.term_label, CourseOffering.course_id,
                   func.count(case((counted, 1))).label("taken"),
                   func.count(case((AttendanceRecord.status == AttendanceStatus.PRESENT, 1))).label("present"))
            .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
            .outerjoin(ClassSession, and_(ClassSession.course_offering_id == Enrollment.course_offering_id,
                                          ClassSession.status != ClassSessionStatus.CANCELLED))
            .outerjoin(AttendanceRecord, and_(AttendanceRecord.class_session_id == ClassSession.id,
                                              AttendanceRecord.student_id == Enrollment.student_id))
            .where(Enrollment.status == EnrollmentStatus.ENROLLED)
            .group_by(Enrollment.student_id, Enrollment.course_offering_id, CourseOffering.term_label, CourseOffering.course_id))
    if term_label is not None:
        stmt = stmt.where(CourseOffering.term_label == term_label)
    if offering_id is not None:
        stmt = stmt.where(CourseOffering.id == offering_id)
    if unlocked_only:
        stmt = stmt.where(CourseOffering.attendance_locked_at.is_(None))
    out = []
    for r in (await session.execute(stmt)).all():
        out.append({"student_id": r.student_id, "course_offering_id": r.course_offering_id, "term_label": r.term_label, "course_id": r.course_id,
                    "taken": int(r.taken), "present": int(r.present), "percentage": pct_of(int(r.present), int(r.taken))})
    return out


async def _linked_fines(session: AsyncSession, shortfall_ids: list) -> dict:
    """{shortfall id: [Fine, ...]} for the fines charged for these shortfalls."""
    from app.fines.models import Fine

    if not shortfall_ids:
        return {}
    out: dict = {}
    for f in (await session.execute(select(Fine).where(Fine.related_entity_type == "attendance_shortfall",
                                                       Fine.related_entity_id.in_(shortfall_ids)))).scalars():
        out.setdefault(f.related_entity_id, []).append(f)
    return out


def _fines_settled(fines: list) -> bool:
    from app.fines.models import FineStatus

    return all(f.status in (FineStatus.PAID, FineStatus.WAIVED, FineStatus.REJECTED) for f in fines)


async def cleared(session: AsyncSession, student_id: uuid.UUID, offering_id: uuid.UUID, pct: float) -> bool:
    """Whether a student below the minimum to pass may still pass this subject on marks: the fine for their band is paid
    (or waived), or an administrator excused them. Always False with the older one-per-term rule."""
    if not graded() or pct >= settings.attendance_eligibility_threshold_percent:
        return False
    from app.attendance.models import AttendanceShortfall, ShortfallStatus

    row = (await session.execute(select(AttendanceShortfall).where(AttendanceShortfall.student_id == student_id,
                                                                    AttendanceShortfall.course_offering_id == offering_id))).scalar_one_or_none()
    if row is None:
        return False
    if row.status == ShortfallStatus.EXCUSED.value:
        return True
    if row.status not in (ShortfallStatus.FINED.value, ShortfallStatus.ALLOWED_WITH_FINE.value):
        return False
    if row.status == ShortfallStatus.FINED.value and band_for(pct) > row.band:
        return False                                          # fell into a worse band after the fine: the difference is due first
    return _fines_settled((await _linked_fines(session, [row.id])).get(row.id, []))


async def not_cleared_reason(session: AsyncSession, student_id: uuid.UUID, offering_id: uuid.UUID, pct: float) -> str:
    """Why a student below the minimum is not (yet) allowed, in words for the admit card."""
    base = f"attendance {pct:g}% (minimum {settings.attendance_eligibility_threshold_percent:g}%)"
    if not graded():
        return base
    from app.attendance.models import AttendanceShortfall, ShortfallStatus

    row = (await session.execute(select(AttendanceShortfall).where(AttendanceShortfall.student_id == student_id,
                                                                    AttendanceShortfall.course_offering_id == offering_id))).scalar_one_or_none()
    if row is None:
        return base + f": attendance fine of Rs. {amount_for(band_for(pct)):,.0f} not charged yet" if band_for(pct) < REVIEW else base + ": awaiting the administration's decision"
    if row.status == ShortfallStatus.AWAITING_DECISION.value:
        return base + ": awaiting the administration's decision"
    if row.status == ShortfallStatus.DROPPED.value:
        return base + ": dropped from the course"
    return base + ": attendance fine not paid yet"


class ShortfallService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _rows(self, keys: list[tuple]) -> dict:
        from app.attendance.models import AttendanceShortfall

        if not keys:
            return {}
        students = list({k[0] for k in keys})
        rows = (await self.session.execute(select(AttendanceShortfall).where(AttendanceShortfall.student_id.in_(students)))).scalars().all()
        return {(r.student_id, r.course_offering_id): r for r in rows}

    async def run(self, *, term_label: str | None = None, offering_id: uuid.UUID | None = None, actor_id: uuid.UUID | None,
                  dry_run: bool) -> dict:
        """Charge (or, with dry_run, preview) the fines for a term or one course. Safe to repeat."""
        from app.academics.models import Course, CourseOffering
        from app.attendance.models import AttendanceShortfall, ShortfallStatus
        from app.students.models import Student

        if not graded():
            raise BusinessRuleViolationError("Attendance fines are set to the older rule (one fine per term at lock). "
                                             "Change it in Settings -> Attendance to use fines by band.")
        if term_label is None and offering_id is None:
            raise BusinessRuleViolationError("Choose the term.")
        data = [d for d in await percentages(self.session, term_label=term_label, offering_id=offering_id) if d["taken"] > 0]
        short = [d | {"band": band_for(d["percentage"])} for d in data]
        short = [d for d in short if d["band"] > 0]
        existing = await self._rows([(d["student_id"], d["course_offering_id"]) for d in short])
        students = {s.id: s for s in (await self.session.execute(select(Student).where(
            Student.id.in_({d["student_id"] for d in short} or {uuid.uuid4()})))).scalars()}
        courses = {c.id: c for c in (await self.session.execute(select(Course).where(
            Course.id.in_({d["course_id"] for d in short} or {uuid.uuid4()})))).scalars()}
        now = datetime.now(timezone.utc)
        items, sms_lines = [], {}
        totals = {"new_fines": 0, "top_ups": 0, "to_review": 0, "unchanged": 0, "amount": 0.0}
        for d in sorted(short, key=lambda x: (x["band"], x["percentage"]), reverse=True):
            row = existing.get((d["student_id"], d["course_offering_id"]))
            band, pct = d["band"], d["percentage"]
            st, course = students.get(d["student_id"]), courses.get(d["course_id"])
            code = course.code if course else "a subject"
            action, charge = "unchanged", 0.0
            if band < REVIEW:
                if row is None:
                    action, charge = "fine", amount_for(band)
                elif row.status in (ShortfallStatus.FINED.value, ShortfallStatus.ALLOWED_WITH_FINE.value) and band > row.band and row.band < REVIEW:
                    action, charge = "top-up", max(0.0, amount_for(band) - float(row.amount_charged))
            else:
                if row is None:
                    action = "review"
                elif row.status in (ShortfallStatus.FINED.value,) and row.band < REVIEW:
                    action = "review"                                  # fell below the lowest band after being fined
            items.append({"student_id": d["student_id"], "student": f"{st.first_name} {st.last_name}" if st else "",
                          "roll_number": st.roll_number if st else None, "course_offering_id": d["course_offering_id"], "course": code,
                          "term_label": d["term_label"], "classes": d["taken"], "present": d["present"], "percentage": pct, "band": band,
                          "band_label": band_label(band), "action": action, "charge": charge,
                          "status": row.status if row is not None else None, "already_charged": float(row.amount_charged) if row is not None else 0.0})
            if action == "fine":
                totals["new_fines"] += 1
            elif action == "top-up":
                totals["top_ups"] += 1 if charge > 0 else 0
            elif action == "review":
                totals["to_review"] += 1
            else:
                totals["unchanged"] += 1
            totals["amount"] += charge
            if dry_run:
                continue
            # ---- apply
            if row is None:
                row = AttendanceShortfall(student_id=d["student_id"], course_offering_id=d["course_offering_id"], term_label=d["term_label"],
                                          percentage=pct, band=band, amount_charged=0,
                                          status=(ShortfallStatus.AWAITING_DECISION if band == REVIEW else ShortfallStatus.FINED).value,
                                          created_at=now, updated_at=now)
                self.session.add(row)
                await self.session.flush()
                existing[(d["student_id"], d["course_offering_id"])] = row
            else:
                row.percentage = pct
            if action in ("fine", "top-up") and charge > 0:
                await self._charge(row, charge, actor_id=actor_id, why=(f"Attendance {pct:g}% in {code} ({d['term_label']}), band {band_label(band)}"
                                                                         + (f": difference after earlier Rs. {float(row.amount_charged):,.0f}" if action == "top-up" else "")))
                row.band = band
                sms_lines.setdefault(d["student_id"], []).append(f"{code} {pct:g}% Rs.{charge:,.0f}")
            elif action == "top-up":
                row.band = band
            elif action == "review":
                row.band, row.status = REVIEW, ShortfallStatus.AWAITING_DECISION.value
                await self._tell(d["student_id"], "Attendance below the lowest limit",
                                 f"Your attendance in {code} is {pct:g}%, below {settings.attendance_band3_from:g}%. The administration will decide "
                                 "whether you can continue in this subject; you will be told the decision.")
        if not dry_run:
            from app.sms.service import SmsService

            for sid, lines in sms_lines.items():
                total = sum(float(x.rsplit("Rs.", 1)[1].replace(",", "")) for x in lines)
                await SmsService(self.session).queue_for_student(
                    sid, event="ATTENDANCE_FINE",
                    body=f"{settings.app_name}: attendance fine Rs.{total:,.0f}: " + "; ".join(lines)[:110] + ". Pay at accounts; then marks decide.",
                    guardian_body=f"{settings.app_name}: attendance fine Rs.{total:,.0f} for your child (" + "; ".join(lines)[:90] + "). Please contact the institute.")
            await self.audit.log(actor_user_id=actor_id, action="attendance.fines_run", entity_type="course_offering" if offering_id else "term",
                                 entity_id=offering_id, after={"term": term_label, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in totals.items()}})
            await self.session.flush()
        unmarked = 0
        if term_label is not None:
            from app.attendance.service import AttendanceService

            overdue = await AttendanceService(self.session).list_overdue_unmarked_sessions()
            offs = set((await self.session.execute(select(CourseOffering.id).where(CourseOffering.term_label == term_label))).scalars())
            unmarked = sum(1 for s in overdue if s.course_offering_id in offs)
        return {"term_label": term_label, "dry_run": dry_run, **totals, "amount": round(totals["amount"], 2), "items": items,
                "unmarked_classes": unmarked, "bands": [{"band": b, "label": band_label(b), "amount": amount_for(b)} for b in (1, 2, 3)],
                "review_below": settings.attendance_band3_from, "minimum": settings.attendance_eligibility_threshold_percent}

    async def _charge(self, row, amount: float, *, actor_id, why: str) -> None:
        from app.fines.models import FinePayerType, FineSource
        from app.fines.service import FineService

        await FineService(self.session).issue(payer_type=FinePayerType.STUDENT, payer_id=row.student_id, amount=round(amount, 2), reason=why[:480],
                                              source=FineSource.ATTENDANCE, actor_id=actor_id, related_entity_type="attendance_shortfall",
                                              related_entity_id=row.id, notify_title="Attendance fine")
        row.amount_charged = float(Decimal(str(row.amount_charged or 0)) + Decimal(str(round(amount, 2))))

    async def _tell(self, student_id, title: str, message: str) -> None:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.students.models import Student

        st = await self.session.get(Student, student_id)
        if st is not None and st.user_id is not None:
            await create_notification(self.session, user_id=st.user_id, category=NotificationCategory.GENERAL, title=title, message=message,
                                      link="/dashboard/student/attendance")

    # ------------------------------------------------------------------ below the lowest band: the administration decides
    async def listing(self, *, term_label: str | None, status: str | None) -> list[dict]:
        from app.academics.models import Course, CourseOffering
        from app.attendance.models import AttendanceShortfall
        from app.students.models import Student

        stmt = (select(AttendanceShortfall, Student, Course).join(Student, Student.id == AttendanceShortfall.student_id)
                .join(CourseOffering, CourseOffering.id == AttendanceShortfall.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
                .order_by(AttendanceShortfall.percentage))
        if term_label:
            stmt = stmt.where(AttendanceShortfall.term_label == term_label)
        if status:
            stmt = stmt.where(AttendanceShortfall.status == status)
        rows = (await self.session.execute(stmt.limit(2000))).all()
        fines = await _linked_fines(self.session, [r.id for r, _s, _c in rows])
        return [{"id": r.id, "student_id": s.id, "student": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number, "course": c.code,
                 "course_title": c.title, "course_offering_id": r.course_offering_id, "term_label": r.term_label, "percentage": float(r.percentage),
                 "band": r.band, "band_label": band_label(r.band), "status": r.status, "amount_charged": float(r.amount_charged),
                 "fines_unpaid": sum(float(f.amount) for f in fines.get(r.id, []) if f.status.value in ("PENDING", "PENDING_APPROVAL")),
                 "decision_note": r.decision_note, "decided_at": r.decided_at} for r, s, c in rows]

    async def decide(self, shortfall_id: uuid.UUID, *, decision: str, note: str, amount: float | None, actor_id: uuid.UUID) -> dict:
        from app.academics.models import Course, CourseOffering, EnrollmentStatus
        from app.academics.repository import EnrollmentRepository
        from app.attendance.models import AttendanceShortfall, ShortfallStatus

        row = (await self.session.execute(select(AttendanceShortfall).where(AttendanceShortfall.id == shortfall_id).with_for_update())).scalar_one_or_none()
        if row is None:
            raise ResourceNotFoundError("Not found.")
        if row.status != ShortfallStatus.AWAITING_DECISION.value:
            raise BusinessRuleViolationError("This has already been decided.")
        if len((note or "").strip()) < 5:
            raise BusinessRuleViolationError("Write the reason for the decision (kept on record).")
        offering = await self.session.get(CourseOffering, row.course_offering_id)
        course = await self.session.get(Course, offering.course_id) if offering else None
        code = course.code if course else "the subject"
        before = row.status
        if decision == "DROP":
            enrollment = await EnrollmentRepository(self.session).get_by_student_and_offering(row.student_id, row.course_offering_id)
            if enrollment is not None and enrollment.status == EnrollmentStatus.ENROLLED:
                from app.academics.service import EnrollmentService

                await EnrollmentService(self.session).withdraw(enrollment.id, student_id=None, actor_id=actor_id, reason="ATTENDANCE")
            row.status = ShortfallStatus.DROPPED.value
            msg = f"You have been withdrawn from {code} because of low attendance ({float(row.percentage):g}%)."
        elif decision == "FINE":
            amount = float(amount if amount is not None else settings.attendance_band3_amount)
            if amount <= 0:
                raise BusinessRuleViolationError("Enter the fine amount.")
            due = round(amount - float(row.amount_charged), 2)
            if due > 0:
                await self._charge(row, due, actor_id=actor_id, why=f"Attendance {float(row.percentage):g}% in {code} ({row.term_label}): allowed to continue with a fine")
            row.status = ShortfallStatus.ALLOWED_WITH_FINE.value
            msg = f"You may continue in {code} once the attendance fine is paid; then your marks decide the result."
        elif decision == "EXCUSE":
            row.status = ShortfallStatus.EXCUSED.value
            msg = f"Your low attendance in {code} has been excused; your marks decide the result."
        else:
            raise BusinessRuleViolationError("Choose drop, allow with a fine, or excuse.")
        row.decision_note, row.decided_by, row.decided_at = note.strip()[:500], actor_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=actor_id, action="attendance.shortfall_decided", entity_type="attendance_shortfall", entity_id=row.id,
                             before={"status": before}, after={"status": row.status, "note": row.decision_note, "amount": amount})
        await self._tell(row.student_id, "Decision on your attendance", msg)
        await self.session.flush()
        return {"id": row.id, "status": row.status, "amount_charged": float(row.amount_charged)}


# ---------------------------------------------------------------------- weekly warnings
async def weekly_warnings(session: AsyncSession, *, force: bool = False) -> int:
    """Every Monday from 10 AM: students with a subject in a fine band get one notification (and an SMS if switched on)
    listing the subjects, their attendance and the fine each would mean. Once a week; never raises. Returns students warned."""
    from app.core.background import beat
    from app.core.clock import institute_now

    try:
        if not graded() or not int(settings.attendance_weekly_warnings):
            return 0
        now = institute_now()
        week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        if not force:
            if now.weekday() != 0 or now.hour < 10:
                return 0
            from app.notifications.email_models import ServiceHeartbeat

            hb = await session.get(ServiceHeartbeat, "attendance-warnings")
            if hb is not None and (hb.detail or "").startswith(week):
                return 0
        async with session.begin_nested():
            n = await _warn(session)
            await beat(session, "attendance-warnings", f"{week}: {n} students warned")
        return n
    except Exception as exc:  # noqa: BLE001 - a warning problem must not stop sending
        logger.warning("attendance_warning_failed", error=str(exc)[:300])
        return 0


async def _warn(session: AsyncSession) -> int:
    from app.academics.models import Course
    from app.attendance.models import AttendanceShortfall
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification
    from app.sms.service import SmsService
    from app.students.models import Student

    data = [d | {"band": band_for(d["percentage"])} for d in await percentages(session, unlocked_only=True)
            if d["taken"] >= int(settings.attendance_warning_min_classes)]
    data = [d for d in data if d["band"] > 0]
    if not data:
        return 0
    done = {(r.student_id, r.course_offering_id): r for r in (await session.execute(select(AttendanceShortfall).where(
        AttendanceShortfall.student_id.in_({d["student_id"] for d in data})))).scalars()}
    courses = {c.id: c.code for c in (await session.execute(select(Course).where(Course.id.in_({d["course_id"] for d in data})))).scalars()}
    per: dict = {}
    for d in data:
        r = done.get((d["student_id"], d["course_offering_id"]))
        if r is not None and (r.band >= d["band"] or r.status != "FINED"):
            continue                                                    # already charged at this band, or already decided
        what = (f"fine Rs. {amount_for(d['band']):,.0f}" if d["band"] < REVIEW else "the administration will decide whether you can continue")
        per.setdefault(d["student_id"], []).append((courses.get(d["course_id"], "a subject"), d["percentage"], what))
    warned = 0
    for sid, subjects in per.items():
        st = await session.get(Student, sid)
        if st is None:
            continue
        lines = "; ".join(f"{c} {p:g}% ({w})" for c, p, w in subjects)
        if st.user_id is not None:
            await create_notification(session, user_id=st.user_id, category=NotificationCategory.GENERAL, title="Attendance warning",
                                      message=f"Below {settings.attendance_eligibility_threshold_percent:g}% in: {lines}. Attend every class to avoid the fine.",
                                      link="/dashboard/student/attendance")
        short = "; ".join(f"{c} {p:g}%" for c, p, _w in subjects)
        await SmsService(session).queue_for_student(
            sid, event="ATTENDANCE_WARNING", once_within_days=6,
            body=f"{settings.app_name}: attendance below {settings.attendance_eligibility_threshold_percent:g}%: {short}. A fine applies if it stays low.",
            guardian_body=f"{settings.app_name}: {st.first_name}'s attendance is low: {short}. A fine applies if it stays low.")
        warned += 1
    return warned
