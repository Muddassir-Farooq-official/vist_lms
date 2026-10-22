from __future__ import annotations

from app.core.clock import institute_today

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.repository import AuthRepository
from app.core.security import new_temp_password
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError
from app.core.pagination import PageParams, RawPage
from app.core.security import hash_password
from app.core.sequences import generate_roll_number
from app.programs.repository import ProgramRepository
from app.students.models import (
    Student,
    StudentAddress,
    StudentEmergencyContact,
    StudentGuardian,
    StudentStatus,
    StudentStatusHistory,
)
from app.students.repository import StudentRepository
from app.students.schemas import AdvanceSemesterRequest, StudentCreateRequest, StudentUpdateRequest

# Allowed status transitions — explicit business rule, not scattered `if`s.
_ALLOWED_TRANSITIONS: dict[StudentStatus, set[StudentStatus]] = {
    StudentStatus.ACTIVE: {StudentStatus.SUSPENDED, StudentStatus.GRADUATED, StudentStatus.DROPPED},
    StudentStatus.SUSPENDED: {StudentStatus.ACTIVE, StudentStatus.DROPPED},
    StudentStatus.GRADUATED: set(),  # terminal
    StudentStatus.DROPPED: set(),  # terminal
}


class StudentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = StudentRepository(session)
        self.audit = AuditService(session)

    async def get_student(self, student_id: uuid.UUID) -> Student:
        student = await self.repo.get_by_id(student_id)
        if student is None:
            raise ResourceNotFoundError("Student not found.")
        return student

    async def get_student_by_user_id(self, user_id: uuid.UUID) -> Student:
        student = await self.repo.get_by_user_id(user_id)
        if student is None:
            raise ResourceNotFoundError("Student record not found for this account.")
        return student

    async def search_students(
        self, *, page: PageParams, query: str | None, program_id: uuid.UUID | None, status: StudentStatus | None
    ) -> RawPage[Student]:
        items, total = await self.repo.search(page=page, query=query, program_id=program_id, status=status)
        return RawPage[Student](items=items, total=total, page=page.page, page_size=page.page_size)

    async def change_status(
        self, student_id: uuid.UUID, *, new_status: StudentStatus, reason: str | None, actor_id: uuid.UUID
    ) -> Student:
        student = await self.get_student(student_id)
        allowed = _ALLOWED_TRANSITIONS.get(student.status, set())
        if new_status not in allowed and new_status != student.status:
            raise BusinessRuleViolationError(
                f"Cannot transition student from {student.status.value} to {new_status.value}."
            )

        # Real gap fix: this method never touched anything outside the
        # students table — a student's Enrollment rows (academics
        # module) and BookIssue rows (library module) just sat there
        # unchanged regardless of status change. Concretely: a
        # GRADUATED student could permanently inflate
        # CourseOffering.enrolled_count and still show up in a
        # teacher's active roster despite having "finished" — and a
        # checked-out library book issued to them was never
        # reconciled either way. GRADUATED is blocked outright here
        # (standard "clearance" requirement — you can't graduate with
        # open coursework or an outstanding library book); DROPPED
        # auto-withdraws remaining enrollments below, after the status
        # change, since leaving doesn't require the same clearance
        # (see the withdraw loop after the status/history writes).
        if new_status == StudentStatus.GRADUATED:
            from app.academics.models import EnrollmentStatus
            from app.academics.repository import EnrollmentRepository
            from app.library.models import BorrowerType
            from app.library.repository import BookIssueRepository

            enrollments = await EnrollmentRepository(self.session).list_for_student(student.id)
            still_enrolled = [e for e in enrollments if e.status == EnrollmentStatus.ENROLLED]
            if still_enrolled:
                raise BusinessRuleViolationError(
                    f"Cannot mark this student as graduated — {len(still_enrolled)} course enrollment(s) "
                    "still have no final outcome. Complete or withdraw them first."
                )
            active_issues = await BookIssueRepository(self.session).list_active_for_borrower(
                BorrowerType.STUDENT.value, student.id
            )
            if active_issues:
                raise BusinessRuleViolationError(
                    f"Cannot mark this student as graduated — {len(active_issues)} library book(s) still "
                    "checked out. Return them first (library clearance)."
                )

        old_status = student.status
        student.status = new_status
        student.is_active_flag = new_status == StudentStatus.ACTIVE

        # Real bug fix: student.is_active_flag was purely a display/
        # business flag — it never actually touched the linked login
        # account, so a suspended/dropped/graduated student could keep
        # logging in indefinitely. This is the actual enforcement point.
        if student.user_id is not None:
            from app.auth.models import User

            user = await self.session.get(User, student.user_id)
            if user is not None:
                user.is_active = new_status == StudentStatus.ACTIVE

        self.session.add(
            StudentStatusHistory(
                student_id=student.id,
                old_status=old_status,
                new_status=new_status,
                changed_by=actor_id,
                reason=reason,
                changed_at=datetime.now(timezone.utc),
            )
        )
        await self.audit.log(
            actor_user_id=actor_id,
            action="student.status_changed",
            entity_type="student",
            entity_id=student.id,
            before={"status": old_status.value},
            after={"status": new_status.value},
            metadata={"reason": reason} if reason else None,
        )

        if new_status == StudentStatus.DROPPED:
            from app.academics.models import EnrollmentStatus
            from app.academics.service import EnrollmentService

            enrollment_service = EnrollmentService(self.session)
            enrollments = await enrollment_service.enrollment_repo.list_for_student(student.id)
            for enrollment in enrollments:
                if enrollment.status == EnrollmentStatus.ENROLLED:
                    await enrollment_service.withdraw(enrollment.id, student_id=None, actor_id=actor_id)

        return student

    async def check_duplicate(self, *, cnic: str) -> None:
        """Used both by manual creation and the CSV importer's pre-check.
        roll_number no longer needs a duplicate check here — it's always
        system-generated (app/core/sequences.py), which guarantees
        uniqueness by construction rather than by checking after the fact."""
        if await self.repo.get_by_cnic(cnic):
            raise DuplicateResourceError(f"A student with CNIC '{cnic}' already exists.")

    async def correct_identity(self, student_id: uuid.UUID, data, *, actor_id: uuid.UUID) -> Student:
        """Fix a mistyped student or guardian CNIC / guardian details. Changing the guardian CNIC means a different person: the old
        guardian's parent-portal access to this student is cut, and the office gives the right guardian a login."""
        from sqlalchemy import delete, select

        from app.parents.models import ParentStudentLink
        from app.students.models import StudentGuardian

        student = await self.get_student(student_id)
        guardian = (await self.session.execute(select(StudentGuardian).where(StudentGuardian.student_id == student_id)
                                               .order_by(StudentGuardian.is_primary.desc()))).scalars().first()
        before = {"cnic": student.cnic, "guardian_name": guardian.name if guardian else None, "guardian_cnic": guardian.cnic if guardian else None,
                  "guardian_relation": guardian.relation if guardian else None}
        changed: dict = {}
        if data.student_cnic and data.student_cnic != student.cnic:
            if await self.repo.get_by_cnic(data.student_cnic) is not None:
                raise BusinessRuleViolationError("Another student already has this CNIC.")
            student.cnic = data.student_cnic
            changed["cnic"] = data.student_cnic
        cut_parent = False
        if guardian is not None:
            if data.guardian_cnic and data.guardian_cnic != guardian.cnic:
                guardian.cnic = data.guardian_cnic
                changed["guardian_cnic"] = data.guardian_cnic
                cut_parent = True
            if data.guardian_name and data.guardian_name != guardian.name:
                guardian.name = data.guardian_name
                changed["guardian_name"] = data.guardian_name
            if data.guardian_relation and data.guardian_relation != guardian.relation:
                guardian.relation = data.guardian_relation
                changed["guardian_relation"] = data.guardian_relation
            if data.guardian_contact_no and data.guardian_contact_no != guardian.contact_no:
                guardian.contact_no = data.guardian_contact_no
                changed["guardian_contact_no"] = data.guardian_contact_no
        elif data.guardian_cnic or data.guardian_name:
            raise BusinessRuleViolationError("This student has no guardian on record to correct.")
        if not changed:
            raise BusinessRuleViolationError("Nothing to change: the details you typed are the same as those on record.")
        if cut_parent:
            await self.session.execute(delete(ParentStudentLink).where(ParentStudentLink.student_id == student_id))
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="student.identity_corrected", entity_type="student", entity_id=student.id, before=before,
                             after={**changed, "reason": data.reason.strip(), "parent_access_cut": cut_parent})
        return student

    async def update_student(self, student_id: uuid.UUID, data: StudentUpdateRequest, *, actor_id: uuid.UUID) -> Student:
        """Partial field update — see StudentUpdateRequest for exactly
        which fields are editable and why cnic/status are deliberately
        excluded from this path."""
        student = await self.get_student(student_id)

        if data.program_id is not None:
            from app.programs.repository import ProgramRepository

            program = await ProgramRepository(self.session).get_by_id(data.program_id)
            if program is None:
                raise ResourceNotFoundError("Selected program does not exist.")

        # Validate current_semester against whichever program will be
        # effective after this update — the one being set now, or the
        # student's existing one if this call doesn't change it.
        effective_program_id = data.program_id if data.program_id is not None else student.program_id
        semester_to_check = data.current_semester if data.current_semester is not None else student.current_semester
        if semester_to_check is not None and effective_program_id is not None:
            from app.programs.repository import ProgramRepository

            program = await ProgramRepository(self.session).get_by_id(effective_program_id)
            if program is not None and semester_to_check > program.total_semesters:
                raise BusinessRuleViolationError(
                    f"Semester {semester_to_check} exceeds this program's {program.total_semesters} total semesters."
                )

        # Changing program mid-term would orphan the student's in-progress
        # enrollments (they belong to the OLD program's curriculum).
        if data.program_id is not None and data.program_id != student.program_id:
            from app.academics.models import EnrollmentStatus
            from app.academics.repository import EnrollmentRepository

            current = await EnrollmentRepository(self.session).list_for_student(student.id)
            if any(e.status == EnrollmentStatus.ENROLLED for e in current):
                raise BusinessRuleViolationError(
                    "This student has courses in progress. Withdraw them before changing the program."
                )

        _prev_program_id, _prev_semester = student.program_id, student.current_semester

        before = {
            "first_name": student.first_name,
            "last_name": student.last_name,
            "date_of_birth": str(student.date_of_birth),
            "gender": student.gender,
            "email": student.email,
            "blood_group": student.blood_group,
            "admission_session": student.admission_session,
            "admission_category": student.admission_category,
            "section_label": student.section_label,
            "contact_no": student.contact_no,
            "marital_status": student.marital_status,
            "religion": student.religion,
            "nationality": student.nationality,
            "program_id": str(student.program_id) if student.program_id else None,
            "current_semester": student.current_semester,
            "roll_number": student.roll_number,
            "admission_fee_paid": student.admission_fee_paid,
            "enrollment_date": str(student.enrollment_date) if student.enrollment_date else None,
        }

        if data.contact_no is not None:
            student.contact_no = data.contact_no
        if data.marital_status is not None:
            student.marital_status = data.marital_status
        if data.religion is not None:
            student.religion = data.religion.strip()
        if data.nationality is not None:
            student.nationality = data.nationality.strip()
        if data.program_id is not None:
            student.program_id = data.program_id
        if data.current_semester is not None:
            student.current_semester = data.current_semester
        if data.admission_fee_paid is not None:
            student.admission_fee_paid = data.admission_fee_paid
        if data.enrollment_date is not None:
            student.enrollment_date = data.enrollment_date
        if data.first_name is not None:
            student.first_name = data.first_name.strip()
        if data.last_name is not None:
            student.last_name = data.last_name.strip()
        if data.date_of_birth is not None:
            student.date_of_birth = data.date_of_birth
        if data.gender is not None:
            student.gender = data.gender
        if data.email is not None:
            student.email = data.email
        if data.blood_group is not None:
            student.blood_group = data.blood_group
        if data.admission_session is not None:
            student.admission_session = data.admission_session.strip() or None
        if data.admission_category is not None:
            student.admission_category = data.admission_category.strip() or None
        if data.section_label is not None:
            student.section_label = data.section_label or None

        # A program with no semester can never enroll in anything
        # (enrollment matches the student's current semester) — default to 1.
        if student.program_id is not None and student.current_semester is None:
            student.current_semester = 1

        if data.present_address is not None or data.province is not None or data.district is not None or data.city is not None:
            address = await self.repo.get_present_address(student.id)
            if address is not None:
                if data.present_address is not None:
                    address.address_line = data.present_address.strip()
                if data.province is not None:
                    address.province = data.province
                if data.district is not None:
                    address.district = data.district
                if data.city is not None:
                    address.city = data.city

        # Program/semester changed -> pick up any already-ACTIVE fee
        # structure scoped to the new program/semester (same rule as
        # advancement); previously only "Advance Semester" did this.
        if (student.program_id, student.current_semester) != (_prev_program_id, _prev_semester):
            from app.fees.service import FeeStructureService

            await FeeStructureService(self.session).assign_matching_active_structures_to_student(
                student, actor_id=actor_id
            )

        await self.audit.log(
            actor_user_id=actor_id,
            action="student.updated",
            entity_type="student",
            entity_id=student.id,
            before=before,
            after={
                "first_name": student.first_name,
                "last_name": student.last_name,
                "date_of_birth": str(student.date_of_birth),
                "gender": student.gender,
                "email": student.email,
                "blood_group": student.blood_group,
                "admission_session": student.admission_session,
                "admission_category": student.admission_category,
                "section_label": student.section_label,
                "contact_no": student.contact_no,
                "marital_status": student.marital_status,
                "religion": student.religion,
                "nationality": student.nationality,
                "program_id": str(student.program_id) if student.program_id else None,
                "current_semester": student.current_semester,
                "roll_number": student.roll_number,
                "admission_fee_paid": student.admission_fee_paid,
                "enrollment_date": str(student.enrollment_date) if student.enrollment_date else None,
            },
        )
        return student

    async def create_student_manually(self, data: StudentCreateRequest, *, actor_id: uuid.UUID) -> Student:
        """Manual single-student creation (Section 4 — 'Add student
        manually'). Validates the same way CSV import does (shared regex,
        shared duplicate checks), but takes a real program_id from a
        dropdown instead of resolving free-text code/name — the ambiguity
        problem CSV import has to solve just doesn't exist here.

        By the time this method runs, `data` has already passed Pydantic
        field validation (CNIC/phone format, gender/marital-status enum)
        — this method only handles what Pydantic can't: duplicate checks
        and program existence, both of which need the database. The
        roll number (also the login username) is generated here, not
        supplied by the caller — see app/core/sequences.py."""
        cnic = data.cnic
        await self.check_duplicate(cnic=cnic)

        program = None
        if data.program_id is not None:
            program = await ProgramRepository(self.session).get_by_id(data.program_id)
            if program is None:
                raise ResourceNotFoundError("Selected program does not exist.")
            if data.current_semester is not None and data.current_semester > program.total_semesters:
                raise BusinessRuleViolationError(
                    f"Semester {data.current_semester} exceeds this program's {program.total_semesters} total semesters."
                )

        # A program without a semester can't enroll in anything; new
        # admissions start at semester 1 unless the admin says otherwise.
        current_semester = data.current_semester
        if data.program_id is not None and current_semester is None:
            current_semester = 1

        roll_year = data.enrollment_date.year if data.enrollment_date else institute_today().year
        roll_number = await generate_roll_number(self.session, year=roll_year)

        student = Student(
            first_name=data.first_name.strip(),
            last_name=data.last_name.strip(),
            date_of_birth=data.date_of_birth,
            gender=data.gender,
            cnic=cnic,
            contact_no=data.contact_no,
            marital_status=data.marital_status,
            religion=data.religion.strip(),
            nationality=data.nationality.strip(),
            roll_number=roll_number,
            admission_fee_paid=data.admission_fee_paid,
            enrollment_date=data.enrollment_date,
            program_id=data.program_id,
            current_semester=current_semester,
            email=data.email,
            blood_group=data.blood_group,
            admission_session=(data.admission_session or "").strip() or None,
            admission_category=(data.admission_category or "").strip() or None,
            section_label=data.section_label,
            status=StudentStatus.ACTIVE,
        )
        self.session.add(student)
        await self.session.flush()

        self.session.add(
            StudentGuardian(
                student_id=student.id,
                name=data.guardian_name.strip(),
                cnic=data.guardian_cnic,
                contact_no=data.guardian_contact_no,
                relation=data.relation_with_guardian.strip(),
                is_primary=True,
            )
        )
        self.session.add(
            StudentEmergencyContact(student_id=student.id, contact_no=data.emergency_contact_no)
        )
        self.session.add(
            StudentAddress(
                student_id=student.id,
                address_type="PRESENT",
                address_line=data.present_address.strip(),
                province=data.province.strip(),
                district=data.district.strip(),
                city=data.city.strip(),
            )
        )

        # The roll number IS the login username now — one identifier,
        # not two, and it's already guaranteed unique by the generator.
        username = roll_number
        temp = new_temp_password()
        user = User(
            username=username,
            password_hash=hash_password(temp),
            temp_password=temp,
            is_active=True,
            must_change_password=True,
        )
        self.session.add(user)
        await self.session.flush()

        student_role_stmt = select(Role).where(Role.code == "STUDENT")
        student_role = (await self.session.execute(student_role_stmt)).scalar_one_or_none()
        if student_role is not None:
            self.session.add(UserRole(user_id=user.id, role_id=student_role.id))

        student.user_id = user.id

        # New admission -> charge every already-ACTIVE fee structure that
        # matches this program/semester (previously only activation and
        # "Advance Semester" ever charged anyone, so a student admitted
        # after activation was silently never billed).
        if student.program_id is not None:
            from app.fees.service import FeeStructureService

            await FeeStructureService(self.session).assign_matching_active_structures_to_student(
                student, actor_id=actor_id
            )

        await self.audit.log(
            actor_user_id=actor_id,
            action="student.created_manually",
            entity_type="student",
            entity_id=student.id,
            after={"cnic": student.cnic, "roll_number": student.roll_number, "username": username},
        )

        # Expose the username on the ORM instance transiently for the
        # router to read — not persisted, just a convenience so the caller
        # doesn't have to re-derive it.
        student._created_username = username  # type: ignore[attr-defined]
        return student

    async def reset_password(self, student_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        """Admin-triggered reset back to the institution default password
        (see app/core/constants.py). Does not require knowing the current
        password — this is an administrative override, distinct from the
        student's own self-service change-password flow."""
        student = await self.get_student(student_id)
        if student.user_id is None:
            raise BusinessRuleViolationError("This student has no linked login account to reset.")

        user = await AuthRepository(self.session).get_user_by_id(student.user_id)
        if user is None:
            raise ResourceNotFoundError("Linked user account not found.")

        temp = new_temp_password()
        user.password_hash = hash_password(temp)
        user.temp_password = temp
        user.must_change_password = True
        await AuthRepository(self.session).revoke_all_for_user(user.id)

        await self.audit.log(
            actor_user_id=actor_id,
            action="student.password_reset",
            entity_type="student",
            entity_id=student.id,
            metadata={"username": user.username},
        )

    async def reactivate_after_attendance_fine(self, student_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        """The manual counterpart to AttendanceService._issue_attendance_fine_and_block
        — approved decision: an admin must actively review and
        reactivate, not an automatic unlock the moment the fine is
        paid. Refuses outright if any ATTENDANCE-source fine against
        this student is still PENDING (not yet PAID or WAIVED) — the
        whole point of the block is to make sure the fine actually gets
        resolved before access returns, so this is a safety check
        against accidentally reactivating someone who hasn't."""
        from app.fines.models import Fine, FinePayerType, FineSource, FineStatus

        student = await self.get_student(student_id)
        if student.user_id is None:
            raise BusinessRuleViolationError("This student has no linked login account to reactivate.")

        stmt = select(Fine).where(
            Fine.payer_type == FinePayerType.STUDENT, Fine.payer_id == student_id,
            Fine.source == FineSource.ATTENDANCE, Fine.status == FineStatus.PENDING,
        )
        still_owed = (await self.session.execute(stmt)).scalars().all()
        if still_owed:
            raise BusinessRuleViolationError(
                f"Cannot reactivate — {len(still_owed)} attendance fine(s) are still unpaid. "
                "Mark them paid or waived first."
            )

        user = await AuthRepository(self.session).get_user_by_id(student.user_id)
        if user is None:
            raise ResourceNotFoundError("Linked user account not found.")
        user.is_active = True

        await self.audit.log(
            actor_user_id=actor_id,
            action="student.reactivated_after_attendance_fine",
            entity_type="student",
            entity_id=student.id,
        )

    async def advance_semester_bulk(
        self, data: AdvanceSemesterRequest, *, actor_id: uuid.UUID
    ) -> dict:
        """Bulk end-of-term action — see AdvanceSemesterRequest for exactly
        which students get skipped and why. One summary audit entry for
        the whole batch, not one per student, to avoid audit-log bloat on a routine
        institute-wide action."""
        from app.programs.repository import ProgramRepository

        stmt = select(Student).where(Student.status == StudentStatus.ACTIVE)
        if data.program_id is not None:
            stmt = stmt.where(Student.program_id == data.program_id)
        students = (await self.session.execute(stmt)).scalars().all()

        program_repo = ProgramRepository(self.session)
        total_semesters_cache: dict[uuid.UUID, int | None] = {}

        advanced_count = 0
        skipped_already_at_max = 0
        skipped_no_semester_set = 0
        skipped_no_program = 0
        skipped_open_enrollments = 0
        skipped_overdue_fees = 0
        skipped_failed_courses = 0
        skipped_low_cgpa = 0
        skipped_unpaid_fines = 0
        skipped_students: list[dict] = []
        from app.academics.transcript_service import TranscriptService
        from app.core.config import settings
        from app.fines.models import Fine, FinePayerType, FineStatus

        transcripts = TranscriptService(self.session)

        from app.academics.models import EnrollmentStatus
        from app.academics.repository import EnrollmentRepository
        from app.fees.assignment_service import FeeAssignmentService
        from app.fees.models import AssignmentStatus, PayerType

        enrollment_repo = EnrollmentRepository(self.session)
        assignment_service = FeeAssignmentService(self.session)

        def _hold(student: Student, reason: str) -> None:
            if len(skipped_students) < 200:
                skipped_students.append(
                    {"roll_number": student.roll_number, "name": f"{student.first_name} {student.last_name}", "reason": reason}
                )

        # See the call site below for why this cross-module import is
        # here rather than at module level.
        from app.fees.service import FeeStructureService

        fee_service = FeeStructureService(self.session)

        for student in students:
            if student.current_semester is None:
                skipped_no_semester_set += 1
                _hold(student, "No current semester set")
                continue
            if student.program_id is None:
                skipped_no_program += 1
                _hold(student, "No program assigned")
                continue

            if student.program_id not in total_semesters_cache:
                program = await program_repo.get_by_id(student.program_id)
                total_semesters_cache[student.program_id] = program.total_semesters if program else None
            total_semesters = total_semesters_cache[student.program_id]

            if total_semesters is not None and student.current_semester >= total_semesters:
                skipped_already_at_max += 1
                _hold(student, "Already at the program's final semester (graduation decision)")
                continue

            if data.skip_open_enrollments:
                open_courses = [
                    e for e in await enrollment_repo.list_for_student(student.id) if e.status == EnrollmentStatus.ENROLLED
                ]
                if open_courses:
                    skipped_open_enrollments += 1
                    _hold(student, f"{len(open_courses)} course(s) still in progress (run End Semester / finish grading)")
                    continue

            if data.skip_overdue_fees:
                overdue_total = 0.0
                for assignment in await assignment_service.list_for_payer(PayerType.STUDENT, student.id):
                    if assignment.status != AssignmentStatus.ACTIVE:
                        continue
                    overdue_total += float((await assignment_service.get_status(assignment)).get("overdue_principal", 0) or 0)
                if overdue_total > 0:
                    skipped_overdue_fees += 1
                    _hold(student, f"Overdue fees of Rs. {overdue_total:,.0f}")
                    continue

            if data.skip_failed_courses or data.skip_low_cgpa:
                summary = (await transcripts.build(student.id))["summary"]
                to_clear = int(summary.get("courses_to_clear") or 0)
                if data.skip_failed_courses and to_clear > settings.promotion_max_courses_to_clear:
                    skipped_failed_courses += 1
                    _hold(student, f"{to_clear} failed course(s) not yet cleared (limit {settings.promotion_max_courses_to_clear})")
                    continue
                cgpa = summary.get("cgpa")
                if data.skip_low_cgpa and cgpa is not None and float(cgpa) < settings.promotion_min_cgpa:
                    skipped_low_cgpa += 1
                    _hold(student, f"CGPA {float(cgpa):.2f} is below the minimum {settings.promotion_min_cgpa:.2f}")
                    continue

            if data.skip_unpaid_fines:
                unpaid = (await self.session.execute(
                    select(func.count(), func.coalesce(func.sum(Fine.amount), 0)).where(
                        Fine.payer_type == FinePayerType.STUDENT, Fine.payer_id == student.id, Fine.status == FineStatus.PENDING)
                )).one()
                if unpaid[0]:
                    skipped_unpaid_fines += 1
                    _hold(student, f"{unpaid[0]} unpaid fine(s) of Rs. {float(unpaid[1]):,.0f}")
                    continue

            advanced_count += 1
            if data.dry_run:
                continue
            student.current_semester += 1

            # Closes a real gap: activating a fee structure only ever
            # charged students already AT that semester at that moment —
            # a student advanced INTO it later never got auto-charged.
            # See FeeStructureService.assign_matching_active_structures_to_student
            # for the full reasoning.
            await fee_service.assign_matching_active_structures_to_student(student, actor_id=actor_id)

        await self.audit.log(
            actor_user_id=actor_id,
            action="student.semester_advanced_bulk" if not data.dry_run else "student.semester_advance_previewed",
            entity_type="student",
            entity_id=None,
            metadata={
                "program_id": str(data.program_id) if data.program_id else None,
                "advanced_count": advanced_count,
                "skipped_already_at_max": skipped_already_at_max,
                "skipped_no_semester_set": skipped_no_semester_set,
                "skipped_no_program": skipped_no_program,
                "skipped_open_enrollments": skipped_open_enrollments,
                "skipped_overdue_fees": skipped_overdue_fees,
                "skipped_failed_courses": skipped_failed_courses,
                "skipped_low_cgpa": skipped_low_cgpa,
                "skipped_unpaid_fines": skipped_unpaid_fines,
            },
        )

        return {
            "advanced_count": advanced_count,
            "skipped_already_at_max": skipped_already_at_max,
            "skipped_no_semester_set": skipped_no_semester_set,
            "skipped_no_program": skipped_no_program,
            "skipped_open_enrollments": skipped_open_enrollments,
            "skipped_overdue_fees": skipped_overdue_fees,
            "skipped_failed_courses": skipped_failed_courses,
            "skipped_low_cgpa": skipped_low_cgpa,
            "skipped_unpaid_fines": skipped_unpaid_fines,
            "min_cgpa": settings.promotion_min_cgpa,
            "max_courses_to_clear": settings.promotion_max_courses_to_clear,
            "dry_run": data.dry_run,
            "skipped_students": skipped_students,
        }