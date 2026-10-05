"""A student's academic record: every published result, term by term, with GPA and CGPA."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.grade_scale import GradeScaleService, gpa
from app.academics.grading_service import GradingService
from app.academics.models import (
    Course,
    CourseOffering,
    Enrollment,
    EnrollmentResult,
    EnrollmentStatus,
    ResultSheet,
    ResultSheetStatus,
)
from app.core.exceptions import ResourceNotFoundError
from app.programs.models import Program
from app.students.models import Student, StudentGuardian


class TranscriptService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.grading = GradingService(session)

    async def _heal(self, student_id: uuid.UUID) -> None:
        """Results published before grades existed have no frozen grade yet: work it out now, once, from the marks and the stored outcome."""
        stmt = (select(Enrollment).join(ResultSheet, ResultSheet.course_offering_id == Enrollment.course_offering_id)
                .outerjoin(EnrollmentResult, EnrollmentResult.enrollment_id == Enrollment.id)
                .where(Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.COMPLETED, Enrollment.outcome.is_not(None),
                       ResultSheet.status == ResultSheetStatus.PUBLISHED, EnrollmentResult.id.is_(None)))
        for enrollment in (await self.session.execute(stmt)).scalars().all():
            ev = await self.grading.evaluate(enrollment)
            if ev is not None:
                await self.grading._store_result(enrollment, ev, outcome=enrollment.outcome)

    async def build(self, student_id: uuid.UUID) -> dict:
        student = await self.session.get(Student, student_id)
        if student is None:
            raise ResourceNotFoundError("Student not found.")
        await self._heal(student_id)
        program = await self.session.get(Program, student.program_id) if student.program_id else None
        guardian = (await self.session.execute(select(StudentGuardian.name).where(StudentGuardian.student_id == student_id).order_by(StudentGuardian.is_primary.desc()).limit(1))).scalar_one_or_none()

        rows = (await self.session.execute(
            select(EnrollmentResult, Enrollment, CourseOffering, Course)
            .join(Enrollment, Enrollment.id == EnrollmentResult.enrollment_id).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id).join(ResultSheet, ResultSheet.course_offering_id == CourseOffering.id)
            .where(Enrollment.student_id == student_id, ResultSheet.status == ResultSheetStatus.PUBLISHED)
        )).all()

        # terms in the order they happened
        terms: dict[str, dict] = {}
        for res, enr, off, course in rows:
            t = terms.setdefault(off.term_label, {"label": off.term_label, "start_date": off.term_start_date, "items": []})
            if off.term_start_date and (t["start_date"] is None or off.term_start_date < t["start_date"]):
                t["start_date"] = off.term_start_date
            t["items"].append((res, enr, off, course))
        ordered = sorted(terms.values(), key=lambda t: (t["start_date"] or date.max, t["label"]))
        position = {t["label"]: i for i, t in enumerate(ordered)}

        # attempts at the same course: a retake REPLACES the earlier attempt in the CGPA (the earlier one still shows, marked as replaced)
        by_course: dict[uuid.UUID, list] = {}
        for item in rows:
            by_course.setdefault(item[3].id, []).append(item)
        attempt, last_attempt = {}, {}
        for course_id, items in by_course.items():
            items.sort(key=lambda it: (position[it[2].term_label], it[1].enrolled_at))
            for n, it in enumerate(items, start=1):
                attempt[it[0].enrollment_id] = n
            last_attempt[course_id] = items[-1][0].enrollment_id

        latest: dict[uuid.UUID, tuple[float, int, bool]] = {}      # course -> (grade points, credit hours, passed) as it stands after each term
        out_terms = []
        for t in ordered:
            courses, pairs = [], []
            for res, enr, off, course in sorted(t["items"], key=lambda it: it[3].code):
                gp, ch = float(res.grade_points), int(res.credit_hours)
                pairs.append((gp, ch))
                latest[course.id] = (gp, ch, res.outcome == "PASSED")
                courses.append({"enrollment_id": res.enrollment_id, "course_code": course.code, "course_title": course.title, "credit_hours": ch,
                                "total_obtained": float(res.total_obtained), "total_max": float(res.total_max), "percentage": float(res.percentage),
                                "letter_grade": res.letter_grade, "grade_points": gp, "quality_points": round(gp * ch, 2), "outcome": res.outcome,
                                "failed_for_attendance": res.failed_for_attendance, "attempt": attempt[res.enrollment_id],
                                "replaced": last_attempt[course.id] != res.enrollment_id})
            out_terms.append({"label": t["label"], "start_date": t["start_date"], "courses": courses,
                              "credits_attempted": sum(c["credit_hours"] for c in courses), "credits_earned": sum(c["credit_hours"] for c in courses if c["outcome"] == "PASSED"),
                              "gpa": gpa(pairs), "cgpa_after": gpa([(g, c) for g, c, _ in latest.values()]),
                              "credits_earned_cumulative": sum(c for _, c, ok in latest.values() if ok)})
        scale = [{"letter": e.letter, "min_percent": float(e.min_percent), "grade_points": float(e.grade_points), "is_passing": e.is_passing} for e in await GradeScaleService(self.session).entries()]
        return {
            "student": {"id": student.id, "name": f"{student.first_name} {student.last_name}", "roll_number": student.roll_number, "program_name": program.name if program else None,
                        "program_code": program.code if program else None, "admission_session": student.admission_session, "current_semester": student.current_semester, "guardian_name": guardian},
            "terms": out_terms,
            "summary": {"cgpa": gpa([(g, c) for g, c, _ in latest.values()]), "credits_attempted": sum(c for _, c, _ in latest.values()),
                        "credits_earned": sum(c for _, c, ok in latest.values() if ok), "terms": len(out_terms), "courses_to_clear": sum(1 for _, _, ok in latest.values() if not ok)},
            "scale": scale, "issued_at": datetime.now(timezone.utc),
        }