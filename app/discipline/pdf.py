"""The warning letter, on the institute's letterhead (app/core/brand.py)."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from app.core.config import settings


def render_warning_letter(parts: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from app.core import brand
    from app.discipline.service import ACTION_TEXT, COUNT_DAYS, LIMIT

    w, who, detail = parts["warning"], parts["person"], parts["detail"]
    ink, muted, line = colors.HexColor("#0f1b3d"), colors.HexColor("#5b6785"), colors.HexColor("#d5dbe8")
    accent = colors.HexColor("#b42318") if w["is_final"] else colors.HexColor(brand.color())
    buf = io.BytesIO()
    width = A4[0] - 36 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm, bottomMargin=50 * mm,
                            title=f"Warning letter {w['reference']}", author=brand.name())
    base = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica", fontSize=10.5, leading=15.5, textColor=ink, spaceAfter=7)
    small = ParagraphStyle("s", parent=body, fontSize=8.5, leading=11.5, textColor=muted)
    title = ParagraphStyle("t", parent=body, fontName="Helvetica-Bold", fontSize=13, textColor=accent, alignment=1, spaceBefore=4, spaceAfter=10)
    label = f"FINAL WARNING ({w['number']} of {LIMIT})" if w["is_final"] else f"WARNING LETTER ({w['number']} of {LIMIT})"
    if w["status"] == "WITHDRAWN":
        label += " — WITHDRAWN"
    story = brand.platypus_header(width) + [Paragraph(label, title)]
    meta = Table([[f"No. {w['reference']}", f"Date: {w['issued_on']:%d %B %Y}"]], colWidths=[width / 2, width / 2])
    meta.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("TEXTCOLOR", (0, 0), (-1, -1), muted), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    is_student = who["kind"] == "STUDENT"
    to = [f"<b>To:</b> {escape(who['name'])}", f"{'Roll no.' if is_student else 'Employee code'}: {escape(who['code'])}"]
    if detail:
        to.append(escape(detail))
    story += [meta, Spacer(1, 6), Paragraph("<br/>".join(to), body), Spacer(1, 4),
              Paragraph(f"<b>Subject:</b> {escape(w['violation'])}", body)]
    story.append(Paragraph(
        f"This letter is to formally warn you about the following, recorded on {w['incident_date']:%d %B %Y}:", body))
    note = Table([[Paragraph(escape(w["note"]).replace("\n", "<br/>"), body)]], colWidths=[width])
    note.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, line), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fc")),
                              ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    story += [note, Spacer(1, 8)]
    months = COUNT_DAYS // 30
    if w["is_final"]:
        story.append(Paragraph(f"This is your <b>{w['number']}{'rd' if w['number'] == 3 else 'th'} warning within {months} months</b> and therefore your "
                               "<b>final warning</b>. The administration will decide the action to be taken, which may include suspension, "
                               + ("expulsion" if is_student else "termination of employment") + ".", body))
    else:
        story.append(Paragraph(f"This is warning <b>{w['number']} of {LIMIT}</b>. Each warning stays on your record and counts for {months} months. "
                               f"A {LIMIT}rd warning within {months} months is final and may lead to strict action such as suspension, "
                               + ("expulsion" if is_student else "termination of employment") + ".", body))
    story.append(Paragraph("You may reply to this warning in writing from <i>My warnings</i> in the portal. Your reply is kept with this record.", body))
    if w["action"] and w["action"] != "PENDING":
        story += [Spacer(1, 4), Paragraph(f"<b>Decision ({w['decided_at']:%d %B %Y}): {escape(ACTION_TEXT[w['action']])}.</b> "
                                          f"{escape(w['action_note'] or '')}", body)]
    if w["status"] == "WITHDRAWN":
        story += [Spacer(1, 4), Paragraph(f"<b>This warning was withdrawn on {w['withdrawn_at']:%d %B %Y}:</b> {escape(w['withdraw_reason'] or '')}. "
                                          "It no longer counts.", body)]
    if w["response"]:
        story += [Spacer(1, 4), Paragraph(f"<b>Your reply ({w['responded_at']:%d %B %Y}):</b> {escape(w['response'])}", small)]

    def page(c, d):
        c.saveState()
        brand.draw_signature(c, x=A4[0] - 78 * mm, y=34 * mm, width=56, label=settings.institute_signatory_title or "Administration")
        c.setFont("Helvetica", 7.5)
        c.setFillColor(muted)
        c.drawString(18 * mm, 24 * mm, "Received by: ____________________________      Date: ______________")
        brand.draw_footer(c, x=18 * mm, y=12 * mm, width=A4[0] - 36 * mm)
        c.restoreState()

    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
