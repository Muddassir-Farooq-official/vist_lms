"""A school report card as a one-page A4 PDF (app/school/results.py builds the data)."""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.records.pdf import INK, LINE, MUTED, NAVY, _draw_logo, _logo_reader, _wrap

RATING = {"A": "A - Excellent", "B": "B - Good", "C": "C - Fair", "D": "D - Needs improvement"}
PASS, FAIL = colors.HexColor("#0b6b5d"), colors.HexColor("#b42318")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _num(v) -> str:
    return "-" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))


def render_report_card(d: dict, *, institute: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    left, right = 16 * mm, W - 16 * mm
    c.setTitle(f"{d['exam']['name']} report card - {d['student']['name']}")

    # header
    _draw_logo(c, left, H - 34 * mm, 20 * mm, _logo_reader())
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(left + 25 * mm, H - 22 * mm, institute[:70])
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(left + 25 * mm, H - 29 * mm, f"Report card - {d['exam']['name']} examination, session {d['session']}")
    c.setStrokeColor(LINE)
    c.line(left, H - 38 * mm, right, H - 38 * mm)

    # the student
    y = H - 46 * mm
    c.setFont("Helvetica", 9.5)
    pairs = [("Student", d["student"]["name"]), ("Roll no.", d["student"]["roll_number"] or "-"),
             ("Class", f"{d['class']} - Section {d['section'] or '-'}"), ("Parent / guardian", d["student"].get("guardian") or "-")]
    for i, (k, v) in enumerate(pairs):
        x = left if i % 2 == 0 else W / 2
        yy = y - (i // 2) * 6 * mm
        c.setFillColor(MUTED)
        c.drawString(x, yy, k)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 30 * mm, yy, str(v)[:48])
        c.setFont("Helvetica", 9.5)

    # marks
    y -= 17 * mm
    cols = [left, left + 78 * mm, left + 103 * mm, left + 128 * mm, left + 153 * mm]
    heads = ["Subject", "Total", "Obtained", "Percentage", "Grade"]
    c.setFillColor(colors.HexColor("#eef2fb"))
    c.rect(left, y - 2 * mm, right - left, 7 * mm, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 9.5)
    for x, h in zip(cols, heads):
        c.drawString(x + 2 * mm, y, h)
    c.setFont("Helvetica", 9.5)
    for s in d["subjects"]:
        y -= 6.5 * mm
        c.setStrokeColor(LINE)
        c.line(left, y - 2 * mm, right, y - 2 * mm)
        c.setFillColor(INK)
        c.drawString(cols[0] + 2 * mm, y, s["subject"][:44])
        c.drawString(cols[1] + 2 * mm, y, _num(s["total"]))
        c.drawString(cols[2] + 2 * mm, y, "Absent" if s["absent"] else _num(s["marks"]))
        c.drawString(cols[3] + 2 * mm, y, "-" if s["percentage"] is None else f"{s['percentage']:.1f}%")
        c.setFillColor(FAIL if s["passed"] is False else INK)
        c.drawString(cols[4] + 2 * mm, y, s["grade"])
    y -= 7.5 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(cols[0] + 2 * mm, y, "Total")
    c.drawString(cols[1] + 2 * mm, y, _num(d["total"]))
    c.drawString(cols[2] + 2 * mm, y, _num(d["obtained"]))
    c.drawString(cols[3] + 2 * mm, y, "-" if d["percentage"] is None else f"{d['percentage']:.2f}%")
    c.drawString(cols[4] + 2 * mm, y, d["grade"])

    # summary line
    y -= 11 * mm
    result = "Pass" if d["passed"] else ("Fail" if d["passed"] is False else "Incomplete")
    pos = f"{_ordinal(d['position'])} of {d['position_of']}" if d.get("position") else "-"
    att = d["attendance"]
    att_text = "-" if att["percentage"] is None else f"{att['present']} of {att['days']} days ({att['percentage']:.0f}%)"
    for i, (k, v, col) in enumerate([("Result", result, PASS if d["passed"] else FAIL if d["passed"] is False else INK),
                                     ("Position in section", pos, INK), ("Attendance", att_text, INK)]):
        x = left + i * 60 * mm
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8.5)
        c.drawString(x, y + 4.5 * mm, k)
        c.setFillColor(col)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x, y, v)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawString(left, y - 5 * mm, f"Pass: {d['pass_percent']:g}% overall and {d['subject_pass_percent']:g}% in every subject. Absent counts as 0.")

    # skills
    y -= 14 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(left, y, "Skills & behaviour")
    c.setFont("Helvetica", 9.5)
    for i, s in enumerate(d["skills"]):
        x = left if i % 2 == 0 else W / 2
        yy = y - (i // 2 + 1) * 5.8 * mm
        c.setFillColor(INK)
        c.drawString(x, yy, s["name"])
        c.setFillColor(MUTED)
        c.drawString(x + 45 * mm, yy, RATING.get(s["rating"] or "", "-"))
    y -= (len(d["skills"]) + 1) // 2 * 5.8 * mm + 9 * mm

    # remarks
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(left, y, "Class teacher's remarks")
    c.setFont("Helvetica", 9.5)
    for line in _wrap(c, d.get("remarks") or "-", "Helvetica", 9.5, right - left)[:4]:
        y -= 5 * mm
        c.drawString(left, y, line)

    # the year (Final only)
    year = d.get("year")
    if year and year.get("percentage") is not None:
        y -= 11 * mm
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(left, y, "Result of the year")
        c.setFont("Helvetica", 9.5)
        parts = "   ".join(f"{e['name']} {e['percentage']:.1f}% (weight {e['weight']:g})" for e in year["exams"])
        y -= 5.5 * mm
        c.drawString(left, y, parts[:110])
        y -= 6 * mm
        c.setFont("Helvetica-Bold", 10)
        yr = "Pass" if year["passed"] else "Fail" if year["passed"] is False else "-"
        c.drawString(left, y, f"Year: {year['percentage']:.2f}%   Grade {year['grade']}   {yr}")
        promo = d.get("promotion")
        if promo:
            y -= 6 * mm
            txt = {"PROMOTED": f"Promoted to {promo['to_class']}", "PROMOTED_ON_APPROVAL": f"Promoted to {promo['to_class']} (on approval)",
                   "KEPT_BACK": f"To repeat {promo['to_class']}"}.get(promo["decision"], "")
            c.drawString(left, y, txt)

    # signatures
    c.setStrokeColor(LINE)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 9)
    for x, label in ((left, "Class teacher"), (W / 2 - 25 * mm, "Principal"), (right - 50 * mm, "Parent / guardian")):
        c.line(x, 26 * mm, x + 50 * mm, 26 * mm)
        c.drawString(x, 21 * mm, label)
    c.setFont("Helvetica", 7)
    c.drawString(left, 12 * mm, "This report card is issued by the school's portal. Grades: A+ 80% and above, A 70, B 60, C 50, D 40, E 33, F below 33"
                 if d.get("stage") not in ("O_LEVEL", "A_LEVEL") else "This report card is issued by the school's portal. Grades: A* 90% and above, A 80, B 70, C 60, D 50, E 40, U below 40.")
    c.showPage()
    c.save()
    return buf.getvalue()
