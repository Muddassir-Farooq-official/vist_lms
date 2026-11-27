"""Seed roles + permissions from the canonical table in app/auth/permissions.py,
and optionally create a bootstrap ADMIN user.

Usage:
    python scripts/seed_permissions.py
    python scripts/seed_permissions.py --with-admin --admin-username admin --admin-password "ChangeMe123!"

Safe to re-run: uses get-or-create semantics throughout.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.access.models import RolePermissionRemoval
from app.auth.models import Permission, Role, RolePermission, User, UserRole
from app.auth.permissions import ROLE_PERMISSION_SEED, Permissions, Roles
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password


ROLE_NAMES = {
    Roles.ADMIN: "Administrator",
    Roles.TEACHER: "Teacher",
    Roles.STUDENT: "Student",
    Roles.ACCOUNTS_OFFICER: "Accounts Officer",
    Roles.LIBRARIAN: "Librarian",
    Roles.SECURITY_GUARD: "Security Guard",
    Roles.EXAM_CONTROLLER: "Exam Controller",
    Roles.HOSTEL_WARDEN: "Hostel Warden",
    Roles.STAFF: "General Staff",
    Roles.HOD: "Head of Department / Coordinator",
    Roles.PRINCIPAL: "Principal / Director",
    Roles.PARENT: "Parent / Guardian",
    Roles.CLINICAL_SUPERVISOR: "Clinical supervisor",
    Roles.ALUMNUS: "Alumni (graduate)",
}

ALL_PERMISSION_CODES = sorted({code for codes in ROLE_PERMISSION_SEED.values() for code in codes})


async def seed(with_admin: bool, admin_username: str | None, admin_password: str | None) -> None:
    async with AsyncSessionLocal() as session:
        # Permissions
        code_to_permission: dict[str, Permission] = {}
        for code in ALL_PERMISSION_CODES:
            existing = (
                await session.execute(select(Permission).where(Permission.code == code))
            ).scalar_one_or_none()
            if existing is None:
                existing = Permission(code=code)
                session.add(existing)
                await session.flush()
                print(f"  + permission {code}")
            code_to_permission[code] = existing

        # Roles
        code_to_role: dict[str, Role] = {}
        for role_code, name in ROLE_NAMES.items():
            existing = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one_or_none()
            if existing is None:
                existing = Role(code=role_code, name=name)
                session.add(existing)
                await session.flush()
                print(f"  + role {role_code}")
            code_to_role[role_code] = existing

        # Role -> Permission mapping. A right an administrator took away from a built-in role (Settings -> Access
        # control) is left out: a deploy must never quietly give it back. New rights added by an update still arrive.
        removed = {(r, p) for r, p in (await session.execute(select(RolePermissionRemoval.role_id, RolePermissionRemoval.permission_id))).all()}
        for role_code, perm_codes in ROLE_PERMISSION_SEED.items():
            role = code_to_role[role_code]
            for perm_code in perm_codes:
                permission = code_to_permission[perm_code]
                if (role.id, permission.id) in removed:
                    continue
                exists = (
                    await session.execute(
                        select(RolePermission).where(
                            RolePermission.role_id == role.id, RolePermission.permission_id == permission.id
                        )
                    )
                ).scalar_one_or_none()
                if exists is None:
                    session.add(RolePermission(role_id=role.id, permission_id=permission.id))

        await session.commit()
        print("Roles and permissions seeded.")

        if with_admin:
            if not admin_username or not admin_password:
                raise SystemExit("--with-admin requires --admin-username and --admin-password")

            existing_user = (
                await session.execute(select(User).where(User.username == admin_username))
            ).scalar_one_or_none()
            if existing_user is not None:
                print(f"Admin user '{admin_username}' already exists — skipping.")
                return

            admin_user = User(
                username=admin_username,
                password_hash=hash_password(admin_password),
                is_active=True,
                must_change_password=True,
            )
            session.add(admin_user)
            await session.flush()
            session.add(UserRole(user_id=admin_user.id, role_id=code_to_role[Roles.ADMIN].id))
            await session.commit()
            print(f"Bootstrap admin '{admin_username}' created. They must change their password on first login.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-admin", action="store_true")
    parser.add_argument("--admin-username", default=None)
    parser.add_argument("--admin-password", default=None)
    # The password can also come from an environment variable, so it never appears on a command line (ps, shell history).
    parser.add_argument("--admin-password-env", default=None, help="name of an environment variable holding the password")
    args = parser.parse_args()
    password = args.admin_password or (os.environ.get(args.admin_password_env) if args.admin_password_env else None)

    asyncio.run(seed(args.with_admin, args.admin_username, password))