from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.core.exceptions import AuthorizationError
from app.tasks.models import TaskStatus
from app.tasks.schemas import (
    CancelIn,
    TaskBulkCreate,
    SendBackIn,
    CommentIn,
    CompleteIn,
    FlagIn,
    PersonReport,
    RespondIn,
    ResolveIn,
    Response,
    StaffOut,
    StaffReport,
    SummaryOut,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)
from app.tasks.service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])
use = require_permission(Permissions.TASK_USE)
manage = require_permission(Permissions.TASK_MANAGE)
Days = Query(30, ge=0, le=3650, description="Look back this many days (30, 60, 90). 0 = all time.")


def _manage(user: CurrentUser) -> bool:
    return Permissions.TASK_MANAGE in user.permissions


async def _one(service: TaskService, task_id: uuid.UUID, user: CurrentUser) -> TaskOut:
    return TaskOut(**await service.detail(task_id, user_id=user.id, manage=_manage(user)))


# ============================================================ people, summary
@router.get("/staff", response_model=list[StaffOut], summary="Staff I can assign a task to")
async def staff(q: str | None = None, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> list[StaffOut]:
    return [StaffOut(**p) for p in await TaskService(db).staff_directory(exclude=current_user.id, q=q)]


@router.get("/summary", response_model=SummaryOut, summary="What needs my attention (for the menu badge and Home)")
async def summary(current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> SummaryOut:
    return SummaryOut(**await TaskService(db).summary(current_user.id, _manage(current_user)))


# ============================================================ my tasks
@router.get("/inbox", response_model=list[TaskOut], summary="Tasks assigned to me")
async def inbox(current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> list[TaskOut]:
    return [TaskOut(**r) for r in await TaskService(db).inbox(current_user.id, _manage(current_user))]


@router.get("/sent", response_model=list[TaskOut], summary="Tasks I have assigned to others")
async def sent(current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> list[TaskOut]:
    return [TaskOut(**r) for r in await TaskService(db).sent(current_user.id, _manage(current_user))]


@router.post("", response_model=TaskOut, status_code=201, summary="Assign a task to another member of staff (the deadline must be at least 3 hours away)")
async def create_task(payload: TaskCreate, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    task = await service.create(assigner=current_user.id, data=payload.model_dump())
    return await _one(service, task.id, current_user)


@router.post("/bulk", status_code=201, summary="Assign the same task to several people at once (each gets their own copy)")
async def create_tasks_bulk(payload: TaskBulkCreate, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> dict:
    return await TaskService(db).create_bulk(assigner=current_user.id, data=payload.model_dump())


# ============================================================ the administration
@router.get("", response_model=list[TaskOut], summary="Every task, with filters (administration)")
async def all_tasks(
    status: TaskStatus | None = None, assignee: uuid.UUID | None = None, assigner: uuid.UUID | None = None, flagged: bool = False, overdue: bool = False,
    q: str | None = None, days: int = Days, limit: int = 50, offset: int = 0, current_user: CurrentUser = Depends(manage), db=Depends(get_db),
) -> list[TaskOut]:
    rows = await TaskService(db).list_all(user_id=current_user.id, status=status, assignee=assignee, assigner=assigner, flagged=flagged, overdue=overdue, q=q, days=days, limit=limit, offset=offset)
    return [TaskOut(**r) for r in rows]


@router.get("/reports/staff", response_model=StaffReport, summary="Everyone's progress over the last 30 / 60 / 90 days or all time (administration)")
async def staff_report(days: int = Days, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> StaffReport:
    return StaffReport(**await TaskService(db).staff_report(days))


@router.get("/reports/me", response_model=PersonReport, summary="My own progress over the last 30 / 60 / 90 days or all time")
async def my_report(days: int = Days, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> PersonReport:
    return PersonReport(**await TaskService(db).person_report(current_user.id, days, viewer=current_user.id, manage=_manage(current_user)))


@router.get("/reports/person/{user_id}", response_model=PersonReport, summary="One person's progress in detail (administration, or the person themselves)")
async def person_report(user_id: uuid.UUID, days: int = Days, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> PersonReport:
    if user_id != current_user.id and not _manage(current_user):
        raise AuthorizationError("You can only see your own progress.")
    return PersonReport(**await TaskService(db).person_report(user_id, days, viewer=current_user.id, manage=_manage(current_user)))


# ============================================================ one task
@router.get("/map", summary="Who gave tasks to whom, by status: everyone's (administration) or only mine")
async def task_map(days: int = Days, mine: bool = False, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> dict:
    only = None if (_manage(current_user) and not mine) else current_user.id
    return await TaskService(db).task_map(days, only_user=only)


@router.get("/{task_id}", response_model=TaskOut, summary="One task in full (only for the two people involved, and the administration)")
async def get_task(task_id: uuid.UUID, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    return await _one(TaskService(db), task_id, current_user)


@router.patch("/{task_id}", response_model=TaskOut, summary="Change a task I assigned (everything before it is accepted; only the deadline afterwards)")
async def update_task(task_id: uuid.UUID, payload: TaskUpdate, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.update(task_id, user_id=current_user.id, changes=payload.model_dump(exclude_unset=True))
    return await _one(service, task_id, current_user)


@router.post("/{task_id}/respond", response_model=TaskOut, summary="Accept the task, or reject it with a reason and an explanation")
async def respond(task_id: uuid.UUID, payload: RespondIn, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.respond(task_id, user_id=current_user.id, accept=payload.action == Response.ACCEPT, reason=payload.reason, explanation=payload.explanation)
    return await _one(service, task_id, current_user)


@router.post("/{task_id}/complete", response_model=TaskOut, summary="Mark an accepted task as done")
async def complete(task_id: uuid.UUID, payload: CompleteIn, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.complete(task_id, user_id=current_user.id, note=payload.note)
    return await _one(service, task_id, current_user)


@router.post("/{task_id}/confirm", response_model=TaskOut, summary="Confirm a completed task is really done (the person who assigned it, or the administration)")
async def confirm(task_id: uuid.UUID, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.confirm(task_id, user_id=current_user.id, manage=_manage(current_user))
    return await _one(service, task_id, current_user)


@router.post("/{task_id}/send-back", response_model=TaskOut, summary="Not done yet: send a completed task back with a reason (and optionally more time)")
async def send_back(task_id: uuid.UUID, payload: SendBackIn, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.send_back(task_id, user_id=current_user.id, manage=_manage(current_user), note=payload.note, new_deadline=payload.new_deadline)
    return await _one(service, task_id, current_user)


@router.post("/{task_id}/cancel", response_model=TaskOut, summary="Call a task off (the person who assigned it, or the administration)")
async def cancel(task_id: uuid.UUID, payload: CancelIn, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.cancel(task_id, user_id=current_user.id, manage=_manage(current_user), reason=payload.reason)
    return await _one(service, task_id, current_user)


@router.post("/{task_id}/comments", response_model=TaskOut, summary="Add a message to the task")
async def add_comment(task_id: uuid.UUID, payload: CommentIn, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.comment(task_id, user_id=current_user.id, manage=_manage(current_user), body=payload.body)
    return await _one(service, task_id, current_user)


@router.post("/{task_id}/flag", response_model=TaskOut, summary="Flag the task so the administration reviews it (either of the two people involved)")
async def flag(task_id: uuid.UUID, payload: FlagIn, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.flag(task_id, user_id=current_user.id, reason=payload.reason)
    return await _one(service, task_id, current_user)


@router.post("/{task_id}/resolve", response_model=TaskOut, summary="Close a flag with a decision and a note (administration)")
async def resolve(task_id: uuid.UUID, payload: ResolveIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> TaskOut:
    service = TaskService(db)
    await service.resolve_flag(task_id, admin_id=current_user.id, outcome=payload.outcome, note=payload.note, new_deadline=payload.new_deadline)
    return await _one(service, task_id, current_user)