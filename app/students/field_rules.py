"""Student field rules: which details are required when a student is added (Add student form and CSV import).

Set by an administrator on Students -> Import (before downloading the template) or from the Add student form. First and
last name are always required; every other detail may be made optional or required. The rules are rows in the existing
policy_settings table, key "student_field:<column>", value "required" / "optional"; a field with no row uses its
default (today's behaviour). Editing an existing student is never blocked by these rules.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationAppError

PREFIX = "student_field:"


@dataclass(frozen=True)
class StudentField:
    key: str             # the CSV column
    label: str
    group: str
    default_required: bool
    locked: bool = False  # always required
    note: str = ""


FIELDS: tuple[StudentField, ...] = (
    StudentField("first_name", "First name", "Personal", True, locked=True),
    StudentField("last_name", "Last name", "Personal", True, locked=True),
    StudentField("date_of_birth", "Date of birth", "Personal", True),
    StudentField("gender", "Gender", "Personal", True),
    StudentField("cnic", "Student CNIC / B-form", "Personal", True,
                 note="Duplicate students are found by CNIC; without it the import cannot spot a student added twice."),
    StudentField("contact_no", "Student mobile", "Personal", True, note="SMS to the student need a mobile number."),
    StudentField("marital_status", "Marital status", "Personal", True),
    StudentField("religion", "Religion", "Personal", True),
    StudentField("nationality", "Nationality", "Personal", True),
    StudentField("guardian_name", "Father / guardian name", "Guardian", True),
    StudentField("guardian_cnic", "Father / guardian CNIC", "Guardian", True,
                 note="Parents sign in with this CNIC and brothers/sisters are linked by it. Without it the student has no parent portal."),
    StudentField("guardian_contact_no", "Guardian mobile", "Guardian", False),
    StudentField("relation_with_guardian", "Relation with guardian", "Guardian", True),
    StudentField("emergency_contact_no", "Emergency contact", "Guardian", True),
    StudentField("present_address", "Present address", "Address", True),
    StudentField("province", "Province", "Address", True),
    StudentField("district", "District", "Address", True),
    StudentField("city", "City", "Address", True),
    StudentField("program", "Program", "Admission", False, note="Without a program no roll number of that program, no fees and no courses."),
    StudentField("current_semester", "Current semester", "Admission", False),
    StudentField("section", "Section / shift", "Admission", False),
    StudentField("enrollment_date", "Enrollment date", "Admission", False),
    StudentField("admission_session", "Admission session", "Admission", False),
    StudentField("admission_category", "Admission category", "Admission", False),
    StudentField("admission_fee_paid", "Admission fee paid", "Admission", False),
    StudentField("reg_number", "University Reg No", "Admission", False,
                 note="The affiliating university's registration number. Often given later: the exam office can fill it in afterwards."),
    StudentField("email", "Email", "Other", False),
    StudentField("blood_group", "Blood group", "Other", False),
)
BY_KEY = {f.key: f for f in FIELDS}


async def get_rules(session: AsyncSession) -> dict[str, bool]:
    """{column: required?} for every field."""
    from app.policy.models import PolicySetting

    rows = {r.key[len(PREFIX):]: r.value for r in (await session.execute(
        select(PolicySetting).where(PolicySetting.key.like(PREFIX + "%")))).scalars()}
    out = {}
    for f in FIELDS:
        if f.locked:
            out[f.key] = True
        elif f.key in rows:
            out[f.key] = rows[f.key] == "required"
        else:
            out[f.key] = f.default_required
    return out


async def required_set(session: AsyncSession) -> set[str]:
    return {k for k, v in (await get_rules(session)).items() if v}


def describe(rules: dict[str, bool]) -> list[dict]:
    return [{"key": f.key, "label": f.label, "group": f.group, "required": rules[f.key], "locked": f.locked,
             "default_required": f.default_required, "note": f.note} for f in FIELDS]


async def save_rules(session: AsyncSession, changes: dict[str, bool], *, actor_id: uuid.UUID) -> dict[str, bool]:
    from app.audit.service import AuditService
    from app.policy.models import PolicySetting

    unknown = [k for k in changes if k not in BY_KEY]
    if unknown:
        raise ValidationAppError(f"Unknown field(s): {', '.join(unknown)}.")
    locked = [BY_KEY[k].label for k, v in changes.items() if BY_KEY[k].locked and not v]
    if locked:
        raise ValidationAppError(f"{', '.join(locked)} is always required.")
    before = await get_rules(session)
    now = datetime.now(timezone.utc)
    for k, required in changes.items():
        if BY_KEY[k].locked:
            continue
        key = PREFIX + k
        row = await session.get(PolicySetting, key)
        if required == BY_KEY[k].default_required:
            if row is not None:
                await session.delete(row)
            continue
        value = "required" if required else "optional"
        if row is None:
            session.add(PolicySetting(key=key, value=value, updated_by=actor_id, updated_at=now))
        else:
            row.value, row.updated_by, row.updated_at = value, actor_id, now
    await session.flush()
    after = await get_rules(session)
    changed = {k: after[k] for k in after if after[k] != before[k]}
    if changed:
        await AuditService(session).log(actor_user_id=actor_id, action="student_field_rules.updated", entity_type="policy",
                                        entity_id=None, before={k: before[k] for k in changed}, after=changed)
    return after
