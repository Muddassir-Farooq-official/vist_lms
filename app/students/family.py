"""The student's father (or, when no guardian is recorded as the father, the main guardian) for the accounts office:
shown next to the student's name in searches, fee lists, statements, challans and receipts so two students with the
same name are told apart ("Ali Khan s/o Ahmed Khan").

Only the guardian's NAME and relation are given out here, never the CNIC or phone: the CNIC is used for searching only.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permissions
from app.students.models import Student, StudentGuardian

FATHER_WORDS = ("father", "abbu", "walid")


def is_father(relation: str | None) -> bool:
    r = (relation or "").strip().lower()
    return any(w in r for w in FATHER_WORDS) and "grand" not in r and "step" not in r


def label_for(guardians: list[StudentGuardian], gender: str | None) -> dict | None:
    """{"name", "relation", "is_father", "label"} for display, or None when the student has no guardian on record.
    The father first; otherwise the main guardian, then the first one, with the relation in brackets."""
    if not guardians:
        return None
    father = next((g for g in guardians if is_father(g.relation)), None)
    g = father or next((g for g in guardians if g.is_primary), None) or guardians[0]
    name = " ".join((g.name or "").split())
    if not name:
        return None
    if father is not None:
        sex = (gender or "").strip().upper()
        label = f"{'d/o' if sex.startswith('F') else 's/o' if sex.startswith('M') else 'c/o'} {name}"
    else:
        rel = " ".join((g.relation or "").split()).title()
        label = f"Guardian: {name}" + (f" ({rel})" if rel else "")
    return {"name": name, "relation": "Father" if father is not None else " ".join((g.relation or "").split()).title() or None,
            "is_father": father is not None, "label": label}


async def guardian_labels(session: AsyncSession, student_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """label_for for many students in one query: {student_id: {...}} (students without a guardian are left out)."""
    ids = {i for i in student_ids if i}
    if not ids:
        return {}
    gender = dict((await session.execute(select(Student.id, Student.gender).where(Student.id.in_(ids)))).all())
    by: dict[uuid.UUID, list[StudentGuardian]] = {}
    for g in (await session.execute(select(StudentGuardian).where(StudentGuardian.student_id.in_(ids))
                                    .order_by(StudentGuardian.is_primary.desc(), StudentGuardian.created_at))).scalars():
        by.setdefault(g.student_id, []).append(g)
    out = {}
    for sid, gs in by.items():
        lab = label_for(gs, gender.get(sid))
        if lab:
            out[sid] = lab
    return out


def sees_guardians(user) -> bool:
    """Who gets the guardian's name in student lists and can search by the father's name or CNIC: the people who handle
    fees (administration and accounts office). Other staff keep the plain directory (no guardian data in lists)."""
    perms = set(getattr(user, "permissions", None) or [])
    return bool(perms & {Permissions.FEE_MANAGE, Permissions.PAYMENT_RECORD})
