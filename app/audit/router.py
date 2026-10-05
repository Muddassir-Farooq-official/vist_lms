from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends

from app.audit.repository import AuditLogRepository
from app.audit.schemas import AuditLogDetail, AuditLogOut
from app.auth.permissions import Permissions
from app.auth.repository import AuthRepository
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.exceptions import AuthorizationError
from app.core.pagination import Page, page_params, PageParams

router = APIRouter(prefix="/audit", tags=["audit"])

# Action-name prefixes considered "financial" — used to scope
# AUDIT_VIEW_FINANCIAL holders (e.g. ACCOUNTS_OFFICER) to only the
# actions relevant to their role, per the permission's own name. Update
# this list as new financial-flavored actions get added elsewhere.
FINANCIAL_ACTION_PREFIXES = ["fee.", "payment.", "fine.", "salary.", "bonus."]


async def _require_audit_access(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Either the full AUDIT_VIEW or the narrower AUDIT_VIEW_FINANCIAL
    grants access — deliberately not just AUDIT_VIEW_FINANCIAL alone,
    since AUDIT_VIEW is the broader permission and holding it should
    obviously include everything the financial-only view covers. A
    single require_permission() call can't express "either of these
    two", hence this small dedicated dependency."""
    if Permissions.AUDIT_VIEW not in current_user.permissions and Permissions.AUDIT_VIEW_FINANCIAL not in current_user.permissions:
        raise AuthorizationError(f"Missing required permission: {Permissions.AUDIT_VIEW} or {Permissions.AUDIT_VIEW_FINANCIAL}")
    return current_user


@router.get("", response_model=Page[AuditLogDetail], summary="Read the audit trail")
async def list_audit_logs(
    action_prefix: str | None = None,
    entity_type: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: PageParams = Depends(page_params),
    current_user: CurrentUser = Depends(_require_audit_access),
    db=Depends(get_db),
) -> Page[AuditLogDetail]:
    """Holding only AUDIT_VIEW_FINANCIAL (not the full AUDIT_VIEW)
    forces the results to financial-flavored actions only, regardless
    of what action_prefix is requested — the scoping is enforced here,
    not left to the client to self-restrict."""
    action_prefixes = None
    if Permissions.AUDIT_VIEW not in current_user.permissions:
        action_prefixes = FINANCIAL_ACTION_PREFIXES
        action_prefix = None  # the broader prefix list takes over; a narrower single-prefix request would just be redundant

    repo = AuditLogRepository(db)
    items, total = await repo.search(
        page=page,
        action_prefix=action_prefix,
        action_prefixes=action_prefixes,
        entity_type=entity_type,
        actor_user_id=actor_user_id,
        since=since,
        until=until,
    )

    auth_repo = AuthRepository(db)
    detailed = []
    for log in items:
        actor_username = None
        if log.actor_user_id is not None:
            user = await auth_repo.get_user_by_id(log.actor_user_id)
            actor_username = user.username if user else None
        base = AuditLogOut.model_validate(log).model_dump()
        detailed.append(AuditLogDetail(**base, actor_username=actor_username))

    return Page[AuditLogDetail](items=detailed, total=total, page=page.page, page_size=page.page_size)