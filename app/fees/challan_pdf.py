"""The printable fee challan: three copies (bank, institute, student) on one landscape A4 page, each with the consumer number and a barcode."""
from __future__ import annotations

import io

from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

INK, MUTED, BLUE, GREEN, LINE = colors.HexColor("#0f1b3d"), colors.HexColor("#5b6785"), colors.HexColor("#2454d6"), colors.HexColor("#17803d"), colors.HexColor("#c5cde0")
ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_1000(n: int) -> str:
    out = []
    if n >= 100:
        out.append(ONES[n // 100] + " Hundred"); n %= 100
    if n >= 20:
        out.append(TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else ""))
    elif n:
        out.append(ONES[n])
    return " ".join(out)


def amount_in_words(amount: float) -> str:
    """Rupees in words, the Pakistani way (thousand, lakh, crore)."""
    rupees, paisa = int(amount), round((amount - int(amount)) * 100)
    if rupees == 0 and paisa == 0:
        return "Zero Rupees Only"
    parts = []
    for size, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if rupees >= size:
            parts.append(_below_1000(rupees // size) + " " + name); rupees %= size
    if rupees:
        parts.append(_below_1000(rupees))
    text = "Rupees " + " ".join(parts) if parts else "Rupees"
    if paisa:
        text += f" and {_below_1000(paisa)} Paisa"
    return text.strip() + " Only"


def render_challan_pdf(d: dict, *, institute_name: str | None = None) -> bytes:
    from app.core import brand

    institute_name = institute_name or brand.name()
    buf = io.BytesIO()
    w, h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"Fee challan {d['consumer_number']}")
    col_w = (w - 20 * mm) / 3
    for i, copy in enumerate(("BANK COPY", "INSTITUTE COPY", "STUDENT COPY")):
        x0 = 10 * mm + i * col_w
        pad = 4 * mm
        x, top = x0 + pad, h - 12 * mm
        right = x0 + col_w - pad
        if i:
            c.setDash(2, 2); c.setStrokeColor(LINE); c.line(x0, 8 * mm, x0, h - 8 * mm); c.setDash()
        # Logo, institute name and address (Settings -> Institute), then which copy this is.
        hy = brand.draw_header(c, x=x, y_top=top + 4 * mm, width=right - x, compact=True, logo_h=8 * mm)
        c.setFont("Helvetica-Bold", 9); c.setFillColor(BLUE); c.drawCentredString(x0 + col_w / 2, hy - 1 * mm, f"FEE CHALLAN \u2014 {copy}")
        y = hy - 6.5 * mm
        c.setStrokeColor(LINE); c.line(x, y + 2 * mm, right, y + 2 * mm)

        def row(label, value, bold=False, size=8.4):
            nonlocal y
            c.setFillColor(MUTED); c.setFont("Helvetica", 7.2); c.drawString(x, y, label.upper())
            c.setFillColor(INK); c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            text = str(value)
            while c.stringWidth(text, "Helvetica-Bold" if bold else "Helvetica", size) > right - x and len(text) > 8:
                text = text[:-2]
            c.drawString(x, y - 3.6 * mm, text)
            y -= 8.6 * mm

        row("Consumer number", d["consumer_number"], bold=True, size=11)
        row("Student / payer", d["payer"]["name"], bold=True)
        row("Roll no. / code", d["payer"]["identifier"] or "\u2014")
        if d["payer"].get("group"):
            row("Program", d["payer"]["group"])
        row("Fee", d["fee_name"])
        row("Installment", d["installment_text"])
        c.setStrokeColor(LINE); c.line(x, y + 4 * mm, right, y + 4 * mm)
        for label, value in (("Fee amount", d["charged"]), ("Scholarship / concession", -d["concession"] if d["concession"] else None)):
            if value is None:
                continue
            c.setFillColor(MUTED); c.setFont("Helvetica", 8); c.drawString(x, y, label)
            c.setFillColor(INK); c.drawRightString(right, y, f"{'-' if value < 0 else ''}Rs. {abs(value):,.2f}")
            y -= 5 * mm
        c.setFillColor(INK); c.setFont("Helvetica-Bold", 11)
        c.drawString(x, y - 1 * mm, "PAYABLE"); c.drawRightString(right, y - 1 * mm, f"Rs. {d['payable']:,.2f}")
        y -= 6.5 * mm
        c.setFont("Helvetica-Oblique", 7.4); c.setFillColor(MUTED)
        words = amount_in_words(d["payable"])
        for line in (words[k:k + 44] for k in range(0, len(words), 44)):
            c.drawString(x, y, line); y -= 3.4 * mm
        y -= 2 * mm
        row("Due date", d["due_date"].strftime("%d %b %Y"), bold=True)
        if d.get("late_fee_per_day"):
            c.setFillColor(colors.HexColor("#92400e")); c.setFont("Helvetica", 7.4)
            c.drawString(x, y + 1 * mm, f"Late fee: Rs. {d['late_fee_per_day']:,.0f} for every day after the due date.")
            y -= 3.5 * mm
        bar = code128.Code128(d["consumer_number"], barHeight=11 * mm, barWidth=0.33 * mm, humanReadable=False)
        bar.drawOn(c, x + max(0, ((right - x) - bar.width) / 2), y - 12 * mm)
        y -= 14.5 * mm
        c.setFillColor(MUTED); c.setFont("Helvetica", 7); c.drawCentredString(x0 + col_w / 2, y, d["consumer_number"])
        y -= 5 * mm
        c.setFont("Helvetica", 6.9)
        for line in ("Pay with this consumer number at any bank branch,", "ATM or banking app (1LINK / 1Bill). Keep your copy.", "A payment is shown on your account after the bank confirms it."):
            c.drawCentredString(x0 + col_w / 2, y, line); y -= 3.2 * mm
        bank = brand.bank_lines()
        if bank:
            y -= 1 * mm
            c.setFillColor(INK); c.setFont("Helvetica-Bold", 6.9)
            for line in bank:
                c.drawCentredString(x0 + col_w / 2, y, brand.fit_text(c, line, "Helvetica-Bold", 6.9, right - x)); y -= 3.2 * mm
            c.setFillColor(MUTED); c.setFont("Helvetica", 6.9)
        if copy == "BANK COPY":
            c.setStrokeColor(MUTED); c.line(x + 4 * mm, 18 * mm, right - 4 * mm, 18 * mm); c.drawCentredString(x0 + col_w / 2, 14.5 * mm, "Bank stamp and signature")
        if d["status"] == "PAID":
            c.saveState(); c.translate(x0 + col_w / 2, h / 2 - 8 * mm); c.rotate(24); c.setStrokeColor(GREEN); c.setFillColor(GREEN); c.setLineWidth(2.2)
            c.roundRect(-22 * mm, -7 * mm, 44 * mm, 14 * mm, 3 * mm, stroke=1, fill=0); c.setFont("Helvetica-Bold", 20); c.drawCentredString(0, -3 * mm, "PAID")
            if d.get("paid_on"):
                c.setFont("Helvetica", 7.5); c.drawCentredString(0, -10.5 * mm, d["paid_on"].strftime("%d %b %Y"))
            c.restoreState()
        elif d["status"] in ("CANCELLED", "EXPIRED"):
            c.saveState(); c.translate(x0 + col_w / 2, h / 2 - 8 * mm); c.rotate(24); c.setFillColor(colors.HexColor("#c62828")); c.setFont("Helvetica-Bold", 20)
            c.drawCentredString(0, 0, "NOT VALID" if d["status"] == "CANCELLED" else "EXPIRED"); c.restoreState()
    c.setFillColor(MUTED); c.setFont("Helvetica", 6.8)
    c.drawString(10 * mm, 5 * mm, brand.fit_text(c, f"Computer-generated on {d['generated_on'].strftime('%d %b %Y')}. The amount above is what is payable now; if a scholarship is given later, the new amount appears on a new copy."
                                                  + (f"  \u00b7  {brand.settings.document_footer}" if brand.settings.document_footer else ""), "Helvetica", 6.8, w - 20 * mm))
    c.showPage(); c.save()
    return buf.getvalue()