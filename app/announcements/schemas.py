from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.announcements.models import AnnouncementCategory, AnnouncementPriority, AnnouncementStatus


class AuthorityIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    designation: str = Field(min_length=2, max_length=120)


class AuthorityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    designation: str | None = Field(default=None, min_length=2, max_length=120)
    is_active: bool | None = None


class AuthorityOut(BaseModel):
    id: uuid.UUID
    name: str
    designation: str
    is_active: bool


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    category: AnnouncementCategory = AnnouncementCategory.GENERAL
    priority: AnnouncementPriority = AnnouncementPriority.NORMAL
    issuer_id: uuid.UUID
    audience_students: bool = True
    audience_teachers: bool = True
    audience_staff: bool = True
    program_ids: list[uuid.UUID] | None = None          # only students of these programs (null / empty = every student)
    semesters: list[int] | None = Field(default=None, max_length=12)          # only students in these semesters
    offering_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)  # only students in these course sections
    pinned: bool = False
    expires_at: datetime | None = None
    publish: bool = False                                # False = save as a draft

    @model_validator(mode="after")
    def _someone_must_be_able_to_read_it(self) -> "AnnouncementCreate":
        if not (self.audience_students or self.audience_teachers or self.audience_staff):
            raise ValueError("Choose who this announcement is for (students, teachers or other staff).")
        return self


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=10000)
    category: AnnouncementCategory | None = None
    priority: AnnouncementPriority | None = None
    issuer_id: uuid.UUID | None = None
    audience_students: bool | None = None
    audience_teachers: bool | None = None
    audience_staff: bool | None = None
    program_ids: list[uuid.UUID] | None = None
    clear_program_filter: bool = False
    semesters: list[int] | None = Field(default=None, max_length=12)
    offering_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    pinned: bool | None = None
    expires_at: datetime | None = None
    clear_expiry: bool = False


class AttachmentOut(BaseModel):
    id: uuid.UUID
    original_name: str
    content_type: str
    size_bytes: int
    is_image: bool
    inline: bool          # may be opened inside the browser (PDF / image); otherwise it downloads


class AnnouncementOut(BaseModel):
    id: uuid.UUID
    title: str
    body: str
    category: AnnouncementCategory
    category_label: str
    priority: AnnouncementPriority
    issuer_name: str
    issuer_designation: str
    pinned: bool
    status: AnnouncementStatus
    published_at: datetime | None
    expires_at: datetime | None
    updated_at: datetime
    is_read: bool
    attachments: list[AttachmentOut]
    # who it is for (so the reader can see "for students of BSCS")
    audience_students: bool
    audience_teachers: bool
    audience_staff: bool
    program_ids: list[uuid.UUID] | None = None
    semesters: list[int] | None = None
    audience_text: str = ""             # "BSCS, semester 3" / "CS-201 A" / "All students" ...


class AnnouncementAdminOut(AnnouncementOut):
    recipients: int | None = None       # how many people it was sent to
    read_count: int | None = None       # how many have opened it
    program_names: list[str] = []
    offering_ids: list[uuid.UUID] | None = None
    # written by staff (review is null for the admin's own)
    review: str | None = None
    review_note: str | None = None
    submitted_by_name: str | None = None
    submitted_at: datetime | None = None


class UnreadCountOut(BaseModel):
    unread_count: int