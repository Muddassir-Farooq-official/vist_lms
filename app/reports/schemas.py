from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class StudentStatusCounts(BaseModel):
    active: int = 0
    suspended: int = 0
    graduated: int = 0
    dropped: int = 0
    total: int = 0


class ProgramBreakdown(BaseModel):
    program_id: uuid.UUID | None
    program_code: str | None
    program_name: str | None
    student_count: int


class RecentImport(BaseModel):
    id: uuid.UUID
    filename: str
    status: str
    total_rows: int
    imported_rows: int
    invalid_rows: int
    created_at: datetime


class RecentActivity(BaseModel):
    action: str
    entity_type: str
    actor_username: str | None
    created_at: datetime


class AdminDashboardResponse(BaseModel):
    student_status_counts: StudentStatusCounts
    total_programs: int
    active_programs: int
    students_by_program: list[ProgramBreakdown]
    recent_imports: list[RecentImport]
    recent_activity: list[RecentActivity]