"""Student CSV uploads never hang: with no worker listening the web app processes the file itself; a crash marks the
job FAILED; a failed or never-started upload can be retried with the same file; a job is never imported twice."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.imports.router as imports_router
import app.imports.tasks as imports_tasks
from app.core.config import settings
from app.imports.models import ImportJob
from app.imports.service import ImportService
from app.students.models import Student
from tests.integration.test_csv_import import CSV_HEADER, _make_program, _row

pytestmark = pytest.mark.asyncio
URL = "/api/v1/imports/students"


@pytest.fixture(autouse=True)
def _setup(monkeypatch, _engine):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    # the background run opens its own session: point it at the test database
    monkeypatch.setattr(imports_tasks, "AsyncSessionLocal", async_sessionmaker(bind=_engine, expire_on_commit=False))
    monkeypatch.setattr(imports_router, "_worker_listening", lambda: False)


async def _login(client):
    r = await client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    assert r.status_code == 200


async def _upload(client, data: bytes, name="students.csv"):
    return await client.post(URL, files={"file": (name, data, "text/csv")})


async def _job(client, db_session, job_id):
    db_session.expire_all()                                   # the run happened in another session
    r = await client.get(f"{URL}/{job_id}")
    assert r.status_code == 200
    return r.json()


async def test_without_a_worker_the_upload_is_processed_by_the_app(app_client, admin_user, db_session):
    await _make_program(db_session)
    await _login(app_client)
    r = await _upload(app_client, (CSV_HEADER + _row("1", "R-001")).encode())
    assert r.status_code == 202, r.text
    job = await _job(app_client, db_session, r.json()["import_job_id"])
    assert job["status"] == "COMPLETED" and job["imported_rows"] == 1
    listed = (await app_client.get(URL)).json()
    assert listed[0]["id"] == job["id"] and listed[0]["status"] == "COMPLETED"


async def test_with_a_worker_listening_the_job_is_queued(app_client, admin_user, db_session, monkeypatch):
    queued = []

    class _Queue:
        def enqueue(self, fn, *args):
            queued.append((fn.__name__, args[0]))

    monkeypatch.setattr(imports_router, "_worker_listening", lambda: True)
    monkeypatch.setattr(imports_router, "get_imports_queue", lambda: _Queue())
    await _make_program(db_session)
    await _login(app_client)
    r = await _upload(app_client, (CSV_HEADER + _row("2", "R-002")).encode())
    assert r.status_code == 202
    assert queued == [("run_import_job", r.json()["import_job_id"])]
    assert (await _job(app_client, db_session, r.json()["import_job_id"]))["status"] == "UPLOADED"


async def test_a_crash_marks_the_job_failed_and_the_same_file_can_be_retried(app_client, admin_user, db_session, monkeypatch):
    await _make_program(db_session)
    await _login(app_client)
    data = (CSV_HEADER + _row("3", "R-003")).encode()
    real = ImportService.process_job

    async def boom(self, job_id, file_bytes):
        raise RuntimeError("database went away")

    monkeypatch.setattr(ImportService, "process_job", boom)
    r = await _upload(app_client, data)
    first = r.json()["import_job_id"]
    job = await _job(app_client, db_session, first)
    assert job["status"] == "FAILED" and "system error" in job["failure_reason"]

    monkeypatch.setattr(ImportService, "process_job", real)
    again = await _upload(app_client, data, name="students_retry.csv")
    assert again.status_code == 202, again.text
    assert again.json()["import_job_id"] == first                  # the same job, run again
    job = await _job(app_client, db_session, first)
    assert job["status"] == "COMPLETED" and job["imported_rows"] == 1 and job["filename"] == "students_retry.csv"
    done = await _upload(app_client, data)
    assert done.status_code == 409 and "already imported" in done.text   # never imported twice


async def test_an_upload_never_started_can_be_retried_after_two_minutes(app_client, admin_user, db_session, monkeypatch):
    monkeypatch.setattr(imports_router, "_worker_listening", lambda: True)   # queued to a worker that never runs it
    monkeypatch.setattr(imports_router, "get_imports_queue", lambda: type("Q", (), {"enqueue": lambda self, *a: None})())
    await _make_program(db_session)
    await _login(app_client)
    data = (CSV_HEADER + _row("4", "R-004")).encode()
    first = (await _upload(app_client, data)).json()["import_job_id"]
    soon = await _upload(app_client, data)
    assert soon.status_code == 409 and "still waiting" in soon.text        # too early to call it lost

    job = await db_session.get(ImportJob, __import__("uuid").UUID(first))
    job.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.commit()
    monkeypatch.setattr(imports_router, "_worker_listening", lambda: False)
    r = await _upload(app_client, data)
    assert r.status_code == 202 and r.json()["import_job_id"] == first
    assert (await _job(app_client, db_session, first))["status"] == "COMPLETED"


async def test_running_the_same_job_twice_imports_it_once(db_session, admin_user):
    await _make_program(db_session)
    data = (CSV_HEADER + _row("5", "R-005")).encode()
    service = ImportService(db_session)
    job = await service.create_job_from_upload(file_bytes=data, filename="once.csv", uploaded_by=admin_user.id)
    await db_session.commit()
    await service.process_job(job.id, data)
    await db_session.commit()
    again = await service.process_job(job.id, data)                       # e.g. the worker came back after a retry
    assert again.status == "COMPLETED"
    assert (await db_session.execute(select(func.count()).select_from(Student))).scalar_one() == 1