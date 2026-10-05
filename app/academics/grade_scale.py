"""The grading scale: which letter and grade points a percentage earns. The administration can edit it; results already published keep their grade."""
from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import GradeScaleEntry
from app.audit.service import AuditService
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError

# The widely used 4.0 scale with plus/minus grades. Used to start a fresh install (and any database that has no scale yet).
STANDARD_SCALE = [("A", 85, 4.00, True), ("A-", 80, 3.66, True), ("B+", 75, 3.33, True), ("B", 71, 3.00, True), ("B-", 68, 2.66, True),
                  ("C+", 64, 2.33, True), ("C", 61, 2.00, True), ("C-", 58, 1.66, True), ("D+", 54, 1.33, True), ("D", 50, 1.00, True), ("F", 0, 0.00, False)]


def grade_for(entries: list[GradeScaleEntry], percentage: float, passed: bool) -> tuple[str, float]:
    """(letter, grade points). A student who FAILED the course (low marks, or attendance below the limit) gets the failing grade whatever the
    percentage; a student who passed gets the highest grade whose minimum they reach (and never lower than the lowest passing grade)."""
    ordered = sorted(entries, key=lambda e: -float(e.min_percent))
    failing = [e for e in ordered if not e.is_passing]
    passing = [e for e in ordered if e.is_passing]
    if not passed:
        pick = next((e for e in failing if float(e.min_percent) <= percentage), None) or (failing[-1] if failing else ordered[-1])
    else:
        pick = next((e for e in passing if float(e.min_percent) <= percentage), None) or (passing[-1] if passing else ordered[0])
    return pick.letter, float(pick.grade_points)


def gpa(pairs: list[tuple[float, float]]) -> float | None:
    """Credit-weighted average. pairs = [(grade points, credit hours)]. None when there are no credits."""
    credits = sum(Decimal(str(c)) for _, c in pairs)
    if credits <= 0:
        return None
    total = sum(Decimal(str(g)) * Decimal(str(c)) for g, c in pairs)
    return float((total / credits).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class GradeScaleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def entries(self) -> list[GradeScaleEntry]:
        rows = list((await self.session.execute(select(GradeScaleEntry).order_by(GradeScaleEntry.min_percent.desc()))).scalars())
        if not rows:                                         # a fresh database: start from the standard scale
            for letter, minimum, points, passing in STANDARD_SCALE:
                self.session.add(GradeScaleEntry(letter=letter, min_percent=minimum, grade_points=points, is_passing=passing))
            await self.session.flush()
            rows = list((await self.session.execute(select(GradeScaleEntry).order_by(GradeScaleEntry.min_percent.desc()))).scalars())
        return rows

    @staticmethod
    def validate(items: list[dict]) -> list[str]:
        """Plain-language problems with a proposed scale (empty list = fine)."""
        problems = []
        if not 2 <= len(items) <= 20:
            return ["A grading scale needs between 2 and 20 grades."]
        letters = [i["letter"].strip().upper() for i in items]
        if any(not l or len(l) > 4 for l in letters):
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
        points = [float(i["grade_points"]) for i in ordered]
        if any(p < 0 or p > 10 for p in points):
            problems.append("Grade points must be between 0 and 10.")
        if any(points[k] < points[k + 1] for k in range(len(points) - 1)):
            problems.append("A higher percentage must never earn fewer grade points than a lower one.")
        flags = [bool(i["is_passing"]) for i in ordered]
        if not any(flags) or all(flags):
            problems.append("Mark at least one grade as passing and at least one (the lowest) as failing.")
        elif any(not flags[k] and flags[k + 1] for k in range(len(flags) - 1)):
            problems.append("A failing grade cannot sit above a passing one.")
        return problems

    async def replace(self, items: list[dict], *, actor_id: uuid.UUID) -> tuple[list[GradeScaleEntry], str | None]:
        problems = self.validate(items)
        if problems:
            raise BusinessRuleViolationError(" ".join(problems))
        before = [{"letter": e.letter, "min": float(e.min_percent), "points": float(e.grade_points), "passing": e.is_passing} for e in await self.entries()]
        await self.session.execute(delete(GradeScaleEntry))
        await self.session.flush()
        for i in items:
            self.session.add(GradeScaleEntry(letter=i["letter"].strip().upper(), min_percent=i["min_percent"], grade_points=i["grade_points"], is_passing=bool(i["is_passing"])))
        await self.session.flush()
        after = await self.entries()
        await self.audit.log(actor_user_id=actor_id, action="grade_scale.replaced", entity_type="grade_scale", entity_id=None, before={"entries": before},
                             after={"entries": [{"letter": e.letter, "min": float(e.min_percent), "points": float(e.grade_points), "passing": e.is_passing} for e in after]})
        lowest_passing = min((float(e.min_percent) for e in after if e.is_passing), default=None)
        warning = None
        if lowest_passing is not None and lowest_passing != float(settings.marks_pass_threshold_percent):
            warning = (f"The lowest passing grade starts at {lowest_passing:g}%, but the institute's pass mark is {float(settings.marks_pass_threshold_percent):g}%. "
                       "Students are passed or failed by the pass mark; the scale only decides which letter a pass earns.")
        return after, warning