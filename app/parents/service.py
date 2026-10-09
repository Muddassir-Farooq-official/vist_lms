"""Parent portal: a guardian signs in and follows their child's attendance, results and fees (read-only).

Accounts: the administration creates the login from a student's record. The
username is the guardian's CNIC (13 digits, no dashes); a one-time password is
shown once and must be changed at first sign-in. Every other student whose
guardian has the same CNIC (brothers and sisters) is linked to the same login.
A parent only ever sees students linked to them.
"""
from __future__ import annotations

import secrets
import string
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.core.security import hash_password
from app.parents.models import ParentStudentLink
from app.students.models import Student, StudentGuardian


def _temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "Pa#" + "".join(secrets.choice(alphabet) for _ in range(9))


class ParentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _primary_guardian(self, student_id: uuid.UUID) -> StudentGuardian:
        rows = (await self.session.execute(select(StudentGuardian).where(StudentGuardian.student_id == student_id))).scalars().all()
        if not rows:
            raise BusinessRuleViolationError("This student has no guardian on record. Add the guardian (name and CNIC) first.")
        return next((g for g in rows if g.is_primary), rows[0])

    async def access_for_student(self, student_id: uuid.UUID) -> dict:
        if await self.session.get(Student, student_id) is None:
            raise ResourceNotFoundError("Student not found.")
        g = await self._primary_guardian(student_id)
        username = g.cnic.replace("-", "")
        user = (await self.session.execute(select(User).where(User.username == username))).scalar_one_or_none()
        linked = False
        if user is not None:
            linked = await self.session.get(ParentStudentLink, (user.id, student_id)) is not None
        return {"guardian_name": g.name, "guardian_cnic": g.cnic, "relation": g.relation, "username": username,
                "has_login": user is not None, "linked": linked, "active": bool(user and user.is_active)}

    async def create_or_link(self, student_id: uuid.UUID, *, actor_id: uuid.UUID, reset_password: bool = False) -> dict:
        student = await self.session.get(Student, student_id)
        if student is None:
            raise ResourceNotFoundError("Student not found.")
        g = await self._primary_guardian(student_id)
        username = g.cnic.replace("-", "")
        user = (await self.session.execute(select(User).where(User.username == username))).scalar_one_or_none()
        temp = None
        role = (await self.session.execute(select(Role).where(Role.code == Roles.PARENT))).scalar_one_or_none()
        if role is None:
            raise BusinessRuleViolationError("The Parent role does not exist yet. Run the permission seed script.")
        if user is None:
            temp = _temp_password()
            user = User(username=username, password_hash=hash_password(temp), is_active=True, must_change_password=True)
            self.session.add(user)
            await self.session.flush()
            self.session.add(UserRole(user_id=user.id, role_id=role.id))
        else:
            codes = set((await self.session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id)
                                                    .where(UserRole.user_id == user.id))).scalars())
            if Roles.PARENT not in codes:
                raise BusinessRuleViolationError(f"The username {username} already belongs to a different kind of account.")
            if reset_password:
                temp = _temp_password()
                user.password_hash = hash_password(temp)
                user.must_change_password = True
                user.is_active = True
        # this student and every brother/sister with the same guardian CNIC
        sibling_ids = set((await self.session.execute(select(StudentGuardian.student_id).where(StudentGuardian.cnic == g.cnic))).scalars())
        sibling_ids.add(student_id)
        added = []
        for sid in sibling_ids:
            if await self.session.get(ParentStudentLink, (user.id, sid)) is None:
                self.session.add(ParentStudentLink(user_id=user.id, student_id=sid))
                added.append(sid)
        await self.session.flush()
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="parent.access_granted", entity_type="student", entity_id=student_id,
                                             after={"username": username, "linked_students": [str(s) for s in added], "password_reset": bool(temp)})
        return {"username": username, "temporary_password": temp, "children_linked": len(sibling_ids),
                "message": "Give these sign-in details to the guardian; the password must be changed at first sign-in." if temp
                else "The guardian's existing login now includes this student."}

    async def revoke(self, student_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        links = (await self.session.execute(select(ParentStudentLink).where(ParentStudentLink.student_id == student_id))).scalars().all()
        for link in links:
            await self.session.delete(link)
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="parent.access_revoked", entity_type="student", entity_id=student_id,
                                             before={"parent_user_ids": [str(l.user_id) for l in links]})

    # ------------------------------------------------------------ what the parent sees
    async def children(self, user_id: uuid.UUID) -> list[dict]:
        rows = (await self.session.execute(select(Student).join(ParentStudentLink, ParentStudentLink.student_id == Student.id)
                                           .where(ParentStudentLink.user_id == user_id))).scalars().all()
        from app.programs.models import Program

        out = []
        for s in sorted(rows, key=lambda x: x.roll_number or ""):
            prog = await self.session.get(Program, s.program_id) if s.program_id else None
            out.append({"id": s.id, "name": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number,
                        "program": prog.code if prog else None, "semester": s.current_semester, "status": s.status.value if hasattr(s.status, "value") else s.status})
        return out

    async def _own_child(self, user_id: uuid.UUID, student_id: uuid.UUID) -> Student:
        if await self.session.get(ParentStudentLink, (user_id, student_id)) is None:
            raise ResourceNotFoundError("Student not found.")
        return await self.session.get(Student, student_id)

    async def overview(self, user_id: uuid.UUID, student_id: uuid.UUID) -> dict:
        student = await self._own_child(user_id, student_id)
        from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus
        from app.academics.transcript_service import TranscriptService
        from app.attendance.service import AttendanceService
        from app.core.config import settings
        from app.fees.account_service import AccountService
        from app.fees.models import PayerType

        att = AttendanceService(self.session)
        current = (await self.session.execute(
            select(Enrollment, CourseOffering, Course).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .where(Enrollment.student_id == student.id, Enrollment.status == EnrollmentStatus.ENROLLED))).all()
        attendance = []
        for _e, off, course in sorted(current, key=lambda r: r[2].code):
            p = await att.compute_percentage(student.id, off.id)
            attendance.append({"course_code": course.code, "course_title": course.title, "term_label": off.term_label, **p,
                               "below_minimum": p["total_sessions_taken"] > 0 and p["percentage"] < settings.attendance_eligibility_threshold_percent})
        transcript = await TranscriptService(self.session).build(student.id)
        fees = await AccountService(self.session).build(PayerType.STUDENT, student.id, staff_view=False)
        return {"student": (await self.children(user_id)) and next(c for c in await self.children(user_id) if c["id"] == student.id),
                "attendance": attendance, "attendance_minimum": settings.attendance_eligibility_threshold_percent,
                "results": {"summary": transcript["summary"], "terms": transcript["terms"]},
                "fees": {"summary": fees["summary"], "assignments": fees["assignments"], "payments": fees["payments"][:20]}}

    async def challan_owner_check(self, user_id: uuid.UUID, challan_id: uuid.UUID) -> None:
        from app.fees.models import FeeAssignment, PayerType
        from app.fees.payment_models import PaymentChallan

        challan = await self.session.get(PaymentChallan, challan_id)
        if challan is None:
            raise ResourceNotFoundError("Challan not found.")
        a = await self.session.get(FeeAssignment, challan.fee_assignment_id)
        if a.payer_type != PayerType.STUDENT or await self.session.get(ParentStudentLink, (user_id, a.payer_id)) is None:
            raise ResourceNotFoundError("Challan not found.")