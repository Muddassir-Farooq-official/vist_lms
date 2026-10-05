from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ProgramCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=150)
    duration_years: int = Field(gt=0, le=10)
    total_semesters: int = Field(gt=0, le=20)
    # Enforced live in EnrollmentService.enroll() (the per-semester
    # credit-hour cap) — that code's own comment calls this "configurable
    # per program," but until this fix nothing anywhere exposed it: not
    # this schema, not ProgramUpdate, not even ProgramOut's response.
    # Every program silently used the model's hardcoded default (18)
    # forever, with zero admin control.
    max_credit_hours_per_semester: int = Field(default=18, gt=0, le=60)


class ProgramUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    duration_years: int | None = Field(default=None, gt=0, le=10)
    total_semesters: int | None = Field(default=None, gt=0, le=20)
    is_active: bool | None = None
    max_credit_hours_per_semester: int | None = Field(default=None, gt=0, le=60)


class ProgramOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    duration_years: int
    total_semesters: int
    is_active: bool
    max_credit_hours_per_semester: int