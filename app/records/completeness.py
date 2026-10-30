"""How complete a student's or employee's profile is: a short checklist of details and documents the office needs.

percent = items done / items on the list. Worked out for many people at once (the Students and Employees lists), with a
few set-based queries rather than one query per person.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.records.models import PersonDocument

# (key, label) in the order shown. A document item is done when a current (not archived) document of that kind is on
# file; "certificates" items also count the combined certificate PDF uploaded on the profile.
STUDENT_ITEMS = [
    ("photo", "Photo"), ("contact_no", "Mobile number"), ("email", "Email"), ("address", "Home address"),
    ("guardian", "Guardian with phone"), ("emergency", "Emergency contact"), ("blood_group", "Blood group"),
    ("doc:CNIC", "CNIC / B-Form copy"), ("doc:MATRIC", "Matric certificate"), ("doc:INTERMEDIATE", "Intermediate certificate"),
    ("doc:DOMICILE", "Domicile"),
]
EMPLOYEE_ITEMS = [
    ("photo", "Photo"), ("contact_no", "Mobile number"), ("email", "Email"), ("address", "Home address"),
    ("emergency", "Emergency contact"), ("blood_group", "Blood group"), ("marital_status", "Marital status"),
    ("bank", "Bank details for salary"), ("qualifications", "Qualifications"),
    ("doc:CNIC", "CNIC copy"), ("doc:DEGREE", "Degree / certificates"), ("doc:CONTRACT", "Contract / appointment letter"),
]
CERTIFICATE_PDF_COUNTS_FOR = {"doc:MATRIC", "doc:INTERMEDIATE", "doc:DEGREE"}


def _filled(v) -> bool:
    return v is not None and str(v).strip() != ""


async def completeness(session: AsyncSession, owner_type: str, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """{id: {"percent", "done", "total", "missing": [labels]}} for students (owner_type STUDENT) or employees."""
    from app.profiles.models import ProfilePartyType, Qualification

    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    docs: dict = defaultdict(set)
    for oid, kind in (await session.execute(select(PersonDocument.owner_id, PersonDocument.kind).where(
            PersonDocument.owner_type == owner_type, PersonDocument.owner_id.in_(ids), PersonDocument.archived_at.is_(None)))).all():
        docs[oid].add(kind)
    quals = dict((await session.execute(select(Qualification.party_id, func.count()).where(
        Qualification.party_type == ProfilePartyType(owner_type), Qualification.party_id.in_(ids)).group_by(Qualification.party_id))).all())

    facts: dict = {}
    if owner_type == "STUDENT":
        from app.students.models import Student, StudentAddress, StudentEmergencyContact, StudentGuardian

        rows = (await session.execute(select(Student).where(Student.id.in_(ids)))).scalars().all()
        addr = {sid for sid, line in (await session.execute(select(StudentAddress.student_id, StudentAddress.address_line).where(
            StudentAddress.student_id.in_(ids)))).all() if _filled(line)}
        guard = {sid for sid, no in (await session.execute(select(StudentGuardian.student_id, StudentGuardian.contact_no).where(
            StudentGuardian.student_id.in_(ids)))).all() if _filled(no)}
        emer = {sid for sid, no in (await session.execute(select(StudentEmergencyContact.student_id, StudentEmergencyContact.contact_no).where(
            StudentEmergencyContact.student_id.in_(ids)))).all() if _filled(no)}
        for r in rows:
            facts[r.id] = {"photo": bool(r.photo_storage_key), "contact_no": _filled(r.contact_no), "email": _filled(r.email), "address": r.id in addr,
                           "guardian": r.id in guard, "emergency": r.id in emer, "blood_group": _filled(r.blood_group),
                           "certificate_pdf": bool(r.certificate_storage_key)}
        items = STUDENT_ITEMS
    else:
        from app.employees.models import Employee

        rows = (await session.execute(select(Employee).where(Employee.id.in_(ids)))).scalars().all()
        for r in rows:
            facts[r.id] = {"photo": bool(r.photo_storage_key), "contact_no": _filled(r.contact_no), "email": _filled(r.email), "address": _filled(r.address_line),
                           "emergency": _filled(r.emergency_contact_no), "blood_group": _filled(r.blood_group), "marital_status": _filled(r.marital_status),
                           "bank": _filled(r.bank_name) and _filled(r.bank_account_title) and _filled(r.bank_iban),
                           "qualifications": quals.get(r.id, 0) > 0, "certificate_pdf": bool(r.certificate_storage_key)}
        items = EMPLOYEE_ITEMS

    out = {}
    for oid, f in facts.items():
        missing = []
        for key, label in items:
            if key.startswith("doc:"):
                ok = key[4:] in docs[oid] or (key in CERTIFICATE_PDF_COUNTS_FOR and f["certificate_pdf"])
            else:
                ok = f.get(key, False)
            if not ok:
                missing.append(label)
        done = len(items) - len(missing)
        out[oid] = {"percent": round(done * 100 / len(items)), "done": done, "total": len(items), "missing": missing}
    return out


async def active_ids(session: AsyncSession, owner_type: str) -> list[uuid.UUID]:
    if owner_type == "STUDENT":
        from app.students.models import Student, StudentStatus

        return list((await session.execute(select(Student.id).where(Student.status == StudentStatus.ACTIVE))).scalars())
    from app.employees.models import Employee, EmployeeStatus

    return list((await session.execute(select(Employee.id).where(Employee.status == EmployeeStatus.ACTIVE))).scalars())


async def incomplete(session: AsyncSession, owner_type: str, *, below: int = 100) -> list[dict]:
    """Active people whose profile is below `below` %, least complete first."""
    if owner_type == "STUDENT":
        from app.students.models import Student as Model

        code = Model.roll_number
    else:
        from app.employees.models import Employee as Model

        code = Model.employee_code
    ids = await active_ids(session, owner_type)
    out: list[dict] = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        result = await completeness(session, owner_type, part)
        people = {r.id: r for r in (await session.execute(select(Model.id, Model.first_name, Model.last_name, code.label("code"), Model.user_id)
                                                          .where(Model.id.in_(part)))).all()}
        for pid, c in result.items():
            if c["percent"] < below:
                p = people[pid]
                out.append({"id": pid, "name": f"{p.first_name} {p.last_name}".strip(), "code": p.code, "has_login": p.user_id is not None, **c})
    out.sort(key=lambda x: (x["percent"], x["name"]))
    return out


REMIND_TITLE = "Please complete your profile"
REMIND_GAP_DAYS = 3


async def remind(session: AsyncSession, owner_type: str, ids: list[uuid.UUID], *, actor_id: uuid.UUID) -> dict:
    """Send each person (with a login) a notification, also by email, listing exactly what is missing. Someone reminded
    in the last REMIND_GAP_DAYS days is skipped, so pressing the button twice does not nag."""
    from datetime import datetime, timedelta, timezone

    from app.audit.service import AuditService
    from app.notifications.models import Notification, NotificationCategory
    from app.notifications.service import create_notification

    if owner_type == "STUDENT":
        from app.students.models import Student as Model
    else:
        from app.employees.models import Employee as Model
    result = await completeness(session, owner_type, ids)
    users = dict((await session.execute(select(Model.id, Model.user_id).where(Model.id.in_(list(result))))).all())
    since = datetime.now(timezone.utc) - timedelta(days=REMIND_GAP_DAYS)
    recent = set((await session.execute(select(Notification.user_id).where(Notification.title == REMIND_TITLE, Notification.created_at >= since))).scalars())
    sent = skipped_recent = no_login = complete = 0
    link = "/dashboard/student/profile" if owner_type == "STUDENT" else "/dashboard/my-profile"
    for pid, c in result.items():
        uid = users.get(pid)
        if c["percent"] >= 100:
            complete += 1
        elif uid is None:
            no_login += 1
        elif uid in recent:
            skipped_recent += 1
        else:
            await create_notification(session, user_id=uid, category=NotificationCategory.GENERAL, title=REMIND_TITLE,
                                      message=f"Your profile is {c['percent']}% complete. Still missing: {', '.join(c['missing'])}. "
                                              "Add the details under Update my details and upload the documents.", link=link)
            sent += 1
    await AuditService(session).log(actor_user_id=actor_id, action="records.profile_reminders", entity_type=owner_type.lower(), entity_id=None,
                                    metadata={"sent": sent, "skipped_recent": skipped_recent, "no_login": no_login, "complete": complete})
    return {"sent": sent, "skipped_recent": skipped_recent, "no_login": no_login, "complete": complete}