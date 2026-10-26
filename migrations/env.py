from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.core.database import Base

# Import every domain's models so Base.metadata is fully populated before
# autogenerate runs. Add new domains here as they're built.
from app.auth import models as auth_models  # noqa: F401
from app.audit import models as audit_models  # noqa: F401
from app.academics import models as academics_models  # noqa: F401
from app.attendance import models as attendance_models  # noqa: F401
from app.core import sequence_models as core_sequence_models  # noqa: F401
from app.employees import models as employees_models  # noqa: F401
from app.notifications import models as notifications_models  # noqa: F401
from app.profiles import models as profiles_models  # noqa: F401
from app.fees import models as fees_models  # noqa: F401
from app.fees import payment_models as fees_payment_models  # noqa: F401
from app.announcements import models as announcements_models  # noqa: F401
from app.applications import models as applications_models  # noqa: F401
from app.tasks import models as tasks_models  # noqa: F401
from app.gate import models as gate_models  # noqa: F401
from app.accounts import models as accounts_models  # noqa: F401
from app.purchasing import models as purchasing_models  # noqa: F401
from app.payroll import models as payroll_models  # noqa: F401
from app.assignments import models as assignments_models  # noqa: F401
from app.complaints import models as complaints_models  # noqa: F401
from app.fines import models as fines_models  # noqa: F401
from app.library import models as library_models  # noqa: F401
from app.programs import models as programs_models  # noqa: F401
from app.students import models as students_models  # noqa: F401
from app.imports import models as imports_models  # noqa: F401
from app.policy import models as policy_models  # noqa: F401
from app.parents import models as parents_models  # noqa: F401
from app.exams import models as exams_models  # noqa: F401
from app.sms import models as sms_models  # noqa: F401
from app.supplementary import models as supplementary_models  # noqa: F401
from app.admissions import models as admissions_models  # noqa: F401
from app.inventory import models as inventory_models  # noqa: F401
from app.assets import models as assets_models  # noqa: F401
from app.approvals import models as approvals_models  # noqa: F401
from app.branding import models as branding_models  # noqa: F401
from app.selfservice import models as selfservice_models  # noqa: F401
from app.timekeeping import models as timekeeping_models  # noqa: F401
from app.calendar import models as calendar_models  # noqa: F401
from app.records import models as records_models  # noqa: F401
from app.hostel import models as hostel_models  # noqa: F401
from app.fees import refund_models as fees_refund_models  # noqa: F401
from app.academics import terms as academics_terms  # noqa: F401

config = context.config
# ALEMBIC_DATABASE_URL lets CI/tests migrate a scratch database without touching .env
import os as _os
# MIGRATION_DATABASE_URL: the table owner's login (scripts/db_roles.sql); the app itself runs with a login that cannot change tables.
config.set_main_option("sqlalchemy.url", (_os.environ.get("ALEMBIC_DATABASE_URL") or _os.environ.get("MIGRATION_DATABASE_URL") or settings.database_url).replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# uq_programs_name_lower is a functional index, lower(trim(name)), created with raw SQL.
# Alembic cannot compare functional indexes, so it always reports it as changed. Skip it.
_SKIP_INDEXES = {"uq_programs_name_lower"}


def _include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    return not (type_ == "index" and name in _SKIP_INDEXES)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=_include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
        include_object=_include_object, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())