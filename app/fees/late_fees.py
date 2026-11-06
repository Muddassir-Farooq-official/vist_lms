"""Late fees that are building up on instalments still unpaid (shown on the Fines screen).

A late fee keeps growing while its instalment is unpaid. It becomes a Fine (in the Fines section, collectible) when the
instalment is paid -- see post_settled_late_fees -- or when the administration posts it earlier ("Post them as fines
now"). Posting never charges a day twice: only what was not posted before is added.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fees.assignment_service import FeeAssignmentService, FeeStatusCalculator, late_pause
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, PayerType


async def _rows(session: AsyncSession):
    """(assignment, structure, row) for every unpaid instalment with a late fee not posted yet."""
    pairs = (await session.execute(select(FeeAssignment, FeeStructure).join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id)
                                   .where(FeeAssignment.status == AssignmentStatus.ACTIVE, FeeStructure.late_fee_per_day > 0))).all()
    if not pairs:
        return []
    ledger: dict[uuid.UUID, list] = defaultdict(list)
    ids = [a.id for a, _ in pairs]
    for i in range(0, len(ids), 1000):
        for e in (await session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id.in_(ids[i:i + 1000]))
                                        .order_by(FeeLedgerEntry.created_at))).scalars():
            ledger[e.fee_assignment_id].append(e)
    out = []
    for a, s in pairs:
        for r in FeeStatusCalculator.installments(ledger[a.id], float(s.late_fee_per_day), pause=late_pause(a)):
            unposted = round(r["late_fee"] - float(r.get("late_fee_posted") or 0), 2)
            if r["remaining"] > 0.005 and unposted > 0.005:
                out.append((a, s, r, unposted))
    return out


async def building(session: AsyncSession, *, limit: int = 300) -> dict:
    from app.employees.models import Employee
    from app.students.models import Student

    rows = await _rows(session)
    per: dict[tuple, dict] = {}
    for a, s, r, unposted in rows:
        k = (a.payer_type, a.payer_id)
        p = per.setdefault(k, {"payer_type": a.payer_type.value, "payer_id": a.payer_id, "amount": 0.0, "instalments": 0, "oldest_due": None,
                               "rate_now": 0.0, "fees": set()})
        p["amount"] += unposted
        p["instalments"] += 1
        p["fees"].add(s.name)
        p["rate_now"] += float(r.get("late_fee_rate_now") or 0)
        if r["due_date"] and (p["oldest_due"] is None or r["due_date"] < p["oldest_due"]):
            p["oldest_due"] = r["due_date"]
    sids = [pid for (t, pid) in per if t == PayerType.STUDENT]
    eids = [pid for (t, pid) in per if t != PayerType.STUDENT]
    names = {}
    if sids:
        for st in (await session.execute(select(Student).where(Student.id.in_(sids)))).scalars():
            names[st.id] = (f"{st.first_name} {st.last_name}".strip(), st.roll_number)
    if eids:
        for e in (await session.execute(select(Employee).where(Employee.id.in_(eids)))).scalars():
            names[e.id] = (f"{e.first_name} {e.last_name}".strip(), e.employee_code)
    people = []
    for (t, pid), p in per.items():
        nm, code = names.get(pid, ("?", ""))
        people.append({**p, "name": nm, "code": code, "amount": round(p["amount"], 2), "rate_now": round(p["rate_now"], 2), "fees": sorted(p["fees"])})
    people.sort(key=lambda x: -x["amount"])
    return {"total": round(sum(x["amount"] for x in people), 2), "people": len(people), "instalments": sum(x["instalments"] for x in people),
            "list": people[:limit]}


async def post_all(session: AsyncSession, actor_id: uuid.UUID) -> dict:
    """Post every late fee built up so far as fines now (the instalments stay owed; later days are added when paid)."""
    svc = FeeAssignmentService(session)
    count, total = 0, 0.0
    for _a, _s, r, _unposted in await _rows(session):
        fine = await svc.post_late_fee(r["ledger_entry_id"], actor_id=actor_id)
        if fine is not None:
            count += 1
            total += float(fine.amount)
    return {"fines": count, "total": round(total, 2)}