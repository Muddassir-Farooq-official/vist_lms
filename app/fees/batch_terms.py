"""Which term a fee charge shows, when batches run on their own dates.

The institute registers one term per batch ("Fall 2026 BSN 8TH" = BSN, semester 8). A fee that covers one batch carries
that batch's term. A fee that covers several batches carries ALL_BATCHES instead, and each student's charge is stamped
with the student's own batch term when they are charged (fee_assignments.term_label), so an 8th-semester student sees
"Fall 2026 BSN 8TH" and a 6th-semester student sees "Fall 2026 BSN 6TH" on the same fee. The stamp does not move when
the student is later promoted: it is the term the charge was for.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.terms import ALL_BATCHES, AcademicTerm, AcademicTermService
from app.core.exceptions import BusinessRuleViolationError


def shown_term(assignment, structure) -> str | None:
    """The term to print for one charge: its own stamp, else the fee's term (never the words "All batches")."""
    label = getattr(assignment, "term_label", None) if assignment is not None else None
    if label:
        return label
    if structure is None or structure.term_label == ALL_BATCHES:
        return None
    return structure.term_label


async def term_for_new_charge(session: AsyncSession, assignment) -> str | None:
    """The batch term to stamp on a new charge of an ALL_BATCHES fee (None for any other fee, or when unknown)."""
    from app.fees.models import FeeStructure, PayerType

    if assignment.term_label or assignment.payer_type != PayerType.STUDENT:
        return None
    structure = await session.get(FeeStructure, assignment.fee_structure_id)
    if structure is None or structure.term_label != ALL_BATCHES:
        return None
    if assignment.enrollment_id is not None:          # a subject fee: the term of the class the student enrolled in
        from app.academics.models import CourseOffering, Enrollment

        enrollment = await session.get(Enrollment, assignment.enrollment_id)
        offering = await session.get(CourseOffering, enrollment.course_offering_id) if enrollment else None
        return offering.term_label if offering else None
    term = await student_batch_term(session, assignment.payer_id)
    return term.label if term else None


async def student_batch_term(session: AsyncSession, student_id: uuid.UUID) -> AcademicTerm | None:
    from app.students.models import Student

    student = await session.get(Student, student_id)
    if student is None:
        return None
    return await AcademicTermService(session).batch_term(student.program_id, student.current_semester)


async def check_term_fits(session: AsyncSession, term_label: str, *, program_ids: list[uuid.UUID], semester_numbers: list[int],
                          course_id: uuid.UUID | None, applies_to_employees: bool = False) -> None:
    """A batch's own term may only go on a fee for that batch. Refuses e.g. "Fall 2026 BSN 6TH" on a fee for every
    program, or for semesters 1-8, and points to "Each student's own term" instead."""
    if term_label.strip() == ALL_BATCHES or course_id is not None:
        return
    term = await AcademicTermService(session).by_label(term_label.strip())
    if term is None or term.program_id is None:
        return
    from app.programs.models import Program

    program = await session.get(Program, term.program_id)
    code = program.code if program else "its program"
    if applies_to_employees or program_ids != [term.program_id] or sorted(set(semester_numbers)) != [term.semester]:
        raise BusinessRuleViolationError(
            f'"{term.label}" is the term of one batch ({code}, semester {term.semester}), but this fee is for '
            + _describe(program_ids, semester_numbers, applies_to_employees, term.program_id, code)
            + f'. Choose "Each student\'s own term" so every student sees their own batch term, or charge only {code} semester {term.semester}.',
            details={"term_label": term.label, "code": "TERM_IS_ONE_BATCH"})


def _describe(program_ids: list, semester_numbers: list[int], employees: bool, own_program, code: str) -> str:
    if not program_ids:
        first = "all programs"
    elif program_ids == [own_program]:
        first = code
    else:
        first = f"{len(program_ids)} programs" if len(program_ids) > 1 else "another program"
    parts = [first]
    parts.append("every semester" if not semester_numbers else "semester " + ", ".join(str(n) for n in sorted(set(semester_numbers))))
    if employees:
        parts.append("employees")
    return " / ".join(parts)
