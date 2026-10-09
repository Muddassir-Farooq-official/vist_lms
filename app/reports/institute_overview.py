"""Institute overview for the Principal / Director (and the administration): one read-only page
answering "how is the institute doing right now?" across students, attendance, results and fees.

Everything is computed with aggregate queries over the OPEN academic terms, so the page stays
fast however many students there are.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering, Enrollment, EnrollmentStatus, ResultSheet
from app.academics.terms import AcademicTerm, TermStatus
from app.attendance.models import AttendanceRecord, AttendanceStatus
from app.core.clock import institute_now, institute_today
from app.core.config import settings
from app.fees.assignment_service import FeeStatusCalculator
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, LedgerEntryType
from app.programs.models import Program
from app.students.models import Student, StudentStatus


class InstituteOverviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(self) -> dict:
        s = self.session
        terms = [t.label for t in (await s.execute(select(AcademicTerm).where(AcademicTerm.status == TermStatus.OPEN))).scalars()]
        term_filter = CourseOffering.term_label.in_(terms) if terms else CourseOffering.term_label.is_(None)
        live = and_(term_filter, CourseOffering.section_label != "IMPORTED")

        # ---- students
        by_status = {(k.value if hasattr(k, "value") else k): n for k, n in (await s.execute(
            select(Student.status, func.count()).group_by(Student.status))).all()}
        by_program = [{"program": code, "students": n} for code, n in (await s.execute(
            select(Program.code, func.count(Student.id)).join(Student, Student.program_id == Program.id)
            .where(Student.status == StudentStatus.ACTIVE).group_by(Program.code).order_by(Program.code))).all()]

        # ---- attendance in open terms: one row per (student, offering) with present/total
        per = (select(AttendanceRecord.student_id, ClassSession.course_offering_id,
                      func.count().label("total"),
                      func.sum(case((AttendanceRecord.status == AttendanceStatus.PRESENT, 1), else_=0)).label("present"))
               .join(ClassSession, ClassSession.id == AttendanceRecord.class_session_id)
               .join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
               .where(live).group_by(AttendanceRecord.student_id, ClassSession.course_offering_id)).subquery()
        rows = (await s.execute(select(per.c.present, per.c.total))).all()
        pcts = [100.0 * p / t for p, t in rows if t]
        threshold = settings.attendance_eligibility_threshold_percent
        cutoff = institute_now() - timedelta(minutes=settings.attendance_marking_window_minutes)
        unmarked = (await s.execute(
            select(func.count()).select_from(ClassSession).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
            .where(live, ClassSession.attendance_taken_at.is_(None), ClassSession.status == ClassSessionStatus.SCHEDULED,
                   ClassSession.session_date < cutoff.date()))).scalar_one()

        # ---- courses and results in open terms
        offerings = (await s.execute(select(func.count()).select_from(CourseOffering).where(live))).scalar_one()
        no_teacher = (await s.execute(select(func.count()).select_from(CourseOffering).where(live, CourseOffering.assigned_teacher_id.is_(None)))).scalar_one()
        enrolled = (await s.execute(select(func.count()).select_from(Enrollment).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                    .where(live, Enrollment.status == EnrollmentStatus.ENROLLED))).scalar_one()
        sheets = {(k.value if hasattr(k, "value") else k): n for k, n in (await s.execute(
            select(ResultSheet.status, func.count()).join(CourseOffering, CourseOffering.id == ResultSheet.course_offering_id)
            .where(live).group_by(ResultSheet.status))).all()}

        # ---- fees on active assignments
        entries = (await s.execute(select(FeeLedgerEntry).join(FeeAssignment, FeeAssignment.id == FeeLedgerEntry.fee_assignment_id)
                                   .where(FeeAssignment.status == AssignmentStatus.ACTIVE))).scalars().all()
        by_assignment: dict = {}
        for e in entries:
            by_assignment.setdefault(e.fee_assignment_id, []).append(e)
        charged = concession = outstanding = overdue = 0.0
        for rows_ in by_assignment.values():
            for r in FeeStatusCalculator.installments(rows_, None):
                charged += r["charged"]
                concession += r["concession"]
                outstanding += r["remaining"]
                if r["status"] == "OVERDUE":
                    overdue += r["remaining"]
        month_start = institute_today().replace(day=1)
        collected_month = sum(float(e.amount) for e in entries if e.entry_type == LedgerEntryType.CREDIT
                              and (e.paid_on or e.created_at.date()) >= month_start)
        collected_month -= sum(float(e.amount) for e in entries if FeeStatusCalculator._is_reversal(e)     # payments reversed by staff
                               and e.created_at.date() >= month_start)

        return {
            "open_terms": terms,
            "students": {"by_status": by_status, "active_by_program": by_program},
            "teaching": {"course_offerings": offerings, "without_teacher": no_teacher, "enrollments": enrolled},
            "attendance": {"average_percent": round(sum(pcts) / len(pcts), 1) if pcts else None,
                           "student_courses_below_minimum": sum(1 for p in pcts if p < threshold), "minimum_percent": threshold,
                           "classes_left_unmarked": unmarked},
            "results": {"sheets_by_status": sheets, "courses_without_a_sheet": max(offerings - sum(sheets.values()), 0)},
            "fees": {"charged": round(charged, 2), "discounts": round(concession, 2), "outstanding": round(outstanding, 2),
                     "overdue": round(overdue, 2), "collected_this_month": round(collected_month, 2),
                     "collection_rate_percent": round(100 * (charged - concession - outstanding) / (charged - concession), 1)
                     if charged - concession > 0 else None},
        }