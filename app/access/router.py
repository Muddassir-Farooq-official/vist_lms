"""Settings -> Access control (Administrator only: role.manage, which is never handed out)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.access.service import AccessService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission

router = APIRouter(prefix="/access", tags=["access control"])
admin = require_permission(Permissions.ROLE_MANAGE)


class RoleIn(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    copy_from: uuid.UUID | None = None
    rights: list[str] = Field(default_factory=list, max_length=200)


class RoleEditIn(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=300)


class RightsIn(BaseModel):
    rights: list[str] = Field(default_factory=list, max_length=200)


class OverrideIn(BaseModel):
    code: str = Field(max_length=64)
    effect: str = Field(pattern="^(GRANT|DENY)$")
    until: date | None = None
    reason: str | None = Field(default=None, max_length=300)


class PersonIn(BaseModel):
    overrides: list[OverrideIn] = Field(default_factory=list, max_length=150)


class PersonRolesIn(BaseModel):
    extra_roles: list[str] = Field(default_factory=list, max_length=20)


class PreviewPersonIn(BaseModel):
    overrides: list[OverrideIn] | None = None
    extra_roles: list[str] | None = None


class DecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


def _ov(items):
    return [{"code": o.code, "effect": o.effect, "until": o.until.isoformat() if o.until else None, "reason": o.reason} for o in items]


@router.get("", summary="Modules, levels, roles and what each can do")
async def overview(current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).overview()


@router.post("/roles", status_code=201, summary="Make a custom role (optionally copying another role's rights)")
async def create_role(body: RoleIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).create_role(name=body.name, description=body.description, copy_from=body.copy_from, rights=body.rights,
                                               actor_id=current_user.id)


@router.patch("/roles/{role_id}", summary="Rename or describe a custom role")
async def edit_role(role_id: uuid.UUID, body: RoleEditIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).update_role(role_id, name=body.name, description=body.description, actor_id=current_user.id)


@router.delete("/roles/{role_id}", summary="Delete a custom role nobody has")
async def delete_role(role_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).delete_role(role_id, actor_id=current_user.id)


@router.put("/roles/{role_id}/rights", summary="Set what a role can do (money/control rights wait for a second administrator)")
async def set_role_rights(role_id: uuid.UUID, body: RightsIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).set_role_rights(role_id, body.rights, actor_id=current_user.id)


@router.post("/roles/{role_id}/preview", summary="What would change for the people who have this role")
async def preview_role(role_id: uuid.UUID, body: RightsIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).preview_role(role_id, body.rights)


@router.get("/people", summary="Staff with a login, their roles, and whether they have rights of their own")
async def people(q: str | None = Query(default=None, max_length=100), current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> list[dict]:
    return await AccessService(db).people(q)


@router.get("/people/{user_id}", summary="One person: roles, every right and where it comes from, own rights, pages")
async def person(user_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).person(user_id)


@router.put("/people/{user_id}/rights", summary="Give or take single rights for one person, optionally until a date")
async def set_person(user_id: uuid.UUID, body: PersonIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).set_person(user_id, overrides=_ov(body.overrides), actor_id=current_user.id)


@router.put("/employees/{employee_id}/roles", summary="A staff member's extra roles, built-in or custom")
async def set_person_roles(employee_id: uuid.UUID, body: PersonRolesIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).set_person_roles(employee_id, body.extra_roles, actor_id=current_user.id)


@router.post("/people/{user_id}/preview", summary="What this person could do after the change (before saving)")
async def preview_person(user_id: uuid.UUID, body: PreviewPersonIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).preview_person(user_id, overrides=_ov(body.overrides) if body.overrides is not None else None,
                                                  extra_roles=body.extra_roles)


@router.get("/history", summary="Every access change, newest first")
async def history(current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> list[dict]:
    return await AccessService(db).history()


@router.post("/history/{change_id}/undo", summary="Put an access change back as it was")
async def undo(change_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).undo(change_id, actor_id=current_user.id)


@router.get("/waiting", summary="Access changes waiting for a second administrator")
async def waiting(current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> list[dict]:
    return await AccessService(db).waiting()


@router.post("/waiting/{request_id}/decide", summary="Confirm or reject another administrator's access change")
async def decide(request_id: uuid.UUID, body: DecideIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).decide(request_id, approve=body.approve, note=body.note, actor_id=current_user.id)


class MembersIn(BaseModel):
    add: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    remove: list[uuid.UUID] = Field(default_factory=list, max_length=200)


@router.get("/who", summary="Who can do this: everyone holding a right, and where it comes from")
async def who(code: str = Query(max_length=64), current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).who_can(code)


@router.get("/roles/{role_id}/members", summary="Staff who have this role")
async def members(role_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> list[dict]:
    return await AccessService(db).role_members(role_id)


@router.post("/roles/{role_id}/members", summary="Give this role to several staff members, or take it from them")
async def set_members(role_id: uuid.UUID, body: MembersIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).set_members(role_id, add=body.add, remove=body.remove, actor_id=current_user.id)


class RuleIn(BaseModel):
    requester_role: str | None = Field(default=None, max_length=32)
    min_amount: float | None = Field(default=None, ge=0, le=1_000_000_000)
    mode: str = Field(pattern="^(NONE|APPROVE)$")
    approver_type: str | None = Field(default=None, pattern="^(DEFAULT|ROLE|USER|HOD|COURSE_TEACHER)$")
    approver_value: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=200)


class RulesIn(BaseModel):
    rules: list[RuleIn] = Field(default_factory=list, max_length=30)


class TryIn(BaseModel):
    user_id: uuid.UUID
    amount: float | None = Field(default=None, ge=0)


@router.get("/approval-rules", summary="Who approves each kind of action, and the rules set")
async def approval_rules(current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).rules_overview()


@router.put("/approval-rules/{action}", summary="Set the rules for one kind of action (rules for money wait for a second administrator)")
async def set_approval_rules(action: str, body: RulesIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).set_rules(action, [r.model_dump() for r in body.rules], actor_id=current_user.id)


@router.post("/approval-rules/{action}/try", summary="Who would decide this person's request (with this amount)?")
async def try_rule(action: str, body: TryIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await AccessService(db).try_rule(action, user_id=body.user_id, amount=body.amount)


@router.get("/my-pages", summary="Pages of the administration's portal this person may open (for the menu)")
async def my_pages(current_user: CurrentUser = Depends(get_current_user)) -> dict:
    from app.access import catalog

    held = set(current_user.extra_permissions or [])
    if "ADMIN" in current_user.roles:
        return {"all": True, "pages": []}
    return {"all": False, "pages": sorted(p for p, rights in catalog.page_rights().items() if held & set(rights))}
