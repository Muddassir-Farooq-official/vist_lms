"""System -> Server health (administrators): what the server reported in its last minute, and what is wrong."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions, Roles
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_any_role, require_permission
from app.serverhealth.service import CERT_DAYS, DISK_PCT, latest, problems

router = APIRouter(prefix="/server-health", tags=["server-health"])


@router.get("", summary="The server's last health report and its problems (administrators)")
async def health(current_user: CurrentUser = Depends(require_permission(Permissions.SYSTEM_HEALTH)), db=Depends(get_db)) -> dict:
    from app.core.background import backup_status
    from app.core.releases import version_info

    row = await latest(db)
    now = datetime.now(timezone.utc)
    return {
        "now": now, "reported": row is not None, "taken_at": row.taken_at if row else None,
        "age_seconds": round((now - row.taken_at).total_seconds()) if row else None,
        "data": row.data if row else None, "problems": problems(row, now),
        "limits": {"disk_pct": DISK_PCT, "cert_days": CERT_DAYS, "memory_pct": 90, "minutes": 10},
        "this_site": {"version": version_info(), "backup": await backup_status(db)},
    }
