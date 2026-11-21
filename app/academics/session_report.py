"""A session's report: one row per batch term of the session, and the session's totals.

For each batch term: its course sections and students; attendance (average of every student's % in every course with
classes held, and how many students are below the minimum to pass in at least one course); results (course result
sheets by approval stage); fees charged to its students for that term (charged, concessions, paid, still owed, of which
overdue), worked out from the fee ledger exactly as the fee screens do (FeeStatusCalculator). Read-only.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.terms import AcademicSession, AcademicTerm
from app.core.config import settings
from app.core.exceptions import ResourceNotFoundError

STAGES = ("DRAFT", "SUBMITTED", "RETURNED", "APPROVED", "PUBLISHED")


async def session_report(session: AsyncSession, session_id: uuid.UUID) -> dict:
    from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus, ResultSheet
    from app.attendance.shortfall import percentages
    from app.core.clock import institute_today
    from app.fees.assignment_service import FeeStatusCalculator, late_pause, late_rate
    from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, PayerType
    from app.programs.models import Program

    ses = await session.get(AcademicSession, session_id)
    if ses is None:
        raise ResourceNotFoundError("Session not found.")
    terms = sorted((await session.execute(select(AcademicTerm).where(AcademicTerm.session_id == ses.id))).scalars(), key=lambda t: t.label.lower())
    labels = [t.label for t in terms]
    progs = {p.id: p.code for p in (await session.execute(select(Program))).scalars()}
    minimum = float(settings.attendance_eligibility_threshold_percent)
    rows = {t.label: {"term": t.label, "program": progs.get(t.program_id), "semester": t.semester, "start_date": t.start_date,
                      "end_date": t.end_date, "status": t.status.value, "sections": 0, "courses": 0, "students": 0,
                      "attendance": {"average": None, "below_minimum": 0},
                      "results": {s: 0 for s in STAGES},
                      "fees": {"charged": 0.0, "concession": 0.0, "paid": 0.0, "remaining": 0.0, "overdue": 0.0}} for t in terms}
    if labels:
        offs = list((await session.execute(select(CourseOffering).where(CourseOffering.term_label.in_(labels)))).scalars())
        for lbl in labels:
            mine = [o for o in offs if o.term_label == lbl]
            rows[lbl]["courses"] = len(mine)
            rows[lbl]["sections"] = len({(o.section_label or "A").upper() for o in mine})
        for lbl, n in (await session.execute(select(CourseOffering.term_label, func.count(func.distinct(Enrollment.student_id)))
                                             .join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
                                             .where(CourseOffering.term_label.in_(labels), Enrollment.status == EnrollmentStatus.ENROLLED)
                                             .group_by(CourseOffering.term_label))).all():
            rows[lbl]["students"] = int(n)
        sheets = {sid: st.value for sid, st in (await session.execute(select(ResultSheet.course_offering_id, ResultSheet.status).where(
            ResultSheet.course_offering_id.in_([o.id for o in offs])))).all()} if offs else {}
        for o in offs:
            rows[o.term_label]["results"][sheets.get(o.id, "DRAFT")] += 1
        for lbl in labels:
            pcs = [p for p in await percentages(session, term_label=lbl) if p["taken"] > 0]
            if pcs:
                rows[lbl]["attendance"]["average"] = round(sum(p["percentage"] for p in pcs) / len(pcs), 1)
                rows[lbl]["attendance"]["below_minimum"] = len({p["student_id"] for p in pcs if p["percentage"] < minimum})

        # fees: each student fee whose term (its own, or its fee structure's) is one of the session's terms
        found = (await session.execute(select(FeeAssignment, FeeStructure).join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id)
                                       .where(FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.status == AssignmentStatus.ACTIVE,
                                              func.coalesce(FeeAssignment.term_label, FeeStructure.term_label).in_(labels)))).all()
        entries = defaultdict(list)
        ids = [a.id for a, _s in found]
        if ids:
            for e in (await session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id.in_(ids)))).scalars():
                entries[e.fee_assignment_id].append(e)
        today = institute_today()
        for a, structure in found:
            f = rows[a.term_label or structure.term_label]["fees"]
            for r in FeeStatusCalculator.installments(entries[a.id], late_rate(structure), as_of=today, pause=late_pause(a)):
                f["charged"] += r["charged"]
                f["concession"] += r["concession"]
                f["paid"] += r["paid"]
                f["remaining"] += r["remaining"]
                if r["status"] == "OVERDUE":
                    f["overdue"] += r["remaining"]
    out = list(rows.values())
    for r in out:
        r["fees"] = {k: round(v, 2) for k, v in r["fees"].items()}
    att = [r["attendance"]["average"] for r in out if r["attendance"]["average"] is not None]
    totals = {"terms": len(out), "courses": sum(r["courses"] for r in out), "students": sum(r["students"] for r in out),
              "below_minimum": sum(r["attendance"]["below_minimum"] for r in out),
              "attendance_average": round(sum(att) / len(att), 1) if att else None,
              "results": {s: sum(r["results"][s] for r in out) for s in STAGES},
              "fees": {k: round(sum(r["fees"][k] for r in out), 2) for k in ("charged", "concession", "paid", "remaining", "overdue")}}
    return {"session": {"id": ses.id, "name": ses.name}, "minimum_attendance": minimum, "terms": out, "totals": totals}
