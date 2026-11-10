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

from sqlalchemy import func, select
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
        # Brothers and sisters (same guardian CNIC) are SUGGESTED, never linked by themselves: the office picks who the login may see.
        sib = (await self.session.execute(
            select(Student).join(StudentGuardian, StudentGuardian.student_id == Student.id)
            .where(StudentGuardian.cnic == g.cnic, Student.id != student_id).order_by(Student.roll_number, Student.first_name))).scalars().unique().all()
        siblings = []
        for s in sib:
            siblings.append({"id": s.id, "name": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number,
                             "linked": bool(user and await self.session.get(ParentStudentLink, (user.id, s.id)) is not None)})
        return {"guardian_name": g.name, "guardian_cnic": g.cnic, "relation": g.relation, "username": username,
                "has_login": user is not None, "linked": linked, "active": bool(user and user.is_active), "siblings": siblings}

    async def create_or_link(self, student_id: uuid.UUID, *, actor_id: uuid.UUID, reset_password: bool = False, sibling_ids: list[uuid.UUID] | None = None) -> dict:
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
        # this student, plus only the brothers/sisters the office chose; each must really share the guardian (same CNIC)
        family = set((await self.session.execute(select(StudentGuardian.student_id).where(StudentGuardian.cnic == g.cnic))).scalars())
        chosen = {student_id} | set(sibling_ids or [])
        if not chosen <= family:
            raise BusinessRuleViolationError("A student of another family cannot be added to this guardian's login.")
        added = []
        for sid in chosen:
            if await self.session.get(ParentStudentLink, (user.id, sid)) is None:
                self.session.add(ParentStudentLink(user_id=user.id, student_id=sid))
                added.append(sid)
        await self.session.flush()
        total = (await self.session.execute(select(func.count()).select_from(ParentStudentLink).where(ParentStudentLink.user_id == user.id))).scalar_one()
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="parent.access_granted", entity_type="student", entity_id=student_id,
                                             after={"username": username, "linked_students": [str(s) for s in added], "password_reset": bool(temp)})
        return {"username": username, "temporary_password": temp, "children_linked": total,
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
        from app.attendance.shortfall import risk_for_student

        risk = await risk_for_student(self.session, student.id, [
            {"offering_id": off.id, "present": a["present_count"], "taken": a["total_sessions_taken"]}
            for (_e, off, _c), a in zip(sorted(current, key=lambda r: r[2].code), attendance)])
        for (_e, off, _c), a in zip(sorted(current, key=lambda r: r[2].code), attendance):
            a["risk"] = risk.get(off.id)
        transcript = await TranscriptService(self.session).build(student.id)
        from app.academics.watchlist import for_students
        fees = await AccountService(self.session).build(PayerType.STUDENT, student.id, staff_view=False)
        return {"student": (await self.children(user_id)) and next(c for c in await self.children(user_id) if c["id"] == student.id),
                "attendance": attendance, "attendance_minimum": settings.attendance_eligibility_threshold_percent,
                "results": {"summary": transcript["summary"], "terms": transcript["terms"]},
                "fees": {"summary": fees["summary"], "assignments": fees["assignments"], "payments": fees["payments"][:20]},
                # low marks so far and missed assignments (attendance is already shown above): the same signs teachers see
                "attention": [i for i in (await for_students(self.session, {student.id})).get(student.id, []) if i["kind"] != "attendance"]}

    async def challan_owner_check(self, user_id: uuid.UUID, challan_id: uuid.UUID) -> None:
        from app.fees.models import FeeAssignment, PayerType
        from app.fees.payment_models import PaymentChallan

        challan = await self.session.get(PaymentChallan, challan_id)
        if challan is None:
            raise ResourceNotFoundError("Challan not found.")
        a = await self.session.get(FeeAssignment, challan.fee_assignment_id)
        if a.payer_type != PayerType.STUDENT or await self.session.get(ParentStudentLink, (user_id, a.payer_id)) is None:
            raise ResourceNotFoundError("Challan not found.")
    # ------------------------------------------------------------ the parent's own pages (each checks the child is theirs)
    async def fees(self, user_id: uuid.UUID, student_id: uuid.UUID) -> dict:
        """The child's whole fee account as the student sees it: fees, instalments, challans (with the consumer number
        for 1Bill), discounts, payments with receipts, and what is still due."""
        await self._own_child(user_id, student_id)
        from app.fees.account_service import AccountService
        from app.fees.models import PayerType

        return await AccountService(self.session).build(PayerType.STUDENT, student_id, staff_view=False)

    async def timetable(self, user_id: uuid.UUID, student_id: uuid.UUID, days: int = 14) -> dict:
        """The child's classes from today for `days` days (cancelled ones left out), and the published datesheets."""
        await self._own_child(user_id, student_id)
        from datetime import timedelta

        from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering, Enrollment, EnrollmentStatus
        from app.core.clock import institute_today
        from app.employees.models import Employee
        from app.exams.service import ExamScheduleService

        today = institute_today()
        rows = (await self.session.execute(
            select(ClassSession, CourseOffering, Course)
            .join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
            .where(Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED,
                   ClassSession.session_date >= today, ClassSession.session_date <= today + timedelta(days=days),
                   ClassSession.status != ClassSessionStatus.CANCELLED)
            .order_by(ClassSession.session_date, ClassSession.start_time))).all()
        teachers = {e.id: e.full_name for e in (await self.session.execute(select(Employee).where(
            Employee.id.in_({o.assigned_teacher_id for _, o, _ in rows if o.assigned_teacher_id} or {uuid.uuid4()})))).scalars()}
        classes = [{"date": s.session_date, "start": s.start_time, "end": s.end_time, "room": s.room, "course_code": c.code,
                    "course_title": c.title, "teacher": teachers.get(o.assigned_teacher_id)} for s, o, c in rows]
        return {"from": today, "to": today + timedelta(days=days), "classes": classes,
                "exams": await ExamScheduleService(self.session).for_student(student_id)}

    async def marks(self, user_id: uuid.UUID, student_id: uuid.UUID) -> list[dict]:
        """Marks entered so far in each current subject (quizzes, assignments, mid-term...), with the running total."""
        await self._own_child(user_id, student_id)
        from app.academics.models import (AssessmentType, Course, CourseOffering, Enrollment, EnrollmentStatus, OfferingAssessment,
                                          StudentMark)

        enrolled = (await self.session.execute(
            select(Enrollment, CourseOffering, Course).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .where(Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED)
            .order_by(Course.code))).all()
        out = []
        for e, o, c in enrolled:
            rows = (await self.session.execute(
                select(AssessmentType.name, AssessmentType.max_marks, StudentMark.obtained_marks)
                .join(OfferingAssessment, OfferingAssessment.assessment_type_id == AssessmentType.id)
                .join(StudentMark, StudentMark.offering_assessment_id == OfferingAssessment.id)
                .where(StudentMark.enrollment_id == e.id).order_by(AssessmentType.name))).all()
            items = [{"name": n, "obtained": float(got), "max": float(mx)} for n, mx, got in rows]
            got = sum(i["obtained"] for i in items)
            mx = sum(i["max"] for i in items)
            out.append({"course_code": c.code, "course_title": c.title, "term_label": o.term_label, "items": items,
                        "obtained": round(got, 2), "max": round(mx, 2), "percentage": round(100 * got / mx, 1) if mx else None})
        return out

    async def announcements(self, user_id: uuid.UUID, student_id: uuid.UUID, limit: int = 30) -> list[dict]:
        """What the institute announced to the child's students (their programme or everyone), newest first."""
        student = await self._own_child(user_id, student_id)
        from app.announcements.service import AnnouncementService, Viewer

        svc = AnnouncementService(self.session)
        viewer = await svc.student_viewer(student, user_id=user_id)
        items = (await svc.visible_to(viewer))[:max(1, min(limit, 100))]
        return [{"id": a.id, "title": a.title, "body": a.body, "category": getattr(a.category, "value", a.category),
                 "priority": getattr(a.priority, "value", a.priority), "pinned": a.pinned, "issuer_name": a.issuer_name,
                 "issuer_designation": a.issuer_designation, "published_at": a.published_at} for a in items]
