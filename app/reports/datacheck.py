"""Data checker: gaps and mistakes in the records, each with a link to fix it.

Only reads; it never changes a record. Every check returns how many records it found and up to LIMIT of them, so a big
institute's page stays quick. Areas: students, staff, academics, fees.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permissions, Roles
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_any_role, require_permission

LIMIT = 200


def _blank(col):
    return or_(col.is_(None), func.trim(col) == "")


def _check(key, area, title, why, rows, *, severity="medium", fix=None):
    return {"key": key, "area": area, "title": title, "why": why, "severity": severity, "count": len(rows), "rows": rows[:LIMIT], "fix": fix}


def _srow(s, detail="") -> dict:
    return {"id": s.id, "label": f"{s.first_name} {s.last_name}", "code": s.roll_number, "detail": detail,
            "link": f"/dashboard/admin/students?q={s.roll_number}"}


def _erow(e, detail="") -> dict:
    return {"id": e.id, "label": f"{e.first_name} {e.last_name}", "code": e.employee_code, "detail": detail,
            "link": f"/dashboard/admin/employees?q={e.employee_code}"}


class DataChecker:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run(self) -> dict:
        checks = await self.students() + await self.staff() + await self.academics() + await self.fees()
        areas = {}
        for c in checks:
            a = areas.setdefault(c["area"], {"area": c["area"], "checks": 0, "problems": 0})
            a["checks"] += 1
            a["problems"] += c["count"]
        return {"checks": checks, "areas": list(areas.values()), "problems": sum(c["count"] for c in checks), "limit": LIMIT}

    # ------------------------------------------------------------------ students
    async def _active_students(self):
        from app.students.models import Student, StudentStatus

        return list((await self.session.execute(select(Student).where(Student.status == StudentStatus.ACTIVE).order_by(Student.roll_number))).scalars())

    async def students(self) -> list[dict]:
        from app.sms.service import normalize_pk_mobile
        from app.students.models import Student, StudentGuardian

        active = await self._active_students()
        no_cnic = [_srow(s) for s in active if not (s.cnic or "").strip()]
        no_phone = [_srow(s) for s in active if not (s.contact_no or "").strip()]
        bad_phone = [_srow(s, s.contact_no) for s in active if (s.contact_no or "").strip() and not normalize_pk_mobile(s.contact_no)]
        no_class = [_srow(s, "no program" if s.program_id is None else "no semester") for s in active if s.program_id is None or not s.current_semester]
        g = defaultdict(list)
        for sid, phone in (await self.session.execute(select(StudentGuardian.student_id, StudentGuardian.contact_no))).all():
            g[sid].append(phone)
        no_parent = [_srow(s, "no number" if not any((p or "").strip() for p in g.get(s.id, [])) else "not a mobile number: " + ", ".join(p for p in g[s.id] if p))
                     for s in active if not any(normalize_pk_mobile(p) for p in g.get(s.id, []))]
        # the same name and date of birth (the database already refuses the same CNIC twice)
        name = func.lower(func.trim(Student.first_name) + " " + func.trim(Student.last_name))
        twins = (await self.session.execute(select(name.label("n"), Student.date_of_birth).where(Student.date_of_birth.is_not(None))
                                            .group_by("n", Student.date_of_birth).having(func.count() > 1))).all()
        dup_name = []
        for n, dob in twins:
            for s in (await self.session.execute(select(Student).where(name == n, Student.date_of_birth == dob).order_by(Student.roll_number))).scalars():
                dup_name.append(_srow(s, f"born {dob:%d %b %Y} ({s.status.value.lower()})"))
        return [
            _check("s_no_cnic", "Students", "No CNIC / B-form", "Needed on certificates, university forms and to spot duplicates.", no_cnic),
            _check("s_no_phone", "Students", "No mobile number", "SMS and WhatsApp messages cannot reach them.", no_phone),
            _check("s_bad_phone", "Students", "Mobile number that is not a mobile number", "SMS to it fails; fix the number.", bad_phone, severity="high"),
            _check("s_no_parent_phone", "Students", "No parent / guardian mobile number", "Fee reminders and absence messages cannot reach the family.", no_parent),
            _check("s_no_class", "Students", "No program or semester", "They are missing from class lists, fee structures and promotions.", no_class, severity="high"),
            _check("s_dup_name", "Students", "Same name and date of birth", "May be the same person entered twice (or real twins).", dup_name, severity="low"),
        ]

    # ------------------------------------------------------------------ staff
    async def staff(self) -> list[dict]:
        from app.academics.models import CourseOffering
        from app.academics.terms import AcademicTerm, TermStatus
        from app.employees.models import Employee, EmployeeSalaryHistory, EmployeeStatus, JobRole
        from app.sms.service import normalize_pk_mobile

        people = (await self.session.execute(select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id)
                                             .where(Employee.status.in_((EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE)))
                                             .order_by(Employee.employee_code))).all()
        paid = set((await self.session.execute(select(EmployeeSalaryHistory.employee_id).distinct())).scalars())
        open_labels = [t.lower() for t in (await self.session.execute(select(AcademicTerm.label).where(AcademicTerm.status == TermStatus.OPEN))).scalars()]
        teaching = set((await self.session.execute(select(CourseOffering.assigned_teacher_id).where(
            CourseOffering.assigned_teacher_id.is_not(None), func.lower(CourseOffering.term_label).in_(open_labels or [""])))).scalars())
        is_teacher = lambda title: any(w in (title or "").lower() for w in ("teacher", "lecturer", "professor", "instructor", "tutor", "demonstrator"))  # noqa: E731
        return [
            _check("e_no_cnic", "Staff", "No CNIC", "Needed for payroll, tax and the bank sheet.", [_erow(e) for e, _ in people if not (e.cnic or "").strip()]),
            _check("e_bad_phone", "Staff", "No mobile number, or not a mobile number", "Duty and pay messages cannot reach them.",
                   [_erow(e, e.contact_no or "none") for e, _ in people if not normalize_pk_mobile(e.contact_no)]),
            _check("e_no_salary", "Staff", "No salary set", "They are left out of payroll.", [_erow(e, t) for e, t in people if e.id not in paid], severity="high"),
            _check("e_no_login", "Staff", "No login", "They cannot mark attendance, see payslips or use the portal.", [_erow(e, t) for e, t in people if e.user_id is None], severity="low"),
            _check("e_teacher_no_subject", "Staff", "Teacher with no subject this term", "A teacher job with no subject in any open term.",
                   [_erow(e, t) for e, t in people if is_teacher(t) and e.id not in teaching and e.status == EmployeeStatus.ACTIVE], severity="low"),
        ]

    # ------------------------------------------------------------------ academics
    async def academics(self) -> list[dict]:
        from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus
        from app.academics.terms import AcademicTerm, TermStatus
        from app.programs.models import Program
        from app.students.models import Student, StudentStatus

        terms = (await self.session.execute(select(AcademicTerm).where(AcademicTerm.status == TermStatus.OPEN).order_by(AcademicTerm.label))).scalars().all()
        labels = [t.label.lower() for t in terms]
        no_teacher = []
        if labels:
            for o, c in (await self.session.execute(select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id).where(
                    CourseOffering.assigned_teacher_id.is_(None), func.lower(CourseOffering.term_label).in_(labels)).order_by(CourseOffering.term_label, Course.code))).all():
                no_teacher.append({"id": o.id, "label": f"{c.code} — {c.title}", "code": f"{o.term_label} · {o.section_label}", "detail": "",
                                   "link": f"/dashboard/admin/courses/offering/{o.id}"})
        enrolled = select(Enrollment.student_id).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id).where(
            Enrollment.student_id == Student.id, Enrollment.status == EnrollmentStatus.ENROLLED, func.lower(CourseOffering.term_label).in_(labels or [""])).exists()
        nothing = [_srow(s) for s in (await self.session.execute(select(Student).where(Student.status == StudentStatus.ACTIVE, ~enrolled)
                                                                   .order_by(Student.roll_number))).scalars()] if labels else []
        no_dates = [{"id": t.id, "label": t.label, "code": "", "detail": "no start date" if not t.start_date else "no end date",
                     "link": "/dashboard/admin/courses"} for t in terms if not t.start_date or not t.end_date]
        no_reg = [_srow(s, p.code) for s, p in (await self.session.execute(select(Student, Program).join(Program, Program.id == Student.program_id).where(
            Student.status == StudentStatus.ACTIVE, Program.exam_body == "AFFILIATED", _blank(Student.reg_number)).order_by(Student.roll_number))).all()]
        for r in no_reg:
            r["link"] = "/dashboard/admin/reg-numbers"
        return [
            _check("a_no_teacher", "Academics", "Subject with no teacher (open terms)", "Nobody can take its attendance or enter its marks.", no_teacher, severity="high"),
            _check("a_not_enrolled", "Academics", "Active student enrolled in nothing this term", "They have no classes, attendance or results this term.", nothing),
            _check("a_term_dates", "Academics", "Open term without dates", "Attendance %, calendars and the rollover need the term's dates.", no_dates),
            _check("a_no_reg_no", "Academics", "Affiliated program student without a university Reg No", "The university needs it on award lists and results.", no_reg),
        ]

    # ------------------------------------------------------------------ fees
    async def fees(self) -> list[dict]:
        from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, LedgerEntryType as T, PayerType
        from app.students.models import Student, StudentStatus

        has_fee = select(FeeAssignment.id).where(FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.payer_id == Student.id,
                                                 FeeAssignment.status == AssignmentStatus.ACTIVE).exists()
        no_fee = [_srow(s) for s in (await self.session.execute(select(Student).where(Student.status == StudentStatus.ACTIVE, ~has_fee)
                                                                  .order_by(Student.roll_number))).scalars()]
        for r in no_fee:
            r["link"] = "/dashboard/admin/fees"
        sign = {T.DEBIT: 1, T.CONCESSION: -1, T.CREDIT: -1, T.CREDIT_IN: -1, T.REFUND: 1, T.CREDIT_OUT: 1}
        bal: dict[uuid.UUID, float] = defaultdict(float)
        rows = (await self.session.execute(select(FeeAssignment.payer_id, FeeLedgerEntry.entry_type, func.sum(FeeLedgerEntry.amount))
                                           .join(FeeAssignment, FeeAssignment.id == FeeLedgerEntry.fee_assignment_id)
                                           .where(FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.status != AssignmentStatus.CANCELLED)
                                           .group_by(FeeAssignment.payer_id, FeeLedgerEntry.entry_type))).all()
        for pid, kind, amount in rows:
            bal[pid] += sign.get(kind, 0) * float(amount or 0)
        over = {pid: -b for pid, b in bal.items() if b < -0.5}
        overpaid = []
        if over:
            for s in (await self.session.execute(select(Student).where(Student.id.in_(list(over))).order_by(Student.roll_number))).scalars():
                overpaid.append({**_srow(s, f"Rs. {over[s.id]:,.0f} paid more than charged"), "link": "/dashboard/admin/fees"})
        return [
            _check("f_no_fee", "Fees", "Active student with no fee", "No challan is made for them, so nothing is collected.", no_fee, severity="high"),
            _check("f_overpaid", "Fees", "Paid more than charged (advance or mistake)", "Move it to the next fee, refund it, or correct a wrong entry.", overpaid, severity="low"),
        ]


router = APIRouter(prefix="/data-check", tags=["reports"])


@router.get("", summary="Gaps and mistakes in the records, with links to fix them (administrators)")
async def data_check(current_user: CurrentUser = Depends(require_permission(Permissions.SYSTEM_HEALTH)), db=Depends(get_db)) -> dict:
    return await DataChecker(db).run()

