"""SMS log and controls (administration)."""
from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.sms import models as _models  # noqa: F401  (registers the table)
from app.sms.models import SmsStatus
from app.sms.service import SmsService

router = APIRouter(prefix="/sms", tags=["sms"])
manage = require_permission(Permissions.SMS_MANAGE)


class TestIn(BaseModel):
    number: str = Field(min_length=10, max_length=20)
    message: str = Field(default="Test message from the institute portal.", min_length=1, max_length=300)


@router.get("", summary="SMS overview: provider set up?, last 7 days by status, latest messages")
async def overview(status: SmsStatus | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SmsService(db).overview(status=status)


class BulkIn(BaseModel):
    audience: str = Field(pattern="^(STUDENTS|STAFF)$")
    message: str = Field(min_length=5, max_length=300)
    program_id: uuid.UUID | None = None
    semester: int | None = Field(default=None, ge=1, le=12)
    section: str | None = Field(default=None, max_length=20)
    defaulters_only: bool = False
    job_role_id: uuid.UUID | None = None
    dry_run: bool = True


@router.post("/bulk", summary="One message to many students or staff (dry_run=true only counts who would get it)")
async def bulk(payload: BulkIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SmsService(db).bulk(**payload.model_dump(), actor_id=current_user.id)


@router.post("/send-now", summary="Send queued messages now (the sender process also does this every 30 seconds)")
async def send_now(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SmsService(db).send_pending(limit=50, time_budget=12)


@router.post("/retry", summary="Put failed (and not-sent LOGGED) messages back in the queue")
async def retry(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return {"requeued": await SmsService(db).retry_failed()}


@router.post("/test", status_code=201, summary="Queue a test SMS to one number")
async def test(payload: TestIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    m = await SmsService(db).queue_test(payload.number, payload.message)
    return {"id": m.id, "status": m.status.value}


@router.post("/fee-reminders", summary="Queue overdue-fee reminders now (normally run daily by the sender)")
async def fee_reminders(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return {"queued": await SmsService(db).queue_fee_overdue_reminders()}