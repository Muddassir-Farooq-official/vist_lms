"""Grading tables: one named, fully editable table per affiliating body (KMU, AUST, Sarhad, Board ...), picked per program.

A table holds:
  * bands: from this percentage the student gets this letter, grade points (fixed, or sliding inside the band, e.g. AUST
    76-79% = 3.6-3.9), whether it passes, a division (1st / 2nd / 3rd) and a remark;
  * pass marks: overall, and theory and practical/clinical separately (each part is passed on its own);
  * a re-sit cap (e.g. a re-sit can earn at most 60%), and the semester rules: GPA for promotion, probation, CGPA for
    the degree; Semester (GPA/CGPA) or Annual (total marks, %, division).
Programs without a table use the institute's default scale (Results -> Grading scale), exactly as before.

The mark split (Sessional / Mid / Final / Practical out of how many marks) is set per program and may be overridden per
subject. Each assessment in the catalog says which part it belongs to; quizzes and assignments without a part count as
Sessional, scaled to the Sessional marks. With no split set, a subject's result is the plain sum of its marks (as before).
Published results are frozen (EnrollmentResult) and never change when a table or a split is edited.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.audit.service import AuditService
from app.core.config import settings
from app.core.database import Base, TimestampMixin, UUIDPKMixin
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError

COMPONENTS = ("SESSIONAL", "MID", "FINAL", "PRACTICAL")
COMPONENT_LABELS = {"SESSIONAL": "Sessional / internal", "MID": "Mid-term", "FINAL": "Final / terminal", "PRACTICAL": "Practical / clinical / viva"}
THEORY = ("SESSIONAL", "MID", "FINAL")
OFFICE_ONLY = ("FINAL", "PRACTICAL")          # entered by the exam office, not the teacher


class GradingScheme(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "grading_schemes"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    body: Mapped[str | None] = mapped_column(String(150), nullable=True)          # "Khyber Medical University, Peshawar"
    system: Mapped[str] = mapped_column(String(10), nullable=False, default="SEMESTER")   # SEMESTER | ANNUAL
    pass_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    theory_pass_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    practical_pass_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    resit_cap_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    min_gpa_promotion: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    probation_gpa: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    min_cgpa_degree: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    sliding_points: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class GradingBand(UUIDPKMixin, Base):
    __tablename__ = "grading_bands"
    __table_args__ = (UniqueConstraint("scheme_id", "letter", name="uq_grading_band_letter"),
                      UniqueConstraint("scheme_id", "min_percent", name="uq_grading_band_min"))

    scheme_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("grading_schemes.id", ondelete="CASCADE"), nullable=False, index=True)
    letter: Mapped[str] = mapped_column(String(4), nullable=False)
    min_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    grade_points: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)            # at the top of the band
    grade_points_low: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # at the bottom (sliding tables)
    is_passing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    division: Mapped[str | None] = mapped_column(String(40), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(60), nullable=True)


class MarkSplit(UUIDPKMixin, TimestampMixin, Base):
    """How a subject's 100 (or any total) is split: per program, or per subject (course) to override it."""
    __tablename__ = "mark_splits"

    program_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=True, unique=True)
    course_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=True, unique=True)
    sessional: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    mid: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    final: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    practical: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)


# ---------------------------------------------------------------------- the rules used to grade one student
@dataclass
class Band:
    letter: str
    min_percent: float
    points: float
    points_low: float | None
    is_passing: bool
    division: str | None = None
    remark: str | None = None


@dataclass
class Rules:
    scheme_id: uuid.UUID | None
    name: str
    body: str | None
    system: str
    pass_percent: float
    theory_pass: float | None
    practical_pass: float | None
    resit_cap: float | None
    sliding: bool
    bands: list[Band] = field(default_factory=list)
    min_gpa_promotion: float | None = None
    probation_gpa: float | None = None
    min_cgpa_degree: float | None = None

    def grade(self, percentage: float, passed: bool) -> tuple[str, float, str | None]:
        """(letter, grade points, division). A failed student gets a failing band whatever the percentage; a passed one the
        highest band reached (never below the lowest passing band). Sliding tables move the points inside the band."""
        ordered = sorted(self.bands, key=lambda b: -b.min_percent)
        pool = [b for b in ordered if b.is_passing == passed] or ordered
        pick = next((b for b in pool if b.min_percent <= percentage), pool[-1])
        points = pick.points
        if self.sliding and pick.points_low is not None and passed == pick.is_passing:
            higher = [b.min_percent for b in ordered if b.min_percent > pick.min_percent]
            top = (min(higher) - 1) if higher else 100.0
            if top > pick.min_percent:
                frac = max(0.0, min(1.0, (percentage - pick.min_percent) / (top - pick.min_percent)))
                points = round(pick.points_low + (pick.points - pick.points_low) * frac, 2)
        return pick.letter, float(points), pick.division


async def default_rules(session: AsyncSession) -> Rules:
    from app.academics.grade_scale import GradeScaleService

    entries = await GradeScaleService(session).entries()
    return Rules(None, "Institute default", None, "SEMESTER", float(settings.marks_pass_threshold_percent), None, None, None, False,
                 [Band(e.letter, float(e.min_percent), float(e.grade_points), None, e.is_passing) for e in entries])


def _rules_of(scheme: GradingScheme, bands: list[GradingBand]) -> Rules:
    f = lambda v: float(v) if v is not None else None      # noqa: E731
    return Rules(scheme.id, scheme.name, scheme.body, scheme.system, float(scheme.pass_percent), f(scheme.theory_pass_percent),
                 f(scheme.practical_pass_percent), f(scheme.resit_cap_percent), bool(scheme.sliding_points),
                 [Band(b.letter, float(b.min_percent), float(b.grade_points), f(b.grade_points_low), b.is_passing, b.division, b.remark) for b in bands],
                 f(scheme.min_gpa_promotion), f(scheme.probation_gpa), f(scheme.min_cgpa_degree))


async def rules_for_program(session: AsyncSession, program_id: uuid.UUID | None) -> Rules:
    from app.programs.models import Program

    program = await session.get(Program, program_id) if program_id else None
    scheme_id = getattr(program, "grading_scheme_id", None) if program else None
    if scheme_id:
        scheme = await session.get(GradingScheme, scheme_id)
        if scheme is not None:
            bands = list((await session.execute(select(GradingBand).where(GradingBand.scheme_id == scheme.id))).scalars())
            if bands:
                return _rules_of(scheme, bands)
    return await default_rules(session)


async def rules_for_student(session: AsyncSession, student_id: uuid.UUID) -> Rules:
    from app.students.models import Student

    student = await session.get(Student, student_id)
    return await rules_for_program(session, student.program_id if student else None)


async def split_for(session: AsyncSession, *, course_id: uuid.UUID | None, program_id: uuid.UUID | None) -> dict | None:
    """{component: marks} for a subject: its own split, else its program's, else None (plain sum of marks)."""
    for col, value in ((MarkSplit.course_id, course_id), (MarkSplit.program_id, program_id)):
        if value is None:
            continue
        row = (await session.execute(select(MarkSplit).where(col == value))).scalar_one_or_none()
        if row is not None:
            out = {"SESSIONAL": float(row.sessional), "MID": float(row.mid), "FINAL": float(row.final), "PRACTICAL": float(row.practical)}
            if sum(out.values()) > 0:
                return out
    return None


def weighted_result(split: dict, parts: dict[str, tuple[float, float]], rules: Rules) -> dict:
    """parts = {component: (obtained, max)} from the subject's marks. Components with no assessment are left out (and listed)."""
    present = {c: w for c, w in split.items() if w > 0 and c in parts and parts[c][1] > 0}
    missing = [c for c, w in split.items() if w > 0 and c not in present]
    score = {c: parts[c][0] / parts[c][1] * w for c, w in present.items()}
    total_w = sum(present.values())
    pct = round(sum(score.values()) / total_w * 100, 2) if total_w else 0.0
    th_w = sum(w for c, w in present.items() if c in THEORY)
    pr_w = present.get("PRACTICAL", 0.0)
    theory = round(sum(v for c, v in score.items() if c in THEORY) / th_w * 100, 2) if th_w else None
    practical = round(score["PRACTICAL"] / pr_w * 100, 2) if pr_w else None
    failed = []
    if theory is not None and rules.theory_pass is not None and theory < rules.theory_pass:
        failed.append("THEORY")
    if practical is not None and rules.practical_pass is not None and practical < rules.practical_pass:
        failed.append("PRACTICAL")
    return {"percentage": pct, "total_obtained": round(sum(score.values()), 2), "total_max": round(total_w, 2), "theory_percent": theory,
            "practical_percent": practical, "failed_parts": failed, "missing_parts": missing,
            "by_part": {c: round(v, 2) for c, v in score.items()}}


# ---------------------------------------------------------------------- managing tables (Results -> Grading tables)
def validate_bands(items: list[dict], sliding: bool) -> list[str]:
    problems = []
    if not 2 <= len(items) <= 25:
        return ["A table needs between 2 and 25 grades."]
    letters = [str(i.get("letter") or "").strip().upper() for i in items]
    if any(not x or len(x) > 4 for x in letters):
        problems.append("Every grade needs a short name (up to 4 characters), for example A or B+.")
    if len(set(letters)) != len(letters):
        problems.append("Two grades have the same name.")
    mins = [float(i["min_percent"]) for i in items]
    if len(set(mins)) != len(mins):
        problems.append("Two grades start at the same percentage.")
    if any(m < 0 or m > 100 for m in mins):
        problems.append("Percentages must be between 0 and 100.")
    if min(mins) != 0:
        problems.append("The lowest grade must start at 0%, so that every mark earns a grade.")
    ordered = sorted(items, key=lambda i: -float(i["min_percent"]))
    for i in ordered:
        p, low = float(i["grade_points"]), i.get("grade_points_low")
        if not 0 <= p <= 10 or (low is not None and not 0 <= float(low) <= p):
            problems.append(f"Grade {i['letter']}: points must be 0-10, and the lowest points of a band not above its highest.")
            break
    points = [float(i["grade_points"]) for i in ordered]
    if any(points[k] < points[k + 1] for k in range(len(points) - 1)):
        problems.append("A higher percentage must never earn fewer grade points than a lower one.")
    flags = [bool(i["is_passing"]) for i in ordered]
    if not any(flags) or all(flags):
        problems.append("Mark at least one grade as passing and at least one (the lowest) as failing.")
    elif any(not flags[k] and flags[k + 1] for k in range(len(flags) - 1)):
        problems.append("A failing grade cannot sit above a passing one.")
    _ = sliding
    return problems


SCHEME_FIELDS = ("name", "body", "system", "pass_percent", "theory_pass_percent", "practical_pass_percent", "resit_cap_percent",
                 "min_gpa_promotion", "probation_gpa", "min_cgpa_degree", "sliding_points", "is_draft", "notes")


def scheme_dict(s: GradingScheme, bands: list[GradingBand], programs: list | None = None) -> dict:
    f = lambda v: float(v) if v is not None else None      # noqa: E731
    return {"id": s.id, "name": s.name, "body": s.body, "system": s.system, "pass_percent": f(s.pass_percent),
            "theory_pass_percent": f(s.theory_pass_percent), "practical_pass_percent": f(s.practical_pass_percent),
            "resit_cap_percent": f(s.resit_cap_percent), "min_gpa_promotion": f(s.min_gpa_promotion), "probation_gpa": f(s.probation_gpa),
            "min_cgpa_degree": f(s.min_cgpa_degree), "sliding_points": s.sliding_points, "is_draft": s.is_draft, "notes": s.notes,
            "bands": [{"letter": b.letter, "min_percent": f(b.min_percent), "grade_points": f(b.grade_points), "grade_points_low": f(b.grade_points_low),
                       "is_passing": b.is_passing, "division": b.division, "remark": b.remark}
                      for b in sorted(bands, key=lambda b: -float(b.min_percent))],
            "programs": programs or []}


def _audit_view(d: dict) -> dict:
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in d.items() if k not in ("id", "programs")}


class GradingSchemeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _bands(self, scheme_id) -> list[GradingBand]:
        return list((await self.session.execute(select(GradingBand).where(GradingBand.scheme_id == scheme_id))).scalars())

    async def listing(self) -> list[dict]:
        from app.programs.models import Program

        schemes = list((await self.session.execute(select(GradingScheme).order_by(GradingScheme.name))).scalars())
        progs = list((await self.session.execute(select(Program).where(Program.grading_scheme_id.is_not(None)))).scalars())
        return [scheme_dict(s, await self._bands(s.id), [{"id": p.id, "code": p.code} for p in progs if p.grading_scheme_id == s.id]) for s in schemes]

    async def get(self, scheme_id: uuid.UUID) -> GradingScheme:
        s = await self.session.get(GradingScheme, scheme_id)
        if s is None:
            raise ResourceNotFoundError("Grading table not found.")
        return s

    def _check(self, data: dict) -> None:
        if data.get("system") not in ("SEMESTER", "ANNUAL"):
            raise BusinessRuleViolationError("Choose Semester or Annual.")
        for k in ("pass_percent", "theory_pass_percent", "practical_pass_percent", "resit_cap_percent"):
            v = data.get(k)
            if v is not None and not 0 <= float(v) <= 100:
                raise BusinessRuleViolationError("Pass marks and the re-sit cap are percentages between 0 and 100.")
        for k in ("min_gpa_promotion", "probation_gpa", "min_cgpa_degree"):
            v = data.get(k)
            if v is not None and not 0 <= float(v) <= 10:
                raise BusinessRuleViolationError("GPA limits must be between 0 and 10.")
        problems = validate_bands(data.get("bands") or [], bool(data.get("sliding_points")))
        if problems:
            raise BusinessRuleViolationError(" ".join(problems))

    async def save(self, data: dict, *, actor_id: uuid.UUID, scheme_id: uuid.UUID | None = None) -> dict:
        """Create a table, or replace an existing one's settings and grades. Published results are never touched."""
        data = {**data, "name": " ".join(str(data.get("name") or "").split())}
        if not data["name"]:
            raise BusinessRuleViolationError("Give the table a name, for example KMU or AUST.")
        self._check(data)
        clash = (await self.session.execute(select(GradingScheme).where(GradingScheme.name == data["name"]))).scalar_one_or_none()
        if clash is not None and clash.id != scheme_id:
            raise DuplicateResourceError(f"A table named {data['name']} already exists.")
        if scheme_id is None:
            s = GradingScheme(name=data["name"], pass_percent=data["pass_percent"])
            self.session.add(s)
            before = None
        else:
            s = await self.get(scheme_id)
            before = _audit_view(scheme_dict(s, await self._bands(s.id)))
        for k in SCHEME_FIELDS:
            if k in data:
                setattr(s, k, data[k] if k != "body" else ((data[k] or "").strip() or None))
        await self.session.flush()
        for b in await self._bands(s.id):
            await self.session.delete(b)
        await self.session.flush()
        for i in data["bands"]:
            self.session.add(GradingBand(scheme_id=s.id, letter=str(i["letter"]).strip().upper(), min_percent=i["min_percent"], grade_points=i["grade_points"],
                                         grade_points_low=i.get("grade_points_low"), is_passing=bool(i["is_passing"]),
                                         division=(i.get("division") or "").strip() or None, remark=(i.get("remark") or "").strip() or None))
        await self.session.flush()
        after = scheme_dict(s, await self._bands(s.id))
        await self.audit.log(actor_user_id=actor_id, action="grading_scheme.saved" if before else "grading_scheme.created", entity_type="grading_scheme",
                             entity_id=s.id, before=before, after=_audit_view(after))
        return after

    async def delete(self, scheme_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        from app.programs.models import Program

        s = await self.get(scheme_id)
        if (await self.session.execute(select(Program.id).where(Program.grading_scheme_id == s.id))).first():
            raise BusinessRuleViolationError("Programs still use this table. Give them another table first.")
        await self.audit.log(actor_user_id=actor_id, action="grading_scheme.deleted", entity_type="grading_scheme", entity_id=s.id, before={"name": s.name})
        await self.session.delete(s)

    async def assign(self, program_id: uuid.UUID, scheme_id: uuid.UUID | None, *, actor_id: uuid.UUID) -> None:
        from app.programs.models import Program

        p = await self.session.get(Program, program_id)
        if p is None:
            raise ResourceNotFoundError("Program not found.")
        if scheme_id is not None:
            s = await self.get(scheme_id)
            if s.is_draft:
                raise BusinessRuleViolationError(f"{s.name} is still a draft. Check it against the official table and untick 'Draft' before programs use it.")
        before = p.grading_scheme_id
        p.grading_scheme_id = scheme_id
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="program.grading_scheme_set", entity_type="program", entity_id=p.id,
                             before={"scheme": str(before) if before else None}, after={"scheme": str(scheme_id) if scheme_id else None})

    # ---- mark splits
    async def splits(self) -> list[dict]:
        rows = list((await self.session.execute(select(MarkSplit))).scalars())
        return [{"id": r.id, "program_id": r.program_id, "course_id": r.course_id, "sessional": float(r.sessional), "mid": float(r.mid),
                 "final": float(r.final), "practical": float(r.practical)} for r in rows]

    async def set_split(self, *, program_id: uuid.UUID | None, course_id: uuid.UUID | None, values: dict | None, actor_id: uuid.UUID) -> dict | None:
        if (program_id is None) == (course_id is None):
            raise BusinessRuleViolationError("A split belongs to one program or one subject.")
        col, key = (MarkSplit.program_id, program_id) if program_id else (MarkSplit.course_id, course_id)
        row = (await self.session.execute(select(MarkSplit).where(col == key))).scalar_one_or_none()
        before = {c: float(getattr(row, c.lower())) for c in COMPONENTS} if row else None
        if values is None:                       # remove (back to the program's split / plain sum)
            if row is not None:
                await self.session.delete(row)
            after = None
        else:
            vals = {c: float(values.get(c, 0) or 0) for c in COMPONENTS}
            if any(v < 0 or v > 1000 for v in vals.values()) or sum(vals.values()) <= 0:
                raise BusinessRuleViolationError("Give each part's marks (0-1000), with at least one part above 0.")
            if row is None:
                row = MarkSplit(program_id=program_id, course_id=course_id)
                self.session.add(row)
            row.sessional, row.mid, row.final, row.practical = vals["SESSIONAL"], vals["MID"], vals["FINAL"], vals["PRACTICAL"]
            after = vals
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="mark_split.set", entity_type="program" if program_id else "course",
                             entity_id=program_id or course_id, before=before, after=after)
        return after

    async def add_ready_made_drafts(self, *, actor_id: uuid.UUID) -> list[str]:
        """Starting points for the usual bodies, marked Draft until someone checks them against the official table."""
        made = []
        for d in DRAFTS:
            if (await self.session.execute(select(GradingScheme.id).where(GradingScheme.name == d["name"]))).first():
                continue
            await self.save({**d, "is_draft": True}, actor_id=actor_id)
            made.append(d["name"])
        return made


def _b(letter, minimum, points, passing=True, low=None, division=None, remark=None):
    return {"letter": letter, "min_percent": minimum, "grade_points": points, "grade_points_low": low, "is_passing": passing, "division": division, "remark": remark}


DRAFTS = [
    {"name": "AUST", "body": "Abbottabad University of Science & Technology", "system": "SEMESTER", "pass_percent": 50, "theory_pass_percent": 50,
     "practical_pass_percent": 50, "resit_cap_percent": 60, "min_gpa_promotion": 2.0, "probation_gpa": 1.5, "min_cgpa_degree": 2.0, "sliding_points": True,
     "notes": "From AUST Undergraduate Rules (revised 2026). The published table was partly unreadable: check every band edge with the Controller of Examinations.",
     "bands": [_b("A+", 90, 4.0, remark="Exceptional"), _b("A", 85, 4.0, remark="Excellent"), _b("A-", 80, 4.0, remark="High Distinction"),
               _b("B+", 76, 3.9, low=3.6, remark="Very Good"), _b("B", 73, 3.5, low=3.3, remark="Good"), _b("B-", 70, 3.2, low=3.0, remark="Fairly Good"),
               _b("C+", 66, 2.9, low=2.6, remark="Satisfactory"), _b("C", 60, 2.5, low=2.0, remark="Adequate"), _b("D+", 58, 1.9, low=1.8, remark="Subjective Pass"),
               _b("D", 50, 1.7, low=1.0, remark="Minimum acceptable"), _b("F", 0, 0.0, passing=False, remark="Fail")]},
    {"name": "KMU", "body": "Khyber Medical University, Peshawar", "system": "SEMESTER", "pass_percent": 60, "theory_pass_percent": 60, "practical_pass_percent": 60,
     "min_cgpa_degree": 2.0, "sliding_points": False,
     "notes": "Rows F (<60), C (60-64, 2.0) and C+ (65-69, 2.5) are from a KMU-affiliated college's published rules; the rows above 70% are placeholders. "
              "Sources disagree on the pass mark (50% or 60%). Enter KMU's official table before use.",
     "bands": [_b("A", 80, 4.0), _b("B+", 75, 3.5), _b("B", 70, 3.0), _b("C+", 65, 2.5), _b("C", 60, 2.0), _b("F", 0, 0.0, passing=False)]},
    {"name": "Sarhad University", "body": "Sarhad University of Science & Information Technology", "system": "SEMESTER", "pass_percent": 50,
     "theory_pass_percent": 50, "practical_pass_percent": 50, "min_cgpa_degree": 2.0, "sliding_points": False,
     "notes": "Placeholder copied from the common HEC 4.0 scale; Sarhad's own table was not found online. Enter the official table before use.",
     "bands": [_b("A", 85, 4.0), _b("A-", 80, 3.66), _b("B+", 75, 3.33), _b("B", 71, 3.0), _b("B-", 68, 2.66), _b("C+", 64, 2.33), _b("C", 61, 2.0),
               _b("C-", 58, 1.66), _b("D+", 54, 1.33), _b("D", 50, 1.0), _b("F", 0, 0.0, passing=False)]},
    {"name": "Board (annual)", "body": "Board of Intermediate & Secondary Education", "system": "ANNUAL", "pass_percent": 33, "theory_pass_percent": 33,
     "practical_pass_percent": 33, "sliding_points": False,
     "notes": "Placeholder for annual (yearly) examinations with divisions. Boards differ (33% or 40% pass); enter your board's official rules before use.",
     "bands": [_b("A1", 80, 4.0, division="1st Division", remark="Exceptional"), _b("A", 70, 3.5, division="1st Division", remark="Excellent"),
               _b("B", 60, 3.0, division="1st Division", remark="Very Good"), _b("C", 50, 2.5, division="2nd Division", remark="Good"),
               _b("D", 40, 2.0, division="3rd Division", remark="Fair"), _b("E", 33, 1.0, division="3rd Division", remark="Pass"),
               _b("F", 0, 0.0, passing=False, remark="Fail")]},
]
