"""Resource-scope checks for the students domain (Section 20).

RBAC (`require_permission`) only proves the user's role CAN view/edit
students in general. It says nothing about WHICH student. That's decided
here, using the caller's identity against the specific record.
"""
from __future__ import annotations

from app.auth.permissions import Roles
from app.auth.schemas import CurrentUser
from app.core.exceptions import AuthorizationError
from app.students.models import Student


def assert_can_view_student(current_user: CurrentUser, student: Student) -> None:
    if Roles.ADMIN in current_user.roles or Roles.ACCOUNTS_OFFICER in current_user.roles:
        return
    if Roles.PRINCIPAL in current_user.roles or Roles.HOD in current_user.roles or Roles.EXAM_CONTROLLER in current_user.roles:
        return                                  # look up, read-only (no edit permission is held)
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