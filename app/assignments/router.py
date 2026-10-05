from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.assignments.schemas import (
    AssignmentCreate,
    AssignmentStudentOut,
    AssignmentTeacherOut,
    AssignmentUpdate,
    ReviewRequest,
    RosterRow,
    SubmissionOut,
    SubmitRequest,
)
from app.assignments.service import AssignmentService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/assignments", tags=["assignments"])
teacher = require_permission(Permissions.ASSIGNMENT_MANAGE)
student = require_permission(Permissions.ASSIGNMENT_SUBMIT)


async def _teacher_view(service: AssignmentService, assignment_id: uuid.UUID, user_id: uuid.UUID) -> AssignmentTeacherOut:
    rows = await service.teaching_list(user_id=user_id)
    return AssignmentTeacherOut(**next(r for r in rows if r["id"] == assignment_id))


# ============================================================ teacher
@router.get("/teaching", response_model=list[AssignmentTeacherOut], summary="Assignments I have set, with how many students have handed in (teacher)")
async def teaching(offering_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(teacher), db=Depends(get_db)) -> list[AssignmentTeacherOut]:
    return [AssignmentTeacherOut(**r) for r in await AssignmentService(db).teaching_list(user_id=current_user.id, offering_id=offering_id)]


@router.post("", response_model=AssignmentTeacherOut, status_code=201, summary="Set an assignment for a class I teach (the students are notified)")
async def create_assignment(payload: AssignmentCreate, current_user: CurrentUser = Depends(teacher), db=Depends(get_db)) -> AssignmentTeacherOut:
    service = AssignmentService(db)
    a = await service.create(user_id=current_user.id, offering_id=payload.course_offering_id, title=payload.title, instructions=payload.instructions, deadline=payload.deadline)
    return await _teacher_view(service, a.id, current_user.id)


@router.post("/submissions/{submission_id}/review", response_model=SubmissionOut, summary="Accept or reject a student's submission (a rejection needs a reason)")
async def review_submission(submission_id: uuid.UUID, payload: ReviewRequest, current_user: CurrentUser = Depends(teacher), db=Depends(get_db)) -> SubmissionOut:
    sub = await AssignmentService(db).review(submission_id, user_id=current_user.id, decision=payload.decision.value, feedback=payload.feedback)
    return SubmissionOut(id=sub.id, drive_url=sub.drive_url, note=sub.note, status=sub.status, submitted_at=sub.submitted_at, resubmitted_at=sub.resubmitted_at,
                         resubmission_count=sub.resubmission_count, feedback=sub.feedback, reviewed_at=sub.reviewed_at)


# ============================================================ student
@router.get("/mine", response_model=list[AssignmentStudentOut], summary="Assignments for the classes I am in, with my own submission (student)")
async def my_assignments(current_user: CurrentUser = Depends(student), db=Depends(get_db)) -> list[AssignmentStudentOut]:
    return [AssignmentStudentOut(**r) for r in await AssignmentService(db).student_list(user_id=current_user.id)]


@router.post("/{assignment_id}/submit", response_model=AssignmentStudentOut, summary="Hand in (or replace) my Google Drive link, until the deadline (student)")
async def submit(assignment_id: uuid.UUID, payload: SubmitRequest, current_user: CurrentUser = Depends(student), db=Depends(get_db)) -> AssignmentStudentOut:
    return AssignmentStudentOut(**await AssignmentService(db).submit(
        assignment_id, user_id=current_user.id, drive_url=payload.drive_url, note=payload.note, acknowledge_private=payload.acknowledge_private))


# ============================================================ teacher, by assignment
@router.get("/{assignment_id}/submissions", response_model=list[RosterRow], summary="Everyone in the class and what they have handed in (teacher)")
async def submissions(assignment_id: uuid.UUID, current_user: CurrentUser = Depends(teacher), db=Depends(get_db)) -> list[RosterRow]:
    return [RosterRow(**r) for r in await AssignmentService(db).roster(assignment_id, user_id=current_user.id)]


@router.patch("/{assignment_id}", response_model=AssignmentTeacherOut, summary="Change the title, instructions or deadline (a later deadline reopens it) (teacher)")
async def update_assignment(assignment_id: uuid.UUID, payload: AssignmentUpdate, current_user: CurrentUser = Depends(teacher), db=Depends(get_db)) -> AssignmentTeacherOut:
    service = AssignmentService(db)
    await service.update(assignment_id, user_id=current_user.id, changes=payload.model_dump(exclude_unset=True))
    return await _teacher_view(service, assignment_id, current_user.id)


@router.post("/{assignment_id}/close", response_model=AssignmentTeacherOut, summary="Close submissions right now (teacher)")
async def close_assignment(assignment_id: uuid.UUID, current_user: CurrentUser = Depends(teacher), db=Depends(get_db)) -> AssignmentTeacherOut:
    service = AssignmentService(db)
    await service.close_now(assignment_id, user_id=current_user.id)
    return await _teacher_view(service, assignment_id, current_user.id)


@router.delete("/{assignment_id}", status_code=204, summary="Delete an assignment nobody has submitted to yet (teacher)")
async def delete_assignment(assignment_id: uuid.UUID, current_user: CurrentUser = Depends(teacher), db=Depends(get_db)) -> Response:
    await AssignmentService(db).delete(assignment_id, user_id=current_user.id)
    return Response(status_code=204)