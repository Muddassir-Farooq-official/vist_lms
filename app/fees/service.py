from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError
from app.employees.models import Employee, EmployeeStatus
from app.fees.models import (
    FeeAssignment,
    FeeLedgerEntry,
    FeeStructure,
    FeeStructureProgram,
    FeeStructureSemester,
    FeeStructureStatus,
    FeeType,
    LedgerEntryType,
    PayerType,
)
from app.fees.payment_gateway import get_payment_gateway
from app.fees.payment_models import ChallanStatus, PaymentChallan
from app.fees.repository import FeeAssignmentRepository, FeeStructureRepository, FeeTypeRepository
from app.fees.schemas import CloneStructureRequest, FeeStructureCreateRequest, FeeTypeCreateRequest, FeeTypeUpdateRequest
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.programs.models import Program
from app.students.models import Student, StudentStatus


async def _resolve_payer_user_id(session: AsyncSession, payer_type: PayerType, payer_id: uuid.UUID) -> uuid.UUID | None:
    """Same one-line lookup as app/fines/service.py's _resolve_user_id —
    notifications are keyed on users.id, not the student/employee
    record. Kept local rather than imported: fees shouldn't depend on
    fines just because they share this shape."""
    if payer_type == PayerType.STUDENT:
        student = await session.get(Student, payer_id)
        return student.user_id if student else None
    employee = await session.get(Employee, payer_id)
    return employee.user_id if employee else None


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class FeeTypeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FeeTypeRepository(session)
        self.audit = AuditService(session)

    async def create(self, data: FeeTypeCreateRequest, *, actor_id: uuid.UUID) -> FeeType:
        if await self.repo.get_by_code(data.code):
            raise DuplicateResourceError(f"Fee type code '{data.code}' already exists.")
        fee_type = FeeType(code=data.code.strip().upper(), name=data.name.strip(), description=data.description)
        await self.repo.create(fee_type)
        await self.audit.log(
            actor_user_id=actor_id,
            action="fee_type.created",
            entity_type="fee_type",
            entity_id=fee_type.id,
            after={"code": fee_type.code, "name": fee_type.name},
        )
        return fee_type

    async def list_all(self, *, active_only: bool = False) -> list[FeeType]:
        return await self.repo.list_all(active_only=active_only)

    async def update(self, fee_type_id: uuid.UUID, data: FeeTypeUpdateRequest, *, actor_id: uuid.UUID) -> FeeType:
        fee_type = await self.repo.get_by_id(fee_type_id)
        if fee_type is None:
            raise ResourceNotFoundError("Fee type not found.")
        before = {"name": fee_type.name, "is_active": fee_type.is_active}
        if data.name is not None:
            fee_type.name = data.name.strip()
        if data.description is not None:
            fee_type.description = data.description or None
        if data.is_active is not None:
            fee_type.is_active = data.is_active
        await self.audit.log(
            actor_user_id=actor_id,
            action="fee_type.updated",
            entity_type="fee_type",
            entity_id=fee_type.id,
            before=before,
            after={"name": fee_type.name, "is_active": fee_type.is_active},
        )
        return fee_type


class FeeStructureService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FeeStructureRepository(session)
        self.fee_type_repo = FeeTypeRepository(session)
        self.assignment_repo = FeeAssignmentRepository(session)
        self.audit = AuditService(session)

    async def create(self, data: FeeStructureCreateRequest, *, actor_id: uuid.UUID) -> FeeStructure:
        from app.academics.terms import AcademicTermService

        await AcademicTermService(self.session).require_usable(data.term_label)
        fee_type = await self.fee_type_repo.get_by_id(data.fee_type_id)
        if fee_type is None:
            raise ResourceNotFoundError("Fee type not found.")
        if not data.applies_to_students and not data.applies_to_employees:
            raise BusinessRuleViolationError("A fee structure must apply to students, employees, or both.")

        for program_id in data.program_ids:
            program = await self.session.get(Program, program_id)
            if program is None:
                raise ResourceNotFoundError(f"Program {program_id} does not exist.")

        if data.course_id is not None:
            from app.academics.models import Course

            course = await self.session.get(Course, data.course_id)
            if course is None:
                raise ResourceNotFoundError("Course not found.")
            if not data.applies_to_students:
                raise BusinessRuleViolationError("A course-scoped fee must apply to students — employees don't enroll in courses.")
            # Fixes a gap: FeeStructure.course_id's own docstring in
            # app/fees/models.py documents course-scoping and
            # program/semester-scoping as mutually exclusive ("see
            # FeeStructureService validation" — that validation never
            # actually existed). Without this check, a course-scoped fee
            # with program_ids/semester_numbers also set would silently
            # store both, leaving it ambiguous which matching semantics
            # actually apply (assigned once at activation, per the
            # program/semester path, or fresh per enrollment including
            # retakes, per the course path) — see the two code paths in
            # FeeStructureService.activate().
            if data.program_ids or data.semester_numbers:
                raise BusinessRuleViolationError(
                    "A fee structure cannot be both course-scoped and program/semester-scoped. "
                    "Choose one: set course_id, or set program_ids/semester_numbers, not both."
                )

        structure = FeeStructure(
            fee_type_id=fee_type.id,
            name=data.name.strip(),
            term_label=data.term_label.strip(),
            total_amount=data.total_amount,
            installment_count=data.installment_count,
            installment_interval_months=data.installment_interval_months,
            effective_from=data.effective_from,
            late_fee_per_day=data.late_fee_per_day,
            applies_to_students=data.applies_to_students,
            applies_to_employees=data.applies_to_employees,
            course_id=data.course_id,
            status=FeeStructureStatus.DRAFT,
            created_by=actor_id,
        )
        await self.repo.create(structure)

        for program_id in data.program_ids:
            await self.repo.add_program_link(
                FeeStructureProgram(fee_structure_id=structure.id, program_id=program_id)
            )
        for semester_number in data.semester_numbers:
            await self.repo.add_semester_link(
                FeeStructureSemester(fee_structure_id=structure.id, semester_number=semester_number)
            )
        await self.session.flush()

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee_structure.created",
            entity_type="fee_structure",
            entity_id=structure.id,
            after={
                "name": structure.name,
                "total_amount": data.total_amount,
                "program_count": len(data.program_ids),
                "semester_numbers": data.semester_numbers,
            },
        )
        return structure

    async def get(self, structure_id: uuid.UUID) -> FeeStructure:
        structure = await self.repo.get_by_id(structure_id)
        if structure is None:
            raise ResourceNotFoundError("Fee structure not found.")
        return structure

    async def list_all(self) -> list[FeeStructure]:
        return await self.repo.list_all()

    async def clone(self, structure_id: uuid.UUID, data: CloneStructureRequest, *, actor_id: uuid.UUID) -> FeeStructure:
        """Creates a new DRAFT structure for a new term, with the amount
        scaled by percentage_change — the answer to 'how do I raise this
        fee 10% next term': clone each fee type's current structure
        forward rather than editing history. The source structure and
        every existing assignment/ledger entry under it are completely
        untouched."""
        from app.academics.terms import AcademicTermService

        await AcademicTermService(self.session).require_usable(data.new_term_label)
        source = await self.get(structure_id)

        new_amount = round(float(source.total_amount) * (1 + data.percentage_change / 100), 2)
        if new_amount <= 0:
            raise BusinessRuleViolationError("The adjusted amount must be greater than zero.")

        # NOTE: fetched explicitly rather than via source.fee_type — that
        # relationship attribute isn't eagerly loaded, and touching it here
        # would trigger an async lazy-load error (MissingGreenlet).
        fee_type = await self.fee_type_repo.get_by_id(source.fee_type_id)
        new_name = data.new_name or f"{fee_type.name if fee_type else 'Fee'} \u2014 {data.new_term_label}"

        clone = FeeStructure(
            fee_type_id=source.fee_type_id,
            name=new_name,
            term_label=data.new_term_label,
            total_amount=new_amount,
            installment_count=source.installment_count,
            installment_interval_months=source.installment_interval_months,
            effective_from=data.effective_from,
            late_fee_per_day=source.late_fee_per_day,
            applies_to_students=source.applies_to_students,
            applies_to_employees=source.applies_to_employees,
            course_id=source.course_id,  # was previously dropped — cloning a
            # course-scoped fee silently produced a non-course-scoped clone
            status=FeeStructureStatus.DRAFT,
            created_by=actor_id,
        )
        await self.repo.create(clone)

        # Only copy program/semester links for a non-course-scoped source —
        # a course-scoped structure has neither (see the mutual-exclusivity
        # check in create() above), so this is a no-op for it anyway, but
        # being explicit avoids relying on that invariant silently holding.
        source_links = await self.repo.get_program_links(source.id)
        for link in source_links:
            await self.repo.add_program_link(FeeStructureProgram(fee_structure_id=clone.id, program_id=link.program_id))
        source_semester_links = await self.repo.get_semester_links(source.id)
        for link in source_semester_links:
            await self.repo.add_semester_link(
                FeeStructureSemester(fee_structure_id=clone.id, semester_number=link.semester_number)
            )
        await self.session.flush()

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee_structure.cloned",
            entity_type="fee_structure",
            entity_id=clone.id,
            after={
                "source_structure_id": str(source.id),
                "percentage_change": data.percentage_change,
                "old_amount": float(source.total_amount),
                "new_amount": new_amount,
            },
        )
        return clone

    async def _generate_installments(
        self, assignment: FeeAssignment, structure: FeeStructure, *, actor_id: uuid.UUID
    ) -> None:
        total = Decimal(str(structure.total_amount))
        count = structure.installment_count
        base = (total / count).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        allocated = Decimal("0.00")
        now = datetime.now(timezone.utc)
        gateway = get_payment_gateway()

        for i in range(1, count + 1):
            amount = total - allocated if i == count else base
            allocated += amount
            due_date = _add_months(structure.effective_from, (i - 1) * structure.installment_interval_months)

            ledger_entry = FeeLedgerEntry(
                fee_assignment_id=assignment.id,
                entry_type=LedgerEntryType.DEBIT,
                amount=float(amount),
                installment_number=i,
                due_date=due_date,
                description=f"Installment {i} of {count} \u2014 {structure.name}",
                created_by=actor_id,
                created_at=now,
            )
            self.session.add(ledger_entry)
            await self.session.flush()  # need ledger_entry.id before creating the linked challan

            # Auto-generate the payable challan the instant the charge
            # exists — this is what replaces the accountant manually
            # entering payments: the student can pay this consumer_number
            # at any bank/ATM/mobile app the moment the fee is set.
            # Collision probability is astronomically low (see
            # SimulatedOneLinkProvider's docstring) but there IS a real
            # DB unique constraint on consumer_number, and nothing was
            # checking for one before generating — an unhandled
            # IntegrityError would have failed the whole activation.
            # Belt-and-suspenders: check before inserting, retry a
            # handful of times, then fail with a clear message rather
            # than a raw DB error if something is very wrong.
            consumer_number = None
            for _attempt in range(5):
                candidate = await gateway.generate_consumer_number(
                    reference=f"{assignment.payer_type.value}-{assignment.payer_id.hex[:8]}-{i}",
                    amount=float(amount),
                    due_date=due_date,
                )
                exists = (
                    await self.session.execute(select(PaymentChallan).where(PaymentChallan.consumer_number == candidate))
                ).scalar_one_or_none()
                if exists is None:
                    consumer_number = candidate
                    break
            if consumer_number is None:
                raise BusinessRuleViolationError(
                    "Could not generate a unique consumer number after several attempts — try activating again."
                )
            self.session.add(
                PaymentChallan(
                    fee_ledger_entry_id=ledger_entry.id,
                    fee_assignment_id=assignment.id,
                    consumer_number=consumer_number,
                    amount=float(amount),
                    due_date=due_date,
                    status=ChallanStatus.PENDING,
                    provider="1LINK_SIMULATED",
                    generated_at=now,
                )
            )

        # Real gap fix: NotificationCategory.FEE has existed since the
        # notifications module was built — nothing in this module ever
        # actually used it. A new charge landing on someone's account
        # was completely silent, in every one of this method's 5 call
        # sites (structure activation for students/employees,
        # course-enrollment auto-charge, semester-advance auto-charge).
        # One notification per assignment here, not per installment —
        # a 3-installment fee shouldn't fire 3 messages.
        payer_user_id = await _resolve_payer_user_id(self.session, assignment.payer_type, assignment.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FEE,
                title="A new fee has been charged to your account",
                message=f"Rs. {float(total):.2f} — {structure.name}",
                link="/dashboard/student/fees" if assignment.payer_type == PayerType.STUDENT else "/dashboard/faculty",
            )

        # A scholarship that is still running takes its share off this new fee straight away (and off the challans just created).
        from app.fees.scholarship_service import ScholarshipService

        await ScholarshipService(self.session).apply_active_to_assignment(assignment, actor_id=actor_id)

    async def undo_activation(self, structure_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID) -> dict:
        """Take back a mistaken activation (wrong amount, wrong programs): the generated charges are removed and the fee is a draft
        again. Only possible while nobody has paid, been given a discount or been refunded on it; after that, correct it with
        a reversal instead."""
        from sqlalchemy import delete, func

        from app.fees.models import FeeAssignment, FeeLedgerEntry
        from app.fees.refund_models import FeeRefund

        structure = await self.get(structure_id)
        if structure.status != FeeStructureStatus.ACTIVE:
            raise BusinessRuleViolationError("Only an active fee structure has an activation to undo.")
        if len((reason or "").strip()) < 5:
            raise BusinessRuleViolationError("Say why the activation is being undone.")
        ids = list((await self.session.execute(select(FeeAssignment.id).where(FeeAssignment.fee_structure_id == structure_id))).scalars())
        if ids:
            touched = (await self.session.execute(select(func.count(func.distinct(FeeLedgerEntry.fee_assignment_id))).where(
                FeeLedgerEntry.fee_assignment_id.in_(ids), FeeLedgerEntry.entry_type != LedgerEntryType.DEBIT))).scalar_one()
            refunds = (await self.session.execute(select(func.count()).select_from(FeeRefund).where(FeeRefund.fee_assignment_id.in_(ids)))).scalar_one()
            if touched or refunds:
                raise BusinessRuleViolationError(
                    f"This activation cannot be undone: payments, discounts or refunds already exist on {max(touched, refunds)} fee(s). Reverse those first, or close this fee instead.")
            await self.session.execute(delete(FeeAssignment).where(FeeAssignment.id.in_(ids)))
        structure.status = FeeStructureStatus.DRAFT
        structure.activated_at = None
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="fee_structure.activation_undone", entity_type="fee_structure", entity_id=structure.id,
                                             metadata={"reason": reason.strip(), "removed_charges": len(ids)})
        return {"removed_charges": len(ids), "status": "DRAFT"}

    async def activate(self, structure_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        structure = await self.get(structure_id)
        if structure.status == FeeStructureStatus.CLOSED:
            raise BusinessRuleViolationError("This fee structure is closed and cannot accept new assignments.")

        newly_assigned = 0

        if structure.applies_to_students and structure.course_id is not None:
            # Course-scoped: backfill everyone CURRENTLY enrolled in this
            # specific course (any offering, any term) who hasn't already
            # been charged for their specific enrollment — this is the
            # "already enrolled when the fee was created" case. Every
            # enrollment AFTER this point is handled by
            # assign_course_fee_for_enrollment, called directly from
            # EnrollmentService.enroll() at enrollment time instead of
            # waiting for a bulk pass like this one.
            from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus

            stmt = (
                select(Enrollment)
                .join(CourseOffering, Enrollment.course_offering_id == CourseOffering.id)
                .where(CourseOffering.course_id == structure.course_id, Enrollment.status == EnrollmentStatus.ENROLLED)
            )
            enrollments = (await self.session.execute(stmt)).scalars().all()

            for enrollment in enrollments:
                existing = await self.assignment_repo.get_existing_for_enrollment(structure.id, enrollment.id)
                if existing is not None:
                    continue
                assignment = await self.assignment_repo.create(
                    FeeAssignment(
                        fee_structure_id=structure.id, payer_type=PayerType.STUDENT, payer_id=enrollment.student_id,
                        enrollment_id=enrollment.id,
                    )
                )
                await self._generate_installments(assignment, structure, actor_id=actor_id)
                newly_assigned += 1

        elif structure.applies_to_students:
            program_links = await self.repo.get_program_links(structure.id)
            program_ids = [link.program_id for link in program_links]
            semester_links = await self.repo.get_semester_links(structure.id)
            semester_numbers = [link.semester_number for link in semester_links]

            stmt = select(Student).where(Student.status == StudentStatus.ACTIVE)
            if program_ids:
                stmt = stmt.where(Student.program_id.in_(program_ids))
            if semester_numbers:
                stmt = stmt.where(Student.current_semester.in_(semester_numbers))
            students = (await self.session.execute(stmt)).scalars().all()

            for student in students:
                existing = await self.assignment_repo.get_existing(structure.id, PayerType.STUDENT, student.id)
                if existing is not None:
                    continue
                assignment = await self.assignment_repo.create(
                    FeeAssignment(fee_structure_id=structure.id, payer_type=PayerType.STUDENT, payer_id=student.id)
                )
                await self._generate_installments(assignment, structure, actor_id=actor_id)
                newly_assigned += 1

        if structure.applies_to_employees:
            stmt = select(Employee).where(Employee.status == EmployeeStatus.ACTIVE)
            employees = (await self.session.execute(stmt)).scalars().all()

            for employee in employees:
                existing = await self.assignment_repo.get_existing(structure.id, PayerType.EMPLOYEE, employee.id)
                if existing is not None:
                    continue
                assignment = await self.assignment_repo.create(
                    FeeAssignment(fee_structure_id=structure.id, payer_type=PayerType.EMPLOYEE, payer_id=employee.id)
                )
                await self._generate_installments(assignment, structure, actor_id=actor_id)
                newly_assigned += 1

        if structure.status == FeeStructureStatus.DRAFT:
            structure.status = FeeStructureStatus.ACTIVE
            structure.activated_at = datetime.now(timezone.utc)

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee_structure.activated",
            entity_type="fee_structure",
            entity_id=structure.id,
            metadata={"newly_assigned": newly_assigned},
        )
        return {"newly_assigned": newly_assigned, "status": structure.status.value}

    async def close(self, structure_id: uuid.UUID, *, actor_id: uuid.UUID) -> FeeStructure:
        """The missing other half of the lifecycle — DRAFT -> ACTIVE
        (activate, above) had no way to ever reach CLOSED, even though
        the status existed and was already checked defensively. Closing
        stops new assignments/enrollment-triggered charges (course-
        scoped fees stop firing on new enrollments; matches
        activate()'s own re-activation guard) but deliberately does NOT
        touch existing assignments or their ledgers — dues already owed
        remain fully payable and visible after the term is over, same
        as Academics' End Semester leaving enrollment history intact."""
        structure = await self.get(structure_id)
        if structure.status == FeeStructureStatus.DRAFT:
            raise BusinessRuleViolationError("A draft fee structure has no active charges to close — activate it first, or just leave it as a draft.")
        if structure.status == FeeStructureStatus.CLOSED:
            raise BusinessRuleViolationError("This fee structure is already closed.")

        structure.status = FeeStructureStatus.CLOSED
        await self.audit.log(
            actor_user_id=actor_id, action="fee_structure.closed", entity_type="fee_structure", entity_id=structure.id,
            before={"status": "ACTIVE"}, after={"status": "CLOSED"},
        )
        return structure

    async def assign_course_fee_for_enrollment(self, *, student_id: uuid.UUID, course_id: uuid.UUID, enrollment_id: uuid.UUID, actor_id: uuid.UUID) -> int:
        """Called directly from EnrollmentService.enroll() right after a
        successful enrollment (first attempt OR retake — both call this
        the same way). Finds every ACTIVE fee structure scoped to this
        specific course and charges this specific enrollment for it,
        skipping any that this enrollment has already been charged for
        (relevant if enroll() is re-activating a previously-withdrawn
        row rather than creating a brand new one). Returns how many new
        assignments were created."""
        stmt = select(FeeStructure).where(
            FeeStructure.status == FeeStructureStatus.ACTIVE,
            FeeStructure.applies_to_students.is_(True),
            FeeStructure.course_id == course_id,
        )
        structures = (await self.session.execute(stmt)).scalars().all()

        newly_assigned = 0
        for structure in structures:
            existing = await self.assignment_repo.get_existing_for_enrollment(structure.id, enrollment_id)
            if existing is not None:
                continue
            assignment = await self.assignment_repo.create(
                FeeAssignment(
                    fee_structure_id=structure.id, payer_type=PayerType.STUDENT, payer_id=student_id,
                    enrollment_id=enrollment_id,
                )
            )
            await self._generate_installments(assignment, structure, actor_id=actor_id)
            newly_assigned += 1

            await self.audit.log(
                actor_user_id=actor_id,
                action="fee_structure.auto_assigned_on_enrollment",
                entity_type="fee_structure",
                entity_id=structure.id,
                metadata={"student_id": str(student_id), "enrollment_id": str(enrollment_id)},
            )

        return newly_assigned

    async def assign_matching_active_structures_to_student(self, student: Student, *, actor_id: uuid.UUID) -> int:
        """Closes a real gap: activating a fee structure only ever
        charged students who were ALREADY at the matching semester at
        that moment — a student later advanced INTO that semester never
        got auto-charged. Called from
        StudentService.advance_semester_bulk right after a student's
        current_semester is bumped, so any fee structure scoped to
        their new semester picks them up immediately, the same way
        activation would have. Returns how many new assignments were
        created (0 or 1 per matching structure)."""
        # Course-scoped structures (course_id set, e.g. a lab fee) are
        # charged ONLY when the student enrolls in that course -- never
        # by program/semester. They carry no program/semester links, so
        # without this filter they looked "institute-wide" and every
        # admitted/advanced student got billed for every course fee.
        stmt = select(FeeStructure).where(
            FeeStructure.status == FeeStructureStatus.ACTIVE,
            FeeStructure.applies_to_students.is_(True),
            FeeStructure.course_id.is_(None),
        )
        active_structures = (await self.session.execute(stmt)).scalars().all()

        newly_assigned = 0
        for structure in active_structures:
            program_links = await self.repo.get_program_links(structure.id)
            if program_links and student.program_id not in [link.program_id for link in program_links]:
                continue
            semester_links = await self.repo.get_semester_links(structure.id)
            if semester_links and student.current_semester not in [link.semester_number for link in semester_links]:
                continue

            existing = await self.assignment_repo.get_existing(structure.id, PayerType.STUDENT, student.id)
            if existing is not None:
                continue

            assignment = await self.assignment_repo.create(
                FeeAssignment(fee_structure_id=structure.id, payer_type=PayerType.STUDENT, payer_id=student.id)
            )
            await self._generate_installments(assignment, structure, actor_id=actor_id)
            newly_assigned += 1

            await self.audit.log(
                actor_user_id=actor_id,
                action="fee_structure.auto_assigned_on_advancement",
                entity_type="fee_structure",
                entity_id=structure.id,
                metadata={"student_id": str(student.id), "new_semester": student.current_semester},
            )

        return newly_assigned