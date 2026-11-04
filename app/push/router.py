"""Turning push on for this device, off again, and a test message."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.schemas import CurrentUser
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.push import service as push

router = APIRouter(prefix="/push", tags=["push"])


class Keys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: Keys


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=2000)


@router.get("/key", summary="Whether push is set up here, the public key browsers need, and how many devices I have")
async def push_key(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return {"enabled": push.enabled(), "public_key": settings.vapid_public_key or None,
            "devices": await push.PushService(db).count(current_user.id) if push.enabled() else 0}


@router.post("/subscribe", summary="Turn on push notifications on this phone or browser")
async def subscribe(payload: SubscriptionIn, request: Request, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await push.PushService(db).subscribe(current_user.id, endpoint=payload.endpoint, p256dh=payload.keys.p256dh, auth=payload.keys.auth,
                                                user_agent=request.headers.get("user-agent"))


@router.post("/unsubscribe", summary="Turn push notifications off on this phone or browser")
async def unsubscribe(payload: EndpointIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await push.PushService(db).unsubscribe(current_user.id, payload.endpoint)


@router.post("/test", summary="Send a test notification to all my devices now")
async def test_push(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    sent = await push.PushService(db).send_to([current_user.id], {
        "title": f"{settings.app_name}: notifications are on", "body": "This is how messages from the portal will reach you.", "url": "/", "tag": "test"})
    return {"sent": sent}