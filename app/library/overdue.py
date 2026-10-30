"""Overdue loans: who to contact, and reminders to borrowers (in-app notification, which is also emailed)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.library.models import BorrowerType

REMIND_PREFIX = "Library book overdue: "


async def borrower_contacts(session: AsyncSession, pairs: list[tuple]) -> dict:
    """{(borrower_type, borrower_id): {"code", "phone", "user_id"}} for the borrowers of these loans."""
    from app.employees.models import Employee
    from app.students.models import Student

    out: dict = {}
    sids = [bid for t, bid in pairs if t == BorrowerType.STUDENT]
    eids = [bid for t, bid in pairs if t == BorrowerType.EMPLOYEE]
    if sids:
        for sid, code, phone, uid in (await session.execute(select(Student.id, Student.roll_number, Student.contact_no, Student.user_id)
                                                            .where(Student.id.in_(sids)))).all():
            out[(BorrowerType.STUDENT, sid)] = {"code": code, "phone": phone, "user_id": uid}
    if eids:
        for eid, code, phone, uid in (await session.execute(select(Employee.id, Employee.employee_code, Employee.contact_no, Employee.user_id)
                                                            .where(Employee.id.in_(eids)))).all():
            out[(BorrowerType.EMPLOYEE, eid)] = {"code": code, "phone": phone, "user_id": uid}
    return out


async def remind(session: AsyncSession, issue_ids: list[uuid.UUID], *, actor_id: uuid.UUID) -> dict:
    """Notify each borrower of each overdue book (all overdue loans when issue_ids is empty). A borrower already reminded
    about the same book in the last 24 hours is skipped."""
    from app.audit.service import AuditService
    from app.core.clock import institute_today
    from app.library.issue_service import IssueService
    from app.library.repository import BookIssueRepository
    from app.notifications.models import Notification, NotificationCategory
    from app.notifications.service import create_notification

    service = IssueService(session)
    loans = await BookIssueRepository(session).list_overdue()
    if issue_ids:
        wanted = set(issue_ids)
        loans = [x for x in loans if x.id in wanted]
    rows = await service._enrich(loans)
    contacts = await borrower_contacts(session, [(e["issue"].borrower_type, e["issue"].borrower_id) for e in rows])
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = set((await session.execute(select(Notification.user_id, Notification.title).where(
        Notification.title.like(REMIND_PREFIX + "%"), Notification.created_at >= since))).all())
    today = institute_today()
    sent = skipped = no_login = 0
    for e in rows:
        issue = e["issue"]
        uid = contacts.get((issue.borrower_type, issue.borrower_id), {}).get("user_id")
        title = (REMIND_PREFIX + e["book_title"])[:200]
        if uid is None:
            no_login += 1
            continue
        if (uid, title) in recent:
            skipped += 1
            continue
        days, fee = service.late_fee(issue.due_date, today)
        await create_notification(session, user_id=uid, category=NotificationCategory.LIBRARY, title=title,
                                  message=f"\"{e['book_title']}\" (copy {e['accession_code']}) was due on {issue.due_date:%d %b %Y}, {days} day"
                                          f"{'s' if days != 1 else ''} ago." + (f" The late fee so far is Rs. {fee:,.0f}." if fee else "")
                                          + " Please return it to the library.", link=None)
        sent += 1
    await AuditService(session).log(actor_user_id=actor_id, action="library.overdue_reminders", entity_type="book_issue", entity_id=None,
                                    metadata={"sent": sent, "skipped_recent": skipped, "no_login": no_login})
    return {"sent": sent, "skipped_recent": skipped, "no_login": no_login}