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

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.database import Base
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError


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
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _norm(label: str) -> str:
    return "".join(label.lower().split())


def to_dict(t: AcademicTerm) -> dict:
    return {"id": t.id, "label": t.label, "start_date": t.start_date, "end_date": t.end_date, "status": t.status.value}


class AcademicTermService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def all(self) -> list[AcademicTerm]:
        rows = (await self.session.execute(select(AcademicTerm))).scalars().all()
        return sorted(rows, key=lambda t: (t.start_date or date.min, t.label), reverse=True)

    async def by_label(self, label: str) -> AcademicTerm | None:
        return (await self.session.execute(select(AcademicTerm).where(AcademicTerm.label == label))).scalar_one_or_none()

    async def create(self, *, label: str, start_date: date | None, end_date: date | None, actor_id: uuid.UUID | None,
                     status: TermStatus = TermStatus.OPEN) -> AcademicTerm:
        label = " ".join(label.split())
        if not label:
            raise ValidationAppError("Give the term a name, for example Fall 2026.")
        if start_date and end_date and end_date <= start_date:
            raise ValidationAppError("The term must end after it starts.")
        for t in await self.all():
            if _norm(t.label) == _norm(label):
                raise DuplicateResourceError(f'The term "{t.label}" already exists.')
        term = AcademicTerm(label=label, start_date=start_date, end_date=end_date, status=status, created_by=actor_id)
        self.session.add(term)
        await self.session.flush()
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="academic_term.created", entity_type="academic_term",
                                             entity_id=term.id, after={"label": label, "start": str(start_date), "end": str(end_date)})
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
        return out

    async def update(self, term_id: uuid.UUID, *, start_date: date | None, end_date: date | None, status: TermStatus | None,
                     actor_id: uuid.UUID, label: str | None = None) -> AcademicTerm:
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
        term.start_date, term.end_date = new_start, new_end
        if status is not None:
            term.status = status
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="academic_term.updated", entity_type="academic_term",
                                             entity_id=term.id, before={k: str(v) for k, v in before.items()},
                                             after={k: str(v) for k, v in to_dict(term).items()})
        return term

    async def require_usable(self, label: str) -> AcademicTerm | None:
        """The registered, open term for a label being used for a NEW offering / fee structure. Returns None when
        the check is switched off (settings.require_registered_terms = False)."""
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