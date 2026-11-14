"""The institute's list of academic terms (Fall 2026, Spring 2027 ...).

Offerings, fee structures and End Semester refer to a term by its label (text).
Typing labels by hand let "Fall 2026", "fall 2026" and "Fal 2026" exist side by
side and silently split GPA terms, fee charges and End Semester. Now every place
that INTRODUCES a term label (creating/generating offerings, creating/cloning a
fee structure) must use a term registered here and not closed. Labels cannot be
renamed by hand on those records: renaming a term here writes the new name on every offering, fee structure,
datesheet and admission round that carries it, in one step (all or nothing), and is recorded.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.database import Base
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError


# What a fee charged to several batches carries instead of one batch's term: each student then sees their own
# batch term (fee_assignments.term_label). Never a registered term's name.
ALL_BATCHES = "All batches"


class TermStatus(str, enum.Enum):
    OPEN = "OPEN"        # can be used for new offerings and fee structures
    CLOSED = "CLOSED"    # finished (or created by mistake): kept for history, not usable for new records


class AcademicTerm(Base):
    __tablename__ = "academic_terms"
    __table_args__ = (UniqueConstraint("label", name="uq_academic_terms_label"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[TermStatus] = mapped_column(Enum(TermStatus, name="academic_term_status"), nullable=False, default=TermStatus.OPEN)
    # Batches start and end at different times, so a term may belong to one batch: a program + the semester it is in.
    program_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("programs.id", ondelete="SET NULL"), nullable=True)
    semester: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _norm(label: str) -> str:
    return "".join(label.lower().split())


def to_dict(t: AcademicTerm) -> dict:
    return {"id": t.id, "label": t.label, "start_date": t.start_date, "end_date": t.end_date, "status": t.status.value,
            "program_id": t.program_id, "semester": t.semester}


class AcademicTermService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def all(self) -> list[AcademicTerm]:
        rows = (await self.session.execute(select(AcademicTerm))).scalars().all()
        return sorted(rows, key=lambda t: (t.start_date or date.min, t.label), reverse=True)

    async def by_label(self, label: str) -> AcademicTerm | None:
        return (await self.session.execute(select(AcademicTerm).where(AcademicTerm.label == label))).scalar_one_or_none()

    async def batch_term(self, program_id: uuid.UUID | None, semester: int | None, *, on: date | None = None) -> AcademicTerm | None:
        """The term of one batch (program + semester): an open one running on `on` (default today), else the open one that
        started last, else the closed one that started last. None when no term is set up for that batch."""
        if program_id is None or semester is None:
            return None
        from app.core.clock import institute_today

        on = on or institute_today()
        rows = (await self.session.execute(select(AcademicTerm).where(AcademicTerm.program_id == program_id, AcademicTerm.semester == semester))).scalars().all()
        if not rows:
            return None

        def rank(t: AcademicTerm):
            running = (t.start_date is None or t.start_date <= on) and (t.end_date is None or on <= t.end_date)
            return (t.status == TermStatus.OPEN, running, t.start_date or date.min, t.label)

        return max(rows, key=rank)

    async def _check_batch(self, program_id: uuid.UUID | None, semester: int | None) -> None:
        if (program_id is None) != (semester is None):
            raise ValidationAppError("Give both the program and the semester of this batch, or neither.")
        if semester is not None and not 1 <= semester <= 20:
            raise ValidationAppError("The semester must be between 1 and 20.")
        if program_id is not None:
            from app.programs.models import Program

            if await self.session.get(Program, program_id) is None:
                raise ResourceNotFoundError("Program not found.")

    async def create(self, *, label: str, start_date: date | None, end_date: date | None, actor_id: uuid.UUID | None,
                     status: TermStatus = TermStatus.OPEN, program_id: uuid.UUID | None = None, semester: int | None = None) -> AcademicTerm:
        label = " ".join(label.split())
        if not label:
            raise ValidationAppError("Give the term a name, for example Fall 2026.")
        if _norm(label) == _norm(ALL_BATCHES):
            raise ValidationAppError(f'"{ALL_BATCHES}" is kept for fees charged to several batches. Pick another name.')
        await self._check_batch(program_id, semester)
        if start_date and end_date and end_date <= start_date:
            raise ValidationAppError("The term must end after it starts.")
        for t in await self.all():
            if _norm(t.label) == _norm(label):
                raise DuplicateResourceError(f'The term "{t.label}" already exists.')
        term = AcademicTerm(label=label, start_date=start_date, end_date=end_date, status=status, created_by=actor_id,
                            program_id=program_id, semester=semester)
        self.session.add(term)
        await self.session.flush()
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="academic_term.created", entity_type="academic_term",
                                             entity_id=term.id, after={"label": label, "start": str(start_date), "end": str(end_date),
                                                                    "program_id": str(program_id) if program_id else None, "semester": semester})
        return term

    async def usage(self, labels: list[str] | None = None) -> dict[str, dict[str, int]]:
        """How many records refer to each term label (by its text): course offerings, fee structures, datesheets, admission rounds."""
        from app.academics.models import CourseOffering
        from app.admissions.models import AdmissionRound
        from app.exams.models import ExamSchedule
        from app.fees.models import FeeStructure

        out: dict[str, dict[str, int]] = {}
        for key, model in (("offerings", CourseOffering), ("fee_structures", FeeStructure), ("datesheets", ExamSchedule), ("admission_rounds", AdmissionRound)):
            stmt = select(model.term_label, func.count()).group_by(model.term_label)
            if labels is not None:
                stmt = stmt.where(model.term_label.in_(labels))
            for label, n in (await self.session.execute(stmt)).all():
                out.setdefault(label, {})[key] = int(n)
        from app.fees.models import FeeAssignment

        stmt = select(FeeAssignment.term_label, func.count()).where(FeeAssignment.term_label.is_not(None)).group_by(FeeAssignment.term_label)
        if labels is not None:
            stmt = stmt.where(FeeAssignment.term_label.in_(labels))
        for label, n in (await self.session.execute(stmt)).all():
            out.setdefault(label, {})["fee_charges"] = int(n)
        return out

    async def _rename_everywhere(self, old: str, new: str) -> dict[str, int]:
        """Write the new name on every record that carries the old one, in this same transaction (all or nothing).
        Issued documents (certificates, printed challans and receipts) keep what was printed on them."""
        from sqlalchemy import update

        from app.academics.models import CourseOffering
        from app.admissions.models import AdmissionRound
        from app.exams.models import ExamSchedule
        from app.fees.models import FeeStructure

        out: dict[str, int] = {}
        for key, model in (("offerings", CourseOffering), ("fee_structures", FeeStructure), ("datesheets", ExamSchedule), ("admission_rounds", AdmissionRound)):
            result = await self.session.execute(update(model).where(model.term_label == old).values(term_label=new).execution_options(synchronize_session="fetch"))
            out[key] = int(result.rowcount or 0)
        from app.fees.models import FeeAssignment

        result = await self.session.execute(update(FeeAssignment).where(FeeAssignment.term_label == old).values(term_label=new)
                                            .execution_options(synchronize_session="fetch"))
        out["fee_charges"] = int(result.rowcount or 0)
        return out

    async def update(self, term_id: uuid.UUID, *, start_date: date | None, end_date: date | None, status: TermStatus | None,
                     actor_id: uuid.UUID, label: str | None = None, batch: tuple[uuid.UUID | None, int | None] | None = None) -> AcademicTerm:
        """`batch` = (program_id, semester) to set the batch this term belongs to, (None, None) to clear it; None leaves it."""
        term = await self.session.get(AcademicTerm, term_id)
        if term is None:
            raise ResourceNotFoundError("Term not found.")
        before = to_dict(term)
        check_start = start_date if start_date is not None else term.start_date
        check_end = end_date if end_date is not None else term.end_date
        if check_start and check_end and check_end <= check_start:
            raise ValidationAppError("The term must end after it starts.")
        if label is not None and " ".join(label.split()) != term.label:
            new = " ".join(label.split())
            if not new:
                raise ValidationAppError("Give the term a name, for example Fall 2026.")
            if _norm(new) == _norm(ALL_BATCHES):
                raise ValidationAppError(f'"{ALL_BATCHES}" is kept for fees charged to several batches. Pick another name.')
            for t in await self.all():
                if t.id != term.id and _norm(t.label) == _norm(new):
                    raise DuplicateResourceError(f'The term "{t.label}" already exists.')
            # Another term's records must never be merged in by renaming onto a name that is already written on them.
            taken = (await self.usage([new])).get(new, {})
            if any(taken.values()):
                raise BusinessRuleViolationError(f'Records already use the name "{new}". Pick a different name.')
            renamed = await self._rename_everywhere(term.label, new)
            old_label = term.label
            term.label = new
            if any(renamed.values()):
                from app.audit.service import AuditService

                await AuditService(self.session).log(actor_user_id=actor_id, action="academic_term.renamed", entity_type="academic_term", entity_id=term.id,
                                                     before={"label": old_label}, after={"label": new}, metadata={"records_updated": renamed})
        new_start = start_date if start_date is not None else term.start_date
        new_end = end_date if end_date is not None else term.end_date
        if new_start and new_end and new_end <= new_start:           # checked before anything changes, so a refused edit saves nothing
            raise ValidationAppError("The term must end after it starts.")
        if batch is not None:
            await self._check_batch(*batch)
            term.program_id, term.semester = batch
        term.start_date, term.end_date = new_start, new_end
        if status is not None:
            term.status = status
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="academic_term.updated", entity_type="academic_term",
                                             entity_id=term.id, before={k: str(v) for k, v in before.items()},
                                             after={k: str(v) for k, v in to_dict(term).items()})
        return term

    async def require_usable(self, label: str, *, all_batches_ok: bool = False) -> AcademicTerm | None:
        """The registered, open term for a label being used for a NEW offering / fee structure. Returns None when
        the check is switched off (settings.require_registered_terms = False), and for a fee charged to each student's
        own term (`all_batches_ok`)."""
        if all_batches_ok and label.strip() == ALL_BATCHES:
            return None
        if not settings.require_registered_terms:
            return None
        term = await self.by_label(label.strip())
        if term is None:
            known = [t for t in await self.all() if t.status == TermStatus.OPEN]
            close = [t.label for t in known if _norm(t.label) == _norm(label)]
            hint = f' Did you mean "{close[0]}"?' if close else (f" Open terms: {', '.join(t.label for t in known[:6])}." if known else "")
            raise BusinessRuleViolationError(
                f'"{label}" is not a registered term.{hint} Add new terms in Courses \u2192 Academic Terms.', details={"term_label": label})
        if term.status == TermStatus.CLOSED:
            raise BusinessRuleViolationError(f'The term "{term.label}" is closed. Use an open term.')
        return term