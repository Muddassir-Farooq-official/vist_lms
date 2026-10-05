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

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
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