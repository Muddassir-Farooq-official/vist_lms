"""One place that answers: "after signing in, which page does this person land on?"

Previously the answer was hard-coded in three separate JavaScript files and
depended on which role card the user had clicked BEFORE signing in. The server
knows the user's roles, so it decides -- one sign-in form for everyone.
"""
from __future__ import annotations

from app.auth.permissions import Roles

CHANGE_PASSWORD_PATH = "/change-password"

# First match wins. A person holding several roles lands on the most
# privileged portal they have.
PORTAL_BY_ROLE: tuple[tuple[str, str], ...] = (
    (Roles.ADMIN, "/dashboard/admin"),
    (Roles.ACCOUNTS_OFFICER, "/dashboard/accounts"),
    (Roles.LIBRARIAN, "/dashboard/library"),
    (Roles.PRINCIPAL, "/dashboard/principal"),
    (Roles.HOD, "/dashboard/hod"),
    (Roles.TEACHER, "/dashboard/faculty"),
    (Roles.STUDENT, "/dashboard/student"),
    (Roles.PARENT, "/dashboard/parent"),
    (Roles.EXAM_CONTROLLER, "/dashboard/exam/home"),
    (Roles.HOSTEL_WARDEN, "/dashboard/warden/home"),
    (Roles.SECURITY_GUARD, "/dashboard/gate/home"),
    (Roles.STAFF, "/dashboard/staff"),
)


def home_for_roles(roles: list[str] | tuple[str, ...], must_change_password: bool = False) -> str | None:
    """The landing path for these roles, or None if none of them has a portal
    yet (e.g. HOSTEL_WARDEN). A pending forced password change always wins."""
    if must_change_password:
        return CHANGE_PASSWORD_PATH
    held = set(roles)
    for role, path in PORTAL_BY_ROLE:
        if role in held:
            return path
    return None

# "My Profile" for each kind of person (a notification about a profile change links here through /dashboard/my-profile).
PROFILE_BY_ROLE: tuple[tuple[str, str], ...] = (
    (Roles.STUDENT, "/dashboard/student/profile"),
    (Roles.TEACHER, "/dashboard/faculty/profile"),
    (Roles.ACCOUNTS_OFFICER, "/dashboard/accounts/profile"),
    (Roles.LIBRARIAN, "/dashboard/library/profile"),
    (Roles.EXAM_CONTROLLER, "/dashboard/exam/profile"),
    (Roles.HOSTEL_WARDEN, "/dashboard/warden/profile"),
    (Roles.SECURITY_GUARD, "/dashboard/gate/profile"),
    (Roles.STAFF, "/dashboard/staff/profile"),
)


def profile_for_roles(roles: list[str] | tuple[str, ...]) -> str | None:
    held = set(roles)
    for role, path in PROFILE_BY_ROLE:
        if role in held:
            return path
    return None
