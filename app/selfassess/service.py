"""Self-assessments: practice quizzes, tests and assignments done on PAPER in class; the portal only keeps their marks.

A teacher (or an administrator) adds an assessment to one of their courses (title, kind, date, total marks, topic) and
enters or uploads each student's marks. Students see their own marks and the class average, parents see their children's,
and the teacher sees the class at a glance (average, highest, lowest, absent, who scored under half).

Completely separate from exams and results: own tables, never read by grading, result sheets, GPA or transcripts.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.audit.service import AuditService
from app.core.database import Base, TimestampMixin, UUIDPKMixin
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError

KINDS = {"QUIZ": "Quiz", "TEST": "Class test", "ASSIGNMENT": "Assignment", "PRESENTATION": "Presentation", "OTHER": "Other"}


class SelfAssessment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "self_assessments"

    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(15), nullable=False, default="QUIZ")
    held_on: Mapped[date] = mapped_column(Date, nullable=False)
    max_marks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class SelfAssessmentMark(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "self_assessment_marks"
    __table_args__ = (UniqueConstraint("self_assessment_id", "enrollment_id", name="uq_self_assessment_mark"),)

    self_assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("self_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False)
    obtained: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    absent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    remark: Mapped[str | None] = mapped_column(String(200), nullable=True)
    entered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _f(v):
    return float(v) if v is not None else None


class SelfAssessmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ who may do what
    async def check_teacher(self, offering_id: uuid.UUID, user) -> None:
        from app.academics.models import CourseOffering
        from app.auth.permissions import Permissions
        from app.employees.repository import EmployeeRepository

        offering = await self.session.get(CourseOffering, offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course not found.")
        if Permissions.ENROLLMENT_MANAGE in user.permissions or "ADMIN" in user.roles:
            return
        emp = await EmployeeRepository(self.session).get_by_user_id(user.id)
        if emp is None or offering.assigned_teacher_id != emp.id:
            raise AuthorizationError("Only the course's teacher (or an administrator) can manage its self-assessments.")

    async def _enrolled(self, offering_id: uuid.UUID) -> list[tuple]:
        from app.academics.models import Enrollment, EnrollmentStatus
        from app.students.models import Student

        return list((await self.session.execute(
            select(Enrollment, Student).join(Student, Student.id == Enrollment.student_id)
            .where(Enrollment.course_offering_id == offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN)
            .order_by(Student.roll_number))).all())

    async def get(self, assessment_id: uuid.UUID) -> SelfAssessment:
        a = await self.session.get(SelfAssessment, assessment_id)
        if a is None:
            raise ResourceNotFoundError("Self-assessment not found.")
        return a

    # ------------------------------------------------------------------ teacher
    async def my_courses(self, user) -> list[dict]:
        from app.academics.models import Course, CourseOffering
        from app.employees.repository import EmployeeRepository

        emp = await EmployeeRepository(self.session).get_by_user_id(user.id)
        if emp is None:
            return []
        rows = (await self.session.execute(select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id)
                                           .where(CourseOffering.assigned_teacher_id == emp.id)
                                           .order_by(CourseOffering.term_end_date.desc().nulls_last(), Course.code))).all()
        return [{"id": o.id, "label": f"{c.code} {o.section_label or ''}".strip(), "title": c.title, "term_label": o.term_label} for o, c in rows]

    def _check_values(self, title: str, kind: str, max_marks: float) -> None:
        if not title.strip():
            raise BusinessRuleViolationError("Give the assessment a title, for example Quiz 1.")
        if kind not in KINDS:
            raise BusinessRuleViolationError("Choose the kind: quiz, class test, assignment, presentation or other.")
        if not 0 < max_marks <= 1000:
            raise BusinessRuleViolationError("Total marks must be more than 0 (and at most 1000).")

    async def create(self, offering_id: uuid.UUID, *, title: str, kind: str, held_on: date, max_marks: float, topic: str | None, user) -> SelfAssessment:
        await self.check_teacher(offering_id, user)
        self._check_values(title, kind, max_marks)
        a = SelfAssessment(course_offering_id=offering_id, title=" ".join(title.split()), kind=kind, held_on=held_on, max_marks=max_marks,
                           topic=(topic or "").strip() or None, created_by=user.id)
        self.session.add(a)
        await self.session.flush()
        await self.audit.log(actor_user_id=user.id, action="self_assessment.created", entity_type="self_assessment", entity_id=a.id,
                             after={"title": a.title, "kind": kind, "max_marks": max_marks, "held_on": str(held_on)})
        return a

    async def update(self, assessment_id: uuid.UUID, *, title: str, kind: str, held_on: date, max_marks: float, topic: str | None, user) -> SelfAssessment:
        a = await self.get(assessment_id)
        await self.check_teacher(a.course_offering_id, user)
        self._check_values(title, kind, max_marks)
        over = (await self.session.execute(select(SelfAssessmentMark.id).where(SelfAssessmentMark.self_assessment_id == a.id,
                                                                              SelfAssessmentMark.obtained > max_marks))).first()
        if over:
            raise BusinessRuleViolationError("Some students already have more marks than the new total. Correct those marks first.")
        before = {"title": a.title, "kind": a.kind, "max_marks": _f(a.max_marks), "held_on": str(a.held_on)}
        a.title, a.kind, a.held_on, a.max_marks, a.topic = " ".join(title.split()), kind, held_on, max_marks, (topic or "").strip() or None
        await self.session.flush()
        await self.audit.log(actor_user_id=user.id, action="self_assessment.changed", entity_type="self_assessment", entity_id=a.id, before=before,
                             after={"title": a.title, "kind": kind, "max_marks": max_marks, "held_on": str(held_on)})
        return a

    async def delete(self, assessment_id: uuid.UUID, *, user) -> None:
        a = await self.get(assessment_id)
        await self.check_teacher(a.course_offering_id, user)
        await self.audit.log(actor_user_id=user.id, action="self_assessment.deleted", entity_type="self_assessment", entity_id=a.id,
                             before={"title": a.title, "max_marks": _f(a.max_marks)})
        await self.session.delete(a)

    async def course_view(self, offering_id: uuid.UUID, user) -> dict:
        """The teacher's grid: every assessment of the course with each student's mark, and the class summary."""
        await self.check_teacher(offering_id, user)
        items = list((await self.session.execute(select(SelfAssessment).where(SelfAssessment.course_offering_id == offering_id)
                                                 .order_by(SelfAssessment.held_on, SelfAssessment.created_at))).scalars())
        students = await self._enrolled(offering_id)
        marks = {}
        if items:
            for m in (await self.session.execute(select(SelfAssessmentMark).where(SelfAssessmentMark.self_assessment_id.in_([i.id for i in items])))).scalars():
                marks[(m.self_assessment_id, m.enrollment_id)] = m
        out_items = []
        for i in items:
            got = [marks[(i.id, e.id)] for e, _s in students if (i.id, e.id) in marks]
            vals = [float(m.obtained) for m in got if m.obtained is not None and not m.absent]
            top = float(i.max_marks)
            out_items.append({"id": i.id, "title": i.title, "kind": i.kind, "kind_label": KINDS.get(i.kind, i.kind), "held_on": i.held_on, "max_marks": top,
                              "topic": i.topic, "entered": len(vals), "absent": sum(1 for m in got if m.absent),
                              "average": round(sum(vals) / len(vals), 2) if vals else None, "highest": max(vals) if vals else None,
                              "lowest": min(vals) if vals else None, "under_half": sum(1 for v in vals if v < top / 2)})
        rows = []
        for e, s in students:
            cells, pct = [], []
            for i in items:
                m = marks.get((i.id, e.id))
                cells.append({"obtained": _f(m.obtained) if m else None, "absent": bool(m and m.absent), "remark": m.remark if m else None})
                if m and m.obtained is not None and not m.absent:
                    pct.append(float(m.obtained) / float(i.max_marks) * 100)
            rows.append({"enrollment_id": e.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}", "marks": cells,
                         "average_percent": round(sum(pct) / len(pct), 1) if pct else None})
        return {"items": out_items, "rows": rows}

    async def save_marks(self, assessment_id: uuid.UUID, entries: list[dict], *, user) -> dict:
        """entries: [{enrollment_id, obtained (None = clear), absent, remark}]."""
        a = await self.get(assessment_id)
        await self.check_teacher(a.course_offering_id, user)
        enrolled = {e.id for e, _s in await self._enrolled(a.course_offering_id)}
        top = float(a.max_marks)
        now = datetime.now(timezone.utc)
        saved = 0
        for x in entries:
            eid = uuid.UUID(str(x["enrollment_id"]))
            if eid not in enrolled:
                raise BusinessRuleViolationError("A student in the list is not enrolled in this course.")
            obtained, absent = x.get("obtained"), bool(x.get("absent"))
            if obtained is not None and not 0 <= float(obtained) <= top:
                raise BusinessRuleViolationError(f"Marks must be between 0 and {top:g}.")
            row = (await self.session.execute(select(SelfAssessmentMark).where(SelfAssessmentMark.self_assessment_id == a.id,
                                                                              SelfAssessmentMark.enrollment_id == eid))).scalar_one_or_none()
            if obtained is None and not absent:
                if row is not None:
                    await self.session.delete(row)
                    saved += 1
                continue
            if row is None:
                row = SelfAssessmentMark(self_assessment_id=a.id, enrollment_id=eid)
                self.session.add(row)
            row.obtained, row.absent = (None if absent else float(obtained)), absent
            row.remark = (x.get("remark") or "").strip()[:200] or None
            row.entered_by, row.entered_at = user.id, now
            saved += 1
        await self.session.flush()
        await self.audit.log(actor_user_id=user.id, action="self_assessment.marks_saved", entity_type="self_assessment", entity_id=a.id,
                             metadata={"changed": saved})
        return {"saved": saved}

    async def upload_csv(self, assessment_id: uuid.UUID, text: str, *, user) -> dict:
        """CSV with a roll_number column and a marks column ("A" or "absent" = absent). Every row is checked first."""
        a = await self.get(assessment_id)
        await self.check_teacher(a.course_offering_id, user)
        by_roll = {(s.roll_number or "").strip().lower(): e.id for e, s in await self._enrolled(a.course_offering_id)}
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        cols = {c.strip().lower().replace(" ", "_"): c for c in (reader.fieldnames or [])}
        roll_col = cols.get("roll_number") or cols.get("roll_no") or cols.get("roll")
        mark_col = cols.get("marks") or cols.get("obtained") or cols.get("obtained_marks")
        if not roll_col or not mark_col:
            raise BusinessRuleViolationError("The file needs a roll_number column and a marks column.")
        entries, problems = [], []
        for n, r in enumerate(reader, start=2):
            roll, raw = (r.get(roll_col) or "").strip(), (r.get(mark_col) or "").strip()
            if not roll and not raw:
                continue
            eid = by_roll.get(roll.lower())
            if eid is None:
                problems.append(f"row {n}: roll number {roll} is not in this course")
                continue
            if raw.lower() in ("a", "ab", "absent"):
                entries.append({"enrollment_id": eid, "obtained": None, "absent": True})
                continue
            try:
                v = float(raw)
            except ValueError:
                problems.append(f"row {n}: '{raw}' is not a number")
                continue
            if not 0 <= v <= float(a.max_marks):
                problems.append(f"row {n}: {v:g} is outside 0-{float(a.max_marks):g}")
                continue
            entries.append({"enrollment_id": eid, "obtained": v, "absent": False})
        if problems:
            raise BusinessRuleViolationError("Nothing was saved. Fix these rows: " + "; ".join(problems[:15]) + ("; ..." if len(problems) > 15 else ""))
        return await self.save_marks(assessment_id, entries, user=user)

    # ------------------------------------------------------------------ student / parent
    async def for_student(self, student_id: uuid.UUID) -> list[dict]:
        """Per course: each assessment with the student's mark and the class average."""
        from app.academics.models import Course, CourseOffering, Enrollment

        rows = (await self.session.execute(
            select(SelfAssessment, Enrollment, Course, CourseOffering)
            .join(Enrollment, Enrollment.course_offering_id == SelfAssessment.course_offering_id)
            .join(CourseOffering, CourseOffering.id == SelfAssessment.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .where(Enrollment.student_id == student_id)
            .order_by(Course.code, SelfAssessment.held_on))).all()
        courses: dict = {}
        for a, e, c, o in rows:
            m = (await self.session.execute(select(SelfAssessmentMark).where(SelfAssessmentMark.self_assessment_id == a.id,
                                                                            SelfAssessmentMark.enrollment_id == e.id))).scalar_one_or_none()
            vals = [float(v) for v in (await self.session.execute(select(SelfAssessmentMark.obtained).where(
                SelfAssessmentMark.self_assessment_id == a.id, SelfAssessmentMark.obtained.is_not(None)))).scalars()]
            course = courses.setdefault(o.id, {"course_offering_id": o.id, "code": c.code, "title": c.title, "term_label": o.term_label, "items": []})
            course["items"].append({"title": a.title, "kind_label": KINDS.get(a.kind, a.kind), "held_on": a.held_on, "max_marks": float(a.max_marks),
                                    "topic": a.topic, "obtained": _f(m.obtained) if m else None, "absent": bool(m and m.absent),
                                    "remark": m.remark if m else None, "class_average": round(sum(vals) / len(vals), 2) if vals else None})
        for course in courses.values():
            pct = [i["obtained"] / i["max_marks"] * 100 for i in course["items"] if i["obtained"] is not None]
            course["average_percent"] = round(sum(pct) / len(pct), 1) if pct else None
        return list(courses.values())
