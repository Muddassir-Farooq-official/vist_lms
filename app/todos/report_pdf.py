"""The monthly to-do report as a printable PDF: a summary table for everyone, then each person's lists day by day."""
from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape

STATUS_TEXT = {"TODO": "To do", "IN_PROGRESS": "In progress", "DONE": "Done", "NOT_DONE": "Not done", "CARRIED_OVER": "Carried over",
               "LEFT_OPEN": "Left open"}


def render(report: dict, people_days: list[tuple[dict, list[dict]]], *, generated: datetime) -> bytes:
    """report: TodoService.report(); people_days: [(person, [{"date", "items": [...]}, ...]), ...] for those with lists."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from app.core import brand

    page = landscape(A4)
    buf = io.BytesIO()
    title = f"Staff to-do lists — {report['label']}"
    head_h = 34 * mm

    def decorate(c, doc):
        c.saveState()
        brand.draw_header(c, x=doc.leftMargin, y_top=page[1] - 10 * mm, width=doc.width, title=title, compact=True)
        brand.draw_footer(c, x=doc.leftMargin, y=8 * mm, width=doc.width, extra=f"Generated {generated:%d %b %Y %H:%M} · page {doc.page}")
        c.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=10 * mm + head_h, bottomMargin=16 * mm,
                            title=title, author=brand.name())
    ink = colors.HexColor("#0b1630")
    muted = colors.HexColor("#5b6785")
    band = colors.HexColor("#eef2f9")
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, textColor=ink, leading=15, spaceBefore=6, spaceAfter=4)
    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, textColor=ink, leading=10.5)
    small = ParagraphStyle("s", fontName="Helvetica", fontSize=7.5, textColor=muted, leading=9.5)
    bold = ParagraphStyle("b", fontName="Helvetica-Bold", fontSize=8.5, textColor=ink, leading=10.5)

    t = report["totals"]
    story = [Paragraph(
        f"{t['people_with_lists']} of {t['people']} staff wrote a list · {t['items']} items planned · {t['done']} done · "
        f"{t['not_done'] + t['left_open']} not done · {t['carried']} carried over · done overall "
        f"{'—' if t['done_pct'] is None else str(t['done_pct']) + '%'} · {report['working_days_so_far']} working days so far", small), Spacer(1, 4 * mm)]

    rows = [["Staff member", "Code", "Department", "Days with a list", "Items", "Done", "In progress", "Not done", "Carried over", "Done %", "Last list"]]
    for p in report["people"]:
        rows.append([Paragraph(escape(p["name"]), cell), p["code"], Paragraph(escape(p["department"] or ""), cell), str(p["days_with_list"]), str(p["items"]),
                     str(p["done"]), str(p["in_progress"]), str(p["not_done"] + p["left_open"]), str(p["carried"]),
                     "—" if p["done_pct"] is None else f"{p['done_pct']}%",
                     datetime.fromisoformat(p["last_day"]).strftime("%a %d %b") if p["last_day"] else "No list"])
    summary = Table(rows, repeatRows=1, colWidths=[52 * mm, 22 * mm, 34 * mm, 24 * mm, 15 * mm, 15 * mm, 20 * mm, 18 * mm, 21 * mm, 17 * mm, 25 * mm])
    summary.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), ink),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5), ("ALIGN", (3, 0), (9, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, band]), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dbe8")),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(summary)

    for person, days in people_days:
        if not days:
            continue
        block = [Spacer(1, 5 * mm), Paragraph(escape(person["name"]) + (f" &nbsp;<font color='#5b6785' size='9'>{escape(person.get('code') or '')} "
                                                                      f"{escape(person.get('department') or '')}</font>"), h2)]
        lines = [["Day", "What they planned", "Note", "Status", "Done at"]]
        for d in sorted(days, key=lambda x: x["date"]):
            day = datetime.fromisoformat(d["date"]).strftime("%a %d %b")
            for i, it in enumerate(d["items"]):
                st = it.get("effective") or it["status"]
                lines.append([day if i == 0 else "", Paragraph(escape(it["title"]) + (" <font color='#5b6785'>(carried over)</font>" if it.get("carried") else ""), cell),
                              Paragraph(escape(it.get("note") or ""), small), Paragraph(STATUS_TEXT.get(st, st), bold if st == "DONE" else cell),
                              datetime.fromisoformat(it["done_at"]).astimezone(generated.tzinfo).strftime("%H:%M") if it.get("done_at") else ""])
        tbl = Table(lines, repeatRows=1, colWidths=[24 * mm, 110 * mm, 84 * mm, 26 * mm, 19 * mm])
        tbl.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("BACKGROUND", (0, 0), (-1, 0), band), ("TEXTCOLOR", (0, 0), (-1, 0), ink),
            ("FONT", (0, 1), (0, -1), "Helvetica-Bold", 8), ("FONT", (4, 1), (4, -1), "Helvetica", 8.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dbe8")), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        block.append(tbl)
        story.append(KeepTogether(block[:2]) if len(lines) > 25 else KeepTogether(block))
        if len(lines) > 25:
            story.append(tbl)
    if not report["people"]:
        story.append(Paragraph("No staff to show.", cell))
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()