"""Visiting faculty: paid per lecture they take (owner decisions, 1.66.0).

A visiting teacher (employment type VISITING) with a lecture rate on their payroll settings is paid per lecture, not a
monthly basic. For a month, every class of theirs that has ended is one lecture:
  * TAKEN      - they marked its attendance (a lab double period is two classes, so two lectures; a combined class taught
                 to several batches at once is ONE lecture; one-off extra and make-up classes count too);
  * COVERED    - another teacher covered it (the cover is paid to that teacher, as before);
  * BY_OFFICE  - the office marked its attendance on their behalf (not paid; add an adjustment if they did teach);
  * NOT_TAKEN  - nobody marked it.
Cancelled classes (holidays, cancelled by the office) are neither paid nor counted. The payslip shows every lecture
scheduled as earnings and the ones not taken / covered as deductions with their dates, so the person sees exactly
what was deducted and why. No day-absence or late-mark deductions apply to them. Read only: nothing here writes.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering
from app.attendance.models import AttendanceRecord
from app.core.clock import institute_now
from app.employees.models import Employee
from app.payroll import rules

VISITING = "VISITING"
LABEL = {"TAKEN": "Taken", "COVERED": "Covered by", "BY_OFFICE": "Marked by the office", "NOT_TAKEN": "Not taken", "UPCOMING": "Upcoming"}


async def lecture_rates(session: AsyncSession, employee_ids) -> dict[uuid.UUID, Decimal]:
    """{employee id: rate} for visiting faculty paid per lecture."""
    from app.payroll.models import EmployeePayrollSetting

    ids = list(employee_ids)
    if not ids:
        return {}
    rows = (await session.execute(select(Employee.id, EmployeePayrollSetting.lecture_rate).join(
        EmployeePayrollSetting, EmployeePayrollSetting.employee_id == Employee.id).where(
        Employee.id.in_(ids), Employee.employment_type == VISITING, EmployeePayrollSetting.lecture_rate.is_not(None),
        EmployeePayrollSetting.lecture_rate > 0))).all()
    return {r[0]: Decimal(str(r[1])) for r in rows}


async def lecture_sheet(session: AsyncSession, employee: Employee, month: date, *, rate: Decimal | None = None,
                        now: datetime | None = None) -> dict:
    """Every lecture of this person in the month, with what happened and what it pays."""
    from app.teaching.models import ClassCover

    start, end = rules.month_start(month), rules.month_end(month)
    now = now or institute_now()
    if rate is None:
        rate = (await lecture_rates(session, [employee.id])).get(employee.id, Decimal(0))
    uid = employee.user_id
    mine_marked = select(AttendanceRecord.class_session_id).where(AttendanceRecord.marked_by == uid) if uid else None
    cond = CourseOffering.assigned_teacher_id == employee.id
    if mine_marked is not None:
        cond = cond | ClassSession.id.in_(mine_marked)
    rows = (await session.execute(select(ClassSession, CourseOffering, Course).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
                                  .join(Course, Course.id == CourseOffering.course_id).where(
        ClassSession.session_date >= start, ClassSession.session_date <= end, ClassSession.status == ClassSessionStatus.SCHEDULED, cond)
        .order_by(ClassSession.session_date, ClassSession.start_time, Course.code))).all()
    ids = [cs.id for cs, _o, _c in rows]
    marked_by: dict[uuid.UUID, set] = {}
    for sid, who in (await session.execute(select(AttendanceRecord.class_session_id, AttendanceRecord.marked_by).where(
            AttendanceRecord.class_session_id.in_(ids or [uuid.uuid4()])))).all():
        marked_by.setdefault(sid, set()).add(who)
    covers = {c.class_session_id: c for c in (await session.execute(select(ClassCover).where(
        ClassCover.class_session_id.in_(ids or [uuid.uuid4()]), ClassCover.status == "ACTIVE"))).scalars()}
    subs = {e.id: f"{e.first_name} {e.last_name}".strip() for e in (await session.execute(select(Employee).where(
        Employee.id.in_({c.substitute_employee_id for c in covers.values()} or {uuid.uuid4()})))).scalars()}

    # a combined class is one lecture: its sections' classes at the same date and time are taken together
    groups: dict[tuple, list] = {}
    for cs, o, c in rows:
        groups.setdefault((o.combined_group_id or o.id, cs.session_date, cs.start_time), []).append((cs, o, c))
    lectures = []
    for (_g, day, _t), items in groups.items():
        cs0, _o0, c0 = items[0]
        ended = datetime.combine(day, cs0.end_time) <= now
        cover = next((covers[cs.id] for cs, _o, _c in items if cs.id in covers), None)
        by_me = uid is not None and any(uid in marked_by.get(cs.id, set()) for cs, _o, _c in items)
        taken_at_all = any(cs.attendance_taken_at is not None for cs, _o, _c in items)
        # a class with nobody enrolled has no records: its own teacher's class counts as theirs when it was taken in time
        empty_mine = taken_at_all and not any(marked_by.get(cs.id) for cs, _o, _c in items) and \
            not any(cs.late_marking_note for cs, _o, _c in items) and any(o.assigned_teacher_id == employee.id for _cs, o, _c in items)
        if not ended:
            state = "UPCOMING"
        elif by_me:
            state = "TAKEN"
        elif cover is not None:
            state = "COVERED"
        elif empty_mine:
            state = "TAKEN"
        elif taken_at_all:
            state = "BY_OFFICE"
        else:
            state = "NOT_TAKEN"
        sections = sorted({f"{o.term_label} ({o.section_label})" for _cs, o, _c in items})
        lectures.append({"class_session_id": cs0.id, "date": day, "start_time": cs0.start_time, "end_time": cs0.end_time,
                         "course": c0.code, "title": c0.title, "sections": sections, "room": cs0.room,
                         "extra": cs0.timetable_slot_id is None, "state": state,
                         "covered_by": subs.get(cover.substitute_employee_id) if cover is not None and state == "COVERED" else None,
                         "amount": float(rate) if state == "TAKEN" else 0.0})
    count = {k: sum(1 for x in lectures if x["state"] == k) for k in LABEL}
    held = count["TAKEN"] + count["COVERED"] + count["BY_OFFICE"] + count["NOT_TAKEN"]
    return {"employee": {"id": employee.id, "name": f"{employee.first_name} {employee.last_name}".strip(), "code": employee.employee_code},
            "month": start, "rate": float(rate), "lectures": lectures, "count": count, "held": held,
            "earned": float(rules.q2(rate * count["TAKEN"])),
            "not_paid": float(rules.q2(rate * (held - count["TAKEN"])))}


def _dates(items: list[dict], limit: int = 8) -> str:
    days = [f"{x['date']:%d %b}" for x in items]
    return ", ".join(days[:limit]) + (f" +{len(days) - limit} more" if len(days) > limit else "")


def payslip_lines(sheet: dict) -> tuple[list[tuple[str, str, Decimal]], list[tuple[str, str, Decimal]], str]:
    """(earnings, deductions, note) for a per-lecture payslip: every lecture held is earned, then the ones not taken
    are taken off again with their dates, so the deduction is visible."""
    rate = Decimal(str(sheet["rate"]))
    held, c = sheet["held"], sheet["count"]
    earn = [("LECTURES", f"Lectures held: {held} at Rs. {float(rate):,.0f} a lecture", rules.q2(rate * held))] if held else []
    ded = []
    by_state = {k: [x for x in sheet["lectures"] if x["state"] == k] for k in ("NOT_TAKEN", "BY_OFFICE", "COVERED")}
    if by_state["NOT_TAKEN"]:
        ded.append(("LECTURE_MISSED", f"Lectures not taken ({c['NOT_TAKEN']}): {_dates(by_state['NOT_TAKEN'])}"[:160], rules.q2(rate * c["NOT_TAKEN"])))
    if by_state["COVERED"]:
        who = sorted({x["covered_by"] or "another teacher" for x in by_state["COVERED"]})
        ded.append(("LECTURE_COVERED", f"Lectures covered by {', '.join(who)} ({c['COVERED']}): {_dates(by_state['COVERED'])}"[:160],
                    rules.q2(rate * c["COVERED"])))
    if by_state["BY_OFFICE"]:
        ded.append(("LECTURE_OFFICE", f"Attendance marked by the office ({c['BY_OFFICE']}): {_dates(by_state['BY_OFFICE'])}"[:160],
                    rules.q2(rate * c["BY_OFFICE"])))
    note = (f"Paid per lecture: {c['TAKEN']} of {held} lectures taken at Rs. {float(rate):,.0f}."
            + (f" {c['UPCOMING']} later this month not counted yet." if c["UPCOMING"] else ""))
    return earn, ded, note


# ---------------------------------------------------------------------- the office: rates and the month's lectures
async def overview(session: AsyncSession, month: date) -> dict:
    """Every visiting teacher still on the staff, their rate, and the month's lectures so far."""
    from app.employees.models import EmployeeStatus

    staff = (await session.execute(select(Employee).where(Employee.employment_type == VISITING, Employee.status != EmployeeStatus.TERMINATED)
                                   .order_by(Employee.first_name, Employee.last_name))).scalars().all()
    rates = await lecture_rates(session, [e.id for e in staff])
    people = []
    for e in staff:
        r = rates.get(e.id)
        s = await lecture_sheet(session, e, month, rate=r or Decimal(0))
        people.append({"employee_id": e.id, "name": s["employee"]["name"], "code": e.employee_code, "rate": float(r) if r else None,
                       "held": s["held"], "count": s["count"], "earned": s["earned"], "not_paid": s["not_paid"]})
    return {"month": rules.month_start(month), "people": people}


async def set_rate(session: AsyncSession, employee_id: uuid.UUID, rate, *, actor_id: uuid.UUID) -> dict:
    from app.audit.service import AuditService
    from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
    from app.payroll.models import EmployeePayrollSetting

    e = await session.get(Employee, employee_id)
    if e is None:
        raise ResourceNotFoundError("Employee not found.")
    if rate is not None and e.employment_type != VISITING:
        raise BusinessRuleViolationError(f"{e.first_name} {e.last_name} is not visiting faculty. Set the employment type to Visiting first "
                                         "(Employees → edit), then the lecture rate.")
    st = await session.get(EmployeePayrollSetting, employee_id)
    if st is None:
        st = EmployeePayrollSetting(employee_id=employee_id, security_opening=0, security_released=0)
        session.add(st)
    before = None if st.lecture_rate is None else str(st.lecture_rate)
    st.lecture_rate = None if rate is None else rules.q2(Decimal(str(rate)))
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action="payroll.lecture_rate_set", entity_type="employee", entity_id=employee_id,
                                    before={"lecture_rate": before}, after={"lecture_rate": None if rate is None else str(st.lecture_rate)})
    return {"employee_id": employee_id, "rate": None if st.lecture_rate is None else float(st.lecture_rate)}


def render_sheet_pdf(sheet: dict) -> bytes:
    """The month's lecture sheet: every lecture with what happened and what it pays."""
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from app.core import brand
    from app.core.timefmt import hm12

    buf = io.BytesIO()
    width = A4[0] - 30 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Lecture sheet {sheet['month']:%B %Y}", author=brand.name())
    base = getSampleStyleSheet()
    sub = ParagraphStyle("sub", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=10, textColor=colors.HexColor("#2454d6"), alignment=1, spaceAfter=8)
    small = ParagraphStyle("small", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10)
    e, c = sheet["employee"], sheet["count"]
    story = brand.platypus_header(width) + [Paragraph(f"LECTURE SHEET — {sheet['month']:%B %Y}".upper(), sub),
                                            Paragraph(f"<b>{e['name']}</b> ({e['code'] or ''}) · visiting faculty · Rs. {sheet['rate']:,.0f} a lecture", small),
                                            Spacer(1, 6)]
    rows = [["Date", "Time", "Course", "Batch / section", "What happened", "Rs."]]
    for x in sheet["lectures"]:
        what = LABEL[x["state"]] + (f" {x['covered_by']}" if x["covered_by"] else "") + (" (extra class)" if x["extra"] else "")
        rows.append([f"{x['date']:%a %d %b}", f"{hm12(x['start_time'])}", Paragraph(f"{x['course']}", small),
                     Paragraph(", ".join(x["sections"]), small), Paragraph(what, small), f"{x['amount']:,.0f}" if x["state"] == "TAKEN" else "—"])
    t = Table(rows, colWidths=[24 * mm, 20 * mm, 22 * mm, 50 * mm, 46 * mm, 18 * mm], repeatRows=1)
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                           ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#d5dbe8")), ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fc")])]))
    story += [t, Spacer(1, 8)]
    summary = [["Lectures held", str(sheet["held"])], ["Taken (paid)", str(c["TAKEN"])], ["Not taken", str(c["NOT_TAKEN"])],
               ["Covered by another teacher", str(c["COVERED"])], ["Marked by the office", str(c["BY_OFFICE"])],
               ["Earned", f"Rs. {sheet['earned']:,.0f}"], ["Not paid", f"Rs. {sheet['not_paid']:,.0f}"]]
    if c["UPCOMING"]:
        summary.append(["Later this month (not counted yet)", str(c["UPCOMING"])])
    s = Table(summary, colWidths=[60 * mm, 30 * mm], hAlign="RIGHT")
    s.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("FONT", (0, 5), (-1, 5), "Helvetica-Bold", 9), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                           ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d5dbe8"))]))
    story.append(s)
    doc.build(story)
    return buf.getvalue()
