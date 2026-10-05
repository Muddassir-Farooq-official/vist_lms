from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError
from app.notifications.models import Notification, NotificationCategory
from app.notifications.repository import NotificationRepository


_PAGE_PATHS: set[str] | None = None


def _known_page(link: str | None) -> str | None:
    """A notification must never point at a page that does not exist (it would open a 404).
    Dashboard links are checked against the app's real page routes; anything else unknown is dropped
    (the notification still shows, it just is not clickable)."""
    global _PAGE_PATHS
    if not link or not link.startswith("/dashboard/"):
        return link or None
    if _PAGE_PATHS is None:
        try:
            from app.main import app  # imported late: app.main imports this module's package

            _PAGE_PATHS = {r.path for r in app.routes if getattr(r, "path", None) and "GET" in getattr(r, "methods", set())}
        except Exception:
            return link
    return link if link.split("?")[0] in _PAGE_PATHS else None


async def create_notification(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category: NotificationCategory,
    title: str,
    message: str,
    link: str | None = None,
) -> Notification:
    """Standalone helper, not a method on a class needing its own
    instantiation — this is what other modules' services call directly
    (Fines on issue/mark-paid/waive, Complaints on admin status change,
    Library on waitlist auto-fulfillment), the same lightweight
    cross-module pattern already used for resolve_party()-style helpers
    elsewhere in this codebase. Best-effort: if user_id doesn't resolve
    to a real user, the FK constraint will raise on flush — callers are
    expected to pass a user_id they already know is valid (e.g. from
    student.user_id / employee.user_id), same as every other place in
    this codebase that does that resolution."""
    notification = Notification(user_id=user_id, category=category, title=title, message=message, link=_known_page(link))
    return await NotificationRepository(session).create(notification)


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = NotificationRepository(session)

    async def list_for_user(
        self, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = 30, offset: int = 0
    ) -> list[Notification]:
        return await self.repo.list_for_user(user_id, unread_only=unread_only, limit=limit, offset=offset)

    async def unread_count(self, user_id: uuid.UUID) -> int:
        return await self.repo.unread_count(user_id)

    async def mark_read(self, notification_id: uuid.UUID, *, user_id: uuid.UUID) -> Notification:
        notification = await self.repo.get_by_id(notification_id)
        if notification is None or notification.user_id != user_id:
            # Same "not found" a stranger's ID would produce.
            raise ResourceNotFoundError("Notification not found.")
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = datetime.now(timezone.utc)
        return notification

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        return await self.repo.mark_all_read(user_id)