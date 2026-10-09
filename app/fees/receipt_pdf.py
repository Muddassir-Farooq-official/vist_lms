"""A printable payment receipt (A5 portrait): receipt number, payer, fee, amount in figures and words, how and when it was paid."""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.fees.challan_pdf import amount_in_words

INK, MUTED, LINE = colors.HexColor("#0f1b3d"), colors.HexColor("#5b6785"), colors.HexColor("#c5cde0")
METHOD_LABEL = {"CASH": "Cash", "BANK_DEPOSIT": "Bank deposit", "ONLINE_TRANSFER": "Online transfer", "CHEQUE": "Cheque", "ONE_BILL": "1LINK 1Bill"}


def render_receipt_pdf(data: dict) -> bytes:
    """data: institute, receipt_number, paid_on, recorded_on, payer{name, identifier, group}, fee_name, term_label,
    amount, method, reference, description, recorded_by."""
    buf = io.BytesIO()
    w, h = A5
    c = canvas.Canvas(buf, pagesize=A5)
    c.setTitle(f"Receipt {data['receipt_number']}")
    x0, y = 14 * mm, h - 18 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x0, y, data.get("institute") or "Fee Receipt")
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    refund = data.get("kind") == "refund"
    c.drawString(x0, y - 5 * mm, "Refund voucher \u2014 money paid back" if refund else "Official payment receipt")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(w - x0, y, data["receipt_number"])
    c.setStrokeColor(LINE)
    c.line(x0, y - 9 * mm, w - x0, y - 9 * mm)

    payer = data.get("payer") or {}
    rows = [
        ("Paid back to" if refund else "Received from", payer.get("name") or "\u2014"),
        ("Roll / Employee no.", payer.get("identifier") or "\u2014"),
        ("Program / Group", payer.get("group") or "\u2014"),
        ("Fee", data.get("fee_name") or "\u2014"),
        ("Term", data.get("term_label") or "\u2014"),
        ("Paid on", str(data.get("paid_on") or "\u2014")),
        ("Payment method", METHOD_LABEL.get(data.get("method") or "", data.get("method") or "\u2014")),
        ("Reference", data.get("reference") or "\u2014"),
        ("Details", (data.get("description") or "\u2014")[:70]),
    ]
    yy = y - 17 * mm
    for label, value in rows:
        c.setFont("Helvetica", 9)
        c.setFillColor(MUTED)
        c.drawString(x0, yy, label)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(x0 + 38 * mm, yy, str(value))
        yy -= 7 * mm

    yy -= 3 * mm
    c.setStrokeColor(INK)
    c.rect(x0, yy - 14 * mm, w - 2 * x0, 16 * mm)
    c.setFont("Helvetica", 9)
    c.drawString(x0 + 3 * mm, yy - 3 * mm, "Amount refunded" if refund else "Amount received")
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(w - x0 - 3 * mm, yy - 4 * mm, f"Rs. {float(data['amount']):,.2f}")
    c.setFont("Helvetica-Oblique", 8.5)
    c.drawString(x0 + 3 * mm, yy - 11 * mm, amount_in_words(float(data["amount"])))

    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    c.drawString(x0, 20 * mm, f"Recorded on {data.get('recorded_on')}" + (f" by {data['recorded_by']}" if data.get("recorded_by") else ""))
    c.drawString(x0, 15 * mm, "Received the above amount: ______________________ (signature)" if refund
                 else "Computer-generated receipt. Keep it until the fee shows as paid on your account.")
    c.showPage()
    c.save()
    return buf.getvalue()