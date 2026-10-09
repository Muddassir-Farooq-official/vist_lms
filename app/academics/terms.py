"""The institute's list of academic terms (Fall 2026, Spring 2027 ...).

Offerings, fee structures and End Semester refer to a term by its label (text).
Typing labels by hand let "Fall 2026", "fall 2026" and "Fal 2026" exist side by
side and silently split GPA terms, fee charges and End Semester. Now every place
that INTRODUCES a term label (creating/generating offerings, creating/cloning a
fee structure) must use a term registered here and not closed. Labels cannot be
renamed (other records keep them as text); a wrong one is closed instead.
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

    async def update(self, term_id: uuid.UUID, *, start_date: date | None, end_date: date | None, status: TermStatus | None,
                     actor_id: uuid.UUID) -> AcademicTerm:
        term = await self.session.get(AcademicTerm, term_id)
        if term is None:
            raise ResourceNotFoundError("Term not found.")
        before = to_dict(term)
        term.start_date = start_date if start_date is not None else term.start_date
        term.end_date = end_date if end_date is not None else term.end_date
        if term.start_date and term.end_date and term.end_date <= term.start_date:
            raise ValidationAppError("The term must end after it starts.")
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