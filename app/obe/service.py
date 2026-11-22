"""Outcome-based education (OBE).

  * PLOs: the administration (or exam office) writes each program's learning outcomes and when one counts as attained
    (a student needs >= student_pct in it; the batch needs >= batch_pct of its students to reach that). Default 50 / 60.
  * CLOs: each class's teacher writes the course outcomes (with Bloom level), maps each to PLOs, and tags what each
    assessment measures: the whole assessment (one or more CLOs, sharing its marks) or question parts (each with its
    own marks and CLO; part marks entered here). Then submits; the exam office locks it (or sends it back with a note).
    Once locked the outcomes, mapping and tags no longer change; part marks can still be entered.
  * Attainment comes from the marks already in the system: per student and CLO, marks obtained / marks possible in
    what measures that CLO; per PLO, the same over every CLO mapped to it in the student's classes.
Nothing here changes a mark, a result or a transcript.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.obe.models import (BLOOM, BLOOM_NAMES, AssessmentItem, CloPlo, CourseFile, CourseOutcome, ItemMark, ObeProgramSetting,
                            ProgramOutcome)

CODE = re.compile(r"^[A-Za-z]{1,6}\s?\d{1,3}[a-z]?$")


def _pct(a: float, b: float) -> float | None:
    return round(100.0 * a / b, 1) if b else None


class ObeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ programs: PLOs and thresholds
    async def thresholds(self, program_id: uuid.UUID) -> tuple[float, float]:
        s = await self.session.get(ObeProgramSetting, program_id)
        return (float(s.student_pct), float(s.batch_pct)) if s else (50.0, 60.0)

    async def program(self, program_id: uuid.UUID) -> dict:
        from app.programs.models import Program

        p = await self.session.get(Program, program_id)
        if p is None:
            raise ResourceNotFoundError("Program not found.")
        plos = (await self.session.execute(select(ProgramOutcome).where(ProgramOutcome.program_id == program_id)
                                           .order_by(ProgramOutcome.position, ProgramOutcome.code))).scalars().all()
        used = dict((await self.session.execute(select(CloPlo.plo_id, func.count()).where(CloPlo.plo_id.in_([x.id for x in plos] or [uuid.uuid4()]))
                                                .group_by(CloPlo.plo_id))).all())
        st, bt = await self.thresholds(program_id)
        return {"program": {"id": p.id, "code": p.code, "name": p.name}, "student_pct": st, "batch_pct": bt,
                "plos": [{"id": x.id, "code": x.code, "title": x.title, "clos_mapped": int(used.get(x.id, 0))} for x in plos]}

    async def save_program(self, program_id: uuid.UUID, *, plos: list[dict], student_pct: float, batch_pct: float, actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService

        await self.program(program_id)
        if not (0 < student_pct <= 100 and 0 < batch_pct <= 100):
            raise ValidationAppError("The pass levels are percentages from 1 to 100.")
        codes = [(" ".join((x.get("code") or "").split())).upper() for x in plos]
        if any(not c or not CODE.match(c) for c in codes):
            raise ValidationAppError("Each PLO needs a short code such as PLO1.")
        if len(set(codes)) != len(codes):
            raise ValidationAppError("Two PLOs have the same code.")
        if any(len((x.get("title") or "").strip()) < 5 for x in plos):
            raise ValidationAppError("Write each PLO (at least 5 characters).")
        existing = {x.id: x for x in (await self.session.execute(select(ProgramOutcome).where(ProgramOutcome.program_id == program_id))).scalars()}
        keep = set()
        for i, (x, code) in enumerate(zip(plos, codes)):
            pid = uuid.UUID(str(x["id"])) if x.get("id") else None
            if pid and pid in existing:
                row = existing[pid]
                row.code, row.title, row.position = code, x["title"].strip()[:300], i
                keep.add(pid)
            else:
                self.session.add(ProgramOutcome(program_id=program_id, code=code, title=x["title"].strip()[:300], position=i))
        gone = [pid for pid in existing if pid not in keep]
        if gone:
            locked = (await self.session.execute(select(func.count()).select_from(CloPlo).join(CourseOutcome, CourseOutcome.id == CloPlo.clo_id)
                                                 .join(CourseFile, CourseFile.course_offering_id == CourseOutcome.course_offering_id)
                                                 .where(CloPlo.plo_id.in_(gone), CourseFile.status == "LOCKED"))).scalar_one()
            if locked:
                raise BusinessRuleViolationError("A PLO you removed is mapped from a locked course file. Keep it (you can rename it).")
            await self.session.execute(delete(ProgramOutcome).where(ProgramOutcome.id.in_(gone)))
        s = await self.session.get(ObeProgramSetting, program_id)
        if s is None:
            self.session.add(ObeProgramSetting(program_id=program_id, student_pct=student_pct, batch_pct=batch_pct))
        else:
            s.student_pct, s.batch_pct = student_pct, batch_pct
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="obe.plos_saved", entity_type="program", entity_id=program_id,
                                             after={"plos": codes, "student_pct": student_pct, "batch_pct": batch_pct})
        return await self.program(program_id)

    # ------------------------------------------------------------------ one class: the course file
    async def _offering(self, offering_id: uuid.UUID):
        from app.academics.affiliation import program_of_offering
        from app.academics.models import Course, CourseOffering

        o = await self.session.get(CourseOffering, offering_id)
        if o is None:
            raise ResourceNotFoundError("Class not found.")
        c = await self.session.get(Course, o.course_id)
        p = await program_of_offering(self.session, o)
        return o, c, p

    async def _file(self, offering_id: uuid.UUID) -> CourseFile:
        f = await self.session.get(CourseFile, offering_id)
        if f is None:
            f = CourseFile(course_offering_id=offering_id, status="DRAFT")
            self.session.add(f)
            await self.session.flush()
        return f

    async def _editable(self, offering_id: uuid.UUID) -> CourseFile:
        f = await self._file(offering_id)
        if f.status != "DRAFT":
            raise BusinessRuleViolationError("This course file is " + ("locked" if f.status == "LOCKED" else "submitted") +
                                             ": ask the exam office to send it back to change it.")
        return f

    async def course_file(self, offering_id: uuid.UUID) -> dict:
        from app.academics.models import AssessmentType, OfferingAssessment

        o, c, p = await self._offering(offering_id)
        f = await self.session.get(CourseFile, offering_id)
        clos = (await self.session.execute(select(CourseOutcome).where(CourseOutcome.course_offering_id == offering_id)
                                           .order_by(CourseOutcome.position, CourseOutcome.code))).scalars().all()
        maps = defaultdict(list)
        for clo_id, plo_id in (await self.session.execute(select(CloPlo.clo_id, CloPlo.plo_id).where(CloPlo.clo_id.in_([x.id for x in clos] or [uuid.uuid4()])))).all():
            maps[clo_id].append(plo_id)
        plos = (await self.program(p.id))["plos"] if p else []
        assess = (await self.session.execute(select(OfferingAssessment, AssessmentType).join(AssessmentType, AssessmentType.id == OfferingAssessment.assessment_type_id)
                                             .where(OfferingAssessment.course_offering_id == offering_id).order_by(AssessmentType.name))).all()
        items = defaultdict(list)
        for it in (await self.session.execute(select(AssessmentItem).where(AssessmentItem.offering_assessment_id.in_([a.id for a, _ in assess] or [uuid.uuid4()]))
                                              .order_by(AssessmentItem.position))).scalars():
            items[it.offering_assessment_id].append({"id": it.id, "label": it.label, "max_marks": float(it.max_marks), "clo_id": it.clo_id})
        return {"offering": {"id": o.id, "course": f"{c.code} — {c.title}", "term_label": o.term_label, "section": o.section_label},
                "program": {"id": p.id, "code": p.code} if p else None, "status": f.status if f else "DRAFT", "note": f.note if f else None,
                "clos": [{"id": x.id, "code": x.code, "text": x.text, "bloom": x.bloom, "plo_ids": maps[x.id]} for x in clos],
                "plos": plos, "bloom": [{"code": b, "name": BLOOM_NAMES[b]} for b in BLOOM],
                "assessments": [{"id": a.id, "name": t.name, "max_marks": float(t.max_marks), "items": items[a.id],
                                 "parts": any(i["label"] for i in items[a.id])} for a, t in assess]}

    async def save_clos(self, offering_id: uuid.UUID, clos: list[dict], *, actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService

        _o, _c, p = await self._offering(offering_id)
        await self._editable(offering_id)
        plo_ids = {x.id for x in (await self.session.execute(select(ProgramOutcome).where(ProgramOutcome.program_id == (p.id if p else None)))).scalars()}
        codes = [(" ".join((x.get("code") or "").split())).upper() for x in clos]
        if any(not cd or not CODE.match(cd) for cd in codes) or len(set(codes)) != len(codes):
            raise ValidationAppError("Each CLO needs its own short code such as CLO1.")
        for x in clos:
            if len((x.get("text") or "").strip()) < 5:
                raise ValidationAppError("Write each CLO (at least 5 characters).")
            if x.get("bloom") not in BLOOM:
                raise ValidationAppError("Choose a Bloom level for each CLO.")
            if any(uuid.UUID(str(i)) not in plo_ids for i in x.get("plo_ids") or []):
                raise ValidationAppError("A CLO is mapped to a PLO of another program.")
        existing = {x.id: x for x in (await self.session.execute(select(CourseOutcome).where(CourseOutcome.course_offering_id == offering_id))).scalars()}
        keep = set()
        for i, (x, code) in enumerate(zip(clos, codes)):
            cid = uuid.UUID(str(x["id"])) if x.get("id") else None
            row = existing.get(cid) if cid else None
            if row is None:
                row = CourseOutcome(course_offering_id=offering_id, code=code, text=x["text"].strip(), bloom=x["bloom"], position=i)
                self.session.add(row)
                await self.session.flush()
            else:
                row.code, row.text, row.bloom, row.position = code, x["text"].strip(), x["bloom"], i
                keep.add(row.id)
            await self.session.execute(delete(CloPlo).where(CloPlo.clo_id == row.id))
            for pid in dict.fromkeys(uuid.UUID(str(i)) for i in x.get("plo_ids") or []):
                self.session.add(CloPlo(clo_id=row.id, plo_id=pid))
        gone = [cid for cid in existing if cid not in keep]
        if gone:
            await self.session.execute(delete(CourseOutcome).where(CourseOutcome.id.in_(gone)))       # its tags and part marks go with it
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="obe.clos_saved", entity_type="course_offering", entity_id=offering_id,
                                             after={"clos": codes})
        return await self.course_file(offering_id)

    async def copy_from_last(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        """Start from the CLOs of this course's last class that has them (mapping kept where the PLO is the same program's)."""
        from app.academics.models import CourseOffering

        o, _c, _p = await self._offering(offering_id)
        await self._editable(offering_id)
        if (await self.session.execute(select(func.count()).select_from(CourseOutcome).where(CourseOutcome.course_offering_id == offering_id))).scalar_one():
            raise BusinessRuleViolationError("This class already has CLOs.")
        src = (await self.session.execute(select(CourseOffering.id).join(CourseOutcome, CourseOutcome.course_offering_id == CourseOffering.id).where(
            CourseOffering.course_id == o.course_id, CourseOffering.id != offering_id).order_by(CourseOffering.created_at.desc()).limit(1))).scalar_one_or_none()
        if src is None:
            raise BusinessRuleViolationError("No earlier class of this course has CLOs.")
        prev = await self.course_file(src)
        return await self.save_clos(offering_id, [{"code": x["code"], "text": x["text"], "bloom": x["bloom"], "plo_ids": x["plo_ids"]} for x in prev["clos"]],
                                    actor_id=actor_id)

    async def save_items(self, offering_id: uuid.UUID, assessment_id: uuid.UUID, items: list[dict], *, actor_id: uuid.UUID) -> dict:
        from app.academics.models import AssessmentType, OfferingAssessment

        await self._editable(offering_id)
        a = await self.session.get(OfferingAssessment, assessment_id)
        if a is None or a.course_offering_id != offering_id:
            raise ResourceNotFoundError("Assessment not found in this class.")
        t = await self.session.get(AssessmentType, a.assessment_type_id)
        clos = {x.id for x in (await self.session.execute(select(CourseOutcome).where(CourseOutcome.course_offering_id == offering_id))).scalars()}
        labelled = [bool((x.get("label") or "").strip()) for x in items]
        if items and any(labelled) and not all(labelled):
            raise ValidationAppError("Either tag the whole assessment, or split it into labelled parts (Q1, Q2 ...), not both.")
        total = 0.0
        for x in items:
            if uuid.UUID(str(x.get("clo_id"))) not in clos:
                raise ValidationAppError("Choose one of this class's CLOs for each line.")
            m = float(x.get("max_marks") or 0)
            if m <= 0:
                raise ValidationAppError("Each line needs marks above 0.")
            total += m
        if total > float(t.max_marks) + 0.001:
            raise ValidationAppError(f"The lines add up to {total:g}, more than the assessment's {float(t.max_marks):g} marks.")
        await self.session.execute(delete(AssessmentItem).where(AssessmentItem.offering_assessment_id == assessment_id))
        for i, x in enumerate(items):
            self.session.add(AssessmentItem(offering_assessment_id=assessment_id, clo_id=uuid.UUID(str(x["clo_id"])), label=(x.get("label") or "").strip()[:20] or None,
                                            max_marks=float(x["max_marks"]), position=i))
        await self.session.flush()
        return await self.course_file(offering_id)

    async def submit(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        f = await self._editable(offering_id)
        cf = await self.course_file(offering_id)
        if not cf["clos"]:
            raise BusinessRuleViolationError("Write the CLOs first.")
        if any(not x["plo_ids"] for x in cf["clos"]):
            raise BusinessRuleViolationError("Map every CLO to at least one PLO.")
        tagged = {i["clo_id"] for a in cf["assessments"] for i in a["items"]}
        missing = [x["code"] for x in cf["clos"] if x["id"] not in tagged]
        if missing:
            raise BusinessRuleViolationError("No assessment measures " + ", ".join(missing) + " yet.")
        f.status, f.submitted_by, f.submitted_at, f.note = "SUBMITTED", actor_id, datetime.now(timezone.utc), None
        await self.session.flush()
        return {"status": f.status}

    async def decide(self, offering_id: uuid.UUID, *, lock: bool, note: str | None, actor_id: uuid.UUID) -> dict:
        from app.audit.service import AuditService

        f = await self._file(offering_id)
        if lock and f.status != "SUBMITTED":
            raise BusinessRuleViolationError("Only a submitted course file can be locked.")
        if not lock and f.status == "DRAFT":
            raise BusinessRuleViolationError("It is already with the teacher.")
        if not lock and len((note or "").strip()) < 3:
            raise ValidationAppError("Write what the teacher should change.")
        f.status = "LOCKED" if lock else "DRAFT"
        f.decided_by, f.decided_at, f.note = actor_id, datetime.now(timezone.utc), (note or "").strip()[:500] or None
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="obe.course_file_locked" if lock else "obe.course_file_returned",
                                             entity_type="course_offering", entity_id=offering_id, after={"note": f.note})
        return {"status": f.status}

    async def files(self, *, status: str | None = None) -> list[dict]:
        """Course files of classes whose term is not closed (the exam office's list)."""
        from app.academics.models import Course, CourseOffering
        from app.academics.terms import AcademicTerm, TermStatus

        closed = [x.lower() for x in (await self.session.execute(select(AcademicTerm.label).where(AcademicTerm.status == TermStatus.CLOSED))).scalars()]
        rows = (await self.session.execute(select(CourseOffering, Course, CourseFile).join(Course, Course.id == CourseOffering.course_id)
                                           .outerjoin(CourseFile, CourseFile.course_offering_id == CourseOffering.id)
                                           .where(func.lower(CourseOffering.term_label).not_in(closed or [""])).order_by(CourseOffering.term_label, Course.code))).all()
        counts = dict((await self.session.execute(select(CourseOutcome.course_offering_id, func.count()).group_by(CourseOutcome.course_offering_id))).all())
        out = []
        for o, c, f in rows:
            st = f.status if f else "DRAFT"
            if status and st != status:
                continue
            out.append({"offering_id": o.id, "course": f"{c.code} — {c.title}", "term_label": o.term_label, "section": o.section_label,
                        "status": st, "clos": int(counts.get(o.id, 0)), "submitted_at": f.submitted_at if f else None})
        return out

    # ------------------------------------------------------------------ question-part marks
    async def part_marks(self, offering_id: uuid.UUID, assessment_id: uuid.UUID) -> dict:
        from app.academics.models import Enrollment, EnrollmentStatus
        from app.students.models import Student

        items = (await self.session.execute(select(AssessmentItem).where(AssessmentItem.offering_assessment_id == assessment_id,
                                                                         AssessmentItem.label.is_not(None)).order_by(AssessmentItem.position))).scalars().all()
        studs = (await self.session.execute(select(Enrollment, Student).join(Student, Student.id == Enrollment.student_id).where(
            Enrollment.course_offering_id == offering_id, Enrollment.status != EnrollmentStatus.WITHDRAWN).order_by(Student.roll_number))).all()
        marks = {(m.item_id, m.enrollment_id): float(m.obtained) for m in (await self.session.execute(select(ItemMark).where(
            ItemMark.item_id.in_([i.id for i in items] or [uuid.uuid4()])))).scalars()}
        return {"parts": [{"id": i.id, "label": i.label, "max_marks": float(i.max_marks)} for i in items],
                "students": [{"enrollment_id": e.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}",
                              "marks": {str(i.id): marks.get((i.id, e.id)) for i in items}} for e, s in studs]}

    async def save_part_marks(self, offering_id: uuid.UUID, assessment_id: uuid.UUID, entries: list[dict], *, actor_id: uuid.UUID) -> dict:
        from app.academics.models import Enrollment

        items = {i.id: i for i in (await self.session.execute(select(AssessmentItem).where(AssessmentItem.offering_assessment_id == assessment_id,
                                                                                           AssessmentItem.label.is_not(None)))).scalars()}
        enr = set((await self.session.execute(select(Enrollment.id).where(Enrollment.course_offering_id == offering_id))).scalars())
        n = 0
        for x in entries:
            iid, eid = uuid.UUID(str(x["item_id"])), uuid.UUID(str(x["enrollment_id"]))
            if iid not in items or eid not in enr:
                raise ValidationAppError("A mark is for a part or student not in this class.")
            row = await self.session.get(ItemMark, (iid, eid))
            if x.get("obtained") is None:
                if row is not None:
                    await self.session.delete(row)
                continue
            v = float(x["obtained"])
            if v < 0 or v > float(items[iid].max_marks) + 0.001:
                raise ValidationAppError(f"Marks in {items[iid].label} must be from 0 to {float(items[iid].max_marks):g}.")
            if row is None:
                self.session.add(ItemMark(item_id=iid, enrollment_id=eid, obtained=v, entered_by=actor_id))
            else:
                row.obtained, row.entered_by, row.entered_at = v, actor_id, datetime.now(timezone.utc)
            n += 1
        await self.session.flush()
        return {"saved": n}

    # ------------------------------------------------------------------ attainment
    async def _student_clo(self, offering_ids: list[uuid.UUID]) -> tuple[dict, dict]:
        """({(student_id, clo_id): [obtained, possible]}, {clo_id: CourseOutcome}) over these classes."""
        from app.academics.models import AssessmentType, Enrollment, EnrollmentStatus, OfferingAssessment, StudentMark

        clos = {x.id: x for x in (await self.session.execute(select(CourseOutcome).where(CourseOutcome.course_offering_id.in_(offering_ids or [uuid.uuid4()])))).scalars()}
        acc: dict = defaultdict(lambda: [0.0, 0.0])
        if not clos:
            return acc, clos
        items = (await self.session.execute(select(AssessmentItem, OfferingAssessment, AssessmentType).join(
            OfferingAssessment, OfferingAssessment.id == AssessmentItem.offering_assessment_id).join(AssessmentType, AssessmentType.id == OfferingAssessment.assessment_type_id)
            .where(AssessmentItem.clo_id.in_(list(clos))))).all()
        enr = {e.id: e.student_id for e in (await self.session.execute(select(Enrollment).where(Enrollment.course_offering_id.in_(offering_ids),
                                                                                              Enrollment.status != EnrollmentStatus.WITHDRAWN))).scalars()}
        whole = [(it, a, t) for it, a, t in items if it.label is None]
        parts = [it for it, _a, _t in items if it.label is not None]
        if whole:
            marks = defaultdict(dict)
            for m in (await self.session.execute(select(StudentMark).where(StudentMark.offering_assessment_id.in_({a.id for _i, a, _t in whole})))).scalars():
                marks[m.offering_assessment_id][m.enrollment_id] = float(m.obtained_marks)
            for it, a, t in whole:
                share = float(it.max_marks) / float(t.max_marks) if float(t.max_marks) else 0
                for eid, got in marks[a.id].items():
                    if eid in enr:
                        cell = acc[(enr[eid], it.clo_id)]
                        cell[0] += got * share
                        cell[1] += float(it.max_marks)
        if parts:
            by = {i.id: i for i in parts}
            for m in (await self.session.execute(select(ItemMark).where(ItemMark.item_id.in_(list(by))))).scalars():
                if m.enrollment_id in enr:
                    cell = acc[(enr[m.enrollment_id], by[m.item_id].clo_id)]
                    cell[0] += float(m.obtained)
                    cell[1] += float(by[m.item_id].max_marks)
        return acc, clos

    async def attainment(self, offering_id: uuid.UUID) -> dict:
        from app.students.models import Student

        o, c, p = await self._offering(offering_id)
        st, bt = await self.thresholds(p.id) if p else (50.0, 60.0)
        acc, clos = await self._student_clo([offering_id])
        students = {s.id: s for s in (await self.session.execute(select(Student).where(Student.id.in_({k[0] for k in acc} or {uuid.uuid4()})))).scalars()}
        rows = []
        for clo in sorted(clos.values(), key=lambda x: (x.position, x.code)):
            pcts = [_pct(v[0], v[1]) for (sid, cid), v in acc.items() if cid == clo.id and v[1]]
            reached = sum(1 for x in pcts if x is not None and x >= st)
            share = _pct(reached, len(pcts))
            rows.append({"clo_id": clo.id, "code": clo.code, "text": clo.text, "bloom": clo.bloom, "assessed": len(pcts),
                         "average": round(sum(pcts) / len(pcts), 1) if pcts else None, "reached": reached, "batch_pct": share,
                         "attained": None if share is None else share >= bt})
        per_student = []
        for sid, s in sorted(students.items(), key=lambda kv: kv[1].roll_number or ""):
            per_student.append({"roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}",
                                "clos": {str(cid): _pct(v[0], v[1]) for (x, cid), v in acc.items() if x == sid}})
        return {"offering": {"id": o.id, "course": f"{c.code} — {c.title}", "term_label": o.term_label, "section": o.section_label},
                "student_pct": st, "batch_pct": bt, "clos": rows, "students": per_student}

    async def plo_report(self, program_id: uuid.UUID, *, term_labels: list[str] | None = None) -> dict:
        """Each PLO of a program over the classes of its students (optionally only some terms)."""
        from app.academics.models import CourseOffering, Enrollment
        from app.students.models import Student

        prog = await self.program(program_id)
        st, bt = prog["student_pct"], prog["batch_pct"]
        students = set((await self.session.execute(select(Student.id).where(Student.program_id == program_id))).scalars())
        q = select(CourseOffering.id).join(Enrollment, Enrollment.course_offering_id == CourseOffering.id).where(Enrollment.student_id.in_(students or {uuid.uuid4()}))
        if term_labels:
            q = q.where(func.lower(CourseOffering.term_label).in_([t.lower() for t in term_labels]))
        offerings = list(set((await self.session.execute(q)).scalars()))
        acc, clos = await self._student_clo(offerings)
        maps = defaultdict(list)
        for clo_id, plo_id in (await self.session.execute(select(CloPlo.clo_id, CloPlo.plo_id).where(CloPlo.clo_id.in_(list(clos) or [uuid.uuid4()])))).all():
            maps[clo_id].append(plo_id)
        per = defaultdict(lambda: [0.0, 0.0])                  # (student, plo) -> [obtained, possible]
        for (sid, cid), (got, poss) in acc.items():
            if sid not in students:
                continue
            for plo in maps.get(cid, []):
                per[(sid, plo)][0] += got
                per[(sid, plo)][1] += poss
        rows = []
        for plo in prog["plos"]:
            pcts = [_pct(v[0], v[1]) for (sid, pid), v in per.items() if pid == plo["id"] and v[1]]
            reached = sum(1 for x in pcts if x is not None and x >= st)
            share = _pct(reached, len(pcts))
            rows.append({**plo, "assessed": len(pcts), "average": round(sum(pcts) / len(pcts), 1) if pcts else None, "reached": reached,
                         "batch_pct": share, "attained": None if share is None else share >= bt})
        return {"program": prog["program"], "student_pct": st, "batch_pct": bt, "plos": rows, "classes": len(offerings)}
