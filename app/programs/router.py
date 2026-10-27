from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.programs.schemas import ProgramCreate, ProgramOut, ProgramUpdate
from app.programs.service import ProgramService

router = APIRouter(prefix="/programs", tags=["programs"])


@router.get("", response_model=list[ProgramOut], summary="List programs")
async def list_programs(
    active_only: bool = False,
    current_user: CurrentUser = Depends(require_permission(Permissions.PROGRAM_VIEW)),
    db=Depends(get_db),
) -> list[ProgramOut]:
    programs = await ProgramService(db).list_programs(active_only=active_only)
    return [ProgramOut.model_validate(p) for p in programs]


@router.get("/overview", summary="Each program with its numbers: students by status, courses and credit hours in its curriculum")
async def programs_overview(current_user: CurrentUser = Depends(require_permission(Permissions.PROGRAM_VIEW)), db=Depends(get_db)) -> list[dict]:
    from sqlalchemy import func, select

    from app.academics.models import Course, CourseProgram
    from app.students.models import Student

    programs = await ProgramService(db).list_programs(active_only=False)
    students: dict = {}
    for pid, status, n in (await db.execute(select(Student.program_id, Student.status, func.count()).where(Student.program_id.is_not(None))
                                            .group_by(Student.program_id, Student.status))).all():
        students.setdefault(pid, {})[status.value if hasattr(status, "value") else str(status)] = int(n)
    curriculum: dict = {}
    for pid, n, ch, sems in (await db.execute(select(CourseProgram.program_id, func.count(), func.coalesce(func.sum(Course.credit_hours), 0),
                                                     func.count(func.distinct(CourseProgram.semester_number)))
                                              .join(Course, Course.id == CourseProgram.course_id).group_by(CourseProgram.program_id))).all():
        curriculum[pid] = {"courses": int(n), "credit_hours": int(ch), "semesters_planned": int(sems)}
    return [{**ProgramOut.model_validate(p).model_dump(), "students": students.get(p.id, {}),
             "curriculum": curriculum.get(p.id, {"courses": 0, "credit_hours": 0, "semesters_planned": 0})} for p in programs]


@router.post("", response_model=ProgramOut, status_code=201, summary="Create a program")
async def create_program(
    payload: ProgramCreate,
    current_user: CurrentUser = Depends(require_permission(Permissions.PROGRAM_CREATE)),
    db=Depends(get_db),
) -> ProgramOut:
    program = await ProgramService(db).create_program(payload, actor_id=current_user.id)
    return ProgramOut.model_validate(program)


@router.get("/{program_id}", response_model=ProgramOut, summary="Get a program")
async def get_program(
    program_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.PROGRAM_VIEW)),
    db=Depends(get_db),
) -> ProgramOut:
    program = await ProgramService(db).get_program(program_id)
    return ProgramOut.model_validate(program)


@router.patch("/{program_id}", response_model=ProgramOut, summary="Update a program")
async def update_program(
    program_id: uuid.UUID,
    payload: ProgramUpdate,
    current_user: CurrentUser = Depends(require_permission(Permissions.PROGRAM_UPDATE)),
    db=Depends(get_db),
) -> ProgramOut:
    program = await ProgramService(db).update_program(program_id, payload, actor_id=current_user.id)
    return ProgramOut.model_validate(program)