from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import ResourceNotFoundError
from app.profiles.models import ProfilePartyType, Qualification
from app.profiles.repository import QualificationRepository
from app.profiles.schemas import QualificationCreateRequest, QualificationUpdateRequest


class QualificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = QualificationRepository(session)
        self.audit = AuditService(session)

    async def get(self, qualification_id: uuid.UUID) -> Qualification:
        qualification = await self.repo.get_by_id(qualification_id)
        if qualification is None:
            raise ResourceNotFoundError("Qualification not found.")
        return qualification

    async def list_for_party(self, party_type: ProfilePartyType, party_id: uuid.UUID) -> list[Qualification]:
        return await self.repo.list_for_party(party_type, party_id)

    async def add(
        self, *, party_type: ProfilePartyType, party_id: uuid.UUID, data: QualificationCreateRequest, actor_id: uuid.UUID
    ) -> Qualification:
        existing = await self.repo.list_for_party(party_type, party_id)
        qualification = Qualification(
            party_type=party_type,
            party_id=party_id,
            degree_title=data.degree_title.strip(),
            institution_name=data.institution_name.strip(),
            year_completed=data.year_completed,
            marks_or_grade=data.marks_or_grade.strip() if data.marks_or_grade else None,
            display_order=len(existing),
        )
        created = await self.repo.create(qualification)

        # Real gap fix: this module had NO audit logging at all — for
        # self-editable academic-credential data (a student or employee
        # claiming their own degrees/institutions), with real incentive
        # to falsify and, until now, zero record of who added, changed,
        # or removed what.
        await self.audit.log(
            actor_user_id=actor_id,
            action="qualification.added",
            entity_type="qualification",
            entity_id=created.id,
            after={
                "party_type": party_type.value, "party_id": str(party_id),
                "degree_title": created.degree_title, "institution_name": created.institution_name,
                "year_completed": created.year_completed,
            },
        )
        return created

    async def update(self, qualification_id: uuid.UUID, data: QualificationUpdateRequest, *, actor_id: uuid.UUID) -> Qualification:
        qualification = await self.get(qualification_id)
        before = {
            "degree_title": qualification.degree_title, "institution_name": qualification.institution_name,
            "year_completed": qualification.year_completed, "marks_or_grade": qualification.marks_or_grade,
        }
        if data.degree_title is not None:
            qualification.degree_title = data.degree_title.strip()
        if data.institution_name is not None:
            qualification.institution_name = data.institution_name.strip()
        if data.year_completed is not None:
            qualification.year_completed = data.year_completed
        if data.marks_or_grade is not None:
            qualification.marks_or_grade = data.marks_or_grade.strip() or None

        await self.audit.log(
            actor_user_id=actor_id,
            action="qualification.updated",
            entity_type="qualification",
            entity_id=qualification.id,
            before=before,
            after={
                "degree_title": qualification.degree_title, "institution_name": qualification.institution_name,
                "year_completed": qualification.year_completed, "marks_or_grade": qualification.marks_or_grade,
            },
        )
        return qualification

    async def delete(self, qualification_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        qualification = await self.get(qualification_id)
        await self.audit.log(
            actor_user_id=actor_id,
            action="qualification.deleted",
            entity_type="qualification",
            entity_id=qualification.id,
            before={
                "party_type": qualification.party_type.value, "party_id": str(qualification.party_id),
                "degree_title": qualification.degree_title, "institution_name": qualification.institution_name,
            },
        )
        await self.repo.delete(qualification)