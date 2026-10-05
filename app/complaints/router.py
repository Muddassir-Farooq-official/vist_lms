from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.auth.permissions import Permissions
from app.auth.repository import AuthRepository
from app.auth.schemas import CurrentUser
from app.complaints.models import Complaint, ComplaintPartyType, ComplaintReason, ComplaintStatus
from app.complaints.repository import ComplaintRepository
from app.complaints.schemas import (
    ComplaintAnalyticsEntry,
    ComplaintCreateRequest,
    ComplaintDetail,
    ComplaintIdentityOut,
    ComplaintOverviewOut,
    ComplaintReasonOption,
    ComplaintSelfActionRequest,
    ComplaintStatusUpdateRequest,
    ComplaintTargetSearchResult,
    PublicComplaintStats,
)
from app.complaints.service import REASON_LABELS, ComplaintService, resolve_party
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.core.exceptions import AuthorizationError
from app.core.pagination import PageParams
from app.employees.models import JobRole
from app.employees.repository import EmployeeRepository
from app.students.repository import StudentRepository

router = APIRouter(prefix="/complaints", tags=["complaints"])


async def _require_submit_or_manage(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """/reasons is needed by both the submit flow (STUDENT/TEACHER) and
    the admin complaints page's own reason filter dropdown — neither
    permission alone should be required to just read this static list."""
    if Permissions.COMPLAINT_SUBMIT not in current_user.permissions and Permissions.COMPLAINT_MANAGE not in current_user.permissions:
        raise AuthorizationError(f"Missing required permission: {Permissions.COMPLAINT_SUBMIT} or {Permissions.COMPLAINT_MANAGE}")
    return current_user


async def _resolve_own_party(db, current_user: CurrentUser) -> tuple[ComplaintPartyType, uuid.UUID] | None:
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is not None:
        return ComplaintPartyType.STUDENT, student.id
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if employee is not None:
        return ComplaintPartyType.EMPLOYEE, employee.id
    return None


async def _require_own_party(db, current_user: CurrentUser) -> tuple[ComplaintPartyType, uuid.UUID]:
    own = await _resolve_own_party(db, current_user)
    if own is None:
        raise AuthorizationError("This account is not linked to a student or employee record.")
    return own


async def _to_detail(db, complaint: Complaint, *, owner_view: bool = False) -> ComplaintDetail:
    """owner_view=True: the person who filed it, looking at their own complaint (they always see themselves).
    Everyone else sees an anonymous complaint's filer as "Anonymous" -- including administrators, who can
    reveal the name deliberately through the reveal-identity action (which is logged)."""
    hide = complaint.is_anonymous and not owner_view
    complainant = await resolve_party(db, complaint.complainant_type, complaint.complainant_id)
    target = await resolve_party(db, complaint.target_type, complaint.target_id)
    resolved_by_name = None
    if complaint.resolved_by is not None:
        user = await AuthRepository(db).get_user_by_id(complaint.resolved_by)
        resolved_by_name = user.username if user else None

    return ComplaintDetail(
        id=complaint.id,
        complainant_type=None if hide else complaint.complainant_type,
        complainant_id=None if hide else complaint.complainant_id,
        is_anonymous=complaint.is_anonymous,
        target_type=complaint.target_type,
        target_id=complaint.target_id,
        reason=complaint.reason,
        custom_reason=complaint.custom_reason,
        description=complaint.description,
        status=complaint.status,
        resolution_note=complaint.resolution_note,
        under_review_at=complaint.under_review_at,
        resolved_at=complaint.resolved_at,
        created_at=complaint.created_at,
        complainant_name="Anonymous" if hide else complainant.name,
        complainant_identifier=None if hide else complainant.identifier,
        target_name=target.name,
        target_identifier=target.identifier,
        target_role_label=target.role_label,
        resolved_by_name=resolved_by_name,
    )


@router.get("/reasons", response_model=list[ComplaintReasonOption], summary="Valid complaint reasons for the dropdown")
async def list_reasons(
    current_user: CurrentUser = Depends(_require_submit_or_manage),
) -> list[ComplaintReasonOption]:
    return [ComplaintReasonOption(value=r.value, label=REASON_LABELS[r]) for r in ComplaintReason]


@router.get(
    "/search-targets",
    response_model=list[ComplaintTargetSearchResult],
    summary="Search students/employees to file a complaint against — self-contained, doesn't need STUDENT_VIEW/EMPLOYEE_VIEW",
)
async def search_targets(
    target_type: ComplaintPartyType,
    q: str = Query(min_length=1, max_length=100),
    role_code: str | None = Query(default=None, description="Optional job-role filter, e.g. TEACHER, for narrowing employee results to 'Faculty' vs everyone else"),
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_SUBMIT)),
    db=Depends(get_db),
) -> list[ComplaintTargetSearchResult]:
    page = PageParams(page=1, page_size=10)
    if target_type == ComplaintPartyType.STUDENT:
        students, _ = await StudentRepository(db).search(page=page, query=q)
        return [
            ComplaintTargetSearchResult(id=s.id, name=f"{s.first_name} {s.last_name}", identifier=s.roll_number, role_label="Student")
            for s in students
        ]

    employees, _ = await EmployeeRepository(db).search(page=page, query=q, status=None, job_role_id=None)
    results = []
    for e in employees:
        job_role = await db.get(JobRole, e.job_role_id)
        role_label = job_role.title if job_role else "Staff"
        if role_code and (job_role is None or job_role.default_role_code != role_code):
            continue
        results.append(
            ComplaintTargetSearchResult(id=e.id, name=f"{e.first_name} {e.last_name}", identifier=e.employee_code, role_label=role_label)
        )
    return results


@router.post("", response_model=ComplaintDetail, status_code=201, summary="File a complaint")
async def submit_complaint(
    payload: ComplaintCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_SUBMIT)),
    db=Depends(get_db),
) -> ComplaintDetail:
    complainant_type, complainant_id = await _require_own_party(db, current_user)
    complaint = await ComplaintService(db).submit(
        complainant_type=complainant_type,
        complainant_id=complainant_id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        reason=payload.reason,
        custom_reason=payload.custom_reason,
        description=payload.description,
        actor_id=current_user.id,
        is_anonymous=payload.is_anonymous,
    )
    return await _to_detail(db, complaint, owner_view=True)


@router.get("/me", response_model=list[ComplaintDetail], summary="My own filed complaints")
async def my_complaints(
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_SUBMIT)),
    db=Depends(get_db),
) -> list[ComplaintDetail]:
    complainant_type, complainant_id = await _require_own_party(db, current_user)
    complaints = await ComplaintService(db).list_own(complainant_type, complainant_id)
    return [await _to_detail(db, c, owner_view=True) for c in complaints]


@router.post("/{complaint_id}/withdraw", response_model=ComplaintDetail, summary="Withdraw my own complaint")
async def withdraw_complaint(
    complaint_id: uuid.UUID,
    payload: ComplaintSelfActionRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_SUBMIT)),
    db=Depends(get_db),
) -> ComplaintDetail:
    complainant_type, complainant_id = await _require_own_party(db, current_user)
    complaint = await ComplaintService(db).self_withdraw(
        complaint_id, complainant_type=complainant_type, complainant_id=complainant_id,
        actor_id=current_user.id, note=payload.note,
    )
    return await _to_detail(db, complaint, owner_view=True)


@router.post("/{complaint_id}/self-resolve", response_model=ComplaintDetail, summary="Mark my own complaint resolved")
async def self_resolve_complaint(
    complaint_id: uuid.UUID,
    payload: ComplaintSelfActionRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_SUBMIT)),
    db=Depends(get_db),
) -> ComplaintDetail:
    complainant_type, complainant_id = await _require_own_party(db, current_user)
    complaint = await ComplaintService(db).self_resolve(
        complaint_id, complainant_type=complainant_type, complainant_id=complainant_id,
        actor_id=current_user.id, note=payload.note,
    )
    return await _to_detail(db, complaint, owner_view=True)


@router.get("", response_model=list[ComplaintDetail], summary="List all complaints (admin)")
async def list_all_complaints(
    status: ComplaintStatus | None = None,
    target_type: ComplaintPartyType | None = None,
    target_id: uuid.UUID | None = None,
    reason: ComplaintReason | None = None,
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_MANAGE)),
    db=Depends(get_db),
) -> list[ComplaintDetail]:
    complaints = await ComplaintService(db).list_all(status=status, target_type=target_type, target_id=target_id, reason=reason)
    return [await _to_detail(db, c) for c in complaints]


@router.get("/analytics", response_model=list[ComplaintAnalyticsEntry], summary="Per-person complaint counts (admin) — the data behind the graph")
async def complaint_analytics(
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_MANAGE)),
    db=Depends(get_db),
) -> list[ComplaintAnalyticsEntry]:
    entries = await ComplaintService(db).analytics()
    return [ComplaintAnalyticsEntry(**e) for e in entries]


@router.get(
    "/stats", response_model=PublicComplaintStats,
    summary="How complaints are handled across the institute -- aggregate numbers only, visible to every student and staff member",
)
async def complaint_stats(
    current_user: CurrentUser = Depends(_require_submit_or_manage),
    db=Depends(get_db),
) -> PublicComplaintStats:
    return PublicComplaintStats(**await ComplaintService(db).public_stats())


@router.get(
    "/analytics/overview", response_model=ComplaintOverviewOut,
    summary="Everything the admin charts need: totals, status and reason breakdowns, weekly trend (admin)",
)
async def complaint_overview(
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_MANAGE)),
    db=Depends(get_db),
) -> ComplaintOverviewOut:
    return ComplaintOverviewOut(**await ComplaintService(db).overview())


@router.post(
    "/{complaint_id}/reveal-identity", response_model=ComplaintIdentityOut,
    summary="Admin: see who filed an anonymous complaint (recorded in the audit log)",
)
async def reveal_complainant(
    complaint_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_MANAGE)),
    db=Depends(get_db),
) -> ComplaintIdentityOut:
    return ComplaintIdentityOut(**await ComplaintService(db).reveal_identity(complaint_id, actor_id=current_user.id))


@router.get("/{complaint_id}", response_model=ComplaintDetail, summary="Get one complaint (admin)")
async def get_complaint(
    complaint_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_MANAGE)),
    db=Depends(get_db),
) -> ComplaintDetail:
    complaint = await ComplaintService(db).get(complaint_id)
    return await _to_detail(db, complaint)


@router.post("/{complaint_id}/status", response_model=ComplaintDetail, summary="Update complaint status (admin)")
async def update_complaint_status(
    complaint_id: uuid.UUID,
    payload: ComplaintStatusUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COMPLAINT_MANAGE)),
    db=Depends(get_db),
) -> ComplaintDetail:
    complaint = await ComplaintService(db).admin_update_status(
        complaint_id, new_status=payload.status, actor_id=current_user.id, note=payload.note
    )
    return await _to_detail(db, complaint)