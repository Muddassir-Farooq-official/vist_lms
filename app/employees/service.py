from __future__ import annotations

from app.core.clock import institute_today

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError
from app.core.pagination import PageParams, RawPage
from app.auth.repository import AuthRepository
from app.core.security import hash_password
from app.core.sequences import generate_employee_id
from app.employees.models import Employee, EmployeeBonus, EmployeeSalaryHistory, EmployeeStatus, JobRole
from app.employees.repository import EmployeeRepository, JobRoleRepository
from app.employees.schemas import (
    BonusCreateRequest,
    EmployeeCreateRequest,
    EmployeeUpdateRequest,
    JobRoleCreate,
    JobRoleUpdate,
    SalaryIncrementRequest,
)

# Same terminal-state discipline as student status transitions.
_ALLOWED_TRANSITIONS: dict[EmployeeStatus, set[EmployeeStatus]] = {
    EmployeeStatus.ACTIVE: {EmployeeStatus.ON_LEAVE, EmployeeStatus.TERMINATED},
    EmployeeStatus.ON_LEAVE: {EmployeeStatus.ACTIVE, EmployeeStatus.TERMINATED},
    EmployeeStatus.TERMINATED: set(),
}


class JobRoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = JobRoleRepository(session)
        self.employee_repo = EmployeeRepository(session)
        self.audit = AuditService(session)

    async def create(self, data: JobRoleCreate, *, actor_id: uuid.UUID) -> JobRole:
        if await self.repo.get_by_code(data.code):
            raise DuplicateResourceError(f"Job role code '{data.code}' already exists.")
        job_role = JobRole(
            code=data.code.strip().upper(),
            title=data.title.strip(),
            default_role_code=data.default_role_code,
        )
        await self.repo.create(job_role)
        await self.audit.log(
            actor_user_id=actor_id,
            action="job_role.created",
            entity_type="job_role",
            entity_id=job_role.id,
            after={"code": job_role.code, "title": job_role.title},
        )
        return job_role

    async def list_all(self, *, active_only: bool = False) -> list[JobRole]:
        return await self.repo.list_all(active_only=active_only)

    async def update(self, job_role_id: uuid.UUID, data: JobRoleUpdate, *, actor_id: uuid.UUID) -> JobRole:
        job_role = await self.repo.get_by_id(job_role_id)
        if job_role is None:
            raise ResourceNotFoundError("Job role not found.")

        before = {
            "title": job_role.title,
            "default_role_code": job_role.default_role_code,
            "is_active": job_role.is_active,
        }

        if data.title is not None:
            job_role.title = data.title.strip()
        if data.default_role_code is not None:
            job_role.default_role_code = data.default_role_code
        if data.is_active is not None:
            job_role.is_active = data.is_active

        await self.audit.log(
            actor_user_id=actor_id,
            action="job_role.updated",
            entity_type="job_role",
            entity_id=job_role.id,
            before=before,
            after={
                "title": job_role.title,
                "default_role_code": job_role.default_role_code,
                "is_active": job_role.is_active,
            },
        )
        return job_role

    async def delete(self, job_role_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        """Hard delete — only allowed when no employee (past or present)
        references this job role, since Employee.job_role_id is a
        protected (RESTRICT) foreign key. If any do, the admin is pointed
        at deactivation instead, which is always safe regardless of
        history. This intentionally does NOT cascade-delete employees."""
        job_role = await self.repo.get_by_id(job_role_id)
        if job_role is None:
            raise ResourceNotFoundError("Job role not found.")

        employee_count = await self.employee_repo.count_by_job_role(job_role_id)
        if employee_count > 0:
            raise BusinessRuleViolationError(
                f"Cannot delete job role '{job_role.code}' — {employee_count} employee(s) are assigned to it. "
                "Deactivate it instead to hide it from new hires."
            )

        await self.audit.log(
            actor_user_id=actor_id,
            action="job_role.deleted",
            entity_type="job_role",
            entity_id=job_role.id,
            before={"code": job_role.code, "title": job_role.title},
        )
        await self.repo.delete(job_role)


# Simple pass-through columns editable via PATCH /employees/{id}.
_EDITABLE_EXTRA_FIELDS = (
    "first_name", "last_name", "date_of_birth", "gender", "hire_date", "email", "blood_group",
    "marital_status", "employment_type", "emergency_contact_name", "emergency_contact_no",
    "bank_name", "bank_account_title", "bank_iban",
)


class EmployeeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EmployeeRepository(session)
        self.job_role_repo = JobRoleRepository(session)
        self.audit = AuditService(session)

    async def get_employee(self, employee_id: uuid.UUID) -> Employee:
        employee = await self.repo.get_by_id(employee_id)
        if employee is None:
            raise ResourceNotFoundError("Employee not found.")
        return employee

    async def get_employee_by_user_id(self, user_id: uuid.UUID) -> Employee:
        employee = await self.repo.get_by_user_id(user_id)
        if employee is None:
            raise ResourceNotFoundError("No employee record linked to this account.")
        return employee

    async def update_employee(self, employee_id: uuid.UUID, data: EmployeeUpdateRequest, *, actor_id: uuid.UUID) -> Employee:
        employee = await self.get_employee(employee_id)
        before = {
            **{f: (str(getattr(employee, f)) if getattr(employee, f) is not None else None) for f in _EDITABLE_EXTRA_FIELDS},
            "contact_no": employee.contact_no,
            "department": employee.department,
            "religion": employee.religion,
            "province": employee.province,
            "district": employee.district,
            "city": employee.city,
            "address_line": employee.address_line,
        }

        if data.contact_no is not None:
            employee.contact_no = data.contact_no
        if data.department is not None:
            employee.department = data.department.strip() or None
        if data.religion is not None:
            employee.religion = data.religion
        if data.province is not None:
            employee.province = data.province
        if data.district is not None:
            employee.district = data.district
        if data.city is not None:
            employee.city = data.city
        if data.address_line is not None:
            employee.address_line = data.address_line.strip() or None
        for _field in _EDITABLE_EXTRA_FIELDS:
            _value = getattr(data, _field)
            if _value is not None:
                if isinstance(_value, str):
                    _value = _value.strip()
                setattr(employee, _field, _value or None if _field not in ("first_name", "last_name") else _value)

        await self.audit.log(
            actor_user_id=actor_id,
            action="employee.updated",
            entity_type="employee",
            entity_id=employee.id,
            before=before,
            after={
                **{f: (str(getattr(employee, f)) if getattr(employee, f) is not None else None) for f in _EDITABLE_EXTRA_FIELDS},
                "contact_no": employee.contact_no,
                "department": employee.department,
                "religion": employee.religion,
                "province": employee.province,
                "district": employee.district,
                "city": employee.city,
                "address_line": employee.address_line,
            },
        )
        return employee

    async def search_employees(
        self, *, page: PageParams, query: str | None, status: EmployeeStatus | None, job_role_id: uuid.UUID | None,
        role_code: str | None = None,
    ) -> RawPage[Employee]:
        items, total = await self.repo.search(
            page=page, query=query, status=status, job_role_id=job_role_id, role_code=role_code
        )
        return RawPage[Employee](items=items, total=total, page=page.page, page_size=page.page_size)

    async def create_employee(self, data: EmployeeCreateRequest, *, actor_id: uuid.UUID) -> Employee:
        if await self.repo.get_by_cnic(data.cnic):
            raise DuplicateResourceError(f"An employee with CNIC '{data.cnic}' already exists.")

        job_role = await self.job_role_repo.get_by_id(data.job_role_id)
        if job_role is None:
            raise ResourceNotFoundError("Selected job role does not exist.")
        if not job_role.is_active:
            raise BusinessRuleViolationError(
                f"Job role '{job_role.code}' has been deactivated and can't be used for new hires."
            )

        # Resolve the RBAC role BEFORE creating anything: previously a
        # missing role was silently skipped, producing a login account
        # with no permissions at all that could reach nothing.
        role = (await self.session.execute(select(Role).where(Role.code == job_role.default_role_code))).scalar_one_or_none()
        if role is None:
            raise BusinessRuleViolationError(
                f"The system role '{job_role.default_role_code}' that job role '{job_role.code}' maps to does not "
                "exist. Run the permission seed script (scripts/seed_permissions.py) and try again."
            )

        employee_code = await generate_employee_id(self.session)

        employee = Employee(
            first_name=data.first_name.strip(),
            last_name=data.last_name.strip(),
            employee_code=employee_code,
            cnic=data.cnic,
            contact_no=data.contact_no,
            date_of_birth=data.date_of_birth,
            gender=data.gender,
            hire_date=data.hire_date,
            department=data.department.strip() if data.department else None,
            job_role_id=job_role.id,
            status=EmployeeStatus.ACTIVE,
            religion=data.religion,
            province=data.province,
            district=data.district,
            city=data.city,
            address_line=data.address_line.strip() if data.address_line else None,
            email=data.email,
            blood_group=data.blood_group,
            marital_status=data.marital_status,
            employment_type=data.employment_type,
            emergency_contact_name=data.emergency_contact_name.strip() if data.emergency_contact_name else None,
            emergency_contact_no=data.emergency_contact_no,
        )
        self.session.add(employee)
        await self.session.flush()

        # Initial salary — first row in the append-only history.
        self.session.add(
            EmployeeSalaryHistory(
                employee_id=employee.id,
                base_salary=data.base_salary,
                effective_from=data.hire_date,
                effective_to=None,
                change_type="INITIAL",
                change_value=None,
                reason="Initial salary at hire",
                changed_by=actor_id,
                created_at=institute_today(),
            )
        )

        # Account creation — admin-set password per approved decision
        # (unlike students, there is no fixed staff default).
        # The employee code IS the login username now — one identifier,
        # not two, already guaranteed unique by the generator.
        username = employee_code
        user = User(
            username=username,
            password_hash=hash_password(data.password),
            is_active=True,
            must_change_password=True,
        )
        self.session.add(user)
        await self.session.flush()

        self.session.add(UserRole(user_id=user.id, role_id=role.id))

        employee.user_id = user.id

        await self.audit.log(
            actor_user_id=actor_id,
            action="employee.created",
            entity_type="employee",
            entity_id=employee.id,
            after={"cnic": employee.cnic, "username": username, "job_role": job_role.code},
        )

        employee._created_username = username  # type: ignore[attr-defined]
        return employee

    async def change_status(
        self, employee_id: uuid.UUID, *, new_status: EmployeeStatus, reason: str | None, actor_id: uuid.UUID
    ) -> Employee:
        employee = await self.get_employee(employee_id)
        allowed = _ALLOWED_TRANSITIONS.get(employee.status, set())
        if new_status not in allowed and new_status != employee.status:
            raise BusinessRuleViolationError(
                f"Cannot transition employee from {employee.status.value} to {new_status.value}."
            )
        old_status = employee.status
        employee.status = new_status

        # Real gap fix, same class as StudentService.change_status: a
        # terminated teacher's CourseOffering.assigned_teacher_id just
        # kept pointing to them forever — no signal anywhere that the
        # offering needs a new teacher. Deliberately auto-UNASSIGN
        # rather than block the status change (unlike student
        # graduation's library/coursework clearance): termination is an
        # HR action and must never be refused just because someone has
        # open commitments.
        #
        # Real bug in an earlier version of this fix: it kept the
        # teacher assigned unless is_open_for_enrollment was True — but
        # CourseOfferingService.end_semester() sets that SAME flag to
        # False on a genuinely concluded offering AND enrollment is
        # routinely closed well before a course actually finishes, so a
        # terminated teacher stayed attached to live, currently-running
        # courses. There is no separate "concluded" flag on
        # CourseOffering, so term_end_date is the only real signal:
        # unassign from anything whose term hasn't definitively ended
        # yet (including offerings with no term_end_date set at all,
        # treated as still active since we can't prove otherwise) and
        # leave only genuinely past offerings as the historical record
        # of who actually taught them.
        if new_status == EmployeeStatus.TERMINATED:
            from app.academics.repository import CourseOfferingRepository

            today = institute_today()
            offerings = await CourseOfferingRepository(self.session).list_by_teacher(employee.id)
            for offering in offerings:
                if offering.term_end_date is None or offering.term_end_date >= today:
                    offering.assigned_teacher_id = None
                    await self.audit.log(
                        actor_user_id=actor_id,
                        action="course_offering.teacher_unassigned",
                        entity_type="course_offering",
                        entity_id=offering.id,
                        metadata={"reason": "assigned teacher was terminated", "former_teacher_id": str(employee.id)},
                    )

        # Same real bug fix as StudentService.change_status — nothing
        # previously touched the linked login account when an employee's
        # status changed, so a terminated employee could keep logging in
        # indefinitely. Deliberately only TERMINATED locks the account
        # out here — ON_LEAVE is a temporary status, not a clear signal
        # that access should be cut off, unlike a student being
        # suspended/dropped/graduated.
        if employee.user_id is not None:
            from app.auth.models import User

            if new_status == EmployeeStatus.TERMINATED:
                from app.core.controls import ensure_another_admin, refuse_self

                refuse_self(actor_id, employee.user_id, "You cannot end your own employment. Another administrator must do it.")
                await ensure_another_admin(self.session, employee.user_id)
            user = await self.session.get(User, employee.user_id)
            if user is not None:
                user.is_active = new_status != EmployeeStatus.TERMINATED
        if new_status == EmployeeStatus.TERMINATED:
            employee.left_on = institute_today()            # paid up to this day in that month's payroll
        elif old_status == EmployeeStatus.TERMINATED:
            employee.left_on = None

        await self.audit.log(
            actor_user_id=actor_id,
            action="employee.status_changed",
            entity_type="employee",
            entity_id=employee.id,
            before={"status": old_status.value},
            after={"status": new_status.value},
            metadata={"reason": reason} if reason else None,
        )
        return employee

    async def reset_password(self, employee_id: uuid.UUID, *, new_password: str, actor_id: uuid.UUID) -> None:
        """Admin sets a new password directly — does not require knowing
        the current one, same administrative-override pattern as the
        student password reset, just without a fixed default value to
        reset TO (employees don't share one)."""
        employee = await self.get_employee(employee_id)
        if employee.user_id is None:
            raise BusinessRuleViolationError("This employee has no linked login account.")

        user = await AuthRepository(self.session).get_user_by_id(employee.user_id)
        if user is None:
            raise ResourceNotFoundError("Linked user account not found.")

        user.password_hash = hash_password(new_password)
        user.must_change_password = True
        await AuthRepository(self.session).revoke_all_for_user(user.id)

        await self.audit.log(
            actor_user_id=actor_id,
            action="employee.password_reset",
            entity_type="employee",
            entity_id=employee.id,
            metadata={"username": user.username},
        )

    async def apply_salary_increment(
        self, employee_id: uuid.UUID, data: SalaryIncrementRequest, *, actor_id: uuid.UUID
    ) -> EmployeeSalaryHistory:
        employee = await self.get_employee(employee_id)
        current = await self.repo.get_current_salary(employee_id)
        if current is None:
            raise BusinessRuleViolationError("Employee has no current salary record to increment from.")

        if data.change_type == "FIXED_AMOUNT":
            new_amount = float(current.base_salary) + data.value
        else:  # PERCENTAGE
            new_amount = float(current.base_salary) * (1 + data.value / 100)

        effective_from = data.effective_from or institute_today()
        if effective_from < current.effective_from:
            raise BusinessRuleViolationError("New effective date cannot be before the current salary's start date.")

        current.effective_to = effective_from

        new_row = EmployeeSalaryHistory(
            employee_id=employee.id,
            base_salary=round(new_amount, 2),
            effective_from=effective_from,
            effective_to=None,
            change_type=data.change_type,
            change_value=data.value,
            reason=data.reason,
            changed_by=actor_id,
            created_at=institute_today(),
        )
        self.session.add(new_row)
        await self.session.flush()

        await self.audit.log(
            actor_user_id=actor_id,
            action="employee.salary_incremented",
            entity_type="employee",
            entity_id=employee.id,
            before={"base_salary": float(current.base_salary)},
            after={"base_salary": float(new_row.base_salary)},
            metadata={"change_type": data.change_type, "change_value": data.value, "reason": data.reason},
        )
        return new_row

    async def award_bonus(
        self, employee_id: uuid.UUID, data: BonusCreateRequest, *, actor_id: uuid.UUID
    ) -> EmployeeBonus:
        employee = await self.get_employee(employee_id)
        bonus = EmployeeBonus(
            employee_id=employee.id,
            amount=data.amount,
            reason=data.reason,
            awarded_by=actor_id,
            awarded_at=data.awarded_at or institute_today(),
        )
        self.session.add(bonus)
        await self.session.flush()

        await self.audit.log(
            actor_user_id=actor_id,
            action="employee.bonus_awarded",
            entity_type="employee",
            entity_id=employee.id,
            after={"amount": data.amount, "reason": data.reason},
        )
        return bonus

    # ------------------------------------------------------------------
    # HR: change job role (promotion / transfer) -- swaps the login role too
    # ------------------------------------------------------------------
    async def change_job_role(
        self, employee_id: uuid.UUID, new_job_role_id: uuid.UUID, *, reason: str | None, actor_id: uuid.UUID
    ) -> Employee:
        """Previously impossible after hire: a librarian promoted to teacher
        (or a teacher moved to admin staff) could not be re-titled, so their
        permissions were wrong forever. Rules:
          * only ACTIVE / ON_LEAVE employees (terminated staff keep history);
          * the new job role must be active;
          * leaving a TEACHER role while still assigned to a running course
            offering is refused -- reassign the course first, so no class is
            left silently without a teacher;
          * the linked login's system role is swapped and every refresh
            token revoked, so the new permissions apply at next sign-in.
        """
        employee = await self.get_employee(employee_id)
        if employee.status == EmployeeStatus.TERMINATED:
            raise BusinessRuleViolationError("A terminated employee's job role cannot be changed.")
        new_job_role = await self.job_role_repo.get_by_id(new_job_role_id)
        if new_job_role is None:
            raise ResourceNotFoundError("Selected job role does not exist.")
        if not new_job_role.is_active:
            raise BusinessRuleViolationError(f"Job role '{new_job_role.code}' has been deactivated.")
        if new_job_role.id == employee.job_role_id:
            raise BusinessRuleViolationError("The employee already holds this job role.")

        old_job_role = await self.job_role_repo.get_by_id(employee.job_role_id)
        new_role = (
            await self.session.execute(select(Role).where(Role.code == new_job_role.default_role_code))
        ).scalar_one_or_none()
        if new_role is None:
            raise BusinessRuleViolationError(
                f"The system role '{new_job_role.default_role_code}' does not exist. Run the permission seed script."
            )

        old_code = old_job_role.default_role_code if old_job_role else None
        if old_code == "TEACHER" and new_job_role.default_role_code != "TEACHER":
            from app.academics.repository import CourseOfferingRepository

            today = institute_today()
            running = [
                o for o in await CourseOfferingRepository(self.session).list_by_teacher(employee.id)
                if o.term_end_date is None or o.term_end_date >= today
            ]
            if running:
                raise BusinessRuleViolationError(
                    f"This teacher is still assigned to {len(running)} running course offering(s). "
                    "Assign those courses to another teacher first."
                )

        employee.job_role_id = new_job_role.id

        if employee.user_id is not None:
            from app.auth.repository import AuthRepository

            # Replace ONLY the staff role that came from the old job title;
            # ADMIN (granted separately) and any other roles stay untouched.
            old_role = None
            if old_code:
                old_role = (await self.session.execute(select(Role).where(Role.code == old_code))).scalar_one_or_none()
            if old_code == "ADMIN" and new_job_role.default_role_code != "ADMIN":
                from app.core.controls import ensure_another_admin

                await ensure_another_admin(self.session, employee.user_id)
            if old_role is not None and old_role.id != new_role.id:
                for link in (
                    await self.session.execute(
                        select(UserRole).where(UserRole.user_id == employee.user_id, UserRole.role_id == old_role.id)
                    )
                ).scalars():
                    await self.session.delete(link)
            already = (
                await self.session.execute(
                    select(UserRole).where(UserRole.user_id == employee.user_id, UserRole.role_id == new_role.id)
                )
            ).scalar_one_or_none()
            if already is None:
                self.session.add(UserRole(user_id=employee.user_id, role_id=new_role.id))
            await AuthRepository(self.session).revoke_all_for_user(employee.user_id)

        await self.audit.log(
            actor_user_id=actor_id,
            action="employee.job_role_changed",
            entity_type="employee",
            entity_id=employee.id,
            before={"job_role": old_job_role.code if old_job_role else None},
            after={"job_role": new_job_role.code, "system_role": new_job_role.default_role_code},
            metadata={"reason": reason} if reason else None,
        )
        return employee

    # ------------------------------------------------------------------
    # Clearance report
    # ------------------------------------------------------------------
    async def clearance(self, employee_id: uuid.UUID) -> dict:
        """What is still attached to this employee. Informational: HR uses it
        before/after termination; it never blocks anything."""
        from app.academics.repository import CourseOfferingRepository
        from app.fees.assignment_service import FeeAssignmentService
        from app.fees.models import AssignmentStatus, PayerType
        from app.fines.models import FinePayerType, FineStatus
        from app.fines.repository import FineRepository
        from app.library.models import BorrowerType
        from app.library.repository import BookIssueRepository

        employee = await self.get_employee(employee_id)
        today = institute_today()
        teaching = [
            o for o in await CourseOfferingRepository(self.session).list_by_teacher(employee.id)
            if o.term_end_date is None or o.term_end_date >= today
        ]
        loans = await BookIssueRepository(self.session).list_active_for_borrower(BorrowerType.EMPLOYEE.value, employee.id)

        balance = 0.0
        fee_service = FeeAssignmentService(self.session)
        for assignment in await fee_service.list_for_payer(PayerType.EMPLOYEE, employee.id):
            if assignment.status == AssignmentStatus.ACTIVE:
                status = await fee_service.get_status(assignment)
                balance += float(status.get("total_due_now", 0) or 0)

        fines = await FineRepository(self.session).list_visible_to_payer(FinePayerType.EMPLOYEE, employee.id)
        unpaid = sum(1 for f in fines if f.status == FineStatus.PENDING)

        return {
            "active_teaching_offerings": len(teaching),
            "open_library_loans": len(loans),
            "unpaid_fines": unpaid,
            "outstanding_fee_balance": round(balance, 2),
            "can_leave_cleanly": not teaching and not loans and unpaid == 0 and balance <= 0,
        }