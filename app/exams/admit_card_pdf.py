"""Admit card / roll-number slip (A4 portrait): student, datesheet, every paper with date, time, room and eligibility."""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

INK, MUTED, LINE, RED = colors.HexColor("#0f1b3d"), colors.HexColor("#5b6785"), colors.HexColor("#c5cde0"), colors.HexColor("#b42318")


def render_admit_card_pdf(card: dict, institute: str | None = None) -> bytes:
    from app.core import brand

    buf = io.BytesIO()
    w, h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    st, sc = card["student"], card["schedule"]
    c.setTitle(f"Admit card {st['roll_number']} {sc['name']}")
    x0 = 16 * mm
    y = brand.draw_header(c, x=x0, y_top=h - 12 * mm, width=w - 2 * x0 - 40 * mm, center=False) - 2 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(w - x0, h - 18 * mm, "ADMIT CARD")
    c.setFont("Helvetica", 10)
    c.setFillColor(MUTED)
    c.drawString(x0, y - 6 * mm, f"{sc['name']} examination \u2014 {sc['term_label']}")
    c.setStrokeColor(LINE)
    c.line(x0, y - 10 * mm, w - x0, y - 10 * mm)

    yy = y - 18 * mm
    for label, value in (("Name", st["name"]), ("Roll number", st["roll_number"] or "\u2014"),
                         ("Program / Semester", f"{st.get('program') or '\u2014'} \u00b7 Semester {st.get('semester') or '\u2014'}")):
        c.setFont("Helvetica", 9.5)
        c.setFillColor(MUTED)
        c.drawString(x0, yy, label)
        c.setFont("Helvetica-Bold", 10.5)
        c.setFillColor(INK)
        c.drawString(x0 + 40 * mm, yy, str(value))
        yy -= 7 * mm

    if card["withheld"]:
        yy -= 2 * mm
        c.setFillColor(RED)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x0, yy, "WITHHELD \u2014 not valid for entry until cleared: " + "; ".join(card["withheld"]))
        yy -= 8 * mm

    yy -= 2 * mm
    cols = [("Date", 0), ("Time", 30), ("Course", 58), ("Room", 128), ("Status", 148)]
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 9.5)
    for title, off in cols:
        c.drawString(x0 + off * mm, yy, title)
    c.line(x0, yy - 2 * mm, w - x0, yy - 2 * mm)
    yy -= 8 * mm
    c.setFont("Helvetica", 9.5)
    for p in card["papers"]:
        if yy < 40 * mm:
            c.showPage()
            yy = h - 25 * mm
            c.setFont("Helvetica", 9.5)
        c.setFillColor(INK)
        c.drawString(x0, yy, p["exam_date"].strftime("%a %d %b %Y") if hasattr(p["exam_date"], "strftime") else str(p["exam_date"]))
        c.drawString(x0 + 30 * mm, yy, f"{p['start_time']}\u2013{p['end_time']}")
        c.drawString(x0 + 58 * mm, yy, f"{p['course_code']} {p['course_title']}"[:42])
        c.drawString(x0 + 128 * mm, yy, p["room"][:14])
        if p["eligible"]:
            c.drawString(x0 + 148 * mm, yy, "Eligible")
        else:
            c.setFillColor(RED)
            c.drawString(x0 + 148 * mm, yy, "NOT ELIGIBLE")
            c.setFont("Helvetica", 7.5)
            c.drawString(x0 + 148 * mm, yy - 3.5 * mm, p["reason"][:34])
            c.setFont("Helvetica", 9.5)
        yy -= 9 * mm

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8.5)
    text = c.beginText(x0, 32 * mm)
    for line in ((card["schedule"].get("instructions") or "Bring this card and your student ID card to every paper. Arrive 15 minutes early.")[:400]).split("\n")[:4]:
        text.textLine(line[:110])
    c.drawText(text)
    c.drawString(x0, 16 * mm, f"Issued {card['issued_at']:%d %b %Y %H:%M} UTC \u00b7 computer-generated")
    c.drawRightString(w - x0, 16 * mm, "Controller of Examinations")
    brand.draw_footer(c, x=x0, y=10 * mm, width=w - 2 * x0)
    c.showPage()
    c.save()
    return buf.getvalue()