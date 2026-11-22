"""Question bank and paper building (models.py)."""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.qbank.models import BankQuestion, PaperItem, QuestionPaper

KINDS = ("MCQ", "SHORT", "LONG")
KIND_NAMES = {"MCQ": "Multiple choice", "SHORT": "Short questions", "LONG": "Long questions"}
LEVELS = ("EASY", "MEDIUM", "HARD")
BLOOMS = tuple(f"C{i}" for i in range(1, 7)) + tuple(f"P{i}" for i in range(1, 8)) + tuple(f"A{i}" for i in range(1, 6))
LETTERS = "ABCDEF"


class QuestionBankService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ who may do what
    async def teacher_course_ids(self, user) -> set[uuid.UUID]:
        from app.academics.models import CourseOffering
        from app.employees.models import Employee

        emp = (await self.session.execute(select(Employee.id).where(Employee.user_id == user.id))).scalar_one_or_none()
        if emp is None:
            return set()
        return set((await self.session.execute(select(CourseOffering.course_id).where(CourseOffering.assigned_teacher_id == emp))).scalars())

    async def _may_use_course(self, user, course_id: uuid.UUID, *, office: bool) -> None:
        if office:
            return
        if course_id not in await self.teacher_course_ids(user):
            raise AuthorizationError("You can add questions only to courses you teach.")

    # ------------------------------------------------------------------ questions
    async def courses(self, user, *, office: bool) -> list[dict]:
        from app.academics.models import Course

        q = select(Course).order_by(Course.code)
        if not office:
            q = q.where(Course.id.in_(await self.teacher_course_ids(user) or {uuid.uuid4()}))
        courses = (await self.session.execute(q)).scalars().all()
        counts = dict((await self.session.execute(select(BankQuestion.course_id, func.count()).where(BankQuestion.is_active.is_(True))
                                                  .group_by(BankQuestion.course_id))).all())
        return [{"id": c.id, "code": c.code, "title": c.title, "questions": int(counts.get(c.id, 0))} for c in courses]

    def _clean(self, data: dict) -> dict:
        kind = str(data.get("kind") or "").upper()
        if kind not in KINDS:
            raise ValidationAppError("Multiple choice, short or long.")
        text = (data.get("text") or "").strip()
        if len(text) < 5:
            raise ValidationAppError("Write the question.")
        level = str(data.get("difficulty") or "MEDIUM").upper()
        if level not in LEVELS:
            raise ValidationAppError("Easy, medium or hard.")
        bloom = (data.get("bloom") or "").strip().upper() or None
        if bloom and bloom not in BLOOMS:
            raise ValidationAppError("Bloom level C1-C6, P1-P7 or A1-A5.")
        marks = Decimal(str(data.get("marks") or 0))
        if not (Decimal("0.5") <= marks <= 100):
            raise ValidationAppError("Marks 0.5 to 100.")
        options, answer = None, (data.get("answer") or "").strip() or None
        if kind == "MCQ":
            options = [str(o).strip() for o in (data.get("options") or []) if str(o).strip()]
            if not (2 <= len(options) <= 6):
                raise ValidationAppError("A multiple-choice question needs 2 to 6 options.")
            answer = (answer or "").upper()
            if answer not in LETTERS[:len(options)]:
                raise ValidationAppError(f"Give the right option's letter (A-{LETTERS[len(options) - 1]}).")
        clo = (data.get("clo") or "").strip().upper() or None
        return {"kind": kind, "text": text[:5000], "options": options, "answer": answer[:3000] if answer else None, "clo": clo[:12] if clo else None,
                "bloom": bloom, "difficulty": level, "marks": marks}

    async def add(self, data: dict, *, user, office: bool) -> dict:
        from app.academics.models import Course

        course_id = data.get("course_id")
        if course_id is None or await self.session.get(Course, course_id) is None:
            raise ResourceNotFoundError("Course not found.")
        await self._may_use_course(user, course_id, office=office)
        q = BankQuestion(course_id=course_id, created_by=user.id, **self._clean(data))
        self.session.add(q)
        await self.session.flush()
        return {"id": q.id}

    async def _final_use(self, qid: uuid.UUID) -> int:
        return int((await self.session.execute(select(func.count()).select_from(PaperItem).join(QuestionPaper, QuestionPaper.id == PaperItem.paper_id)
                                               .where(PaperItem.question_id == qid, QuestionPaper.status == "FINAL"))).scalar_one())

    async def edit(self, qid: uuid.UUID, data: dict, *, user, office: bool) -> dict:
        q = await self.session.get(BankQuestion, qid)
        if q is None:
            raise ResourceNotFoundError("Question not found.")
        if not office and q.created_by != user.id:
            raise AuthorizationError("Only the teacher who wrote it, or the exam office, can change a question.")
        if "is_active" in data and len(data) == 1:
            q.is_active = bool(data["is_active"])
        else:
            if await self._final_use(q.id):
                raise BusinessRuleViolationError("This question is in a final paper; it stays as it was. Retire it and add a new one.")
            for k, v in self._clean({**data, "kind": data.get("kind") or q.kind}).items():
                setattr(q, k, v)
            if "is_active" in data:
                q.is_active = bool(data["is_active"])
        q.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return {"id": q.id}

    async def questions(self, course_id: uuid.UUID, *, user, office: bool, kind: str | None = None, include_retired: bool = False) -> dict:
        await self._may_use_course(user, course_id, office=office)
        q = select(BankQuestion).where(BankQuestion.course_id == course_id).order_by(BankQuestion.kind, BankQuestion.created_at)
        if kind:
            q = q.where(BankQuestion.kind == kind)
        if not include_retired:
            q = q.where(BankQuestion.is_active.is_(True))
        rows = (await self.session.execute(q)).scalars().all()
        summary: dict = {}
        for x in rows:
            if x.is_active:
                summary.setdefault(x.kind, {lv: 0 for lv in LEVELS})[x.difficulty] += 1
        return {"summary": summary, "questions": [self._q(x, mine=x.created_by == user.id) for x in rows]}

    @staticmethod
    def _q(x: BankQuestion, *, mine: bool = False) -> dict:
        return {"id": x.id, "kind": x.kind, "text": x.text, "options": x.options, "answer": x.answer, "clo": x.clo, "bloom": x.bloom, "difficulty": x.difficulty,
                "marks": float(x.marks), "is_active": x.is_active, "times_used": x.times_used, "mine": mine}

    # ------------------------------------------------------------------ papers (the exam office)
    async def build(self, *, course_id: uuid.UUID, title: str, blueprint: list[dict], exam_paper_id: uuid.UUID | None, duration_minutes: int | None,
                    instructions: str | None, actor_id: uuid.UUID) -> dict:
        """Pick questions for each blueprint row: matching kind (and difficulty, Bloom level, CLO when given), the least used
        first, at random among equals. Refused, saying what is short, if the bank has too few."""
        from app.academics.models import Course

        if await self.session.get(Course, course_id) is None:
            raise ResourceNotFoundError("Course not found.")
        if not blueprint:
            raise ValidationAppError("Say how many questions of each kind.")
        picked: list[tuple[BankQuestion, Decimal]] = []
        taken: set = set()
        short = []
        rows = []
        for row in blueprint:
            kind = str(row.get("kind") or "").upper()
            count = int(row.get("count") or 0)
            if kind not in KINDS or not (1 <= count <= 100):
                raise ValidationAppError("Each row needs a kind and how many (1-100).")
            q = select(BankQuestion).where(BankQuestion.course_id == course_id, BankQuestion.kind == kind, BankQuestion.is_active.is_(True))
            for field in ("difficulty", "bloom", "clo"):
                if row.get(field):
                    q = q.where(getattr(BankQuestion, field) == str(row[field]).upper())
            pool = [x for x in (await self.session.execute(q.order_by(BankQuestion.times_used, func.random()))).scalars() if x.id not in taken]
            if len(pool) < count:
                short.append(f"{count} {KIND_NAMES[kind].lower()}" + "".join(f" {row[f]}" for f in ("difficulty", "bloom", "clo") if row.get(f)) +
                             f" (the bank has {len(pool)})")
                continue
            marks = Decimal(str(row["marks"])) if row.get("marks") else None
            for x in pool[:count]:
                taken.add(x.id)
                picked.append((x, marks if marks is not None else Decimal(str(x.marks))))
            rows.append({**{k: row.get(k) for k in ("kind", "count", "difficulty", "bloom", "clo")}, "marks": float(marks) if marks is not None else None})
        if short:
            raise BusinessRuleViolationError("Not enough questions in the bank: " + "; ".join(short) + ".")
        p = QuestionPaper(course_id=course_id, title=(title or "").strip()[:150] or "Paper", exam_paper_id=exam_paper_id, duration_minutes=duration_minutes,
                          instructions=(instructions or None), blueprint=rows, created_by=actor_id)
        self.session.add(p)
        await self.session.flush()
        order = {k: i for i, k in enumerate(KINDS)}
        for i, (x, m) in enumerate(sorted(picked, key=lambda t: order[t[0].kind])):
            self.session.add(PaperItem(paper_id=p.id, question_id=x.id, position=i + 1, marks=m))
        await self.session.flush()
        return await self.paper(p.id)

    async def papers(self, *, course_id: uuid.UUID | None = None) -> list[dict]:
        from app.academics.models import Course

        q = select(QuestionPaper, Course).join(Course, Course.id == QuestionPaper.course_id).order_by(QuestionPaper.created_at.desc()).limit(200)
        if course_id:
            q = q.where(QuestionPaper.course_id == course_id)
        totals = dict((await self.session.execute(select(PaperItem.paper_id, func.sum(PaperItem.marks)).group_by(PaperItem.paper_id))).all())
        return [{"id": p.id, "title": p.title, "course": f"{c.code} {c.title}", "status": p.status, "created_at": p.created_at,
                 "total_marks": float(totals.get(p.id) or 0)} for p, c in (await self.session.execute(q)).all()]

    async def paper(self, paper_id: uuid.UUID) -> dict:
        from app.academics.models import Course

        p = await self.session.get(QuestionPaper, paper_id)
        if p is None:
            raise ResourceNotFoundError("Paper not found.")
        c = await self.session.get(Course, p.course_id)
        items = (await self.session.execute(select(PaperItem, BankQuestion).join(BankQuestion, BankQuestion.id == PaperItem.question_id)
                                            .where(PaperItem.paper_id == p.id).order_by(PaperItem.position))).all()
        out = [{**self._q(q), "item_id": it.id, "position": it.position, "bank_marks": float(q.marks), "marks": float(it.marks)} for it, q in items]
        clos: dict = {}
        blooms: dict = {}
        for x in out:
            clos[x["clo"] or "—"] = clos.get(x["clo"] or "—", 0) + x["marks"]
            blooms[x["bloom"] or "—"] = blooms.get(x["bloom"] or "—", 0) + x["marks"]
        return {"id": p.id, "title": p.title, "course_id": p.course_id, "course": f"{c.code} {c.title}" if c else "", "status": p.status,
                "duration_minutes": p.duration_minutes, "instructions": p.instructions, "blueprint": p.blueprint, "items": out,
                "total_marks": sum(x["marks"] for x in out), "marks_by_clo": clos, "marks_by_bloom": blooms}

    async def _draft(self, paper_id: uuid.UUID) -> QuestionPaper:
        p = await self.session.get(QuestionPaper, paper_id)
        if p is None:
            raise ResourceNotFoundError("Paper not found.")
        if p.status != "DRAFT":
            raise BusinessRuleViolationError("A final paper cannot be changed.")
        return p

    async def swap(self, paper_id: uuid.UUID, item_id: uuid.UUID) -> dict:
        """Replace one question with another of the same kind, difficulty and Bloom level not already in the paper."""
        p = await self._draft(paper_id)
        it = await self.session.get(PaperItem, item_id)
        if it is None or it.paper_id != p.id:
            raise ResourceNotFoundError("Question not in this paper.")
        cur = await self.session.get(BankQuestion, it.question_id)
        used = set((await self.session.execute(select(PaperItem.question_id).where(PaperItem.paper_id == p.id))).scalars())
        q = select(BankQuestion).where(BankQuestion.course_id == p.course_id, BankQuestion.kind == cur.kind, BankQuestion.difficulty == cur.difficulty,
                                       BankQuestion.is_active.is_(True), BankQuestion.id.not_in(used))
        alt = (await self.session.execute((q.where(BankQuestion.bloom == cur.bloom) if cur.bloom else q).order_by(BankQuestion.times_used, func.random()).limit(1))).scalar_one_or_none()
        if alt is None:
            alt = (await self.session.execute(q.order_by(BankQuestion.times_used, func.random()).limit(1))).scalar_one_or_none()
        if alt is None:
            raise BusinessRuleViolationError("No other question of this kind and difficulty in the bank.")
        same_marks = Decimal(str(it.marks)) == Decimal(str(cur.marks))
        it.question_id = alt.id
        if same_marks:
            it.marks = alt.marks
        await self.session.flush()
        return await self.paper(p.id)

    async def add_item(self, paper_id: uuid.UUID, question_id: uuid.UUID, marks: Decimal | None) -> dict:
        p = await self._draft(paper_id)
        q = await self.session.get(BankQuestion, question_id)
        if q is None or q.course_id != p.course_id or not q.is_active:
            raise ValidationAppError("Choose an active question of this course.")
        if (await self.session.execute(select(PaperItem.id).where(PaperItem.paper_id == p.id, PaperItem.question_id == q.id))).first():
            raise BusinessRuleViolationError("That question is already in the paper.")
        n = int((await self.session.execute(select(func.coalesce(func.max(PaperItem.position), 0)).where(PaperItem.paper_id == p.id))).scalar_one())
        self.session.add(PaperItem(paper_id=p.id, question_id=q.id, position=n + 1, marks=marks if marks is not None else q.marks))
        await self.session.flush()
        return await self.paper(p.id)

    async def remove_item(self, paper_id: uuid.UUID, item_id: uuid.UUID) -> dict:
        p = await self._draft(paper_id)
        it = await self.session.get(PaperItem, item_id)
        if it is None or it.paper_id != p.id:
            raise ResourceNotFoundError("Question not in this paper.")
        await self.session.delete(it)
        await self.session.flush()
        rest = (await self.session.execute(select(PaperItem).where(PaperItem.paper_id == p.id).order_by(PaperItem.position))).scalars().all()
        for i, x in enumerate(rest):
            x.position = i + 1
        await self.session.flush()
        return await self.paper(p.id)

    async def move(self, paper_id: uuid.UUID, item_id: uuid.UUID, step: int) -> dict:
        p = await self._draft(paper_id)
        rest = list((await self.session.execute(select(PaperItem).where(PaperItem.paper_id == p.id).order_by(PaperItem.position))).scalars())
        i = next((k for k, x in enumerate(rest) if x.id == item_id), None)
        if i is None:
            raise ResourceNotFoundError("Question not in this paper.")
        j = max(0, min(len(rest) - 1, i + (1 if step > 0 else -1)))
        rest[i], rest[j] = rest[j], rest[i]
        for k, x in enumerate(rest):
            x.position = 1000 + k                       # avoid clashing positions mid-way (no unique on position, but keep it clean)
        await self.session.flush()
        for k, x in enumerate(rest):
            x.position = k + 1
        await self.session.flush()
        return await self.paper(p.id)

    async def finalise(self, paper_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService

        p = await self._draft(paper_id)
        items = (await self.session.execute(select(PaperItem).where(PaperItem.paper_id == p.id))).scalars().all()
        if not items:
            raise BusinessRuleViolationError("The paper has no questions.")
        for it in items:
            q = await self.session.get(BankQuestion, it.question_id)
            q.times_used = (q.times_used or 0) + 1
        p.status, p.finalised_by, p.finalised_at = "FINAL", actor_id, datetime.now(timezone.utc)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="qbank.paper_final", entity_type="qbank_paper", entity_id=p.id,
                                             metadata={"title": p.title, "questions": len(items)})
        return await self.paper(p.id)

    async def delete_draft(self, paper_id: uuid.UUID) -> None:
        p = await self._draft(paper_id)
        await self.session.delete(p)
        await self.session.flush()


# ====================================================================== the printed paper / answer key
def paper_pdf(paper: dict, *, key: bool) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from app.core import brand

    buf = io.BytesIO()
    width = A4[0] - 36 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=("Answer key — " if key else "") + paper["title"], author=brand.name())
    base = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica", fontSize=10.5, leading=14.5)
    head = ParagraphStyle("h", parent=body, fontName="Helvetica-Bold", fontSize=11.5, spaceBefore=8, spaceAfter=4)
    small = ParagraphStyle("s", parent=body, fontSize=9, textColor="#555555")
    story = brand.platypus_header(width) + [Spacer(1, 4), Paragraph(f"<b>{escape(paper['course'])}</b> — {escape(paper['title'])}" + (" — ANSWER KEY" if key else ""), head),
                                           Paragraph(f"Total marks: {paper['total_marks']:g}" + (f" · Time: {paper['duration_minutes']} minutes" if paper.get("duration_minutes") else ""), body)]
    if paper.get("instructions"):
        story.append(Paragraph(escape(paper["instructions"]).replace("\n", "<br/>"), small))
    current = None
    for n, x in enumerate(paper["items"], 1):
        if x["kind"] != current:
            current = x["kind"]
            story.append(Paragraph(KIND_NAMES[current], head))
        story.append(Paragraph(f"<b>Q{n}.</b> {escape(x['text']).replace(chr(10), '<br/>')} <i>({x['marks']:g})</i>", body))
        if x["kind"] == "MCQ":
            story.append(Paragraph("&nbsp;&nbsp;&nbsp;".join(f"({LETTERS[i]}) {escape(o)}" for i, o in enumerate(x["options"] or [])), body))
        if key and x.get("answer"):
            story.append(Paragraph(f"<b>Answer:</b> {escape(x['answer'])}" + (f" · {x['clo']}" if x.get("clo") else "") + (f" · {x['bloom']}" if x.get("bloom") else ""), small))
        story.append(Spacer(1, 4))
    doc.build(story)
    return buf.getvalue()
