from __future__ import annotations

import uuid

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.core.search import _escape_like, name_search_condition
from app.students.models import Student, StudentAddress, StudentGuardian, StudentStatus


class StudentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, student_id: uuid.UUID) -> Student | None:
        return await self.session.get(Student, student_id)

    async def get_by_cnic(self, cnic: str) -> Student | None:
        stmt = select(Student).where(Student.cnic == cnic)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_roll_number(self, roll_number: str) -> Student | None:
        stmt = select(Student).where(Student.roll_number == roll_number)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: uuid.UUID) -> Student | None:
        stmt = select(Student).where(Student.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_present_address(self, student_id: uuid.UUID) -> StudentAddress | None:
        stmt = select(StudentAddress).where(
            StudentAddress.student_id == student_id, StudentAddress.address_type == "PRESENT"
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search(
        self,
        *,
        page: PageParams,
        query: str | None = None,
        program_id: uuid.UUID | None = None,
        status: StudentStatus | None = None,
        semester: int | None = None,
        section: str | None = None,
        guardians: bool = False,
    ) -> tuple[list[Student], int]:
        stmt = select(Student)
        count_stmt = select(func.count()).select_from(Student)

        if query:
            # Exact-match first-class fields (roll number, CNIC) go through
            # index lookups; free-text name search assumes pg_trgm index in
            # production (see migration) rather than a blind LIKE '%x%'.
            matches = [
                name_search_condition(Student.first_name, Student.last_name, query),
                Student.roll_number == query,
                Student.cnic == query,
                Student.contact_no == query,
            ]
            if guardians:
                # The accounts office can also find a student by the father's (guardian's) name or CNIC: every word in
                # the guardian's name, or the CNIC typed with or without dashes. Brothers and sisters come up together.
                digits = "".join(ch for ch in query if ch.isdigit())
                cnic = f"{digits[:5]}-{digits[5:12]}-{digits[12]}" if len(digits) == 13 else query.strip()
                words = [w for w in query.split() if w]
                by_name = and_(*[StudentGuardian.name.ilike(f"%{_escape_like(w)}%", escape="\\") for w in words]) if words else false()
                matches.append(Student.id.in_(select(StudentGuardian.student_id).where(or_(by_name, StudentGuardian.cnic == cnic))))
            cond = or_(*matches)
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        if program_id:
            stmt = stmt.where(Student.program_id == program_id)
            count_stmt = count_stmt.where(Student.program_id == program_id)

        if status:
            stmt = stmt.where(Student.status == status)
            count_stmt = count_stmt.where(Student.status == status)

        if semester:
            stmt = stmt.where(Student.current_semester == semester)
            count_stmt = count_stmt.where(Student.current_semester == semester)

        if section:
            cond = func.upper(func.trim(Student.section_label)) == section.strip().upper()
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Student.last_name, Student.first_name).offset(page.offset).limit(page.page_size)
        result = await self.session.execute(stmt)
        return list(result.scalars()), total

    async def create(self, student: Student) -> Student:
        self.session.add(student)
        await self.session.flush()
        return student