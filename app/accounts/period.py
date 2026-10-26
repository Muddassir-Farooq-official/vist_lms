"""Closing the books.

Once a month (or a year) has been reconciled and reported, the administrator
closes it: from then on nothing can be recorded, approved, changed or voided
with a date on or before the closing date. Mistakes found later are corrected
with a new entry in an open period (a reversal), so the reported figures for
the closed period never change.

Stored as one row in policy_settings (key "books_closed_through", value YYYY-MM-DD).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleViolationError

KEY = "books_closed_through"


async def closed_through(session: AsyncSession) -> date | None:
    from app.policy.models import PolicySetting

    row = await session.get(PolicySetting, KEY)
    if row is None or not row.value:
        return None
    try:
        return date.fromisoformat(row.value)
    except ValueError:
        return None


async def ensure_open(session: AsyncSession, day: date | datetime | None, what: str = "This entry") -> None:
    if day is None:
        return
    if isinstance(day, datetime):
        from app.core.clock import local_date

        day = local_date(day)
    closed = await closed_through(session)
    if closed is not None and day <= closed:
        raise BusinessRuleViolationError(
            f"{what} is dated {day:%d %b %Y}, but the books are closed up to {closed:%d %b %Y}. "
            "Use a date after that (a correcting entry), or ask the administrator to reopen the period.")


async def set_closed_through(session: AsyncSession, day: date | None, *, actor_id: uuid.UUID, reason: str | None, approved: bool = False):
    """Close the books up to `day`. Reopening (moving the date back, or clearing it) needs a second administrator
    (owner decision 10): unless `approved`, it returns the approval request instead of changing anything."""
    from app.audit.service import AuditService
    from app.core.clock import institute_today
    from app.policy.models import PolicySetting

    before = await closed_through(session)
    if day is not None and day >= institute_today():
        raise BusinessRuleViolationError("You can only close a period that has already ended (a date before today).")
    if before is not None and (day is None or day < before) and len((reason or "").strip()) < 10:
        raise BusinessRuleViolationError("Reopening closed books needs a reason (at least 10 letters). It is kept in the audit log.")
    if before is not None and (day is None or day < before) and not approved:
        from app.approvals.service import gate

        req = await gate(session, kind="BOOKS_REOPEN", actor_id=actor_id, target_id=None, reason=reason,
                         payload={"closed_through": day.isoformat() if day else None, "reason": reason},
                         summary=f"Reopen the books: closed up to {before:%d %b %Y}, move to " + (f"{day:%d %b %Y}" if day else "fully open"))
        if req is not None:
            return req
    row = await session.get(PolicySetting, KEY)
    now = datetime.now(timezone.utc)
    if day is None:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(PolicySetting(key=KEY, value=day.isoformat(), updated_by=actor_id, updated_at=now))
    else:
        row.value, row.updated_by, row.updated_at = day.isoformat(), actor_id, now
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action="accounts.books_closed" if day and (before is None or day > before) else "accounts.books_reopened",
                                    entity_type="policy", entity_id=None, before={"closed_through": str(before) if before else None},
                                    after={"closed_through": str(day) if day else None}, metadata={"reason": (reason or "").strip() or None})
    return day