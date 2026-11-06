from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.fees.models import FeeStructureProgram
from app.fees.repository import FeeStructureRepository
from app.fees.schemas import (
    CloneStructureRequest,
    FeeStructureCreateRequest,
    FeeStructureOut,
    FeeTypeCreateRequest,
    FeeTypeOut,
    FeeTypeUpdateRequest,
)
from app.fees.service import FeeStructureService, FeeTypeService

types_router = APIRouter(prefix="/fees/types", tags=["fees"])
structures_router = APIRouter(prefix="/fees/structures", tags=["fees"])


@types_router.get("", response_model=list[FeeTypeOut], summary="List fee types")
async def list_fee_types(
    active_only: bool = False,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_VIEW)),
    db=Depends(get_db),
) -> list[FeeTypeOut]:
    fee_types = await FeeTypeService(db).list_all(active_only=active_only)
    return [FeeTypeOut.model_validate(t) for t in fee_types]


@types_router.post("", response_model=FeeTypeOut, status_code=201, summary="Create a fee type")
async def create_fee_type(
    payload: FeeTypeCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> FeeTypeOut:
    fee_type = await FeeTypeService(db).create(payload, actor_id=current_user.id)
    return FeeTypeOut.model_validate(fee_type)


@types_router.patch("/{fee_type_id}", response_model=FeeTypeOut, summary="Edit a fee type")
async def update_fee_type(
    fee_type_id: uuid.UUID,
    payload: FeeTypeUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> FeeTypeOut:
    fee_type = await FeeTypeService(db).update(fee_type_id, payload, actor_id=current_user.id)
    return FeeTypeOut.model_validate(fee_type)


async def _to_structure_out(db, structure) -> FeeStructureOut:
    from app.fees.models import FeeType
    from app.programs.models import Program

    repo = FeeStructureRepository(db)
    fee_type = await db.get(FeeType, structure.fee_type_id)
    program_links = await repo.get_program_links(structure.id)
    program_names = []
    for link in program_links:
        program = await db.get(Program, link.program_id)
        if program:
            program_names.append(program.name)
    semester_links = await repo.get_semester_links(structure.id)
    semester_numbers = sorted(link.semester_number for link in semester_links)
    assignment_count = await repo.count_assignments(structure.id)

    course_label = None
    if structure.course_id is not None:
        from app.academics.models import Course

        course = await db.get(Course, structure.course_id)
        if course:
            course_label = f"{course.code} \u2014 {course.title}"

    return FeeStructureOut(
        id=structure.id,
        fee_type_id=structure.fee_type_id,
        fee_type_name=fee_type.name if fee_type else "Unknown",
        name=structure.name,
        term_label=structure.term_label,
        total_amount=float(structure.total_amount),
        installment_count=structure.installment_count,
        installment_interval_months=structure.installment_interval_months,
        effective_from=structure.effective_from,
        late_fee_per_day=float(structure.late_fee_per_day) if structure.late_fee_per_day else None,
        applies_to_students=structure.applies_to_students,
        applies_to_employees=structure.applies_to_employees,
        status=structure.status,
        program_names=program_names,
        semester_numbers=semester_numbers,
        course_id=structure.course_id,
        course_label=course_label,
        assignment_count=assignment_count,
    )


@structures_router.get("", response_model=list[FeeStructureOut], summary="List fee structures")
async def list_fee_structures(
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_VIEW)),
    db=Depends(get_db),
) -> list[FeeStructureOut]:
    structures = await FeeStructureService(db).list_all()
    return [await _to_structure_out(db, s) for s in structures]


@structures_router.post("", response_model=FeeStructureOut, status_code=201, summary="Create a fee structure (DRAFT)")
async def create_fee_structure(
    payload: FeeStructureCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> FeeStructureOut:
    structure = await FeeStructureService(db).create(payload, actor_id=current_user.id)
    return await _to_structure_out(db, structure)


@structures_router.get("/{structure_id}", response_model=FeeStructureOut, summary="Get a fee structure")
async def get_fee_structure(
    structure_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_VIEW)),
    db=Depends(get_db),
) -> FeeStructureOut:
    structure = await FeeStructureService(db).get(structure_id)
    return await _to_structure_out(db, structure)


class UndoActivationIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@structures_router.post("/{structure_id}/undo-activation", summary="Undo a mistaken activation (only while nobody has paid on it)")
async def undo_fee_activation(
    structure_id: uuid.UUID,
    payload: UndoActivationIn,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> dict:
    return await FeeStructureService(db).undo_activation(structure_id, reason=payload.reason, actor_id=current_user.id)


@structures_router.post(
    "/{structure_id}/activate",
    summary="Activate a fee structure — auto-charges every currently matching student/employee",
)
async def activate_fee_structure(
    structure_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> dict:
    return await FeeStructureService(db).activate(structure_id, actor_id=current_user.id)


@structures_router.post(
    "/{structure_id}/close",
    response_model=FeeStructureOut,
    summary="Close a fee structure — stops new charges (existing dues remain payable)",
)
async def close_fee_structure(
    structure_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> FeeStructureOut:
    structure = await FeeStructureService(db).close(structure_id, actor_id=current_user.id)
    return await _to_structure_out(db, structure)


@structures_router.post(
    "/{structure_id}/clone",
    response_model=FeeStructureOut,
    status_code=201,
    summary="Clone into a new term with a percentage amount adjustment (e.g. a 10% fee increase)",
)
async def clone_fee_structure(
    structure_id: uuid.UUID,
    payload: CloneStructureRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> FeeStructureOut:
    clone = await FeeStructureService(db).clone(structure_id, payload, actor_id=current_user.id)
    return await _to_structure_out(db, clone)


class DueDateForAll(BaseModel):
    due_date: date
    reason: str = Field(min_length=4, max_length=300)


@structures_router.post(
    "/{structure_id}/installments/{installment_number}/due-date",
    summary="Move one instalment's due date for everyone on this fee (instalments already paid in full are left as they are)",
)
async def change_due_date_for_all(
    structure_id: uuid.UUID,
    installment_number: int,
    payload: DueDateForAll,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> dict:
    from app.fees.assignment_service import FeeAssignmentService

    return await FeeAssignmentService(db).change_due_date_for_structure(structure_id, installment_number, new_date=payload.due_date,
                                                                       reason=payload.reason, actor_id=current_user.id)


class LateFeeRate(BaseModel):
    late_fee_per_day: float = Field(ge=0, le=100_000)          # 0 = no late fee on this fee
    reason: str = Field(min_length=4, max_length=300)


@structures_router.post(
    "/{structure_id}/late-fee",
    summary="Set this fee's daily late fee (Rs. per day after the due date; 0 = none). Late fines already posted stay as they are",
)
async def set_late_fee(
    structure_id: uuid.UUID,
    payload: LateFeeRate,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> dict:
    from app.audit.service import AuditService
    from app.core.exceptions import ResourceNotFoundError

    structure = await FeeStructureRepository(db).get_by_id(structure_id)
    if structure is None:
        raise ResourceNotFoundError("Fee structure not found.")
    before = float(structure.late_fee_per_day) if structure.late_fee_per_day else 0.0
    structure.late_fee_per_day = round(payload.late_fee_per_day, 2) or None
    await AuditService(db).log(actor_user_id=current_user.id, action="fee.late_fee_rate_changed", entity_type="fee_structure", entity_id=structure.id,
                               before={"late_fee_per_day": before}, after={"late_fee_per_day": payload.late_fee_per_day, "reason": payload.reason.strip()})
    await db.flush()
    return {"id": structure.id, "late_fee_per_day": float(structure.late_fee_per_day or 0)}