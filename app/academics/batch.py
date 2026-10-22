"""Fetch related rows for a whole list in ONE query each, instead of one query per row.

Building a list of 150 course offerings used to run ~450 tiny queries (course, teacher and
enrolled-count for every row). Loading them together keeps a page snappy as the school grows.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm.util import identity_key
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import Enrollment, EnrollmentStatus


async def load_by_ids(session: AsyncSession, model, ids: Iterable[uuid.UUID | None]) -> dict:
    """{id: row} for every id given (missing ids are simply absent)."""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    # Rows this request already loaded (e.g. through an eager relationship) cost nothing: only the
    # rest go to the database. Matters most when the database is far away (every query is a round trip).
    found = {}
    for i in wanted:
        obj = session.identity_map.get(identity_key(model, i))
        if obj is not None:
            found[i] = obj
    missing = wanted - found.keys()
    if missing:
        rows = (await session.execute(select(model).where(model.id.in_(missing)))).scalars().all()
        found.update({row.id: row for row in rows})
    return found


async def enrolled_counts(session: AsyncSession, offering_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    """{offering_id: number of currently ENROLLED students}."""
    wanted = set(offering_ids)
    if not wanted:
        return {}
    stmt = (
        select(Enrollment.course_offering_id, func.count())
        .where(Enrollment.course_offering_id.in_(wanted), Enrollment.status == EnrollmentStatus.ENROLLED)
        .group_by(Enrollment.course_offering_id)
    )
    return {oid: n for oid, n in (await session.execute(stmt)).all()}