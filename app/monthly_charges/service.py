"""Monthly hostel / mess / transport charges: prepared as a draft, approved by the accounts office (models.py)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.monthly_charges.models import ChargeLine, ChargeRun

KINDS = ("HOSTEL", "TRANSPORT")
TITLES = {"HOSTEL": "Hostel and mess", "TRANSPORT": "Transport"}


class MonthlyChargeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prepare(self, kind: str, month: date, *, actor_id: uuid.UUID) -> dict:
        """Work out the month's lines into a draft (replacing an earlier draft of the same month). Nothing is charged."""
        if kind not in KINDS:
            raise BusinessRuleViolationError("Hostel or transport.")
        month = month.replace(day=1)
        old = (await self.session.execute(select(ChargeRun).where(ChargeRun.kind == kind, ChargeRun.month == month, ChargeRun.status == "DRAFT"))).scalar_one_or_none()
        if old is not None:
            old.status, old.decided_by, old.decided_at, old.note = "DISCARDED", actor_id, datetime.now(timezone.utc), "Prepared again"
            await self.session.flush()
        lines = await (self._hostel_lines(month) if kind == "HOSTEL" else self._transport_lines(month))
        run = ChargeRun(kind=kind, month=month, status="DRAFT", prepared_by=actor_id)
        self.session.add(run)
        await self.session.flush()
        for ln in lines:
            self.session.add(ChargeLine(run_id=run.id, month=month, **ln))
        run.students = len({ln["student_id"] for ln in lines})
        run.total = sum((Decimal(str(ln["amount"])) for ln in lines), Decimal(0))
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="charges.prepared", entity_type="charge_run", entity_id=run.id,
                                             metadata={"kind": kind, "month": str(month), "lines": len(lines), "total": str(run.total)})
        await self._tell_accounts(run)
        return await self.detail(run.id)

    async def _hostel_lines(self, month: date) -> list[dict]:
        from app.hostel.service import HostelService

        out = []
        for ln in (await HostelService(self.session).month_lines(month))["lines"]:
            st = ln["stay"]
            if ln["rent"] > 0:
                out.append({"kind": "HOSTEL", "ref_id": st.id, "student_id": st.student_id, "amount": ln["rent"], "days": ln["covered"], "label": ln["label"]})
            if ln["mess"] > 0:
                out.append({"kind": "MESS", "ref_id": st.id, "student_id": st.student_id, "amount": ln["mess"], "days": ln["covered"], "label": ln["mess_label"]})
        return out

    async def _transport_lines(self, month: date) -> list[dict]:
        from app.transport.service import TransportService

        return await TransportService(self.session).month_lines(month)

    async def _tell_accounts(self, run: ChargeRun) -> None:
        """Never raises."""
        try:
            async with self.session.begin_nested():
                from app.auth.models import Role, RolePermission, UserRole, Permission
                from app.notifications.models import NotificationCategory
                from app.notifications.service import create_notification

                users = set((await self.session.execute(select(UserRole.user_id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
                                                        .join(Permission, Permission.id == RolePermission.permission_id)
                                                        .join(Role, Role.id == UserRole.role_id)
                                                        .where(Permission.code == "charges.approve", Role.code == "ACCOUNTS_OFFICER"))).scalars())
                for uid in users:
                    await create_notification(self.session, user_id=uid, category=NotificationCategory.FEE,
                                              title=f"{TITLES[run.kind]} charges for {run.month:%B %Y} to approve",
                                              message=f"{run.students} student(s), Rs. {float(run.total):,.0f}.", link="/dashboard/accounts/monthly-charges")
        except Exception:                       # noqa: BLE001
            pass

    async def runs(self, *, status: str | None = None, kind: str | None = None) -> list[dict]:
        q = select(ChargeRun).order_by(ChargeRun.prepared_at.desc()).limit(100)
        if status:
            q = q.where(ChargeRun.status == status)
        if kind:
            q = q.where(ChargeRun.kind == kind)
        return [self._run(r) for r in (await self.session.execute(q)).scalars()]

    @staticmethod
    def _run(r: ChargeRun) -> dict:
        return {"id": r.id, "kind": r.kind, "title": TITLES.get(r.kind, r.kind), "month": r.month, "status": r.status, "students": r.students,
                "total": float(r.total or 0), "prepared_at": r.prepared_at, "decided_at": r.decided_at, "note": r.note}

    async def detail(self, run_id: uuid.UUID) -> dict:
        from app.students.models import Student

        r = await self.session.get(ChargeRun, run_id)
        if r is None:
            raise ResourceNotFoundError("Draft not found.")
        rows = (await self.session.execute(select(ChargeLine, Student).join(Student, Student.id == ChargeLine.student_id).where(ChargeLine.run_id == r.id)
                                           .order_by(Student.roll_number, ChargeLine.kind))).all()
        return {**self._run(r), "lines": [{"id": ln.id, "kind": ln.kind, "student_id": s.id, "student": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number,
                                           "amount": float(ln.amount), "days": ln.days, "label": ln.label, "charged": ln.fine_id is not None, "skipped": ln.skipped}
                                          for ln, s in rows]}

    async def approve(self, run_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        """Add every line to the students' dues. A line whose stay or seat was already charged for the month (by an earlier
        approval or the administrator) is skipped and says why, so nothing is charged twice."""
        from app.fines.models import FinePayerType, FineSource
        from app.fines.service import FineService
        from app.hostel.models import HostelCharge, HostelStay
        from app.hostel.service import HostelService

        r = (await self.session.execute(select(ChargeRun).where(ChargeRun.id == run_id).with_for_update())).scalar_one_or_none()
        if r is None:
            raise ResourceNotFoundError("Draft not found.")
        if r.status != "DRAFT":
            raise BusinessRuleViolationError("This draft was already decided.")
        lines = (await self.session.execute(select(ChargeLine).where(ChargeLine.run_id == r.id))).scalars().all()
        charged, total, skipped = 0, Decimal(0), 0
        for ln in lines:
            if ln.kind == "HOSTEL":
                if (await self.session.execute(select(HostelCharge.id).where(HostelCharge.stay_id == ln.ref_id, HostelCharge.month == ln.month))).first():
                    ln.skipped, skipped = "Already charged for this month", skipped + 1
                    continue
                st = await self.session.get(HostelStay, ln.ref_id)
                fine = await HostelService(self.session).charge_stay(st, ln.month, Decimal(str(ln.amount)), ln.label, actor_id=actor_id)
            else:
                dup = (await self.session.execute(select(ChargeLine.id).where(ChargeLine.kind == ln.kind, ChargeLine.ref_id == ln.ref_id,
                                                                              ChargeLine.month == ln.month, ChargeLine.fine_id.is_not(None)))).first()
                if dup:
                    ln.skipped, skipped = "Already charged for this month", skipped + 1
                    continue
                transport = ln.kind == "TRANSPORT"
                fine = await FineService(self.session).issue(payer_type=FinePayerType.STUDENT, payer_id=ln.student_id, amount=float(ln.amount), reason=ln.label,
                                                             source=FineSource.TRANSPORT if transport else FineSource.HOSTEL, actor_id=actor_id,
                                                             related_entity_type="transport_rider" if transport else "hostel_stay", related_entity_id=ln.ref_id,
                                                             notify_title=("Transport" if transport else "Mess") + " charge added to your dues")
            ln.fine_id = fine.id
            charged += 1
            total += Decimal(str(ln.amount))
            await self.session.flush()
        r.status, r.decided_by, r.decided_at = "APPROVED", actor_id, datetime.now(timezone.utc)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="charges.approved", entity_type="charge_run", entity_id=r.id,
                                             metadata={"kind": r.kind, "month": str(r.month), "charged": charged, "total": str(total), "skipped": skipped})
        return {"id": r.id, "status": r.status, "charged": charged, "total": float(total), "skipped": skipped}

    async def discard(self, run_id: uuid.UUID, *, note: str, actor_id: uuid.UUID) -> dict:
        r = await self.session.get(ChargeRun, run_id)
        if r is None:
            raise ResourceNotFoundError("Draft not found.")
        if r.status != "DRAFT":
            raise BusinessRuleViolationError("This draft was already decided.")
        if len((note or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why, so the office can correct it.")
        r.status, r.decided_by, r.decided_at, r.note = "DISCARDED", actor_id, datetime.now(timezone.utc), note.strip()[:300]
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="charges.discarded", entity_type="charge_run", entity_id=r.id,
                                             metadata={"kind": r.kind, "month": str(r.month), "note": r.note})
        return {"id": r.id, "status": r.status}

    async def waiting(self) -> int:
        return int((await self.session.execute(select(func.count()).select_from(ChargeRun).where(ChargeRun.status == "DRAFT"))).scalar_one())
