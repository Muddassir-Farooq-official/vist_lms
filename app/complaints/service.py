from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.complaints.models import Complaint, ComplaintPartyType, ComplaintReason, ComplaintStatus
from app.complaints.repository import OPEN_STATUSES, ComplaintRepository
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.employees.models import Employee, JobRole
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.students.models import Student

# Threshold for flagging a target in the analytics view — documented
# ASSUMPTION (no approved policy exists yet for what counts as "a
# pattern"). Easy to change in one place, or make configurable later.
HIGHLIGHT_THRESHOLD = 3

TERMINAL_STATUSES = (ComplaintStatus.RESOLVED, ComplaintStatus.DISMISSED, ComplaintStatus.WITHDRAWN)

REASON_LABELS = {
    ComplaintReason.HARASSMENT: "Harassment",
    ComplaintReason.MISBEHAVIOR: "Misbehavior",
    ComplaintReason.DISCRIMINATION: "Discrimination",
    ComplaintReason.NEGLIGENCE: "Negligence",
    ComplaintReason.ACADEMIC_DISPUTE: "Academic Dispute",
    ComplaintReason.FACILITY_ISSUE: "Facility Issue",
    ComplaintReason.FINANCIAL_DISPUTE: "Financial Dispute",
    ComplaintReason.OTHER: "Other",
}


@dataclass
class PartyInfo:
    name: str
    identifier: str | None
    role_label: str


async def resolve_party(session: AsyncSession, party_type: ComplaintPartyType, party_id: uuid.UUID) -> PartyInfo:
    if party_type == ComplaintPartyType.STUDENT:
        student = await session.get(Student, party_id)
        if student is None:
            return PartyInfo(name="Unknown student", identifier=None, role_label="Student")
        return PartyInfo(
            name=f"{student.first_name} {student.last_name}", identifier=student.roll_number, role_label="Student"
        )

    employee = await session.get(Employee, party_id)
    if employee is None:
        return PartyInfo(name="Unknown employee", identifier=None, role_label="Staff")
    job_role = await session.get(JobRole, employee.job_role_id)
    role_label = job_role.title if job_role else "Staff"
    return PartyInfo(name=f"{employee.first_name} {employee.last_name}", identifier=employee.employee_code, role_label=role_label)


async def _resolve_user_id(session: AsyncSession, party_type: ComplaintPartyType, party_id: uuid.UUID) -> uuid.UUID | None:
    """Same one-line lookup as app/fines/service.py — notifications are
    keyed on users.id, not the student/employee record."""
    if party_type == ComplaintPartyType.STUDENT:
        student = await session.get(Student, party_id)
        return student.user_id if student else None
    employee = await session.get(Employee, party_id)
    return employee.user_id if employee else None


async def party_exists(session: AsyncSession, party_type: ComplaintPartyType, party_id: uuid.UUID) -> bool:
    if party_type == ComplaintPartyType.STUDENT:
        return (await session.get(Student, party_id)) is not None
    return (await session.get(Employee, party_id)) is not None


class ComplaintService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ComplaintRepository(session)
        self.audit = AuditService(session)

    async def get(self, complaint_id: uuid.UUID) -> Complaint:
        complaint = await self.repo.get_by_id(complaint_id)
        if complaint is None:
            raise ResourceNotFoundError("Complaint not found.")
        return complaint

    async def submit(
        self,
        *,
        complainant_type: ComplaintPartyType,
        complainant_id: uuid.UUID,
        target_type: ComplaintPartyType,
        target_id: uuid.UUID,
        reason: ComplaintReason,
        custom_reason: str | None,
        description: str,
        actor_id: uuid.UUID,
        is_anonymous: bool = False,
    ) -> Complaint:
        if not await party_exists(self.session, target_type, target_id):
            raise ResourceNotFoundError(f"{target_type.value.title()} not found.")

        if target_type == complainant_type and target_id == complainant_id:
            raise BusinessRuleViolationError("You cannot file a complaint against yourself.")

        complaint = Complaint(
            complainant_type=complainant_type,
            complainant_id=complainant_id,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            custom_reason=custom_reason.strip() if custom_reason else None,
            description=description.strip(),
            is_anonymous=is_anonymous,
            status=ComplaintStatus.SUBMITTED,
        )
        await self.repo.create(complaint)

        await self.audit.log(
            actor_user_id=actor_id,
            action="complaint.submitted",
            entity_type="complaint",
            entity_id=complaint.id,
            after={
                "target_type": target_type.value,
                "target_id": str(target_id),
                "reason": reason.value,
                "anonymous": is_anonymous,
            },
        )
        return complaint

    async def list_own(self, complainant_type: ComplaintPartyType, complainant_id: uuid.UUID) -> list[Complaint]:
        return await self.repo.list_for_complainant(complainant_type, complainant_id)

    async def list_all(
        self,
        *,
        status: ComplaintStatus | None = None,
        target_type: ComplaintPartyType | None = None,
        target_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> list[Complaint]:
        return await self.repo.list_all(status=status, target_type=target_type, target_id=target_id, reason=reason)

    async def admin_update_status(
        self, complaint_id: uuid.UUID, *, new_status: ComplaintStatus, actor_id: uuid.UUID, note: str | None
    ) -> Complaint:
        complaint = await self.get(complaint_id)
        old_status = complaint.status
        complaint.status = new_status
        if new_status == ComplaintStatus.UNDER_REVIEW and complaint.under_review_at is None:
            complaint.under_review_at = datetime.now(timezone.utc)
        complaint.resolution_note = note
        if new_status in TERMINAL_STATUSES:
            complaint.resolved_by = actor_id
            complaint.resolved_at = datetime.now(timezone.utc)

        await self.audit.log(
            actor_user_id=actor_id,
            action="complaint.status_updated_by_admin",
            entity_type="complaint",
            entity_id=complaint.id,
            before={"status": old_status.value},
            after={"status": new_status.value},
        )

        complainant_user_id = await _resolve_user_id(self.session, complaint.complainant_type, complaint.complainant_id)
        if complainant_user_id is not None:
            status_labels = {
                ComplaintStatus.UNDER_REVIEW: "is now under review",
                ComplaintStatus.RESOLVED: "has been resolved",
                ComplaintStatus.DISMISSED: "has been dismissed",
            }
            await create_notification(
                self.session, user_id=complainant_user_id, category=NotificationCategory.COMPLAINT,
                title="Your complaint status changed",
                message=f"Your complaint {status_labels.get(new_status, 'status changed to ' + new_status.value)}.",
            )

        return complaint

    async def _self_action(
        self,
        complaint_id: uuid.UUID,
        *,
        complainant_type: ComplaintPartyType,
        complainant_id: uuid.UUID,
        actor_id: uuid.UUID,
        new_status: ComplaintStatus,
        action_label: str,
        note: str | None,
    ) -> Complaint:
        complaint = await self.get(complaint_id)
        if complaint.complainant_type != complainant_type or complaint.complainant_id != complainant_id:
            # Same "not found" a stranger's ID would produce.
            raise ResourceNotFoundError("Complaint not found.")
        if complaint.status in TERMINAL_STATUSES:
            raise BusinessRuleViolationError(f"This complaint is already {complaint.status.value.lower()}.")

        complaint.status = new_status
        complaint.resolution_note = note
        complaint.resolved_by = actor_id
        complaint.resolved_at = datetime.now(timezone.utc)

        await self.audit.log(
            actor_user_id=actor_id,
            action=f"complaint.{action_label}_by_complainant",
            entity_type="complaint",
            entity_id=complaint.id,
            after={"status": new_status.value},
        )
        return complaint

    async def self_withdraw(
        self, complaint_id: uuid.UUID, *, complainant_type: ComplaintPartyType, complainant_id: uuid.UUID,
        actor_id: uuid.UUID, note: str | None = None,
    ) -> Complaint:
        return await self._self_action(
            complaint_id, complainant_type=complainant_type, complainant_id=complainant_id, actor_id=actor_id,
            new_status=ComplaintStatus.WITHDRAWN, action_label="withdrawn", note=note,
        )

    async def self_resolve(
        self, complaint_id: uuid.UUID, *, complainant_type: ComplaintPartyType, complainant_id: uuid.UUID,
        actor_id: uuid.UUID, note: str | None = None,
    ) -> Complaint:
        return await self._self_action(
            complaint_id, complainant_type=complainant_type, complainant_id=complainant_id, actor_id=actor_id,
            new_status=ComplaintStatus.RESOLVED, action_label="resolved", note=note,
        )

    async def analytics(self) -> list[dict]:
        """One entry per target person who has at least one complaint,
        sorted by total count descending — the data behind the admin
        'who has the most complaints' graph. Flags anyone at or above
        HIGHLIGHT_THRESHOLD so the frontend can visually call them out."""
        raw = await self.repo.target_counts()
        entries = []
        for target_type, target_id, total_count, open_count in raw:
            party = await resolve_party(self.session, target_type, target_id)
            entries.append(
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "target_name": party.name,
                    "target_role_label": party.role_label,
                    "total_count": total_count,
                    "open_count": open_count,
                    "highlighted": total_count >= HIGHLIGHT_THRESHOLD,
                }
            )
        return entries

    async def reveal_identity(self, complaint_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        """An administrator deliberately looks up who filed a complaint. For an anonymous one this is
        written to the audit log: the name is available, but never casually."""
        complaint = await self.get(complaint_id)
        party = await resolve_party(self.session, complaint.complainant_type, complaint.complainant_id)
        if complaint.is_anonymous:
            await self.audit.log(
                actor_user_id=actor_id, action="complaint.identity_revealed", entity_type="complaint", entity_id=complaint.id,
            )
        return {
            "complaint_id": complaint.id, "complainant_type": complaint.complainant_type, "complainant_id": complaint.complainant_id,
            "complainant_name": party.name, "complainant_identifier": party.identifier,
        }

    async def overview(self) -> dict:
        """Everything the admin charts need, in one call (computed in the institute's own timezone)."""
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        from sqlalchemy import select

        from app.core.clock import institute_today
        from app.core.config import settings

        rows = (await self.session.execute(
            select(Complaint.status, Complaint.reason, Complaint.created_at, Complaint.resolved_at, Complaint.is_anonymous,
                   Complaint.complainant_type, Complaint.target_type).limit(50000)
        )).all()
        tz = ZoneInfo(settings.institute_timezone)
        today = institute_today()
        local = lambda dt: dt.astimezone(tz).date()          # noqa: E731

        status_counts = {st: 0 for st in ComplaintStatus}
        reason_counts: dict = {}
        by_party: dict = {"filed_by": {}, "filed_against": {}}
        hours, last30, prev30, anonymous, oldest_open = [], 0, 0, 0, None
        monday = today - timedelta(days=today.weekday())
        weeks = {monday - timedelta(weeks=i): [0, 0] for i in range(11, -1, -1)}
        for status, reason, created_at, resolved_at, is_anon, c_type, t_type in rows:
            status_counts[status] += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            by_party["filed_by"][c_type] = by_party["filed_by"].get(c_type, 0) + 1
            by_party["filed_against"][t_type] = by_party["filed_against"].get(t_type, 0) + 1
            anonymous += 1 if is_anon else 0
            created = local(created_at)
            age = (today - created).days
            last30 += 1 if age < 30 else 0
            prev30 += 1 if 30 <= age < 60 else 0
            if status in OPEN_STATUSES:
                oldest_open = age if oldest_open is None else max(oldest_open, age)
            if resolved_at is not None and status in (ComplaintStatus.RESOLVED, ComplaintStatus.DISMISSED):
                hours.append((resolved_at - created_at).total_seconds() / 3600)
            wk = created - timedelta(days=created.weekday())
            if wk in weeks:
                weeks[wk][0] += 1
            if resolved_at is not None:
                rk = local(resolved_at)
                rk = rk - timedelta(days=rk.weekday())
                if rk in weeks:
                    weeks[rk][1] += 1
        total = len(rows)
        status_labels = {
            ComplaintStatus.SUBMITTED: "Submitted", ComplaintStatus.UNDER_REVIEW: "Under review", ComplaintStatus.RESOLVED: "Resolved",
            ComplaintStatus.DISMISSED: "Dismissed", ComplaintStatus.WITHDRAWN: "Withdrawn",
        }
        party_label = {ComplaintPartyType.STUDENT: "Students", ComplaintPartyType.EMPLOYEE: "Staff"}
        return {
            "kpis": {
                "total": total,
                "open": status_counts[ComplaintStatus.SUBMITTED] + status_counts[ComplaintStatus.UNDER_REVIEW],
                "under_review": status_counts[ComplaintStatus.UNDER_REVIEW],
                "resolved": status_counts[ComplaintStatus.RESOLVED], "dismissed": status_counts[ComplaintStatus.DISMISSED],
                "withdrawn": status_counts[ComplaintStatus.WITHDRAWN], "anonymous": anonymous,
                "anonymous_pct": round(anonymous / total * 100, 1) if total else 0.0,
                "avg_resolution_hours": round(sum(hours) / len(hours), 1) if hours else None,
                "filed_last_30_days": last30, "filed_previous_30_days": prev30, "oldest_open_days": oldest_open,
            },
            "by_status": [{"key": st.value, "label": status_labels[st], "count": n} for st, n in status_counts.items()],
            "by_reason": sorted(
                ({"key": r.value, "label": REASON_LABELS[r], "count": n} for r, n in reason_counts.items()), key=lambda x: -x["count"]
            ),
            "by_week": [{"week_start": wk, "filed": v[0], "closed": v[1]} for wk, v in weeks.items()],
            "filed_by": [{"key": k.value, "label": party_label[k], "count": n} for k, n in by_party["filed_by"].items()],
            "filed_against": [{"key": k.value, "label": party_label[k], "count": n} for k, n in by_party["filed_against"].items()],
        }


    async def public_stats(self) -> dict:
        """The picture that is safe to show EVERYONE: how many complaints, what kinds, how fast they are dealt with.
        No names, no identifiers, nothing about any individual or about who is complained against, no anonymity
        figures. Small groups are reported as "1-2" rather than exact, so no single complaint can be picked out."""
        full = await self.overview()
        k = full["kpis"]
        keep = {n: k[n] for n in ("total", "open", "under_review", "resolved", "dismissed", "withdrawn", "avg_resolution_hours",
                                  "filed_last_30_days", "filed_previous_30_days")}
        return {
            "kpis": keep,
            "by_status": full["by_status"],
            "by_reason": [r for r in full["by_reason"]],
            "by_week": full["by_week"],
            "resolution_rate": round((k["resolved"] / max(k["total"] - k["withdrawn"], 1)) * 100, 1) if k["total"] else None,
        }