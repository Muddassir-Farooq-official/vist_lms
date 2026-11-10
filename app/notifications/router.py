from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.notifications.schemas import NotificationOut, UnreadCountOut
from app.notifications.service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/me", response_model=list[NotificationOut], summary="My notifications, most recent first")
async def list_my_notifications(
    unread_only: bool = False,
    limit: int = 30,
    offset: int = 0,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[NotificationOut]:
    items = await NotificationService(db).list_for_user(
        current_user.id, unread_only=unread_only, limit=max(1, min(limit, 100)), offset=max(0, offset)
    )
    return [NotificationOut.model_validate(n) for n in items]


@router.get("/sound", summary="The notification tone and volume set for everyone (Settings -> Notifications)")
async def notification_sound(current_user: CurrentUser = Depends(get_current_user)) -> dict:
    from app.core.config import settings

    return {"tone": int(settings.notify_sound_tone), "urgent_tone": int(settings.notify_sound_urgent_tone) or int(settings.notify_sound_tone),
            "volume": int(settings.notify_sound_volume), "repeat": int(settings.notify_sound_repeat)}


@router.get("/me/unread-count", response_model=UnreadCountOut, summary="How many of my notifications are unread")
async def my_unread_count(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> UnreadCountOut:
    count = await NotificationService(db).unread_count(current_user.id)
    return UnreadCountOut(unread_count=count)


@router.post("/{notification_id}/read", response_model=NotificationOut, summary="Mark one notification read")
async def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> NotificationOut:
    notification = await NotificationService(db).mark_read(notification_id, user_id=current_user.id)
    return NotificationOut.model_validate(notification)


@router.post("/me/read-all", response_model=UnreadCountOut, summary="Mark all my notifications read")
async def mark_all_read(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> UnreadCountOut:
    await NotificationService(db).mark_all_read(current_user.id)
    return UnreadCountOut(unread_count=0)