from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ImportJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    imported_rows: int
    skipped_rows: int
    failed_rows: int
    started_at: datetime | None
    finished_at: datetime | None
    processing_duration_ms: int | None
    failure_reason: str | None
    created_at: datetime


class ImportRowErrorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    field: str
    error_type: str
    message: str


class ImportJobCreatedResponse(BaseModel):
    import_job_id: uuid.UUID
    status: str
    message: str
