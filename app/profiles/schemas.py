from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.profiles.models import ProfilePartyType


class QualificationCreateRequest(BaseModel):
    degree_title: str = Field(min_length=1, max_length=150)
    institution_name: str = Field(min_length=1, max_length=200)
    year_completed: int | None = Field(default=None, ge=1950, le=2100)
    marks_or_grade: str | None = Field(default=None, max_length=50)


class QualificationUpdateRequest(BaseModel):
    degree_title: str | None = Field(default=None, min_length=1, max_length=150)
    institution_name: str | None = Field(default=None, min_length=1, max_length=200)
    year_completed: int | None = Field(default=None, ge=1950, le=2100)
    marks_or_grade: str | None = Field(default=None, max_length=50)


class QualificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    party_type: ProfilePartyType
    party_id: uuid.UUID
    degree_title: str
    institution_name: str
    year_completed: int | None
    marks_or_grade: str | None
    display_order: int