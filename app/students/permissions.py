"""Resource-scope checks for the students domain (Section 20).

RBAC (`require_permission`) only proves the user's role CAN view/edit
students in general. It says nothing about WHICH student. That's decided
here, using the caller's identity against the specific record.
"""
from __future__ import annotations

from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.core.exceptions import AuthorizationError
from app.students.models import Student


def assert_can_view_student(current_user: CurrentUser, student: Student) -> None:
    # The offices (Administrator, Accounts, Principal, HOD, Exam Controller by default, or anyone given "See any
    # student's record" on Access control) look any student up; editing still needs student.update.
    if Permissions.STUDENT_LOOKUP in current_user.permissions or Roles.ADMIN in current_user.roles:
        return
    if Roles.STUDENT in current_user.roles:
        if student.user_id != current_user.id:
            raise AuthorizationError("You may only view your own student record.")
        return
    if Roles.TEACHER in current_user.roles:
        # Full section-scoping lands with the academics/course_sections module
        # (Phase B). Until then, teachers get no student-detail access beyond
        # what's explicitly wired through an assigned-section endpoint.
        raise AuthorizationError("Teacher access to student records is scoped by course section.")
    raise AuthorizationError("You do not have access to this student record.")