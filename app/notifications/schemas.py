from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.notifications.models import NotificationCategory


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: NotificationCategory
    title: str
    message: str
    link: str | None
    is_read: bool
    created_at: datetime


class UnreadCountOut(BaseModel):
    unread_count: int