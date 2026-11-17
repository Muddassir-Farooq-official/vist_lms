"""The user manual: Help & manual pages in every portal, and the manual as a PDF.

The text lives in app/manual/*.md (one chapter per role, plus "Getting started" and the tools every staff member has);
app/manual/manual.json lists the chapters, which portals see each one, and the manual's version. A "What changed"
chapter is built from the release notes (app/releases.json), so the manual always says what each version brought.
tests/integration/test_manual.py fails a release whose manual was not brought up to date: the manual's version must be
the app's version, and every menu item of a portal must be explained in that portal's chapters.

Only a small, safe part of Markdown is used (headings, paragraphs, lists, **bold**); everything is HTML-escaped first.
"""
from __future__ import annotations

import html
import io
import json
import re
from functools import lru_cache
from pathlib import Path

MANUAL_DIR = Path(__file__).resolve().parent / "manual"


@lru_cache(maxsize=1)
def index() -> dict:
    return json.loads((MANUAL_DIR / "manual.json").read_text(encoding="utf-8"))


def chapters_for(portal: str | None) -> list[dict]:
    """The chapters a portal sees (the administrator sees every chapter). portal=None: the whole manual."""
    out = []
    for c in index()["chapters"]:
        if portal is None or "*" in c["portals"] or portal in c["portals"]:
            out.append({**c, "text": (MANUAL_DIR / c["file"]).read_text(encoding="utf-8")})
    return out


def what_changed(limit: int = 12) -> dict:
    from app.core.releases import releases

    lines = ["# What changed", "", "The newest versions first. Each version's full notes are under **What's new** in the menu.", ""]
    for r in releases()[:limit]:
        lines.append(f"## Version {r.get('version')} ({r.get('date', '')}): {r.get('title', '')}")
        lines.append("")
        for ch in r.get("changes", []):
            lines.append(f"- **{ch.get('for', 'Everyone')}**: {ch.get('text', '')}")
        lines.append("")
    return {"slug": "changes", "title": "What changed", "text": "\n".join(lines)}


# ---------------------------------------------------------------------- tiny Markdown: blocks
def blocks(text: str) -> list[tuple]:
    """[(kind, payload)] with kind h1/h2/h3/p/ul/ol. Lists collect their items."""
    out: list[tuple] = []
    para: list[str] = []

    def flush():
        if para:
            out.append(("p", " ".join(para)))
            para.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush()
            out.append((f"h{len(m.group(1))}", m.group(2).strip()))
            continue
        m = re.match(r"^\s*(?:[-*]|(\d+)\.)\s+(.*)$", line)
        if m:
            flush()
            kind = "ol" if m.group(1) else "ul"
            indent = len(line) - len(line.lstrip())
            if out and out[-1][0] == kind:
                out[-1][1].append((indent > 0, m.group(2).strip()))
            else:
                out.append((kind, [(indent > 0, m.group(2).strip())]))
            continue
        if not line.strip():
            flush()
            continue
        if out and out[-1][0] in ("ul", "ol") and raw.startswith("  ") and not para:
            sub, last = out[-1][1][-1]
            out[-1][1][-1] = (sub, last + " " + line.strip())
            continue
        para.append(line.strip())
    flush()
    return out


def _inline_html(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def to_html(text: str, chapter_slug: str) -> str:
    parts = []
    for kind, payload in blocks(text):
        if kind in ("h1", "h2", "h3"):
            level = {"h1": 2, "h2": 3, "h3": 4}[kind]          # the page title is the h1
            parts.append(f'<h{level} id="{chapter_slug}-{slugify(payload)}">{_inline_html(payload)}</h{level}>')
        elif kind == "p":
            parts.append(f"<p>{_inline_html(payload)}</p>")
        else:
            tag = kind
            items = "".join(f'<li{" class=sub" if sub else ""}>{_inline_html(t)}</li>' for sub, t in payload)
            parts.append(f"<{tag}>{items}</{tag}>")
    return "\n".join(parts)


def plain(text: str) -> str:
    """The chapter as plain words (for searching and the menu-coverage test)."""
    return re.sub(r"\*\*|`", "", text)


# ---------------------------------------------------------------------- PDF
def render_pdf(portal: str | None) -> bytes:
    from datetime import datetime

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate, Spacer

    from app.core import brand
    from app.core.releases import current_version

    def inline(s: str) -> str:
        s = html.escape(s, quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        return re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', s)

    base = getSampleStyleSheet()
    ink, blue = colors.HexColor("#0f1b3d"), colors.HexColor("#2454d6")
    h1 = ParagraphStyle("m_h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=17, textColor=blue, spaceAfter=8)
    h2 = ParagraphStyle("m_h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, textColor=ink, spaceBefore=8, spaceAfter=4)
    h3 = ParagraphStyle("m_h3", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, textColor=ink, spaceBefore=6, spaceAfter=3)
    body = ParagraphStyle("m_p", parent=base["Normal"], fontName="Helvetica", fontSize=9.6, leading=13, textColor=ink, spaceAfter=5)
    title = ParagraphStyle("m_t", parent=h1, fontSize=24, alignment=1, spaceBefore=40)
    sub = ParagraphStyle("m_s", parent=body, alignment=1, fontSize=11)

    chapters = chapters_for(portal) + [what_changed()]
    version = current_version()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"{brand.name()} user manual", author=brand.name())
    story = brand.platypus_header(A4[0] - 36 * mm) + [Paragraph("User manual", title), Paragraph(f"Version {version}", sub),
                                                      Paragraph(datetime.now().strftime("%d %B %Y"), sub), Spacer(1, 18),
                                                      Paragraph("<b>Contents</b>", h2)]
    for n, c in enumerate(chapters, start=1):
        story.append(Paragraph(f"{n}. {html.escape(c['title'])}", body))
    for c in chapters:
        story.append(PageBreak())
        for kind, payload in blocks(c["text"]):
            if kind == "h1":
                story.append(Paragraph(inline(payload), h1))
            elif kind == "h2":
                story.append(Paragraph(inline(payload), h2))
            elif kind == "h3":
                story.append(Paragraph(inline(payload), h3))
            elif kind == "p":
                story.append(Paragraph(inline(payload), body))
            else:
                items = [ListItem(Paragraph(inline(t), body), leftIndent=(22 if sub_ else 10)) for sub_, t in payload]
                opts = {"bulletType": "1", "start": "1"} if kind == "ol" else {"bulletType": "bullet"}
                story.append(ListFlowable(items, bulletFontSize=8, leftIndent=12, **opts))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#5b6785"))
        canvas.drawString(18 * mm, 9 * mm, f"{brand.name()} · user manual · version {version}")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
