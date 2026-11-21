"""Academic sessions: "Fall 2026" groups the batch terms that run at the same time ("BSN 6th Fall 2026",
"BSN 4th Fall 2026", "MLT 2nd Fall 2026" ...).

An institute may run one term per batch (each batch has its own dates, and fees, attendance and results stay per
batch). A session groups them so a person can pick "Fall 2026" once. It changes no record: offerings, fees,
attendance and results keep their batch term exactly as before.

  suggest()      proposes sessions from the term names ("... Fall 2026", "... 2026-27") and, for terms with no batch,
                 the batch (program + semester) from names like "BSN 6th"; the administrator checks and saves
  groups()       sessions with their term labels, current session first (term pickers and dropdowns use it)
  next_preview() / start_next(): for every batch of a session, the next semester's batch term in a new session, its
                 course sections from the curriculum (same sections as now) and the students already promoted to that
                 semester enrolled -- previewed first, then done in one step (all or nothing)
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.terms import AcademicSession, AcademicTerm, AcademicTermService, TermStatus, _norm
from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError

SEASON = re.compile(r"\b(fall|spring|summer|winter|autumn)[\s\-_]*((?:19|20)\d{2})\b", re.I)
YEARS = re.compile(r"\b((?:19|20)\d{2})\s*[-/]\s*((?:19|20)?\d{2})\b")
ORDINAL = re.compile(r"\b(\d{1,2})\s*(st|nd|rd|th)\b", re.I)
SEMWORD = re.compile(r"\b(?:sem(?:ester)?|year)[\s\-.]*(\d{1,2})\b", re.I)


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def session_name_of(label: str) -> str | None:
    m = SEASON.search(label or "")
    if m:
        return f"{m.group(1).title()} {m.group(2)}"
    m = YEARS.search(label or "")
    if m:
        a, b = m.group(1), m.group(2)
        return f"{a}-{b[-2:]}"
    return None


def semester_of(label: str) -> int | None:
    m = ORDINAL.search(label or "") or SEMWORD.search(label or "")
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 20 else None
    return None


def next_label(label: str, old_session: str, new_session: str, sem: int, program_code: str) -> str:
    """'BSN 6th Fall 2026' -> 'BSN 7th Spring 2027'. A name without both parts is built as 'BSN 7th Spring 2027'."""
    out = label
    found_session = re.search(re.escape(old_session), out, re.I) if old_session else None
    found_sem = re.search(rf"\b{sem}\s*(st|nd|rd|th)\b", out, re.I)
    if found_session and found_sem:
        out = out[:found_session.start()] + new_session + out[found_session.end():]
        found_sem = re.search(rf"\b{sem}\s*(st|nd|rd|th)\b", out, re.I)
        out = out[:found_sem.start()] + ordinal(sem + 1) + out[found_sem.end():]
        return " ".join(out.split())
    return f"{program_code} {ordinal(sem + 1)} {new_session}"


def _running(start: date | None, end: date | None, on: date) -> bool:
    return (start is None or start <= on) and (end is None or on <= end)


class SessionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ reading
    async def _sessions(self) -> list[AcademicSession]:
        return list((await self.session.execute(select(AcademicSession))).scalars())

    async def _terms(self) -> list[AcademicTerm]:
        return list((await self.session.execute(select(AcademicTerm))).scalars())

    @staticmethod
    def _span(s: AcademicSession, terms: list[AcademicTerm]) -> tuple[date | None, date | None]:
        starts = [t.start_date for t in terms if t.start_date] + ([s.start_date] if s.start_date else [])
        ends = [t.end_date for t in terms if t.end_date] + ([s.end_date] if s.end_date else [])
        return (s.start_date or (min(starts) if starts else None), s.end_date or (max(ends) if ends else None))

    async def groups(self) -> dict:
        """Sessions in the order people want them: the one running today first, then coming ones, then past ones
        (newest first). Each with its term labels (A-Z). Terms in no session are listed apart."""
        from app.core.clock import institute_today

        today = institute_today()
        sessions = await self._sessions()
        terms = await self._terms()
        by = {s.id: [] for s in sessions}
        loose = []
        for t in terms:
            (by[t.session_id] if t.session_id in by else loose).append(t)
        rows = []
        for s in sessions:
            start, end = self._span(s, by[s.id])
            when = "current" if _running(start, end, today) else "coming" if start and start > today else "past"
            rows.append({"id": s.id, "name": s.name, "start_date": start, "end_date": end, "when": when,
                         "terms": sorted((t.label for t in by[s.id]), key=str.lower),
                         "term_details": [{"id": t.id, "label": t.label, "program_id": t.program_id, "semester": t.semester,
                                           "start_date": t.start_date, "end_date": t.end_date, "status": t.status.value}
                                          for t in sorted(by[s.id], key=lambda x: x.label.lower())]})
        order = {"current": 0, "coming": 1, "past": 2}
        rows.sort(key=lambda r: (order[r["when"]], -(r["start_date"] or date.min).toordinal() if r["when"] != "coming"
                                 else (r["start_date"] or date.max).toordinal(), r["name"]))
        return {"sessions": rows, "ungrouped": sorted((t.label for t in loose), key=str.lower)}

    # ------------------------------------------------------------------ suggestions from the names
    async def suggest(self) -> dict:
        from app.programs.models import Program

        terms = [t for t in await self._terms() if t.session_id is None]
        sessions = {_norm(s.name): s for s in await self._sessions()}
        programs = list((await self.session.execute(select(Program))).scalars())
        out: dict[str, dict] = {}
        left = []
        for t in sorted(terms, key=lambda x: x.label.lower()):
            name = session_name_of(t.label)
            guess = None
            if t.program_id is None:
                sem = semester_of(t.label)
                hits = [p for p in programs if re.search(rf"\b{re.escape(p.code)}\b", t.label, re.I)
                        or (p.name and p.name.lower() in t.label.lower())]
                hits.sort(key=lambda p: -len(p.code or ""))
                if sem and hits and (not hits[0].total_semesters or sem <= hits[0].total_semesters):
                    guess = {"program_id": hits[0].id, "program": hits[0].code, "semester": sem}
            item = {"id": t.id, "label": t.label, "start_date": t.start_date, "end_date": t.end_date, "batch_guess": guess,
                    "program_id": t.program_id, "semester": t.semester}
            if name is None:
                left.append(item)
                continue
            row = out.setdefault(_norm(name), {"name": name, "existing_session_id": sessions[_norm(name)].id if _norm(name) in sessions else None,
                                               "terms": []})
            row["terms"].append(item)
        # a term whose name says no session but whose dates mostly fall in one: offered there, unticked ("by dates")
        still = []
        for item in left:
            best, best_days = None, 0
            if item["start_date"] and item["end_date"]:
                length = (item["end_date"] - item["start_date"]).days + 1
                for row in out.values():
                    starts = [x["start_date"] for x in row["terms"] if x["start_date"]]
                    ends = [x["end_date"] for x in row["terms"] if x["end_date"]]
                    if not starts or not ends:
                        continue
                    days = (min(max(ends), item["end_date"]) - max(min(starts), item["start_date"])).days + 1
                    if days > best_days and days * 2 >= length:
                        best, best_days = row, days
            if best is None:
                still.append(item)
            else:
                best["terms"].append({**item, "by_dates": True})
        return {"suggestions": sorted(out.values(), key=lambda r: r["name"]), "not_matched": still}

    async def save_suggestions(self, items: list[dict], *, actor_id: uuid.UUID) -> dict:
        """items: [{name, term_ids, batches: {term_id: {program_id, semester}}}]. Creates or reuses each session by name."""
        tsvc = AcademicTermService(self.session)
        made, linked, batches = 0, 0, 0
        async with self.session.begin_nested():
            for it in items:
                name = " ".join((it.get("name") or "").split())
                if not name:
                    raise ValidationAppError("Each session needs a name.")
                s = await self._by_name(name)
                if s is None:
                    s = AcademicSession(name=name, created_by=actor_id)
                    self.session.add(s)
                    await self.session.flush()
                    made += 1
                for tid in it.get("term_ids") or []:
                    t = await self.session.get(AcademicTerm, tid)
                    if t is None:
                        raise ResourceNotFoundError("Term not found.")
                    t.session_id = s.id
                    linked += 1
                for tid, b in (it.get("batches") or {}).items():
                    t = await self.session.get(AcademicTerm, uuid.UUID(str(tid)))
                    if t is not None and t.program_id is None and b and b.get("program_id") and b.get("semester"):
                        await tsvc.update(t.id, start_date=None, end_date=None, status=None, actor_id=actor_id,
                                          batch=(uuid.UUID(str(b["program_id"])), int(b["semester"])))
                        batches += 1
            await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="academic_session.grouped", entity_type="academic_session", entity_id=None,
                             after={"sessions_created": made, "terms_linked": linked, "batches_set": batches,
                                    "sessions": [i.get("name") for i in items]})
        return {"sessions_created": made, "terms_linked": linked, "batches_set": batches}

    # ------------------------------------------------------------------ editing
    async def _by_name(self, name: str) -> AcademicSession | None:
        return next((s for s in await self._sessions() if _norm(s.name) == _norm(name)), None)

    async def create(self, *, name: str, start_date: date | None, end_date: date | None, actor_id: uuid.UUID) -> AcademicSession:
        name = " ".join((name or "").split())
        if not name:
            raise ValidationAppError("Give the session a name, for example Fall 2026.")
        if await self._by_name(name):
            raise DuplicateResourceError(f'The session "{name}" already exists.')
        if start_date and end_date and end_date <= start_date:
            raise ValidationAppError("The session must end after it starts.")
        s = AcademicSession(name=name, start_date=start_date, end_date=end_date, created_by=actor_id)
        self.session.add(s)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="academic_session.created", entity_type="academic_session", entity_id=s.id,
                             after={"name": name, "start": str(start_date), "end": str(end_date)})
        return s

    async def update(self, session_id: uuid.UUID, *, name: str | None, start_date: date | None, end_date: date | None,
                     term_ids: list[uuid.UUID] | None, actor_id: uuid.UUID) -> AcademicSession:
        s = await self.session.get(AcademicSession, session_id)
        if s is None:
            raise ResourceNotFoundError("Session not found.")
        before = {"name": s.name, "start": str(s.start_date), "end": str(s.end_date)}
        if name is not None:
            name = " ".join(name.split())
            if not name:
                raise ValidationAppError("Give the session a name.")
            other = await self._by_name(name)
            if other is not None and other.id != s.id:
                raise DuplicateResourceError(f'The session "{name}" already exists.')
            s.name = name
        if start_date and end_date and end_date <= start_date:
            raise ValidationAppError("The session must end after it starts.")
        s.start_date, s.end_date = start_date, end_date
        if term_ids is not None:
            for t in await self._terms():
                if t.id in set(term_ids):
                    t.session_id = s.id
                elif t.session_id == s.id:
                    t.session_id = None
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="academic_session.updated", entity_type="academic_session", entity_id=s.id,
                             before=before, after={"name": s.name, "start": str(s.start_date), "end": str(s.end_date),
                                                   "terms": len(term_ids) if term_ids is not None else None})
        return s

    async def delete(self, session_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        """Only the grouping goes: its terms stay, in no session."""
        s = await self.session.get(AcademicSession, session_id)
        if s is None:
            raise ResourceNotFoundError("Session not found.")
        for t in await self._terms():
            if t.session_id == s.id:
                t.session_id = None
        await self.session.delete(s)
        await self.audit.log(actor_user_id=actor_id, action="academic_session.deleted", entity_type="academic_session", entity_id=session_id,
                             before={"name": s.name})

    async def labels_of(self, session_ids: list[uuid.UUID]) -> list[str]:
        rows = (await self.session.execute(select(AcademicTerm.label).where(AcademicTerm.session_id.in_(session_ids)))).scalars()
        return sorted(rows, key=str.lower)

    # ------------------------------------------------------------------ the next session
    async def _plan(self, session_id: uuid.UUID, new_name: str, labels: dict) -> dict:
        from app.academics.models import Course, CourseOffering, CourseProgram
        from app.programs.models import Program
        from app.students.models import Student, StudentStatus

        src = await self.session.get(AcademicSession, session_id)
        if src is None:
            raise ResourceNotFoundError("Session not found.")
        new_name = " ".join((new_name or "").split())
        if not new_name:
            raise ValidationAppError("Give the new session a name, for example Spring 2027.")
        if _norm(new_name) == _norm(src.name):
            raise ValidationAppError("The new session needs a different name.")
        terms = sorted([t for t in await self._terms() if t.session_id == src.id], key=lambda t: t.label.lower())
        all_terms = {_norm(t.label): t for t in await self._terms()}
        rows = []
        for t in terms:
            row = {"term_id": t.id, "label": t.label, "program_id": t.program_id, "semester": t.semester}
            if t.program_id is None or t.semester is None:
                rows.append({**row, "status": "NO_BATCH", "note": "Set this term's batch (program + semester) first, on Academic terms."})
                continue
            program = await self.session.get(Program, t.program_id)
            row["program"] = program.code if program else "?"
            if program and program.total_semesters and t.semester >= program.total_semesters:
                rows.append({**row, "status": "LAST", "note": f"Semester {t.semester} is {program.code}'s last: this batch finishes, nothing to start."})
                continue
            nxt = t.semester + 1
            new_label = " ".join((labels.get(str(t.id)) or next_label(t.label, src.name, new_name, t.semester, row["program"])).split())
            existing = all_terms.get(_norm(new_label))
            status = "NEW"
            if existing is not None:
                status = "EXISTS" if existing.program_id == t.program_id and existing.semester == nxt else "NAME_TAKEN"
            sections = sorted({(s or "A") for s in (await self.session.execute(select(CourseOffering.section_label).where(
                CourseOffering.term_label == t.label))).scalars()}, key=str.lower) or ["A"]
            courses = [c for c, in (await self.session.execute(select(Course.code).join(CourseProgram, CourseProgram.course_id == Course.id).where(
                CourseProgram.program_id == t.program_id, CourseProgram.semester_number == nxt, Course.is_active.is_(True)).order_by(Course.code))).all()]
            counts = dict((await self.session.execute(select(Student.current_semester, func.count()).where(
                Student.program_id == t.program_id, Student.status == StudentStatus.ACTIVE, Student.current_semester.in_([t.semester, nxt]))
                .group_by(Student.current_semester))).all())
            note = None
            if status == "NAME_TAKEN":
                note = f'A different term is already called "{new_label}". Change the name.'
            elif not courses:
                note = f"No courses are linked to {row['program']} semester {nxt}: the term is created, link the curriculum to open its sections."
            elif not counts.get(nxt):
                note = "No students have been promoted to this semester yet: sections are opened empty; enroll them after promoting."
            rows.append({**row, "status": status, "new_label": new_label, "new_semester": nxt, "sections": sections, "courses": courses,
                         "promoted": int(counts.get(nxt, 0)), "not_promoted": int(counts.get(t.semester, 0)), "note": note})
        return {"from": src, "new_name": new_name, "rows": rows}

    @staticmethod
    def _public(plan: dict) -> dict:
        return {"from_session": plan["from"].name, "new_session": plan["new_name"], "rows": plan["rows"],
                "will_create_terms": sum(r["status"] == "NEW" for r in plan["rows"]),
                "will_open_sections": sum(len(r.get("courses", [])) * len(r.get("sections", [])) for r in plan["rows"] if r["status"] in ("NEW", "EXISTS")),
                "will_enroll_up_to": sum(r.get("promoted", 0) * len(r.get("courses", [])) for r in plan["rows"] if r["status"] in ("NEW", "EXISTS")),
                "blocked": [r for r in plan["rows"] if r["status"] == "NAME_TAKEN"]}

    async def next_preview(self, session_id: uuid.UUID, *, new_name: str, labels: dict | None = None) -> dict:
        return self._public(await self._plan(session_id, new_name, labels or {}))

    async def start_next(self, session_id: uuid.UUID, *, new_name: str, start_date: date, end_date: date, labels: dict | None,
                         enroll: bool, actor_id: uuid.UUID) -> dict:
        from app.academics.service import CourseOfferingService, EnrollmentService

        if not start_date or not end_date or end_date <= start_date:
            raise ValidationAppError("Give the new session's start and end dates (end after start).")
        plan = await self._plan(session_id, new_name, labels or {})
        if any(r["status"] == "NAME_TAKEN" for r in plan["rows"]):
            raise BusinessRuleViolationError("Some new term names are taken by other terms. Change them in the preview first.")
        todo = [r for r in plan["rows"] if r["status"] in ("NEW", "EXISTS")]
        if not todo:
            raise BusinessRuleViolationError("No batch in this session has a next semester to start.")
        tsvc = AcademicTermService(self.session)
        osvc = CourseOfferingService(self.session)
        esvc = EnrollmentService(self.session)
        made_terms, opened, enrolled, failed = 0, 0, 0, []
        async with self.session.begin_nested():                 # all or nothing
            target = await self._by_name(plan["new_name"])
            if target is None:
                target = AcademicSession(name=plan["new_name"], start_date=start_date, end_date=end_date, created_by=actor_id)
                self.session.add(target)
                await self.session.flush()
            for r in todo:
                term = await tsvc.by_label(r["new_label"])
                if term is None:
                    term = await tsvc.create(label=r["new_label"], start_date=start_date, end_date=end_date, actor_id=actor_id,
                                             program_id=r["program_id"], semester=r["new_semester"])
                    made_terms += 1
                if term.status == TermStatus.CLOSED:
                    raise BusinessRuleViolationError(f'The term "{term.label}" is closed. Reopen it or choose another name.')
                term.session_id = target.id
                if not r["courses"]:
                    continue
                for sec in r["sections"]:
                    res = await osvc.generate_for_program_semester(term_label=term.label, program_id=r["program_id"], semester_number=r["new_semester"],
                                                                   section_label=sec, term_start_date=term.start_date or start_date,
                                                                   term_end_date=term.end_date or end_date, actor_id=actor_id)
                    opened += len(res["created"])
                    if enroll and r["promoted"]:
                        from app.academics.models import CourseOffering

                        offs = (await self.session.execute(select(CourseOffering).where(CourseOffering.term_label == term.label,
                                                                                       CourseOffering.section_label == sec))).scalars().all()
                        for o in offs:
                            # one section: every promoted student of the batch (some have no section written on them)
                            out = await esvc.enroll_many(course_offering_id=o.id, actor_id=actor_id, program_id=r["program_id"],
                                                         semester_number=r["new_semester"], section_label=sec if len(r["sections"]) > 1 else None)
                            enrolled += int(out.get("enrolled", 0))
                            failed += [{"term": term.label, "section": sec, **f} for f in (out.get("failed") or [])][:20]
            await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="academic_session.started_next", entity_type="academic_session", entity_id=target.id,
                             after={"from": plan["from"].name, "to": plan["new_name"], "terms_created": made_terms, "sections_opened": opened,
                                    "enrollments": enrolled, "enroll_failed": len(failed)})
        return {"session_id": target.id, "session": plan["new_name"], "terms_created": made_terms, "sections_opened": opened,
                "enrolled": enrolled, "enroll_failed": failed[:50]}
