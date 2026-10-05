from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.exceptions import AuthorizationError
from app.employees.repository import EmployeeRepository
from app.profiles.models import ProfilePartyType
from app.profiles.schemas import QualificationCreateRequest, QualificationOut, QualificationUpdateRequest
from app.profiles.service import QualificationService
from app.students.repository import StudentRepository

router = APIRouter(prefix="/profiles/qualifications", tags=["profiles"])


async def _resolve_own_party(db, current_user: CurrentUser) -> tuple[ProfilePartyType, uuid.UUID] | None:
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is not None:
        return ProfilePartyType.STUDENT, student.id
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if employee is not None:
        return ProfilePartyType.EMPLOYEE, employee.id
    return None


def _is_staff_for(party_type: ProfilePartyType, current_user: CurrentUser) -> bool:
    """Mirrors app/students/permissions.py::assert_can_view_student's role
    logic exactly, rather than checking permission-string presence —
    STUDENT_VIEW is held by every STUDENT too (scoped to their own
    record only by convention), so checking for the string alone would
    wrongly let any student view any OTHER student's qualifications.
    Role checks are the actual scoping mechanism here, same as the rest
    of this codebase."""
    if party_type == ProfilePartyType.STUDENT:
        return Roles.ADMIN in current_user.roles or Roles.ACCOUNTS_OFFICER in current_user.roles
    # EMPLOYEE_VIEW/EMPLOYEE_UPDATE are ADMIN-only permissions today —
    # no other role to disambiguate yet, unlike students.
    return Roles.ADMIN in current_user.roles


def _can_view(party_type: ProfilePartyType, own: tuple[ProfilePartyType, uuid.UUID] | None, party_id: uuid.UUID, current_user: CurrentUser) -> bool:
    if own == (party_type, party_id):
        return True
    return _is_staff_for(party_type, current_user)


def _can_edit(party_type: ProfilePartyType, own: tuple[ProfilePartyType, uuid.UUID] | None, party_id: uuid.UUID, current_user: CurrentUser) -> bool:
    if own == (party_type, party_id):
        return True
    return _is_staff_for(party_type, current_user)


@router.get("", response_model=list[QualificationOut], summary="List qualifications for a student/employee")
async def list_qualifications(
    party_type: ProfilePartyType,
    party_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[QualificationOut]:
    own = await _resolve_own_party(db, current_user)
    if not _can_view(party_type, own, party_id, current_user):
        raise AuthorizationError("You do not have access to this record's qualifications.")
    items = await QualificationService(db).list_for_party(party_type, party_id)
    return [QualificationOut.model_validate(q) for q in items]


@router.post("", response_model=QualificationOut, status_code=201, summary="Add a qualification for a student/employee")
async def add_qualification(
    party_type: ProfilePartyType,
    party_id: uuid.UUID,
    payload: QualificationCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> QualificationOut:
    own = await _resolve_own_party(db, current_user)
    if not _can_edit(party_type, own, party_id, current_user):
        raise AuthorizationError("You do not have access to edit this record's qualifications.")
    qualification = await QualificationService(db).add(party_type=party_type, party_id=party_id, data=payload, actor_id=current_user.id)
    return QualificationOut.model_validate(qualification)


@router.patch("/{qualification_id}", response_model=QualificationOut, summary="Edit a qualification")
async def update_qualification(
    qualification_id: uuid.UUID,
    payload: QualificationUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> QualificationOut:
    service = QualificationService(db)
    existing = await service.get(qualification_id)
    own = await _resolve_own_party(db, current_user)
    if not _can_edit(existing.party_type, own, existing.party_id, current_user):
        raise AuthorizationError("You do not have access to edit this record's qualifications.")
    qualification = await service.update(qualification_id, payload, actor_id=current_user.id)
    return QualificationOut.model_validate(qualification)


@router.delete("/{qualification_id}", status_code=204, summary="Remove a qualification")
async def delete_qualification(
    qualification_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> None:
    service = QualificationService(db)
    existing = await service.get(qualification_id)
    own = await _resolve_own_party(db, current_user)
    if not _can_edit(existing.party_type, own, existing.party_id, current_user):
        raise AuthorizationError("You do not have access to edit this record's qualifications.")
    await service.delete(qualification_id, actor_id=current_user.id)