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
    (Roles.ACCOUNTS_OFFICER, "/dashboard/accounts/fees"),
    (Roles.LIBRARIAN, "/dashboard/library"),
    (Roles.TEACHER, "/dashboard/faculty"),
    (Roles.STUDENT, "/dashboard/student"),
    (Roles.EXAM_CONTROLLER, "/dashboard/exam"),
    (Roles.HOSTEL_WARDEN, "/dashboard/warden"),
    (Roles.SECURITY_GUARD, "/dashboard/gate"),
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