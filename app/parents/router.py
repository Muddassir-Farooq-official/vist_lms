"""Parent portal API (read-only, own children only) and the administration's parent-access actions."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.parents import models as _models  # noqa: F401  (registers the table)
from app.parents.service import ParentService

router = APIRouter(prefix="/parents", tags=["parents"])


@router.get("/me/children", summary="The students this parent may follow")
async def my_children(current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)), db=Depends(get_db)) -> list[dict]:
    return await ParentService(db).children(current_user.id)


@router.get("/me/children/{student_id}", summary="One child's current attendance, results (GPA/CGPA) and fees")
async def child_overview(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)),
                         db=Depends(get_db)) -> dict:
    return await ParentService(db).overview(current_user.id, student_id)


@router.get("/students/{student_id}", summary="The guardian's parent-portal access for this student (administration)")
async def parent_access(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_ACCESS_MANAGE)),
                        db=Depends(get_db)) -> dict:
    return await ParentService(db).access_for_student(student_id)


@router.post("/students/{student_id}", summary="Create the guardian's login (or add this student to it); reset_password=true issues a new one-time password")
async def grant_parent_access(student_id: uuid.UUID, reset_password: bool = False,
                              current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_ACCESS_MANAGE)), db=Depends(get_db)) -> dict:
    return await ParentService(db).create_or_link(student_id, actor_id=current_user.id, reset_password=reset_password)


@router.delete("/students/{student_id}", status_code=204, summary="Stop every parent login from seeing this student")
async def revoke_parent_access(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_ACCESS_MANAGE)),
                               db=Depends(get_db)) -> None:
    await ParentService(db).revoke(student_id, actor_id=current_user.id)