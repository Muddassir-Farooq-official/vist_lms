"""Semester rollover wizard: the end of one session and the start of the next, in four checked steps.

For one academic session ("Fall 2026" = every batch term running at the same time):

  1. close   -- each batch term: what is unfinished (attendance not locked, results not published, enrollments still
                open), then End Semester (enrollments completed) and the term closed. Unfinished courses need a reason.
  2. promote -- every active student of each batch moves to the next semester, except those the administrator holds
                back; failed subjects stay listed as carry-over (re-sit them as supplementary exams). A batch at its
                program's last semester is not promoted (that is a graduation decision). Undo within 7 days puts
                back every student whose semester was not changed since. Fees already charged by the promotion are
                never removed by an undo (financial records are kept); they are listed for the accounts office.
  3. next    -- the next session's batch terms, their course sections from the curriculum and the promoted students
                enrolled (app/academics/sessions.py start_next), with the same teachers where they still work here.
  4. fees    -- each batch's active fee structures copied to the next batch term as DRAFTS (never activated here).

Every step shows a preview first and is all or nothing.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import DateTime, ForeignKey, String, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

UNDO_DAYS = 7


class RolloverRun(Base):
    """One promotion done by the wizard, kept so it can be undone (within 7 days) and so it stays on record."""
    __tablename__ = "rollover_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("academic_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="PROMOTE")
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)             # {"students": [{"id", "from", "to", "fees": [assignment ids]}], "held": [...]}
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    undone_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RolloverService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _session(self, session_id: uuid.UUID):
        from app.academics.terms import AcademicSession

        s = await self.session.get(AcademicSession, session_id)
        if s is None:
            raise ResourceNotFoundError("Session not found.")
        return s

    async def _terms(self, session_id: uuid.UUID) -> list:
        from app.academics.terms import AcademicTerm

        return list((await self.session.execute(select(AcademicTerm).where(AcademicTerm.session_id == session_id)
                                                .order_by(func.lower(AcademicTerm.label)))).scalars())

    # ------------------------------------------------------------------ overview
    async def overview(self, session_id: uuid.UUID) -> dict:
        from app.academics.terms import TermStatus

        s = await self._session(session_id)
        terms = await self._terms(session_id)
        runs = (await self.session.execute(select(RolloverRun).where(RolloverRun.session_id == session_id).order_by(RolloverRun.created_at.desc()))).scalars().all()
        return {"session": {"id": s.id, "name": s.name, "start_date": s.start_date, "end_date": s.end_date},
                "terms": len(terms), "closed": sum(t.status == TermStatus.CLOSED for t in terms),
                "promotions": [self._run_dict(r) for r in runs]}

    def _run_dict(self, r: RolloverRun) -> dict:
        now = datetime.now(timezone.utc)
        students = r.data.get("students", [])
        return {"id": r.id, "created_at": r.created_at, "undone_at": r.undone_at, "students": len(students), "held": len(r.data.get("held", [])),
                "can_undo": r.undone_at is None and r.created_at is not None and now - r.created_at <= timedelta(days=UNDO_DAYS),
                "undo_until": (r.created_at + timedelta(days=UNDO_DAYS)) if r.created_at else None}

    # ------------------------------------------------------------------ 1. close
    async def close_preview(self, session_id: uuid.UUID) -> dict:
        from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus
        from app.academics.service import EnrollmentService
        from app.academics.terms import TermStatus

        await self._session(session_id)
        rows = []
        for t in await self._terms(session_id):
            offerings = (await self.session.execute(select(CourseOffering).where(func.lower(CourseOffering.term_label) == t.label.lower()))).scalars().all()
            blockers = await EnrollmentService(self.session)._end_semester_blockers(offerings) if offerings else []
            open_n = 0
            if offerings:
                open_n = int((await self.session.execute(select(func.count()).select_from(Enrollment).where(
                    Enrollment.course_offering_id.in_([o.id for o in offerings]), Enrollment.status == EnrollmentStatus.ENROLLED))).scalar_one())
            rows.append({"term_id": t.id, "label": t.label, "status": t.status.value, "end_date": t.end_date, "subjects": len(offerings),
                         "enrollments_open": open_n, "unfinished": blockers, "ready": t.status == TermStatus.OPEN and not blockers})
        return {"terms": rows, "open": sum(r["status"] == "OPEN" for r in rows), "unfinished": sum(len(r["unfinished"]) for r in rows)}

    async def close(self, session_id: uuid.UUID, *, term_ids: list[uuid.UUID], reason: str | None, actor_id: uuid.UUID) -> dict:
        from app.academics.models import CourseOffering
        from app.academics.service import EnrollmentService
        from app.academics.terms import AcademicTermService, TermStatus

        await self._session(session_id)
        mine = {t.id: t for t in await self._terms(session_id)}
        if not term_ids:
            raise ValidationAppError("Choose the terms to close.")
        if any(i not in mine for i in term_ids):
            raise ValidationAppError("A chosen term is not in this session.")
        done = []
        async with self.session.begin_nested():                 # all or nothing
            for tid in term_ids:
                t = mine[tid]
                if t.status == TermStatus.CLOSED:
                    continue
                has = (await self.session.execute(select(func.count()).select_from(CourseOffering).where(
                    func.lower(CourseOffering.term_label) == t.label.lower()))).scalar_one()
                ended = None
                if has:
                    ended = await EnrollmentService(self.session).end_semester(t.label, actor_id=actor_id, override_reason=reason)
                await AcademicTermService(self.session).update(t.id, start_date=None, end_date=None, status=TermStatus.CLOSED, actor_id=actor_id)
                done.append({"label": t.label, "enrollments_completed": (ended or {}).get("enrollments_completed", 0),
                             "ungraded": (ended or {}).get("ungraded_count", 0)})
        return {"closed": done}

    # ------------------------------------------------------------------ 2. promote
    async def _batches(self, session_id: uuid.UUID) -> list[dict]:
        """Each batch (program + semester) of the session, with its active students."""
        from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentOutcome
        from app.programs.models import Program
        from app.students.models import Student, StudentStatus

        out = []
        for t in await self._terms(session_id):
            if t.program_id is None or t.semester is None:
                out.append({"term_id": t.id, "label": t.label, "status": "NO_BATCH", "note": "Set this term's batch (program + semester) on Academic terms.", "students": []})
                continue
            program = await self.session.get(Program, t.program_id)
            last = bool(program and program.total_semesters and t.semester >= program.total_semesters)
            students = (await self.session.execute(select(Student).where(Student.program_id == t.program_id, Student.current_semester == t.semester,
                                                                         Student.status == StudentStatus.ACTIVE).order_by(Student.roll_number))).scalars().all()
            failed: dict = {}
            if students:
                for sid, code in (await self.session.execute(select(Enrollment.student_id, Course.code).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                                             .join(Course, Course.id == CourseOffering.course_id).where(
                        func.lower(CourseOffering.term_label) == t.label.lower(), Enrollment.outcome == EnrollmentOutcome.FAILED,
                        Enrollment.student_id.in_([s.id for s in students])))).all():
                    failed.setdefault(sid, []).append(code)
            out.append({"term_id": t.id, "label": t.label, "program_id": t.program_id, "program": program.code if program else "?", "semester": t.semester,
                        "status": "LAST" if last else "PROMOTE",
                        "note": f"Semester {t.semester} is the program's last: these students finish (graduation), they are not promoted." if last else None,
                        "students": [{"id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}", "carry_over": sorted(failed.get(s.id, []))}
                                     for s in students]})
        return out

    async def promote_preview(self, session_id: uuid.UUID) -> dict:
        await self._session(session_id)
        b = await self._batches(session_id)
        return {"batches": b, "will_promote": sum(len(x["students"]) for x in b if x["status"] == "PROMOTE"),
                "carry_over": sum(1 for x in b if x["status"] == "PROMOTE" for s in x["students"] if s["carry_over"])}

    async def promote(self, session_id: uuid.UUID, *, hold: dict[uuid.UUID, str], actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService
        from app.core.clock import institute_today
        from app.fees.models import FeeAssignment, PayerType
        from app.fees.service import FeeStructureService
        from app.students.models import Student

        await self._session(session_id)
        live = (await self.session.execute(select(RolloverRun).where(RolloverRun.session_id == session_id, RolloverRun.kind == "PROMOTE",
                                                                     RolloverRun.undone_at.is_(None)))).scalars().first()
        if live is not None:
            raise BusinessRuleViolationError("This session's students were already promoted. Undo that first to promote again.")
        for sid, why in hold.items():
            if len((why or "").strip()) < 3:
                raise ValidationAppError("Write why each held-back student stays (at least 3 letters).")
        batches = [b for b in await self._batches(session_id) if b["status"] == "PROMOTE"]
        if not batches or not any(b["students"] for b in batches):
            raise BusinessRuleViolationError("Nobody to promote in this session.")
        moved, held = [], []
        fees = FeeStructureService(self.session)
        today = institute_today()
        async with self.session.begin_nested():                 # all or nothing
            for b in batches:
                for row in b["students"]:
                    if row["id"] in hold:
                        held.append({"id": str(row["id"]), "roll_number": row["roll_number"], "reason": hold[row["id"]].strip()[:200]})
                        continue
                    s = await self.session.get(Student, row["id"])
                    before = set((await self.session.execute(select(FeeAssignment.id).where(FeeAssignment.payer_type == PayerType.STUDENT,
                                                                                            FeeAssignment.payer_id == s.id))).scalars())
                    s.current_semester = b["semester"] + 1
                    s.semester_advanced_on = today
                    await fees.assign_matching_active_structures_to_student(s, actor_id=actor_id)
                    after = set((await self.session.execute(select(FeeAssignment.id).where(FeeAssignment.payer_type == PayerType.STUDENT,
                                                                                           FeeAssignment.payer_id == s.id))).scalars())
                    moved.append({"id": str(s.id), "roll_number": s.roll_number, "from": b["semester"], "to": b["semester"] + 1,
                                  "carry_over": row["carry_over"], "fees": [str(x) for x in after - before]})
            run = RolloverRun(session_id=session_id, kind="PROMOTE", data={"students": moved, "held": held}, created_by=actor_id)
            self.session.add(run)
            await self.session.flush()
            await AuditService(self.session).log(actor_user_id=actor_id, action="rollover.promoted", entity_type="academic_session", entity_id=session_id,
                                                 metadata={"promoted": len(moved), "held": len(held), "run_id": str(run.id)})
        return {"run": self._run_dict(run), "promoted": len(moved), "held": held,
                "carry_over": [m for m in moved if m["carry_over"]], "fees_charged": sum(len(m["fees"]) for m in moved)}

    async def undo(self, run_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService
        from app.students.models import Student

        run = await self.session.get(RolloverRun, run_id)
        if run is None:
            raise ResourceNotFoundError("Promotion not found.")
        if run.undone_at is not None:
            raise BusinessRuleViolationError("It was already undone.")
        if datetime.now(timezone.utc) - run.created_at > timedelta(days=UNDO_DAYS):
            raise BusinessRuleViolationError(f"A promotion can be undone within {UNDO_DAYS} days only.")
        back, changed, fees = 0, [], []
        async with self.session.begin_nested():
            for m in run.data.get("students", []):
                s = await self.session.get(Student, uuid.UUID(m["id"]))
                if s is None or s.current_semester != m["to"]:
                    changed.append(m["roll_number"])              # changed since: left as it is
                    continue
                s.current_semester = m["from"]
                back += 1
                fees += m.get("fees", [])
            run.undone_at = datetime.now(timezone.utc)
            run.undone_by = actor_id
            await self.session.flush()
            await AuditService(self.session).log(actor_user_id=actor_id, action="rollover.promotion_undone", entity_type="academic_session",
                                                 entity_id=run.session_id, metadata={"run_id": str(run.id), "put_back": back, "left": changed})
        return {"put_back": back, "left_as_is": changed, "fees_to_review": fees,
                "note": ("Fees charged when they were promoted were kept (financial records are never removed): review them in Fees." if fees else None)}

    # ------------------------------------------------------------------ 3. next session
    async def next_preview(self, session_id: uuid.UUID, new_name: str, labels: dict | None = None) -> dict:
        from app.academics.sessions import SessionService as AcademicSessionService

        return await AcademicSessionService(self.session).next_preview(session_id, new_name=new_name, labels=labels)

    async def start_next(self, session_id: uuid.UUID, *, new_name: str, start_date: date, end_date: date, labels: dict | None,
                         enroll: bool, same_teachers: bool, actor_id: uuid.UUID) -> dict:
        from app.academics.models import CourseOffering
        from app.academics.sessions import SessionService as AcademicSessionService
        from app.employees.models import Employee, EmployeeStatus

        src_labels = [t.label.lower() for t in await self._terms(session_id)]
        out = await AcademicSessionService(self.session).start_next(session_id, new_name=new_name, start_date=start_date, end_date=end_date,
                                                                    labels=labels, enroll=enroll, actor_id=actor_id)
        out["teachers_set"] = 0
        if same_teachers:
            new_labels = [t.label.lower() for t in await self._terms(out["session_id"])]
            new_offs = (await self.session.execute(select(CourseOffering).where(func.lower(CourseOffering.term_label).in_(new_labels or [""]),
                                                                                CourseOffering.assigned_teacher_id.is_(None)))).scalars().all()
            active = set((await self.session.execute(select(Employee.id).where(Employee.status == EmployeeStatus.ACTIVE))).scalars())
            for o in new_offs:
                # who taught this course most recently (this session first), still working here
                prev = (await self.session.execute(select(CourseOffering.assigned_teacher_id, CourseOffering.term_label).where(
                    CourseOffering.course_id == o.course_id, CourseOffering.assigned_teacher_id.is_not(None), CourseOffering.id != o.id)
                    .order_by(CourseOffering.created_at.desc()))).all()
                prev.sort(key=lambda r: 0 if (r[1] or "").lower() in src_labels else 1)
                tid = next((t for t, _ in prev if t in active), None)
                if tid:
                    o.assigned_teacher_id = tid
                    out["teachers_set"] += 1
            await self.session.flush()
        return out

    # ------------------------------------------------------------------ 4. fee structures (drafts)
    async def _fee_plan(self, session_id: uuid.UUID, new_name: str) -> list[dict]:
        from app.academics.sessions import SessionService as AcademicSessionService
        from app.fees.models import FeeStructure, FeeStructureSemester, FeeStructureStatus

        plan = await AcademicSessionService(self.session)._plan(session_id, new_name, {})
        rows = []
        for r in plan["rows"]:
            if r["status"] not in ("NEW", "EXISTS"):
                continue
            for fs in (await self.session.execute(select(FeeStructure).where(func.lower(FeeStructure.term_label) == r["label"].lower(),
                                                                             FeeStructure.status == FeeStructureStatus.ACTIVE,
                                                                             FeeStructure.course_id.is_(None)).order_by(FeeStructure.name))).scalars():
                already = (await self.session.execute(select(FeeStructure.id).where(func.lower(FeeStructure.term_label) == r["new_label"].lower(),
                                                                                    FeeStructure.fee_type_id == fs.fee_type_id))).scalars().first()
                sems = [x for x, in (await self.session.execute(select(FeeStructureSemester.semester_number).where(
                    FeeStructureSemester.fee_structure_id == fs.id))).all()]
                rows.append({"structure_id": fs.id, "name": fs.name, "amount": float(fs.total_amount), "from_term": r["label"], "to_term": r["new_label"],
                             "term_exists": r["status"] == "EXISTS", "to_semester": r["new_semester"], "semesters": sems,
                             "status": "EXISTS" if already else ("NO_TERM" if r["status"] != "EXISTS" else "COPY")})
        return rows

    async def fees_preview(self, session_id: uuid.UUID, new_name: str) -> dict:
        await self._session(session_id)
        rows = await self._fee_plan(session_id, new_name)
        return {"structures": rows, "will_copy": sum(r["status"] == "COPY" for r in rows)}

    async def copy_fees(self, session_id: uuid.UUID, *, new_name: str, percentage_change: float, effective_from: date, actor_id: uuid.UUID) -> dict:
        """Each structure is copied like Fees -> Clone (same fee type, programs, instalments and late fee), but for the
        batch's next semester and next batch term, as a DRAFT."""
        from app.audit.service import AuditService
        from app.fees.batch_terms import check_term_fits
        from app.fees.models import FeeStructure, FeeStructureProgram, FeeStructureSemester, FeeStructureStatus

        await self._session(session_id)
        rows = [r for r in await self._fee_plan(session_id, new_name) if r["status"] == "COPY"]
        if not rows:
            raise BusinessRuleViolationError("Nothing to copy: start the next session first (step 3), or its fees already exist.")
        made = []
        async with self.session.begin_nested():                 # all or nothing
            for r in rows:
                src = await self.session.get(FeeStructure, r["structure_id"])
                programs = [x for x, in (await self.session.execute(select(FeeStructureProgram.program_id).where(
                    FeeStructureProgram.fee_structure_id == src.id))).all()]
                sems = [r["to_semester"]] if r["semesters"] else []
                await check_term_fits(self.session, r["to_term"], program_ids=programs, semester_numbers=sems, course_id=None,
                                      applies_to_employees=src.applies_to_employees)
                amount = round(float(src.total_amount) * (1 + percentage_change / 100), 2)
                if amount <= 0:
                    raise BusinessRuleViolationError("The changed amount must be more than 0.")
                name = src.name.replace(r["from_term"], r["to_term"]) if r["from_term"] in src.name else f"{src.name} \u2014 {r['to_term']}"
                clone = FeeStructure(fee_type_id=src.fee_type_id, name=name[:150], term_label=r["to_term"], total_amount=amount,
                                     installment_count=src.installment_count, installment_interval_months=src.installment_interval_months,
                                     effective_from=effective_from, late_fee_per_day=src.late_fee_per_day, late_fee_once=src.late_fee_once,
                                     applies_to_students=src.applies_to_students, applies_to_employees=src.applies_to_employees, course_id=None,
                                     status=FeeStructureStatus.DRAFT, created_by=actor_id)
                self.session.add(clone)
                await self.session.flush()
                for pid in programs:
                    self.session.add(FeeStructureProgram(fee_structure_id=clone.id, program_id=pid))
                for sem in sems:
                    self.session.add(FeeStructureSemester(fee_structure_id=clone.id, semester_number=sem))
                await self.session.flush()
                await AuditService(self.session).log(actor_user_id=actor_id, action="fee_structure.cloned", entity_type="fee_structure", entity_id=clone.id,
                                                     after={"source_structure_id": str(src.id), "percentage_change": percentage_change,
                                                            "old_amount": float(src.total_amount), "new_amount": amount, "by": "rollover"})
                made.append({"id": clone.id, "name": clone.name, "amount": amount, "term": r["to_term"]})
        return {"drafts": made}
