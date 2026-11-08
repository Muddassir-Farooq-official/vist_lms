"""Parent portal API (read-only, own children only) and the administration's parent-access actions."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.parents import models as _models  # noqa: F401  (registers the table)
from app.parents.service import ParentService

router = APIRouter(prefix="/parents", tags=["parents"])


class GrantIn(BaseModel):
    sibling_ids: list[uuid.UUID] = Field(default_factory=list, max_length=12)     # brothers / sisters (same guardian) to give the same login


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
async def grant_parent_access(student_id: uuid.UUID, payload: GrantIn | None = None, reset_password: bool = False,
                              current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_ACCESS_MANAGE)), db=Depends(get_db)) -> dict:
    return await ParentService(db).create_or_link(student_id, actor_id=current_user.id, reset_password=reset_password,
                                                  sibling_ids=payload.sibling_ids if payload else None)


@router.delete("/students/{student_id}", status_code=204, summary="Stop every parent login from seeing this student")
async def revoke_parent_access(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_ACCESS_MANAGE)),
                               db=Depends(get_db)) -> None:
    await ParentService(db).revoke(student_id, actor_id=current_user.id)

@router.get("/me/children/{student_id}/fees", summary="One child's fee account: challans to download, payments with receipts, what is due")
async def child_fees(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)), db=Depends(get_db)) -> dict:
    return await ParentService(db).fees(current_user.id, student_id)


@router.get("/me/children/{student_id}/timetable", summary="One child's classes for the next two weeks and published datesheets")
async def child_timetable(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)), db=Depends(get_db)) -> dict:
    return await ParentService(db).timetable(current_user.id, student_id)


@router.get("/me/children/{student_id}/marks", summary="One child's marks entered so far in each current subject")
async def child_marks(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)), db=Depends(get_db)) -> list[dict]:
    return await ParentService(db).marks(current_user.id, student_id)


@router.get("/me/children/{student_id}/announcements", summary="What the institute announced to the child's students")
async def child_announcements(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)),
                              db=Depends(get_db)) -> list[dict]:
    return await ParentService(db).announcements(current_user.id, student_id)
