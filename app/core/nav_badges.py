"""Counts of waiting work shown beside menu links (templates/_nav.html `data-badge`, static/js/admin-common.js).

Only counts the person can act on are returned: each needs the same permission as the page that decides them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/nav", tags=["navigation"])


async def _count(db, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one() or 0)


@router.get("/badges", summary="Waiting work for the menu: approvals, profile changes, certificate requests, admissions, result sheets")
async def badges(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    perms = set(current_user.permissions)
    out: dict[str, int] = {}
    if Permissions.APPROVALS_DECIDE in perms:
        from app.approvals.models import ApprovalRequest

        out["approvals"] = await _count(db, select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == "REQUESTED"))
    if Permissions.PROFILE_CHANGE_DECIDE in perms:
        from app.selfservice.models import ProfileChangeRequest

        out["profile_changes"] = await _count(db, select(func.count()).select_from(ProfileChangeRequest).where(ProfileChangeRequest.status == "PENDING"))
    if Permissions.RECORDS_ISSUE in perms:
        from app.records.models import CertificateRequest

        out["certificates"] = await _count(db, select(func.count()).select_from(CertificateRequest).where(CertificateRequest.status == "REQUESTED"))
    if Permissions.ADMISSION_MANAGE in perms:
        from app.admissions.models import AdmissionRound, Applicant, ApplicantStatus, RoundStatus

        out["admissions"] = await _count(db, select(func.count()).select_from(Applicant).join(AdmissionRound, AdmissionRound.id == Applicant.round_id)
                                         .where(Applicant.status == ApplicantStatus.SUBMITTED, AdmissionRound.status == RoundStatus.OPEN))
    if Permissions.RESULT_APPROVE in perms:
        from app.academics.models import ResultSheet, ResultSheetStatus

        out["result_sheets"] = await _count(db, select(func.count()).select_from(ResultSheet).where(ResultSheet.status == ResultSheetStatus.SUBMITTED))
    return out
