"""One PDF with everything on a person's file: a cover page listing what is inside, then the photo, the combined
certificate PDF they uploaded, and every current document (PDF pages are copied as they are; JPG / PNG scans become a
page each). Files that cannot go into a PDF (e.g. Word) or cannot be read are named on the cover page instead.

Pure function of bytes in, bytes out, so it runs in a worker thread (see records/router.py)."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MAX_TOTAL_BYTES = 80 * 1024 * 1024          # stop adding files beyond this (they are listed as "too large to include")


@dataclass
class Part:
    title: str               # e.g. "Matric certificate: Matric 2019"
    note: str                # e.g. "uploaded 3 Aug 2026 · checked"
    content: bytes | None    # None: file missing from storage
    content_type: str
    filename: str


def _image_page(content: bytes, title: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    img = ImageReader(io.BytesIO(content))
    iw, ih = img.getSize()
    box_w, box_h = w - 30 * mm, h - 40 * mm
    scale = min(box_w / iw, box_h / ih)
    dw, dh = iw * scale, ih * scale
    c.setFont("Helvetica", 9)
    c.drawString(15 * mm, h - 12 * mm, title[:120])
    c.drawImage(img, (w - dw) / 2, h - 20 * mm - dh, dw, dh, preserveAspectRatio=True, mask="auto")
    c.showPage()
    c.save()
    return buf.getvalue()


def _cover(person: dict, included: list[tuple[str, str, int]], left_out: list[tuple[str, str]], generated: datetime) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 25 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, y, (person.get("institute") or "Documents on file")[:70])
    y -= 10 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * mm, y, f"{person['name']}  ({person['code']})"[:80])
    y -= 6 * mm
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, f"{person['kind']} · documents on file · made {generated:%d %b %Y, %H:%M}")
    y -= 12 * mm

    def line(text: str, bold: bool = False, indent: float = 0) -> None:
        nonlocal y
        if y < 20 * mm:
            c.showPage()
            y = h - 20 * mm
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        c.drawString((20 + indent) * mm, y, text[:110])
        y -= 6 * mm

    line("Inside this file", bold=True)
    if not included:
        line("Nothing could be included.", indent=4)
    for title, note, pages in included:
        line(f"• {title}  ({pages} page{'s' if pages != 1 else ''})", indent=4)
        if note:
            line(note, indent=8)
    if left_out:
        y -= 4 * mm
        line("Not included (open these from the Documents tab)", bold=True)
        for title, why in left_out:
            line(f"• {title}: {why}", indent=4)
    c.showPage()
    c.save()
    return buf.getvalue()


def build_bundle(person: dict, parts: list[Part], generated: datetime) -> bytes:
    """person: {"name", "code", "kind", "institute"}. Returns the PDF bytes."""
    body = PdfWriter()
    included: list[tuple[str, str, int]] = []
    left_out: list[tuple[str, str]] = []
    total = 0
    for p in parts:
        if p.content is None:
            left_out.append((p.title, "the file is missing from storage"))
            continue
        if total + len(p.content) > MAX_TOTAL_BYTES:
            left_out.append((p.title, "too large to include in one file"))
            continue
        ctype = (p.content_type or "").lower()
        name = (p.filename or "").lower()
        try:
            if ctype == "application/pdf" or name.endswith(".pdf"):
                reader = PdfReader(io.BytesIO(p.content))
                if reader.is_encrypted:
                    try:
                        reader.decrypt("")
                    except Exception:
                        left_out.append((p.title, "the PDF is password-protected"))
                        continue
                before = len(body.pages)
                for page in reader.pages:
                    body.add_page(page)
                pages = len(body.pages) - before
            elif ctype.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
                for page in PdfReader(io.BytesIO(_image_page(p.content, p.title))).pages:
                    body.add_page(page)
                pages = 1
            else:
                left_out.append((p.title, f"a {name.rsplit('.', 1)[-1].upper() if '.' in name else 'non-PDF'} file cannot be put in a PDF"))
                continue
        except Exception:
            left_out.append((p.title, "the file could not be read (damaged or unusual format)"))
            continue
        total += len(p.content)
        included.append((p.title, p.note, pages))

    out = PdfWriter()
    for page in PdfReader(io.BytesIO(_cover(person, included, left_out, generated))).pages:
        out.add_page(page)
    for page in body.pages:
        out.add_page(page)
    buf = io.BytesIO()
    out.write(buf)
    return buf.getvalue()