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