from __future__ import annotations

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit import models as _audit_models  # noqa: F401
from app.academics import models as _academics_models  # noqa: F401
from app.auth.models import LoginAttempt, PasswordResetToken, Permission, RefreshToken, Role, RolePermission, User, UserRole
from app.attendance import models as _attendance_models  # noqa: F401
from app.auth.permissions import ROLE_PERMISSION_SEED, Roles
from app.core import database as db_module
from app.core import sequence_models as _sequence_models  # noqa: F401
from app.core.config import settings
from app.core.security import hash_password
from app.employees import models as _employees_models  # noqa: F401
from app.notifications import models as _notifications_models  # noqa: F401
from app.profiles import models as _profiles_models  # noqa: F401
from app.fees import models as _fees_models  # noqa: F401
from app.fees import payment_models as _fees_payment_models  # noqa: F401
from app.announcements import models as _announcements_models  # noqa: F401
from app.applications import models as _applications_models  # noqa: F401
from app.tasks import models as _tasks_models  # noqa: F401
from app.gate import models as _gate_models  # noqa: F401
from app.accounts import models as _accounts_models  # noqa: F401
from app.purchasing import models as _purchasing_models  # noqa: F401
from app.payroll import models as _payroll_models  # noqa: F401
from app.assignments import models as _assignments_models  # noqa: F401
from app.complaints import models as _complaints_models  # noqa: F401
from app.fines import models as _fines_models  # noqa: F401
from app.imports import models as _imports_models  # noqa: F401
from app.library import models as _library_models  # noqa: F401
from app.programs import models as _programs_models  # noqa: F401
from app.policy import models as _policy_models  # noqa: F401
from app.academics import terms as _terms_models  # noqa: F401
from app.exams import models as _exam_models  # noqa: F401
from app.sms import models as _sms_models  # noqa: F401
from app.supplementary import models as _supp_models  # noqa: F401
from app.fees import refund_models as _refund_models  # noqa: F401
from app.admissions import models as _admission_models  # noqa: F401
from app.inventory import models as _inventory_models  # noqa: F401
from app.assets import models as _assets_models  # noqa: F401
from app.approvals import models as _approvals_models  # noqa: F401
from app.branding import models as _branding_models  # noqa: F401
from app.selfservice import models as _selfservice_models  # noqa: F401
from app.timekeeping import models as _timekeeping_models  # noqa: F401
from app.parents import models as _parents_models  # noqa: F401
from app.students import models as _students_models  # noqa: F401
from app.hostel import models as _hostel_models  # noqa: F401
from app.records import models as _records_models  # noqa: F401
from app.calendar import models as _calendar_models  # noqa: F401
from app.security import models as _security_models  # noqa: F401
from app.todos import models as _todos_models  # noqa: F401
from app.banners import models as _banners_models  # noqa: F401
from app.push import models as _push_models  # noqa: F401

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/vist_lms_test"


def _refuse_unsafe_test_database() -> None:
    """The tests create and wipe a database called vist_lms_test on the SAME server as DATABASE_URL.
    Never do that next to the real data: not with APP_ENV=production, and not on a remote server
    unless ALLOW_REMOTE_TEST_DB=1 is set on purpose."""
    import os
    from urllib.parse import urlsplit

    host = (urlsplit(settings.database_url.replace("+asyncpg", "")).hostname or "").lower()
    if settings.is_production:
        pytest.exit("Refusing to run the tests: APP_ENV is production. Run them on a development machine.", returncode=2)
    if host not in ("localhost", "127.0.0.1", "::1", "postgres", "db") and os.environ.get("ALLOW_REMOTE_TEST_DB") != "1":
        pytest.exit(f"Refusing to run the tests against the database server '{host}': they create and wipe vist_lms_test there. "
                    "Use a local PostgreSQL for tests (or set ALLOW_REMOTE_TEST_DB=1 if this server holds no real data).", returncode=2)


_refuse_unsafe_test_database()


@pytest_asyncio.fixture(autouse=True)
async def _flush_redis():
    """Rate-limit counters live in real Redis and would otherwise leak
    state between tests. get_redis() is process-wide lru_cache'd, which
    binds its connection to whatever event loop created it — since each
    test gets its own event loop, the cache must be cleared every test or
    later tests reuse a connection tied to an already-closed loop."""
    from app.core.redis import get_redis

    get_redis.cache_clear()
    client = get_redis()
    await client.flushdb()
    yield
    await client.aclose()
    get_redis.cache_clear()


@pytest.fixture(autouse=True)
def _reset_policy_overrides():
    """Policy values saved through the API live on the shared settings object; undo them after each test."""
    yield
    from app.policy.service import reset_for_tests

    reset_for_tests()


@pytest.fixture(autouse=True)
def _legacy_launch_guards(monkeypatch, request):
    """The suite predates the Step 2 launch guards and exercises the original
    mechanics (publishing straight after approval, locking mid-term, login
    block on attendance fines). Run it with those guards off; tests marked
    `launch_guards` keep the real (production) defaults."""
    if request.node.get_closest_marker("launch_guards"):
        return
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "require_attendance_lock_before_publish", False)
    monkeypatch.setattr(_settings, "block_early_attendance_lock", False)
    monkeypatch.setattr(_settings, "attendance_fine_blocks_login", True)
    monkeypatch.setattr(_settings, "require_registered_terms", False)
    monkeypatch.setattr(_settings, "require_results_before_end_semester", False)
    monkeypatch.setattr(_settings, "withdrawal_deadline_days", 100000)
    monkeypatch.setattr(_settings, "supplementary_publish_by_other", False)


@pytest.fixture(autouse=True)
def _known_student_temp_password(monkeypatch, request):
    """Older tests sign a new student in with the former shared default. Keep
    that working by making the one-time password predictable in tests; tests
    marked `real_temp_passwords` get the real random ones."""
    if request.node.get_closest_marker("real_temp_passwords"):
        return
    import app.imports.service as _imports
    import app.students.service as _students

    monkeypatch.setattr(_students, "new_temp_password", lambda: "vertex123")
    monkeypatch.setattr(_imports, "new_temp_password", lambda: "vertex123")


@pytest.fixture(autouse=True)
def _no_duplicate_window(monkeypatch, request):
    """Many tests send the same request twice on purpose (to check the second is refused by the business rule).
    Tests marked `duplicate_guard` keep the real double-submission guard."""
    if request.node.get_closest_marker("duplicate_guard"):
        return
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "duplicate_post_window_seconds", 0)


@pytest.fixture(autouse=True)
def _check_sessions_every_time(monkeypatch):
    """Tests switch users and accounts quickly; never trust a remembered session check."""
    from app.core import dependencies
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "session_check_seconds", 0)
    dependencies._session_ok.clear()


@pytest.fixture(autouse=True)
def _disable_migration_guard(monkeypatch):
    """The guard looks at the app's real database; tests use their own, so switch it off (it has its own tests)."""
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "check_migrations", False)


TEST_DATABASE_URL_SYNC = settings.database_url_sync.rsplit("/", 1)[0] + "/vist_lms_test"


@pytest.fixture(scope="session", autouse=True)
def _schema_once():
    """Build the schema ONCE per test run (sync engine, no event-loop
    coupling). Previously drop_all/create_all ran before every single
    test, which made the suite ~10x slower than necessary."""
    from sqlalchemy import create_engine, text

    sync_engine = create_engine(TEST_DATABASE_URL_SYNC)
    with sync_engine.begin() as conn:
        # Always the dedicated vist_lms_test database (see TEST_DATABASE_URL). Starting from an empty schema also
        # removes tables of modules that no longer exist (e.g. the old gate check-in tables).
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        db_module.Base.metadata.create_all(conn)
    yield sync_engine
    sync_engine.dispose()


@pytest_asyncio.fixture
async def _engine(_schema_once):
    """Function-scoped async engine (binds to this test's event loop).
    Isolation = TRUNCATE every table before the test, which is far
    cheaper than rebuilding the schema."""
    from sqlalchemy import text

    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    tables = ", ".join(f'"{t.name}"' for t in db_module.Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(bind=_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def seeded_roles(db_session: AsyncSession) -> dict[str, Role]:
    code_to_permission: dict[str, Permission] = {}
    all_codes = sorted({c for codes in ROLE_PERMISSION_SEED.values() for c in codes})
    for code in all_codes:
        p = Permission(code=code)
        db_session.add(p)
        code_to_permission[code] = p
    await db_session.flush()

    code_to_role: dict[str, Role] = {}
    for role_code in ROLE_PERMISSION_SEED:
        r = Role(code=role_code, name=role_code.title())
        db_session.add(r)
        code_to_role[role_code] = r
    await db_session.flush()

    for role_code, perm_codes in ROLE_PERMISSION_SEED.items():
        for perm_code in perm_codes:
            db_session.add(
                RolePermission(role_id=code_to_role[role_code].id, permission_id=code_to_permission[perm_code].id)
            )
    await db_session.commit()
    return code_to_role


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession, seeded_roles: dict[str, Role]) -> User:
    user = User(username="admin_test", password_hash=hash_password("AdminPass123!"), must_change_password=False)
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=seeded_roles[Roles.ADMIN].id))
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def app_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI app with the DB dependency overridden to the test session."""
    from app.main import app

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[db_module.get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()

@pytest.fixture(autouse=True)
def _forget_cached_test_passwords():
    """Several test modules remember the password a test account chose on its first sign-in (module `_cache`, or
    `_as.cache`). The database is wiped before every test, so a remembered password from an earlier test belongs to an
    account that no longer exists: a test that borrows another module's helpers (test_finance_controls uses
    test_purchasing's) then signs in with it and gets 401, depending only on which tests ran before it in the same
    process. Forget them all before each test."""
    import sys

    for name, module in list(sys.modules.items()):
        if not name.startswith("tests."):
            continue
        cache = getattr(module, "_cache", None)
        if isinstance(cache, dict):
            cache.clear()
        helper = getattr(module, "_as", None)
        if isinstance(getattr(helper, "cache", None), dict):
            helper.cache.clear()
    yield