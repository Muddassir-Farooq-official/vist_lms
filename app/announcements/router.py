from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.announcements.models import CATEGORY_LABELS, Announcement, AnnouncementCategory, AnnouncementPriority, AnnouncementStatus
from app.announcements.schemas import (
    AnnouncementAdminOut,
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementUpdate,
    AttachmentOut,
    AuthorityIn,
    AuthorityOut,
    AuthorityUpdate,
    UnreadCountOut,
)
from app.announcements.service import AnnouncementService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.core.storage import (
    MAX_ATTACHMENT_BYTES,
    SAFE_SERVE_HEADERS,
    FileTooLargeError,
    UnsupportedFileTypeError,
    attachment_is_inline,
    get_file_path,
)

router = APIRouter(prefix="/announcements", tags=["announcements"])
manage = require_permission(Permissions.ANNOUNCEMENT_MANAGE)


def _attachment_out(att) -> AttachmentOut:
    return AttachmentOut(
        id=att.id, original_name=att.original_name, content_type=att.content_type, size_bytes=att.size_bytes,
        is_image=att.content_type.startswith("image/"), inline=attachment_is_inline(att.content_type),
    )


async def _build(service: AnnouncementService, items: list[Announcement], *, user_id: uuid.UUID, admin: bool) -> list[AnnouncementAdminOut]:
    atts: dict[uuid.UUID, list] = {}
    for att in await service.attachments_of([a.id for a in items]):
        atts.setdefault(att.announcement_id, []).append(att)
    seen = await service.read_ids(user_id)
    reach = await service.reach(items) if admin else {}
    names = await service.program_names([uuid.UUID(p) for a in items for p in (a.program_ids or [])]) if admin else {}
    from app.announcements.submissions import audience_text

    texts = await audience_text(service.session, items)
    writers = {}
    sub_ids = {a.submitted_by for a in items if a.submitted_by}
    if admin and sub_ids:
        from app.auth.models import User

        writers = {u.id: u.username for u in (await service.session.execute(select(User).where(User.id.in_(sub_ids)))).scalars()}
    out = []
    for a in items:
        recipients, reads = reach.get(a.id, (None, None))
        out.append(AnnouncementAdminOut(
            id=a.id, title=a.title, body=a.body, category=a.category, category_label=CATEGORY_LABELS[a.category], priority=a.priority,
            issuer_name=a.issuer_name, issuer_designation=a.issuer_designation, pinned=a.pinned, status=a.status,
            published_at=a.published_at, expires_at=a.expires_at, updated_at=a.updated_at, is_read=a.id in seen,
            attachments=[_attachment_out(x) for x in atts.get(a.id, [])],
            audience_students=a.audience_students, audience_teachers=a.audience_teachers, audience_staff=a.audience_staff,
            program_ids=[uuid.UUID(p) for p in a.program_ids] if a.program_ids else None,
            recipients=recipients, read_count=reads, program_names=[names.get(uuid.UUID(p), "?") for p in (a.program_ids or [])] if admin else [],
            semesters=a.semesters, audience_text=texts.get(a.id, ""),
            offering_ids=[uuid.UUID(o) for o in a.offering_ids] if a.offering_ids else None,
            review=a.review, review_note=a.review_note, submitted_at=a.submitted_at,
            submitted_by_name=(f"{a.issuer_name} ({writers.get(a.submitted_by, '')})" if a.submitted_by and admin else None),
        ))
    return out


async def _can_see(service: AnnouncementService, a: Announcement, user: CurrentUser) -> bool:
    if Permissions.ANNOUNCEMENT_MANAGE in user.permissions:
        return True
    import datetime as dt

    viewer = await service.viewer_for(user.id, user.roles)
    return service.is_live(a, dt.datetime.now(dt.timezone.utc)) and service.is_for(a, viewer)


# ============================================================ written by teachers and staff (approved by the admin)
class SubmitIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    category: AnnouncementCategory = AnnouncementCategory.GENERAL
    priority: AnnouncementPriority = AnnouncementPriority.NORMAL
    audience_students: bool = True
    audience_teachers: bool = False
    audience_staff: bool = False
    program_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    semesters: list[int] | None = Field(default=None, max_length=12)
    offering_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    expires_at: datetime | None = None


class RejectIn(BaseModel):
    note: str = Field(min_length=4, max_length=500)


def _mine_out(a: Announcement, text: str, atts: list) -> dict:
    from app.announcements.submissions import REVIEW_TEXT

    return {"id": a.id, "title": a.title, "body": a.body, "category": a.category, "priority": a.priority, "issuer_name": a.issuer_name,
            "issuer_designation": a.issuer_designation, "audience_students": a.audience_students, "audience_teachers": a.audience_teachers,
            "audience_staff": a.audience_staff, "program_ids": a.program_ids, "semesters": a.semesters, "offering_ids": a.offering_ids,
            "expires_at": a.expires_at, "audience_text": text, "review": a.review, "review_text": REVIEW_TEXT.get(a.review or "", ""),
            "review_note": a.review_note, "submitted_at": a.submitted_at, "published_at": a.published_at, "status": a.status,
            "can_edit": a.review in ("PENDING", "REJECTED"), "attachments": [_attachment_out(x).model_dump() for x in atts]}


@router.get("/mine", summary="Announcements I have written (teachers and staff), with whether they are approved")
async def my_submissions(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.announcements.submissions import SubmissionService, audience_text

    svc = SubmissionService(db)
    name, job = await svc.writer(current_user.id, current_user.roles)
    items = await svc.mine(current_user.id)
    texts = await audience_text(db, items)
    atts: dict = {}
    for att in await svc.base.attachments_of([a.id for a in items]):
        atts.setdefault(att.announcement_id, []).append(att)
    return {"issued_as": {"name": name, "designation": job}, "targets": await svc.targets(current_user.id, current_user.roles),
            "categories": [{"value": c.value, "label": CATEGORY_LABELS[c]} for c in AnnouncementCategory],
            "announcements": [_mine_out(a, texts.get(a.id, ""), atts.get(a.id, [])) for a in items]}


@router.post("/mine", status_code=201, summary="Write an announcement; it is published once the admin approves it")
async def submit_announcement(payload: SubmitIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.announcements.submissions import SubmissionService, audience_text

    a = await SubmissionService(db).submit(payload.model_dump(), user_id=current_user.id, roles=current_user.roles)
    return _mine_out(a, (await audience_text(db, [a])).get(a.id, ""), [])


@router.patch("/mine/{announcement_id}", summary="Change my announcement while it waits, or fix a rejected one (it goes back to the admin)")
async def edit_my_announcement(announcement_id: uuid.UUID, payload: SubmitIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.announcements.submissions import SubmissionService, audience_text

    svc = SubmissionService(db)
    a = await svc.edit(announcement_id, payload.model_dump(), user_id=current_user.id, roles=current_user.roles)
    return _mine_out(a, (await audience_text(db, [a])).get(a.id, ""), await svc.base.attachments_of([a.id]))


@router.post("/mine/{announcement_id}/cancel", summary="Take back my announcement before it is published")
async def cancel_my_announcement(announcement_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.announcements.submissions import SubmissionService

    a = await SubmissionService(db).cancel(announcement_id, user_id=current_user.id)
    return {"id": a.id, "review": a.review}


@router.post("/mine/{announcement_id}/attachments", response_model=AttachmentOut, status_code=201, summary="Attach a file to my announcement")
async def add_my_attachment(announcement_id: uuid.UUID, file: UploadFile = File(...), current_user: CurrentUser = Depends(get_current_user),
                            db=Depends(get_db)) -> AttachmentOut:
    a = await AnnouncementService(db).get(announcement_id)
    if a.submitted_by != current_user.id:
        raise ResourceNotFoundError("Announcement not found.")
    if a.review not in ("PENDING", "REJECTED"):
        raise BusinessRuleViolationError("Files can be added only before it is published.")
    content = await file.read(MAX_ATTACHMENT_BYTES + 1)
    try:
        att = await AnnouncementService(db).add_attachment(announcement_id, content=content, filename=file.filename or "file",
                                                           declared_type=file.content_type, actor_id=current_user.id)
    except (UnsupportedFileTypeError, FileTooLargeError) as exc:
        raise BusinessRuleViolationError(str(exc)) from exc
    return _attachment_out(att)


@router.delete("/mine/attachments/{attachment_id}", status_code=204, summary="Remove a file from my announcement before it is published")
async def remove_my_attachment(attachment_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    svc = AnnouncementService(db)
    att = await svc.get_attachment(attachment_id)
    a = await svc.get(att.announcement_id)
    if a.submitted_by != current_user.id or a.review not in ("PENDING", "REJECTED"):
        raise ResourceNotFoundError("Attachment not found.")
    await svc.remove_attachment(attachment_id, actor_id=current_user.id)
    return Response(status_code=204)


@router.post("/{announcement_id}/approve", response_model=AnnouncementAdminOut, summary="Approve a teacher's or staff member's announcement: it is published (admin)")
async def approve_announcement(announcement_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> AnnouncementAdminOut:
    from app.announcements.submissions import SubmissionService

    a = await SubmissionService(db).approve(announcement_id, actor_id=current_user.id)
    return (await _build(AnnouncementService(db), [a], user_id=current_user.id, admin=True))[0]


@router.post("/{announcement_id}/reject", response_model=AnnouncementAdminOut, summary="Send it back with what to change (admin)")
async def reject_announcement(announcement_id: uuid.UUID, payload: RejectIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> AnnouncementAdminOut:
    from app.announcements.submissions import SubmissionService

    a = await SubmissionService(db).reject(announcement_id, actor_id=current_user.id, note=payload.note)
    return (await _build(AnnouncementService(db), [a], user_id=current_user.id, admin=True))[0]


# ============================================================ issuing authorities (admin)
@router.get("/authorities", response_model=list[AuthorityOut], summary="The officials announcements can be issued by")
async def list_authorities(
    active_only: bool = False, current_user: CurrentUser = Depends(manage), db=Depends(get_db),
) -> list[AuthorityOut]:
    return [AuthorityOut(id=a.id, name=a.name, designation=a.designation, is_active=a.is_active)
            for a in await AnnouncementService(db).list_authorities(active_only=active_only)]


@router.post("/authorities", response_model=AuthorityOut, status_code=201, summary="Add an official (e.g. Managing Director) announcements can be issued by")
async def create_authority(payload: AuthorityIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> AuthorityOut:
    a = await AnnouncementService(db).create_authority(name=payload.name, designation=payload.designation, actor_id=current_user.id)
    return AuthorityOut(id=a.id, name=a.name, designation=a.designation, is_active=a.is_active)


@router.patch("/authorities/{authority_id}", response_model=AuthorityOut, summary="Rename, change the designation of, or switch off an official")
async def update_authority(authority_id: uuid.UUID, payload: AuthorityUpdate, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> AuthorityOut:
    a = await AnnouncementService(db).update_authority(authority_id, actor_id=current_user.id, **payload.model_dump())
    return AuthorityOut(id=a.id, name=a.name, designation=a.designation, is_active=a.is_active)


# ============================================================ writing and publishing (admin)
@router.get("/manage", response_model=list[AnnouncementAdminOut], summary="Every announcement, drafts included, with how many people have read it (admin)")
async def list_for_admin(
    status: AnnouncementStatus | None = None, review: str | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db),
) -> list[AnnouncementAdminOut]:
    """review=PENDING: written by teachers/staff and waiting for approval. Taken-back and rejected ones are left out unless asked for."""
    stmt = select(Announcement).order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).limit(300)
    if status:
        stmt = stmt.where(Announcement.status == status)
    if review:
        stmt = stmt.where(Announcement.review == review.upper())
    else:
        stmt = stmt.where((Announcement.review.is_(None)) | (Announcement.review.in_(["PENDING", "APPROVED"])))
    items = list((await db.execute(stmt)).scalars())
    return await _build(AnnouncementService(db), items, user_id=current_user.id, admin=True)


@router.post("", response_model=AnnouncementAdminOut, status_code=201, summary="Write an announcement (publish now, or save as a draft)")
async def create_announcement(payload: AnnouncementCreate, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> AnnouncementAdminOut:
    service = AnnouncementService(db)
    a = await service.create(data=payload.model_dump(), actor_id=current_user.id)
    return (await _build(service, [a], user_id=current_user.id, admin=True))[0]


# ============================================================ reading (everyone)
@router.get("", response_model=list[AnnouncementOut], summary="The announcements meant for me, newest first (pinned on top)")
async def my_announcements(
    category: AnnouncementCategory | None = None, unread_only: bool = False, q: str | None = None, limit: int = 30, offset: int = 0,
    current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db),
) -> list[AnnouncementOut]:
    service = AnnouncementService(db)
    viewer = await service.viewer_for(current_user.id, current_user.roles)
    items = await service.visible_to(viewer)
    if category:
        items = [a for a in items if a.category == category]
    if q and q.strip():
        needle = q.strip().lower()
        items = [a for a in items if needle in a.title.lower() or needle in a.body.lower() or needle in a.issuer_name.lower()]
    seen = await service.read_ids(current_user.id)
    if unread_only:
        items = [a for a in items if a.id not in seen]
    items = items[max(0, offset): max(0, offset) + max(1, min(limit, 100))]
    return await _build(service, items, user_id=current_user.id, admin=False)


@router.get("/unread-count", response_model=UnreadCountOut, summary="How many announcements for me I have not opened yet")
async def unread_count(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> UnreadCountOut:
    service = AnnouncementService(db)
    viewer = await service.viewer_for(current_user.id, current_user.roles)
    seen = await service.read_ids(current_user.id)
    return UnreadCountOut(unread_count=len([a for a in await service.visible_to(viewer) if a.id not in seen]))


@router.post("/read-all", response_model=UnreadCountOut, summary="Mark every announcement for me as read")
async def read_all(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> UnreadCountOut:
    service = AnnouncementService(db)
    await service.mark_all_read(await service.viewer_for(current_user.id, current_user.roles))
    return UnreadCountOut(unread_count=0)


@router.get("/attachments/{attachment_id}/file", summary="Download or open an attachment (only for people the announcement is for)")
async def download_attachment(attachment_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    service = AnnouncementService(db)
    att = await service.get_attachment(attachment_id)
    a = await service.get(att.announcement_id)
    if not await _can_see(service, a, current_user):
        raise AuthorizationError("This announcement is not for you.")
    path = get_file_path(att.storage_key)
    if path is None:
        raise ResourceNotFoundError("The file is no longer available.")
    inline = attachment_is_inline(att.content_type)
    return Response(
        content=path.read_bytes(), media_type=att.content_type,
        headers={"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{att.original_name}"', **SAFE_SERVE_HEADERS},
    )


@router.delete("/attachments/{attachment_id}", status_code=204, summary="Remove an attachment (admin)")
async def delete_attachment(attachment_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await AnnouncementService(db).remove_attachment(attachment_id, actor_id=current_user.id)
    return Response(status_code=204)


@router.get("/{announcement_id}", response_model=AnnouncementAdminOut, summary="One announcement in full (only if it is for me)")
async def get_announcement(announcement_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> AnnouncementAdminOut:
    service = AnnouncementService(db)
    a = await service.get(announcement_id)
    if not await _can_see(service, a, current_user):
        raise ResourceNotFoundError("Announcement not found.")        # not "forbidden": it is simply not there for this person
    return (await _build(service, [a], user_id=current_user.id, admin=Permissions.ANNOUNCEMENT_MANAGE in current_user.permissions))[0]


@router.post("/{announcement_id}/read", response_model=UnreadCountOut, summary="I have opened this announcement")
async def mark_read(announcement_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> UnreadCountOut:
    service = AnnouncementService(db)
    a = await service.get(announcement_id)
    if not await _can_see(service, a, current_user):
        raise ResourceNotFoundError("Announcement not found.")
    await service.mark_read(a.id, current_user.id)
    await db.flush()
    return await unread_count(current_user, db)


@router.patch("/{announcement_id}", response_model=AnnouncementAdminOut, summary="Edit an announcement (admin)")
async def update_announcement(announcement_id: uuid.UUID, payload: AnnouncementUpdate, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> AnnouncementAdminOut:
    service = AnnouncementService(db)
    a = await service.update(announcement_id, changes=payload.model_dump(exclude_unset=True), actor_id=current_user.id)
    return (await _build(service, [a], user_id=current_user.id, admin=True))[0]


@router.post("/{announcement_id}/publish", response_model=AnnouncementAdminOut, summary="Publish it: it goes live and its audience is notified (admin)")
async def publish_announcement(announcement_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> AnnouncementAdminOut:
    service = AnnouncementService(db)
    a = await service.get(announcement_id)
    if a.review == "PENDING":                                  # written by staff: publishing it is approving it
        from app.announcements.submissions import SubmissionService

        a = await SubmissionService(db).approve(announcement_id, actor_id=current_user.id)
    else:
        a = await service.publish(announcement_id, actor_id=current_user.id)
    return (await _build(service, [a], user_id=current_user.id, admin=True))[0]


@router.post("/{announcement_id}/archive", response_model=AnnouncementAdminOut, summary="Take it down (kept for the record) (admin)")
async def archive_announcement(announcement_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> AnnouncementAdminOut:
    service = AnnouncementService(db)
    a = await service.archive(announcement_id, actor_id=current_user.id)
    return (await _build(service, [a], user_id=current_user.id, admin=True))[0]


@router.delete("/{announcement_id}", status_code=204, summary="Delete an announcement and its files for good (admin)")
async def delete_announcement(announcement_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await AnnouncementService(db).delete(announcement_id, actor_id=current_user.id)
    return Response(status_code=204)


@router.post("/{announcement_id}/attachments", response_model=AttachmentOut, status_code=201, summary="Attach a file (admin)")
async def add_attachment(
    announcement_id: uuid.UUID, file: UploadFile = File(...), current_user: CurrentUser = Depends(manage), db=Depends(get_db),
) -> AttachmentOut:
    content = await file.read(MAX_ATTACHMENT_BYTES + 1)
    try:
        att = await AnnouncementService(db).add_attachment(
            announcement_id, content=content, filename=file.filename or "file", declared_type=file.content_type, actor_id=current_user.id
        )
    except (UnsupportedFileTypeError, FileTooLargeError) as exc:
        raise BusinessRuleViolationError(str(exc)) from exc
    return _attachment_out(att)