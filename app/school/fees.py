"""School mode, stage 4: school fees on top of the fee ledger.

Each class has a fee plan per session (monthly tuition, admission fee, annual charges); van / bus routes have a monthly
fee. On the 1st of every month (a daily job catches students who join later in the month) each student in a school
class gets ONE voucher for the month: tuition + transport + (on their first voucher of the session) the annual charges,
less the sibling discount. A new student placed in a class with "charge the admission fee" gets an admission voucher.

How it sits on the fee ledger (so payments, the bank's 1Bill, receipts, reminders, late fees, refunds and the
finance reports all work as for any other fee):
  * one fee structure "School fees - <session>" (fee type SCHOOL) with a FIXED late fine (late_fee_once): it is never
    charged to anyone by itself (applies_to_students is off);
  * one fee account (FeeAssignment) per student per session under it;
  * each voucher = one DEBIT and its challan; the sibling discount = a CONCESSION on that DEBIT; the items are kept in
    school_vouchers. Nothing is ever deleted: a mistake is put right the usual way (write-off, refund).
Brothers and sisters are matched by the guardian's CNIC; the eldest pays in full, the 2nd child gets Settings ->
school_sibling_discount_second % off the tuition and the 3rd and later school_sibling_discount_third %.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.terms import AcademicTerm
from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import (
    BusinessRuleViolationError,
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationAppError,
)
from app.core.logging import get_logger
from app.programs.models import Program
from app.school.models import (
    SchoolFeePlan,
    SchoolSection,
    SchoolVoucher,
    SchoolVoucherKind,
    StudentTransport,
    TransportRoute,
)
from app.school.service import SchoolService, school_mode
from app.students.models import Student, StudentGuardian, StudentStatus

logger = get_logger(__name__)
FEE_TYPE_CODE = "SCHOOL"


def first_of(d: date) -> date:
    return date(d.year, d.month, 1)


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_name(d: date) -> str:
    return d.strftime("%B %Y")


def _rs(v: float) -> str:
    return f"{v:,.0f}"


def due_day() -> int:
    return max(1, min(int(getattr(settings, "school_fee_due_day", 10) or 10), 28))


def sibling_percent(rank: int | None) -> float:
    if not rank or rank < 2:
        return 0.0
    key = "school_sibling_discount_second" if rank == 2 else "school_sibling_discount_third"
    return max(0.0, min(float(getattr(settings, key, 0) or 0), 100.0))


class SchoolFeeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ the fee account on the ledger
    async def _fee_type(self):
        from app.fees.models import FeeType

        ft = (await self.session.execute(select(FeeType).where(FeeType.code == FEE_TYPE_CODE))).scalars().first()
        if ft is None:
            ft = FeeType(code=FEE_TYPE_CODE, name="School fees", description="School mode: monthly vouchers and admission fees", is_active=True)
            self.session.add(ft)
            await self.session.flush()
        return ft

    async def structure(self, term: AcademicTerm, actor_id: uuid.UUID | None):
        """The session's 'School fees' structure (made the first time). Its late fine follows Settings."""
        from app.fees.models import FeeStructure, FeeStructureStatus

        ft = await self._fee_type()
        fine = round(float(getattr(settings, "school_late_fine", 0) or 0), 2)
        s = (await self.session.execute(select(FeeStructure).where(FeeStructure.fee_type_id == ft.id, FeeStructure.term_label == term.label))).scalars().first()
        if s is None:
            s = FeeStructure(fee_type_id=ft.id, name=f"School fees — {term.label}", term_label=term.label, total_amount=1, installment_count=1,
                             installment_interval_months=1, effective_from=term.start_date or institute_today(), late_fee_per_day=fine or None,
                             late_fee_once=True, applies_to_students=False, applies_to_employees=False, course_id=None,
                             status=FeeStructureStatus.ACTIVE, activated_at=datetime.now(timezone.utc), created_by=actor_id)
            self.session.add(s)
            await self.session.flush()
        elif round(float(s.late_fee_per_day or 0), 2) != fine or not s.late_fee_once:
            before = float(s.late_fee_per_day or 0)
            s.late_fee_per_day, s.late_fee_once = (fine or None), True
            await self.audit.log(actor_user_id=actor_id, action="school.late_fine_changed", entity_type="fee_structure", entity_id=s.id,
                                 before={"late_fine": before}, after={"late_fine": fine, "why": "Settings -> school late fine"})
        return s

    async def account(self, student_id: uuid.UUID, term: AcademicTerm, actor_id: uuid.UUID | None):
        from app.fees.models import AssignmentStatus, FeeAssignment, PayerType

        st = await self.structure(term, actor_id)
        a = (await self.session.execute(select(FeeAssignment).where(FeeAssignment.fee_structure_id == st.id, FeeAssignment.payer_type == PayerType.STUDENT,
                                                                    FeeAssignment.payer_id == student_id))).scalars().first()
        if a is None:
            a = FeeAssignment(fee_structure_id=st.id, payer_type=PayerType.STUDENT, payer_id=student_id, status=AssignmentStatus.ACTIVE)
            self.session.add(a)
            await self.session.flush()
        return a

    async def _charge(self, student: Student, term: AcademicTerm, *, amount: float, discount: float, discount_text: str | None, due: date,
                      text: str, actor_id: uuid.UUID | None):
        """One DEBIT (+ the sibling discount as a CONCESSION) and its challan for what is still to pay."""
        from app.fees.assignment_service import lock_assignment, sync_challans
        from app.fees.challan_factory import create_challan
        from app.fees.models import AssignmentStatus, FeeLedgerEntry, LedgerEntryType
        from app.fees.scholarship_service import ScholarshipService

        a = await self.account(student.id, term, actor_id)
        a = await lock_assignment(self.session, a.id)
        if a.status != AssignmentStatus.ACTIVE:
            raise BusinessRuleViolationError("This student's school fee account is not active.")
        n = (await self.session.execute(select(func.count()).select_from(FeeLedgerEntry).where(
            FeeLedgerEntry.fee_assignment_id == a.id, FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT))).scalar_one() + 1
        now = datetime.now(timezone.utc)
        debit = FeeLedgerEntry(fee_assignment_id=a.id, entry_type=LedgerEntryType.DEBIT, amount=round(amount, 2), installment_number=n, due_date=due,
                               description=text[:255], created_by=actor_id, created_at=now)
        self.session.add(debit)
        await self.session.flush()
        if discount > 0.005:
            self.session.add(FeeLedgerEntry(fee_assignment_id=a.id, entry_type=LedgerEntryType.CONCESSION, amount=round(discount, 2), installment_number=n,
                                            related_entry_id=debit.id, description=(discount_text or "Sibling discount")[:255], created_by=actor_id,
                                            created_at=now))
            await self.session.flush()
        await create_challan(self.session, assignment=a, entry=debit, amount=round(amount - discount, 2), due_date=due)
        await ScholarshipService(self.session).apply_active_to_assignment(a, actor_id=actor_id)   # a running scholarship counts as on any fee
        await sync_challans(self.session, a)
        return debit

    # ------------------------------------------------------------------ fee plans (office)
    async def plans(self, term_label: str | None) -> dict:
        term = await SchoolService(self.session).term(term_label)
        classes = (await self.session.execute(select(Program).where(Program.school_level.is_not(None), Program.is_active.is_(True))
                                              .order_by(Program.school_level, Program.name))).scalars().all()
        have = {p.program_id: p for p in (await self.session.execute(select(SchoolFeePlan).where(SchoolFeePlan.term_id == term.id))).scalars()}
        counts = dict((await self.session.execute(select(Student.program_id, func.count()).where(
            Student.status == StudentStatus.ACTIVE, Student.section_label.is_not(None), Student.program_id.in_([c.id for c in classes] or [uuid.uuid4()]))
            .group_by(Student.program_id))).all())
        return {"session": term.label, "due_day": due_day(), "late_fine": float(getattr(settings, "school_late_fine", 0) or 0),
                "sibling_second": sibling_percent(2), "sibling_third": sibling_percent(3),
                "classes": [{"program_id": c.id, "class": c.name, "students": counts.get(c.id, 0),
                             "monthly_tuition": float(have[c.id].monthly_tuition) if c.id in have else 0.0,
                             "admission_fee": float(have[c.id].admission_fee) if c.id in have else 0.0,
                             "annual_charges": float(have[c.id].annual_charges) if c.id in have else 0.0,
                             "set": c.id in have} for c in classes]}

    async def save_plans(self, term_label: str | None, items: list[dict], actor_id: uuid.UUID) -> dict:
        if not school_mode():
            raise BusinessRuleViolationError("School mode is off.")
        term = await SchoolService(self.session).term(term_label)
        changed = []
        for it in items:
            p = await self.session.get(Program, it["program_id"])
            if p is None or p.school_level is None:
                raise ValidationAppError("Choose school classes only.")
            vals = {k: round(float(it[k]), 2) for k in ("monthly_tuition", "admission_fee", "annual_charges")}
            if any(v < 0 or v > 10_000_000 for v in vals.values()):
                raise ValidationAppError(f"Check the amounts of {p.name}.")
            plan = await self.session.get(SchoolFeePlan, (term.id, p.id))
            before = None if plan is None else {k: float(getattr(plan, k)) for k in vals}
            if before == vals:
                continue
            if plan is None:
                plan = SchoolFeePlan(term_id=term.id, program_id=p.id)
                self.session.add(plan)
            for k, v in vals.items():
                setattr(plan, k, v)
            plan.updated_by = actor_id
            changed.append({"class": p.name, "before": before, "after": vals})
        await self.session.flush()
        if changed:
            await self.audit.log(actor_user_id=actor_id, action="school.fee_plans_changed", entity_type="academic_term", entity_id=term.id,
                                 after={"session": term.label, "classes": changed[:60]})
        return await self.plans(term.label)

    # ------------------------------------------------------------------ transport
    async def routes(self, *, all_: bool = True) -> list[dict]:
        rows = (await self.session.execute(select(TransportRoute).order_by(TransportRoute.active.desc(), TransportRoute.name))).scalars().all()
        today = first_of(institute_today())
        riders = dict((await self.session.execute(select(StudentTransport.route_id, func.count()).where(     # on it now or from a later month
            (StudentTransport.until_month.is_(None)) | (StudentTransport.until_month >= today))
            .group_by(StudentTransport.route_id))).all())
        return [{"id": r.id, "name": r.name, "monthly_fee": float(r.monthly_fee), "active": r.active, "students": riders.get(r.id, 0)}
                for r in rows if all_ or r.active]

    async def save_route(self, route_id: uuid.UUID | None, *, name: str, monthly_fee: float, active: bool, actor_id: uuid.UUID) -> dict:
        name = " ".join((name or "").split())
        if len(name) < 2:
            raise ValidationAppError("Give the route a name, e.g. Route 1 - Gulberg.")
        if not 0 <= monthly_fee <= 1_000_000:
            raise ValidationAppError("Check the monthly fee.")
        clash = (await self.session.execute(select(TransportRoute).where(func.lower(TransportRoute.name) == name.lower()))).scalars().first()
        if clash is not None and clash.id != route_id:
            raise DuplicateResourceError("A route with this name already exists.")
        if route_id is None:
            r = TransportRoute(name=name, monthly_fee=round(monthly_fee, 2), active=active)
            self.session.add(r)
            before = None
        else:
            r = await self.session.get(TransportRoute, route_id)
            if r is None:
                raise ResourceNotFoundError("Route not found.")
            before = {"name": r.name, "monthly_fee": float(r.monthly_fee), "active": r.active}
            r.name, r.monthly_fee, r.active = name, round(monthly_fee, 2), active
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.transport_route_saved", entity_type="school_transport_route", entity_id=r.id,
                             before=before, after={"name": r.name, "monthly_fee": float(r.monthly_fee), "active": r.active})
        return next(x for x in await self.routes() if x["id"] == r.id)

    async def riders(self, route_id: uuid.UUID) -> list[dict]:
        today = first_of(institute_today())
        rows = (await self.session.execute(select(StudentTransport, Student, Program.name).join(Student, Student.id == StudentTransport.student_id)
                                           .outerjoin(Program, Program.id == Student.program_id)
                                           .where(StudentTransport.route_id == route_id,
                                                  (StudentTransport.until_month.is_(None)) | (StudentTransport.until_month >= today))
                                           .order_by(Program.school_level, Student.first_name))).all()
        return [{"id": t.id, "student_id": s.id, "name": f"{s.first_name} {s.last_name}".strip(), "roll_number": s.roll_number,
                 "class": cls, "section": s.section_label, "from_month": t.from_month, "until_month": t.until_month} for t, s, cls in rows]

    async def _route_on(self, student_id: uuid.UUID, month: date) -> TransportRoute | None:
        t = (await self.session.execute(select(StudentTransport).where(
            StudentTransport.student_id == student_id, StudentTransport.from_month <= month,
            (StudentTransport.until_month.is_(None)) | (StudentTransport.until_month >= month)).order_by(StudentTransport.from_month.desc()))).scalars().first()
        return await self.session.get(TransportRoute, t.route_id) if t else None

    async def add_riders(self, route_id: uuid.UUID, student_ids: list[uuid.UUID], from_month: date | None, actor_id: uuid.UUID) -> dict:
        r = await self.session.get(TransportRoute, route_id)
        if r is None or not r.active:
            raise ResourceNotFoundError("Route not found (or switched off).")
        start = first_of(from_month or institute_today())
        added, failed = [], []
        for sid in dict.fromkeys(student_ids):
            s = await self.session.get(Student, sid)
            if s is None or s.status != StudentStatus.ACTIVE:
                failed.append({"student_id": sid, "reason": "Not an active student."})
                continue
            billed = (await self.session.execute(select(SchoolVoucher.id).where(SchoolVoucher.student_id == sid, SchoolVoucher.kind == SchoolVoucherKind.MONTHLY,
                                                                                 SchoolVoucher.month >= start))).first()
            if billed:
                failed.append({"student_id": sid, "name": s.first_name, "reason": f"the voucher for {month_name(start)} is already made; start from next month"})
                continue
            already = False
            for old in (await self.session.execute(select(StudentTransport).where(
                    StudentTransport.student_id == sid, (StudentTransport.until_month.is_(None)) | (StudentTransport.until_month >= start)))).scalars().all():
                if old.route_id == route_id and old.from_month <= start:
                    already = True                                   # already on this route
                elif old.from_month >= start:
                    await self.session.delete(old)                   # starts later and was never on a voucher (checked above)
                else:
                    old.until_month = date(start.year - (start.month == 1), (start.month - 2) % 12 + 1, 1)   # its last month: the one before
            if already:
                continue
            self.session.add(StudentTransport(student_id=sid, route_id=route_id, from_month=start, added_by=actor_id))
            added.append(sid)
        await self.session.flush()
        if added:
            await self.audit.log(actor_user_id=actor_id, action="school.transport_students_added", entity_type="school_transport_route", entity_id=r.id,
                                 after={"route": r.name, "from_month": start.isoformat(), "students": len(added)})
        return {"added": len(added), "failed": failed, "students": await self.riders(route_id)}

    async def end_ride(self, ride_id: uuid.UUID, last_month: date | None, actor_id: uuid.UUID) -> dict:
        t = await self.session.get(StudentTransport, ride_id)
        if t is None:
            raise ResourceNotFoundError("Not found.")
        last = first_of(last_month or institute_today())
        billed_after = (await self.session.execute(select(SchoolVoucher.month).where(
            SchoolVoucher.student_id == t.student_id, SchoolVoucher.kind == SchoolVoucherKind.MONTHLY, SchoolVoucher.month > last,
            SchoolVoucher.transport > 0))).scalars().first()
        if billed_after:
            raise BusinessRuleViolationError(f"Transport is already on the {month_name(billed_after)} voucher; the last month can be {month_name(billed_after)} or later.")
        if last < t.from_month:
            raise ValidationAppError("The last month cannot be before the first month on the route.")
        t.until_month = last
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.transport_student_ended", entity_type="school_transport_route", entity_id=t.route_id,
                             after={"student_id": str(t.student_id), "last_month": last.isoformat()})
        return {"ok": True, "students": await self.riders(t.route_id)}

    # ------------------------------------------------------------------ brothers and sisters
    async def sibling_rank(self, student: Student) -> int | None:
        """1 = the eldest (pays in full), 2 = second child ... among active students in school classes with the same
        guardian CNIC. None when the student has no brother or sister in the school."""
        cnics = [g.cnic for g in (await self.session.execute(select(StudentGuardian).where(StudentGuardian.student_id == student.id)
                                                             .order_by(StudentGuardian.is_primary.desc()))).scalars()]
        if not cnics:
            return None
        cnic = cnics[0]
        kids = (await self.session.execute(select(Student).join(StudentGuardian, StudentGuardian.student_id == Student.id)
                                           .join(Program, Program.id == Student.program_id)
                                           .where(StudentGuardian.cnic == cnic, Student.status == StudentStatus.ACTIVE, Program.school_level.is_not(None),
                                                  Student.section_label.is_not(None))
                                           .order_by(Student.date_of_birth, Student.roll_number, Student.id))).scalars().unique().all()
        ids = list(dict.fromkeys(k.id for k in kids))
        if len(ids) < 2 or student.id not in ids:
            return None
        return ids.index(student.id) + 1

    # ------------------------------------------------------------------ monthly vouchers
    async def _term_for(self, month: date) -> AcademicTerm | None:
        end = next_month(month) - timedelta(days=1)
        with_sections = select(SchoolSection.term_id)
        return (await self.session.execute(select(AcademicTerm).where(
            AcademicTerm.id.in_(with_sections), (AcademicTerm.start_date.is_(None)) | (AcademicTerm.start_date <= end),
            (AcademicTerm.end_date.is_(None)) | (AcademicTerm.end_date >= month)).order_by(AcademicTerm.start_date.desc().nulls_last()))).scalars().first()

    async def make_month(self, month: date | None = None, *, actor_id: uuid.UUID | None, student_ids: set | None = None) -> dict:
        """Make the month's voucher for every student in a school class who has none yet. Safe to run again."""
        month = first_of(month or institute_today())
        term = await self._term_for(month)
        if term is None:
            return {"month": month, "made": 0, "skipped": [], "total": 0.0, "session": None}
        plans = {p.program_id: p for p in (await self.session.execute(select(SchoolFeePlan).where(SchoolFeePlan.term_id == term.id))).scalars()}
        secs = {(s.program_id, s.label.upper()) for s in (await self.session.execute(select(SchoolSection).where(SchoolSection.term_id == term.id))).scalars()}
        q = select(Student).join(Program, Program.id == Student.program_id).where(
            Student.status == StudentStatus.ACTIVE, Program.school_level.is_not(None), Student.section_label.is_not(None))
        if student_ids is not None:
            q = q.where(Student.id.in_(student_ids or {uuid.uuid4()}))
        pupils = (await self.session.execute(q.order_by(Student.roll_number))).scalars().all()
        done = set((await self.session.execute(select(SchoolVoucher.student_id).where(SchoolVoucher.kind == SchoolVoucherKind.MONTHLY,
                                                                                       SchoolVoucher.month == month))).scalars())
        made, total, skipped = 0, 0.0, []
        today = institute_today()
        for s in pupils:
            if s.id in done or (s.program_id, (s.section_label or "").upper()) not in secs:
                continue
            plan = plans.get(s.program_id)
            if plan is None:
                skipped.append({"student_id": s.id, "reason": "no fee plan for the class"})
                continue
            try:
                async with self.session.begin_nested():
                    v = await self._monthly(s, term, plan, month, today, actor_id)
                if v is not None:
                    made += 1
                    total += float(v.tuition) + float(v.transport) + float(v.annual) - float(v.sibling_discount)
            except Exception as exc:  # noqa: BLE001 - one student's problem must not stop the others
                logger.warning("school_voucher_failed", student_id=str(s.id), error=str(exc)[:200])
                skipped.append({"student_id": s.id, "reason": str(getattr(exc, "message", exc))[:200]})
        if made:
            await self.audit.log(actor_user_id=actor_id, action="school.monthly_vouchers", entity_type="academic_term", entity_id=term.id,
                                 after={"month": month.isoformat(), "vouchers": made, "total": round(total, 2)})
        return {"month": month, "made": made, "skipped": skipped, "total": round(total, 2), "session": term.label}

    async def _monthly(self, s: Student, term: AcademicTerm, plan: SchoolFeePlan, month: date, today: date, actor_id) -> SchoolVoucher | None:
        tuition = float(plan.monthly_tuition)
        route = await self._route_on(s.id, month)
        transport = float(route.monthly_fee) if route else 0.0
        first_in_session = (await self.session.execute(select(SchoolVoucher.id).where(
            SchoolVoucher.student_id == s.id, SchoolVoucher.kind == SchoolVoucherKind.MONTHLY, SchoolVoucher.term_id == term.id))).first() is None
        annual = float(plan.annual_charges) if first_in_session else 0.0
        amount = tuition + transport + annual
        if amount <= 0.005:
            return None
        rank = await self.sibling_rank(s)
        pct = sibling_percent(rank)
        discount = round(tuition * pct / 100.0) if pct else 0.0
        due = date(month.year, month.month, due_day())
        if due < today + timedelta(days=3):                       # made late (joined after the due date): a few days to pay
            due = today + timedelta(days=max(int(getattr(settings, "school_admission_due_days", 7) or 7), 3))
        parts = [f"Tuition {_rs(tuition)}"] + ([f"Transport ({route.name}) {_rs(transport)}"] if transport else []) + \
            ([f"Annual charges {_rs(annual)}"] if annual else [])
        text = f"School fee, {month_name(month)}: " + " + ".join(parts)
        why = f"Sibling discount {pct:g}% on tuition ({_ordinal(rank)} child)" if discount else None
        debit = await self._charge(s, term, amount=amount, discount=discount, discount_text=why, due=due, text=text, actor_id=actor_id)
        v = SchoolVoucher(student_id=s.id, kind=SchoolVoucherKind.MONTHLY, month=month, term_id=term.id, program_id=s.program_id, tuition=tuition,
                          transport=transport, route_name=route.name if route else None, annual=annual, sibling_discount=discount,
                          sibling_rank=rank if discount else None, ledger_entry_id=debit.id, created_by=actor_id)
        self.session.add(v)
        await self.session.flush()
        await self._tell(s, f"{month_name(month)} fee voucher", amount - discount, due)
        return v

    async def charge_admission(self, student_id: uuid.UUID, actor_id: uuid.UUID) -> SchoolVoucher | None:
        """The admission fee of the student's class, once ever. None when the class has none (or it was charged before)."""
        s = await self.session.get(Student, student_id)
        if s is None or s.program_id is None:
            return None
        if (await self.session.execute(select(SchoolVoucher.id).where(SchoolVoucher.student_id == s.id, SchoolVoucher.kind == SchoolVoucherKind.ADMISSION))).first():
            return None
        term = await SchoolService(self.session).term(None)
        plan = await self.session.get(SchoolFeePlan, (term.id, s.program_id))
        fee = float(plan.admission_fee) if plan else 0.0
        if fee <= 0.005:
            return None
        today = institute_today()
        due = today + timedelta(days=max(int(getattr(settings, "school_admission_due_days", 7) or 7), 1))
        debit = await self._charge(s, term, amount=fee, discount=0.0, discount_text=None, due=due, text=f"Admission fee: {_rs(fee)}", actor_id=actor_id)
        v = SchoolVoucher(student_id=s.id, kind=SchoolVoucherKind.ADMISSION, month=first_of(today), term_id=term.id, program_id=s.program_id,
                          admission=fee, ledger_entry_id=debit.id, created_by=actor_id)
        self.session.add(v)
        await self.session.flush()
        await self._tell(s, "Admission fee voucher", fee, due)
        return v

    async def _tell(self, s: Student, title: str, amount: float, due: date) -> None:
        from app.notifications.family import _notify, family

        try:
            async with self.session.begin_nested():
                f = (await family(self.session, {s.id})).get(s.id)
                text = f"{title} for {s.first_name}: Rs. {amount:,.0f}, due {due:%d %b %Y}. Pay at the bank, by 1Bill or at the school."
                if f:
                    for uid, _l in f["parents"]:
                        await _notify(self.session, uid, title + " for " + s.first_name, text, "/dashboard/parent/fees")
                    await _notify(self.session, f["user_id"], title, text, "/dashboard/student/fees")
                from app.sms.service import SmsService

                await SmsService(self.session).queue_to_guardian(s.id, event="SCHOOL_FEE", body=f"{settings.app_name}: {text}")
        except Exception as exc:  # noqa: BLE001 - the voucher is made; a message problem must not undo it
            logger.warning("school_fee_message_failed", error=str(exc)[:200])

    # ------------------------------------------------------------------ the month at a glance (office)
    async def month_view(self, month: date | None = None) -> dict:
        from app.fees.models import FeeLedgerEntry
        from app.fees.payment_models import ChallanStatus, PaymentChallan

        month = first_of(month or institute_today())
        term = await self._term_for(month)
        rows = (await self.session.execute(select(SchoolVoucher, PaymentChallan.status, PaymentChallan.amount)
                                           .outerjoin(FeeLedgerEntry, FeeLedgerEntry.id == SchoolVoucher.ledger_entry_id)
                                           .outerjoin(PaymentChallan, PaymentChallan.fee_ledger_entry_id == FeeLedgerEntry.id)
                                           .where(SchoolVoucher.kind == SchoolVoucherKind.MONTHLY, SchoolVoucher.month == month))).all()
        billed = sum(float(v.tuition) + float(v.transport) + float(v.annual) - float(v.sibling_discount) for v, _s, _a in rows)
        paid = sum(1 for _v, st, _a in rows if st == ChallanStatus.PAID)
        expected = 0
        if term is not None:
            secs = {(s.program_id, s.label.upper()) for s in (await self.session.execute(select(SchoolSection).where(SchoolSection.term_id == term.id))).scalars()}
            for pid, lab in (await self.session.execute(select(Student.program_id, Student.section_label).join(Program, Program.id == Student.program_id).where(
                    Student.status == StudentStatus.ACTIVE, Program.school_level.is_not(None), Student.section_label.is_not(None)))).all():
                expected += (pid, (lab or "").upper()) in secs
        return {"month": month, "month_name": month_name(month), "session": term.label if term else None, "vouchers": len(rows), "students": expected,
                "paid": paid, "billed": round(billed, 2), "sibling_discounts": round(sum(float(v.sibling_discount) for v, _s, _a in rows), 2),
                "due_day": due_day()}


async def voucher_lines(session: AsyncSession, ledger_entry_id) -> dict | None:
    """The items of a school voucher for its challan: {"title", "items": [(label, amount)]}; None for any other fee."""
    v = (await session.execute(select(SchoolVoucher).where(SchoolVoucher.ledger_entry_id == ledger_entry_id))).scalars().first()
    if v is None:
        return None
    if v.kind == SchoolVoucherKind.ADMISSION:
        return {"title": "Admission fee", "items": [("Admission fee", float(v.admission))]}
    items = [("Tuition", float(v.tuition))]
    if float(v.transport):
        items.append((f"Transport ({v.route_name})" if v.route_name else "Transport", float(v.transport)))
    if float(v.annual):
        items.append(("Annual charges", float(v.annual)))
    return {"title": month_name(v.month), "items": items}


def _ordinal(n: int | None) -> str:
    if not n:
        return ""
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


async def monthly_round(session: AsyncSession) -> int:
    """Once a day from 6 AM (School mode): this month's vouchers for everyone who has none yet (on the 1st that is
    everyone; later in the month only students who joined since). Never raises. Returns how many were made."""
    from app.notifications.family import _once_a_day

    try:
        if not school_mode():
            return 0
        day = await _once_a_day(session, "school-fees", hour=6)
        if day is None:
            return 0
        from app.core.background import beat

        res = await SchoolFeeService(session).make_month(date.fromisoformat(day), actor_id=None)
        await session.flush()
        await beat(session, "school-fees", f"{day} made {res['made']}")
        return res["made"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("school_fees_round_failed", error=str(exc)[:300])
        return 0
