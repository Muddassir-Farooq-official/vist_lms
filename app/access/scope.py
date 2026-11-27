"""Department and program limits (Access control -> People -> Limit to).

A person can be limited to some departments and/or programs. Then, in the places that follow limits, they only see and
act on what is inside them:
  - classes (attendance, corrections, results): a class is inside when its teacher's department is one of theirs, or
    its batch term belongs to one of their programs;
  - batch terms (the timetable): inside when the term belongs to one of their programs (a limit by department only
    does not narrow the timetable, which is planned per program);
  - students (directory and records): inside when they are in one of their programs (a limit by department only does
    not narrow students);
  - requests to decide (My decisions and each module's decide): inside when the class, student or person concerned is.
Administrators are never limited. A Head of Department with no limit set keeps the rule they always had: their own
department (from their staff record).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Roles


def norm(value: str | None) -> str | None:
    cleaned = " ".join((value or "").split()).lower()
    return cleaned or None


@dataclass(frozen=True)
class Scope:
    departments: frozenset = field(default_factory=frozenset)      # normalised department names
    programs: frozenset = field(default_factory=frozenset)         # program ids
    source: str = "set"                                            # set (Access control) / hod (own department)

    @property
    def empty(self) -> bool:
        return not self.departments and not self.programs


async def get_scope(db: AsyncSession, user) -> Scope | None:
    """None = not limited."""
    roles = set(getattr(user, "roles", None) or [])
    if Roles.ADMIN in roles:
        return None
    from app.access.scope_models import AccessScope

    uid = uuid.UUID(str(user.id))
    rows = (await db.execute(select(AccessScope.kind, AccessScope.value).where(AccessScope.user_id == uid))).all()
    if rows:
        deps = frozenset(n for k, v in rows if k == "DEPARTMENT" and (n := norm(v)))
        progs = frozenset(uuid.UUID(v) for k, v in rows if k == "PROGRAM")
        return Scope(deps, progs, "set")
    if Roles.HOD in roles:
        from app.employees.models import Employee

        dept = norm((await db.execute(select(Employee.department).where(Employee.user_id == uid))).scalar_one_or_none())
        if dept:
            return Scope(frozenset({dept}), frozenset(), "hod")
    return None


async def term_programs(db: AsyncSession, labels) -> dict[str, uuid.UUID | None]:
    from app.academics.terms import AcademicTerm

    labels = {lbl for lbl in labels if lbl}
    if not labels:
        return {}
    return {lbl: pid for lbl, pid in (await db.execute(select(AcademicTerm.label, AcademicTerm.program_id).where(AcademicTerm.label.in_(labels)))).all()}


async def allows_terms(db: AsyncSession, scope: Scope | None, labels) -> bool:
    if scope is None or not scope.programs:
        return True
    progs = await term_programs(db, labels)
    return all(progs.get(lbl) in scope.programs for lbl in labels)


async def allowed_offering_ids(db: AsyncSession, scope: Scope | None, offering_ids) -> set:
    """Of these classes, the ones inside the limits (all of them when not limited)."""
    from app.academics.models import CourseOffering
    from app.employees.models import Employee

    ids = {uuid.UUID(str(i)) for i in offering_ids if i is not None}
    if scope is None or scope.empty or not ids:
        return ids
    offs = (await db.execute(select(CourseOffering.id, CourseOffering.assigned_teacher_id, CourseOffering.term_label)
                             .where(CourseOffering.id.in_(ids)))).all()
    teacher_ids = {t for _i, t, _l in offs if t is not None}
    depts = dict((await db.execute(select(Employee.id, Employee.department).where(Employee.id.in_(teacher_ids)))).all()) if teacher_ids else {}
    progs = await term_programs(db, {lbl for _i, _t, lbl in offs})
    out = set()
    for oid, teacher, label in offs:
        if scope.departments and norm(depts.get(teacher)) in scope.departments:
            out.add(oid)
        elif scope.programs and progs.get(label) in scope.programs:
            out.add(oid)
    return out


async def allows_offering(db: AsyncSession, scope: Scope | None, offering_id) -> bool:
    if scope is None or scope.empty or offering_id is None:
        return True
    return uuid.UUID(str(offering_id)) in await allowed_offering_ids(db, scope, [offering_id])


def allows_student_program(scope: Scope | None, program_id) -> bool:
    if scope is None or not scope.programs:
        return True
    return program_id in scope.programs


def allows_department(scope: Scope | None, department: str | None) -> bool:
    """For requests about a staff member (leave): inside when their department is one of the limits (a limit by
    program only does not narrow staff requests)."""
    if scope is None or not scope.departments:
        return True
    return norm(department) in scope.departments


async def describe(db: AsyncSession, scope: Scope | None) -> str:
    if scope is None or scope.empty:
        return "Not limited"
    from app.programs.models import Program

    names = []
    if scope.programs:
        names += [n for (n,) in (await db.execute(select(Program.code).where(Program.id.in_(scope.programs)))).all()]
    names += sorted(d.title() for d in scope.departments)
    return ", ".join(names)


async def teacher_ids_in_scope(db: AsyncSession, scope: Scope | None) -> set | None:
    """Staff (employee ids) whose classes are inside the limits: their department is one of the limits, or they teach a
    class of one of the programs. None = not limited."""
    if scope is None or scope.empty:
        return None
    from sqlalchemy import func

    from app.academics.models import CourseOffering
    from app.academics.terms import AcademicTerm
    from app.employees.models import Employee

    ids: set = set()
    if scope.departments:
        squeezed = func.lower(func.regexp_replace(func.trim(Employee.department), r"\s+", " ", "g"))
        ids |= set((await db.execute(select(Employee.id).where(Employee.department.is_not(None), squeezed.in_(list(scope.departments))))).scalars())
    if scope.programs:
        labels = select(AcademicTerm.label).where(AcademicTerm.program_id.in_(list(scope.programs)))
        ids |= set((await db.execute(select(CourseOffering.assigned_teacher_id).where(
            CourseOffering.assigned_teacher_id.is_not(None), CourseOffering.term_label.in_(labels)).distinct())).scalars())
    return ids
