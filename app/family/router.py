"""Home cards for parents (one per child) and students (their own)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/family", tags=["family"])


@router.get("/cards", summary="Home cards: a parent's children, or the signed-in student")
async def cards(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.family.service import card
    from app.parents.models import ParentStudentLink
    from app.students.models import Student

    me = (await db.execute(select(Student).where(Student.user_id == current_user.id))).scalar_one_or_none()
    kids = list((await db.execute(select(Student).join(ParentStudentLink, ParentStudentLink.student_id == Student.id)
                                  .where(ParentStudentLink.user_id == current_user.id).order_by(Student.roll_number))).scalars())
    out = []
    for s in ([me] if me else []) + [k for k in kids if not me or k.id != me.id]:
        out.append(await card(db, s))
    return out
