"""Students and employees ask to change their own contact details and photo; an administrator approves or rejects.

Editable by the person (everything else stays office-only): phone, email, address, city, district, province, emergency
contact, marital status, blood group, religion; for students the primary guardian's phone; for staff the bank details
used for salary; plus the photo. Name, CNIC, date of birth and gender stay with the office (identity correction). A photo must pass the automatic face check (face_check.py) before it
can even be sent for approval. Approving applies every change in one go and is kept in the activity log with before / after.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.core.storage import (ALLOWED_PHOTO_CONTENT_TYPES, MAX_PHOTO_BYTES, FileTooLargeError, UnsupportedFileTypeError, archive_file,
                              delete_file, save_uploaded_file)
from app.selfservice.face_check import check_face
from app.selfservice.models import ProfileChangeRequest

PHONE = re.compile(r"^\+?[0-9][0-9 \-]{6,18}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str          # phone | email | text | choice | iban
    max_len: int
    required: bool = False
    choices: tuple = ()
    group: str = "Contact"


MARITAL = ("Single", "Married", "Divorced", "Widowed")
BLOOD = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
_CONTACT = [
    Field("contact_no", "Mobile number", "phone", 20, True), Field("email", "Email", "email", 255),
    Field("address_line", "Home address", "text", 255), Field("city", "City", "text", 100),
    Field("district", "District", "text", 100, group="Contact"), Field("province", "Province", "text", 100, group="Contact"),
    Field("emergency_contact_name", "Emergency contact: name", "text", 150), Field("emergency_contact_no", "Emergency contact: phone", "phone", 20),
]
STUDENT_FIELDS = _CONTACT + [
    Field("guardian_contact_no", "Guardian's phone", "phone", 20),
    Field("marital_status", "Marital status", "choice", 20, True, MARITAL, "Personal"),
    Field("blood_group", "Blood group", "choice", 5, False, BLOOD, "Personal"),
    Field("religion", "Religion", "text", 50, True, group="Personal"),
]
EMPLOYEE_FIELDS = _CONTACT + [
    Field("marital_status", "Marital status", "choice", 20, False, MARITAL, "Personal"),
    Field("blood_group", "Blood group", "choice", 5, False, BLOOD, "Personal"),
    Field("religion", "Religion", "text", 50, group="Personal"),
    Field("bank_name", "Bank name", "text", 100, group="Bank (for salary)"),
    Field("bank_account_title", "Account title", "text", 150, group="Bank (for salary)"),
    Field("bank_iban", "IBAN or account number", "iban", 34, group="Bank (for salary)"),
]
FIELDS = {"STUDENT": STUDENT_FIELDS, "EMPLOYEE": EMPLOYEE_FIELDS}


def _clean(f: Field, value: Any) -> str | None:
    v = ("" if value is None else str(value)).strip()
    if not v:
        if f.required:
            raise ValidationAppError(f"{f.label} cannot be empty.")
        return None
    if len(v) > f.max_len:
        raise ValidationAppError(f"{f.label} is too long (at most {f.max_len} characters).")
    if f.kind == "phone" and not PHONE.match(v):
        raise ValidationAppError(f"{f.label}: enter a phone number such as 0300-1234567.")
    if f.kind == "email" and not EMAIL.match(v):
        raise ValidationAppError(f"{f.label}: enter a valid email address.")
    if f.kind == "choice":
        match = next((c for c in f.choices if c.lower() == v.lower().replace(" ", "")), None) or next((c for c in f.choices if c.lower() == v.lower()), None)
        if match is None:
            raise ValidationAppError(f"{f.label}: choose one of {', '.join(f.choices)}.")
        return match
    if f.kind == "iban":
        v = "".join(v.split()).upper().replace("-", "")
        if v.startswith("PK"):
            if not re.fullmatch(r"PK\d{2}[A-Z]{4}\d{16}", v):
                raise ValidationAppError("A Pakistani IBAN is PK, 2 digits, the 4-letter bank code and 16 digits (24 characters).")
        elif not re.fullmatch(r"\d{6,20}", v):
            raise ValidationAppError("Enter the IBAN (PK...) or the account number (digits only).")
    return v


class Subject:
    """One student or employee: reads and writes the editable fields wherever they are stored."""

    def __init__(self, session: AsyncSession, kind: str, row) -> None:
        self.session, self.kind, self.row = session, kind, row

    @property
    def name(self) -> str:
        return f"{self.row.first_name} {self.row.last_name}".strip()

    @property
    def code(self) -> str:
        return self.row.roll_number if self.kind == "STUDENT" else self.row.employee_code

    async def _student_parts(self):
        from app.students.models import StudentAddress, StudentEmergencyContact, StudentGuardian

        sid = self.row.id
        addr = (await self.session.execute(select(StudentAddress).where(StudentAddress.student_id == sid)
                                           .order_by((StudentAddress.address_type != "PRESENT"), StudentAddress.created_at))).scalars().first()
        emer = (await self.session.execute(select(StudentEmergencyContact).where(StudentEmergencyContact.student_id == sid)
                                           .order_by(StudentEmergencyContact.created_at))).scalars().first()
        guard = (await self.session.execute(select(StudentGuardian).where(StudentGuardian.student_id == sid)
                                            .order_by(StudentGuardian.is_primary.desc(), StudentGuardian.created_at))).scalars().first()
        return addr, emer, guard

    async def values(self) -> dict[str, str | None]:
        r = self.row
        if self.kind == "EMPLOYEE":
            return {f.key: getattr(r, f.key) for f in EMPLOYEE_FIELDS}
        addr, emer, guard = await self._student_parts()
        return {"contact_no": r.contact_no, "email": r.email, "address_line": addr.address_line if addr else None, "city": addr.city if addr else None,
                "district": addr.district if addr else None, "province": addr.province if addr else None,
                "emergency_contact_name": emer.name if emer else None, "emergency_contact_no": emer.contact_no if emer else None,
                "guardian_contact_no": guard.contact_no if guard else None,
                "marital_status": r.marital_status, "blood_group": r.blood_group, "religion": r.religion}

    async def apply(self, key: str, value: str | None) -> None:
        r = self.row
        if self.kind == "EMPLOYEE" or key in ("contact_no", "email", "marital_status", "blood_group", "religion"):
            setattr(r, key, value)
            return
        from app.students.models import StudentAddress, StudentEmergencyContact

        addr, emer, guard = await self._student_parts()
        if key in ("address_line", "city", "district", "province"):
            if addr is None:
                addr = StudentAddress(student_id=r.id, address_type="PRESENT", address_line="", province="", district="", city="")
                self.session.add(addr)
            setattr(addr, key, value or "")
        elif key in ("emergency_contact_name", "emergency_contact_no"):
            if emer is None:
                emer = StudentEmergencyContact(student_id=r.id, name=None, contact_no="")
                self.session.add(emer)
            if key == "emergency_contact_name":
                emer.name = value
            else:
                emer.contact_no = value or ""
        elif key == "guardian_contact_no":
            if guard is None:
                raise BusinessRuleViolationError("This student has no guardian on record; the office must add one first.")
            guard.contact_no = value


async def subject_for_user(session: AsyncSession, user_id: uuid.UUID) -> Subject:
    from app.employees.models import Employee
    from app.students.models import Student

    st = (await session.execute(select(Student).where(Student.user_id == user_id))).scalar_one_or_none()
    if st is not None:
        return Subject(session, "STUDENT", st)
    emp = (await session.execute(select(Employee).where(Employee.user_id == user_id))).scalar_one_or_none()
    if emp is not None:
        return Subject(session, "EMPLOYEE", emp)
    raise BusinessRuleViolationError("Only students and staff have a profile to change.")


async def subject_of(session: AsyncSession, req: ProfileChangeRequest) -> Subject:
    from app.employees.models import Employee
    from app.students.models import Student

    row = await session.get(Student if req.subject_type == "STUDENT" else Employee, req.subject_id)
    if row is None:
        raise ResourceNotFoundError("The student or employee of this request no longer exists.")
    return Subject(session, req.subject_type, row)


class ProfileChangeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def pending_for(self, subj: Subject) -> ProfileChangeRequest | None:
        return (await self.session.execute(select(ProfileChangeRequest).where(
            ProfileChangeRequest.subject_type == subj.kind, ProfileChangeRequest.subject_id == subj.row.id,
            ProfileChangeRequest.status == "PENDING"))).scalar_one_or_none()

    async def _open(self, subj: Subject, user_id: uuid.UUID) -> ProfileChangeRequest:
        req = await self.pending_for(subj)
        if req is None:
            req = ProfileChangeRequest(subject_type=subj.kind, subject_id=subj.row.id, requested_by=user_id, changes={}, status="PENDING")
            self.session.add(req)
            await self.session.flush()
        return req

    async def ask_fields(self, subj: Subject, user_id: uuid.UUID, values: dict[str, Any], note: str | None) -> ProfileChangeRequest:
        allowed = {f.key: f for f in FIELDS[subj.kind]}
        unknown = set(values) - set(allowed)
        if unknown:
            raise ValidationAppError("These details can only be changed by the office: " + ", ".join(sorted(unknown)) + ".")
        current = await subj.values()
        wanted = {}
        for key, raw in values.items():
            new = _clean(allowed[key], raw)
            if (new or None) != (current.get(key) or None):
                wanted[key] = {"old": current.get(key), "new": new}
        req = await self.pending_for(subj)
        if not wanted and not (req and note):
            raise BusinessRuleViolationError("Nothing is different from what is on record.")
        req = await self._open(subj, user_id)
        merged = dict(req.changes or {})
        for key in values:                                   # a field changed back to what is on record is dropped from the request
            merged.pop(key, None)
        merged.update(wanted)
        req.changes = merged
        if note is not None:
            req.note = note.strip()[:500] or None
        await self.audit.log(actor_user_id=user_id, action="profile_change.requested", entity_type="profile_change_request", entity_id=req.id,
                             after={k: v["new"] for k, v in wanted.items()}, metadata={"subject": subj.kind, "username": subj.code})
        await self._drop_if_empty(req)
        return req

    async def ask_photo(self, subj: Subject, user_id: uuid.UUID, content: bytes, filename: str, content_type: str) -> ProfileChangeRequest:
        if len(content) > MAX_PHOTO_BYTES:
            raise ValidationAppError(f"The photo is too large: at most {MAX_PHOTO_BYTES // (1024 * 1024)} MB.")
        if content_type not in ALLOWED_PHOTO_CONTENT_TYPES:
            raise ValidationAppError("Upload a JPG, PNG or WEBP photo.")
        result = check_face(content)
        if not result.ok:
            await self.audit.log(actor_user_id=user_id, action="profile_change.photo_refused", entity_type=subj.kind.lower(), entity_id=subj.row.id,
                                 metadata={"reason": result.reason, "check": result.detail})
            await self.session.commit()
            raise BusinessRuleViolationError("This photo cannot be used. " + result.reason)
        try:
            key, safe = save_uploaded_file(category="pending-photos", entity_id=subj.row.id, content=content, original_filename=filename or "photo.jpg",
                                           content_type=content_type, allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=MAX_PHOTO_BYTES)
        except (UnsupportedFileTypeError, FileTooLargeError) as exc:
            raise ValidationAppError(str(exc)) from exc
        req = await self._open(subj, user_id)
        if req.photo_storage_key:
            delete_file(req.photo_storage_key)               # an earlier photo in the same request that was never approved
        req.photo_storage_key, req.photo_original_name, req.photo_check = key, safe, result.detail
        await self.audit.log(actor_user_id=user_id, action="profile_change.photo_requested", entity_type="profile_change_request", entity_id=req.id,
                             metadata={"subject": subj.kind, "username": subj.code, "check": result.detail})
        return req

    async def drop_photo(self, subj: Subject, user_id: uuid.UUID) -> ProfileChangeRequest | None:
        req = await self.pending_for(subj)
        if req is None or not req.photo_storage_key:
            return req
        delete_file(req.photo_storage_key)
        req.photo_storage_key = req.photo_original_name = None
        req.photo_check = None
        await self._drop_if_empty(req)
        return req

    async def _drop_if_empty(self, req: ProfileChangeRequest) -> None:
        if not req.changes and not req.photo_storage_key:
            req.status = "CANCELLED"
            req.decided_at = datetime.now(timezone.utc)

    async def cancel(self, req_id: uuid.UUID, user_id: uuid.UUID) -> ProfileChangeRequest:
        req = await self.session.get(ProfileChangeRequest, req_id)
        if req is None or req.requested_by != user_id:
            raise ResourceNotFoundError("Request not found.")
        if req.status != "PENDING":
            raise BusinessRuleViolationError("Only a request that is still waiting can be withdrawn.")
        if req.photo_storage_key:
            delete_file(req.photo_storage_key)
            req.photo_storage_key = None
        req.status, req.decided_at = "CANCELLED", datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=user_id, action="profile_change.withdrawn", entity_type="profile_change_request", entity_id=req.id)
        return req

    async def decide(self, req_id: uuid.UUID, *, approve: bool, actor_id: uuid.UUID, reason: str | None) -> ProfileChangeRequest:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        req = (await self.session.execute(select(ProfileChangeRequest).where(ProfileChangeRequest.id == req_id).with_for_update())).scalar_one_or_none()
        if req is None:
            raise ResourceNotFoundError("Request not found.")
        if req.status != "PENDING":
            raise BusinessRuleViolationError("This request has already been decided or withdrawn.")
        reason = (reason or "").strip() or None
        if not approve and not reason:
            raise ValidationAppError("Give the reason, so the person knows what to fix.")
        subj = await subject_of(self.session, req)
        before = await subj.values()
        after: dict[str, Any] = {}
        if approve:
            for key, change in (req.changes or {}).items():
                await subj.apply(key, change.get("new"))
                after[key] = change.get("new")
            if req.photo_storage_key:
                kept = archive_file(subj.row.photo_storage_key)
                subj.row.photo_storage_key, subj.row.photo_original_name = req.photo_storage_key, req.photo_original_name
                after["photo"] = "new photo published" + (f" (old one kept as {kept})" if kept else "")
        elif req.photo_storage_key:
            delete_file(req.photo_storage_key)
            req.photo_storage_key = None
        req.status = "APPROVED" if approve else "REJECTED"
        req.decided_by, req.decided_at, req.decision_reason = actor_id, datetime.now(timezone.utc), reason
        await self.audit.log(actor_user_id=actor_id, action="profile_change.approved" if approve else "profile_change.rejected",
                             entity_type=subj.kind.lower(), entity_id=subj.row.id,
                             before={k: before.get(k) for k in (req.changes or {})} if approve else None, after=after or None,
                             metadata={"request_id": str(req.id), "username": subj.code, "reason": reason})
        if subj.row.user_id:
            what = ", ".join([f.label.lower() for f in FIELDS[subj.kind] if f.key in (req.changes or {})] + (["photo"] if after.get("photo") or (not approve and req.photo_original_name) else []))
            await create_notification(self.session, user_id=subj.row.user_id, category=NotificationCategory.APPLICATION,
                                      title=("Your profile change was approved" if approve else "Your profile change was not approved"),
                                      message=(f"Approved: {what}." if approve else f"Reason: {reason}") if what else (reason or "Decided."),
                                      link="/dashboard/student/profile" if subj.kind == "STUDENT" else "/dashboard/my-profile")
        return req

    async def count_pending(self) -> int:
        return (await self.session.execute(select(func.count()).select_from(ProfileChangeRequest).where(ProfileChangeRequest.status == "PENDING"))).scalar_one()


async def request_out(session: AsyncSession, req: ProfileChangeRequest, *, with_subject: bool = False, names: Callable | None = None) -> dict:
    labels = {f.key: f.label for fs in FIELDS.values() for f in fs}
    out = {"id": req.id, "status": req.status, "created_at": req.created_at, "updated_at": req.updated_at, "note": req.note,
           "changes": [{"field": k, "label": labels.get(k, k), "old": v.get("old"), "new": v.get("new")} for k, v in (req.changes or {}).items()],
           "has_photo": bool(req.photo_storage_key), "photo_check": req.photo_check,
           "decided_at": req.decided_at, "decision_reason": req.decision_reason, "subject_type": req.subject_type}
    if with_subject:
        subj = await subject_of(session, req)
        out["subject"] = {"id": subj.row.id, "name": subj.name, "code": subj.code, "kind": subj.kind,
                          "has_current_photo": bool(subj.row.photo_storage_key)}
    return out