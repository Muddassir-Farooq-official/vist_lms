"""University Reg No (unique, admin + exam office, bulk CSV) and the student field rules (required / optional) shared
by the Add student form and the CSV import."""
from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.imports.router as imports_router
import app.imports.tasks as imports_tasks
from app.core.config import settings
from app.students.models import Student, StudentGuardian

pytestmark = pytest.mark.asyncio
S = "/api/v1/students"


@pytest.fixture(autouse=True)
def _setup(monkeypatch, _engine):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(imports_tasks, "AsyncSessionLocal", async_sessionmaker(bind=_engine, expire_on_commit=False))
    monkeypatch.setattr(imports_router, "_worker_listening", lambda: False)


async def _login(client):
    assert (await client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})).status_code == 200


async def test_field_rules_drive_the_form_the_template_and_the_import(app_client, admin_user, db_session):
    await _login(app_client)
    rules = {f["key"]: f for f in (await app_client.get(f"{S}/field-rules")).json()}
    assert rules["cnic"]["required"] and rules["first_name"]["locked"] and not rules["reg_number"]["required"]

    # first and last name can never be optional
    locked = await app_client.put(f"{S}/field-rules", json={"rules": {"first_name": False}})
    assert locked.status_code == 422

    # an affiliated college: only names and the university Reg No are required
    optional = {k: False for k, f in rules.items() if not f["locked"]}
    saved = await app_client.put(f"{S}/field-rules", json={"rules": {**optional, "reg_number": True}})
    assert saved.status_code == 200, saved.text
    assert {f["key"] for f in saved.json() if f["required"]} == {"first_name", "last_name", "reg_number"}

    # the template follows the rules: Reg No column, red header and REQUIRED on the Rules sheet
    csv_text = (await app_client.get("/api/v1/imports/students/template")).text
    assert "reg_number" in csv_text.splitlines()[0]
    wb = load_workbook(io.BytesIO((await app_client.get("/api/v1/imports/students/template.xlsx")).content))
    rule_rows = {r[0]: r[1] for r in wb["Rules"].iter_rows(min_row=2, values_only=True)}
    assert rule_rows["reg_number"] == "REQUIRED" and rule_rows["cnic"] == "optional" and rule_rows["first_name"] == "REQUIRED"

    # the Add student form: Reg No now required, everything else optional
    missing = await app_client.post(S, json={"first_name": "Sana", "last_name": "Gul"})
    assert missing.status_code == 422 and "University Reg No" in missing.text
    ok = await app_client.post(S, json={"first_name": "Sana", "last_name": "Gul", "reg_number": "kmu-2026-0001", "cnic": ""})
    assert ok.status_code == 201, ok.text
    sid = ok.json()["id"]
    detail = await app_client.get(f"{S}/{sid}")
    assert detail.status_code == 200 and detail.json()["cnic"] is None and detail.json()["reg_number"] == "kmu-2026-0001"
    profile = await app_client.get(f"{S}/{sid}/full-profile")
    assert profile.status_code == 200 and profile.json()["guardians"] == [] and profile.json()["date_of_birth"] is None

    # the Reg No is unique, whatever the capitals
    dup = await app_client.post(S, json={"first_name": "Ali", "last_name": "Raza", "reg_number": "KMU-2026-0001"})
    assert dup.status_code == 409 and "already belongs" in dup.text

    # the CSV import: only the required columns need to be in the file
    body = "first_name,last_name,reg_number\nAmna,Bibi,KMU-2026-0002\nZara,Khan,KMU-2026-0001\n"
    r = await app_client.post("/api/v1/imports/students", files={"file": ("s.csv", body.encode(), "text/csv")})
    db_session.expire_all()
    job = (await app_client.get(f"/api/v1/imports/students/{r.json()['import_job_id']}")).json()
    assert job["status"] == "FAILED"                                                   # row 3 reuses an existing Reg No
    errors = (await app_client.get(f"/api/v1/imports/students/{job['id']}/errors")).json()
    errors = errors["items"] if isinstance(errors, dict) else errors
    assert any(e["field"] == "reg_number" and "already belongs" in e["message"] for e in errors)

    body = "first_name,last_name,reg_number\nAmna,Bibi,KMU-2026-0002\nZara,Khan,\n"
    r = await app_client.post("/api/v1/imports/students", files={"file": ("s.csv", body.encode(), "text/csv")})
    db_session.expire_all()
    job = (await app_client.get(f"/api/v1/imports/students/{r.json()['import_job_id']}")).json()
    assert job["status"] == "FAILED"                                                   # Reg No is required now
    errors = (await app_client.get(f"/api/v1/imports/students/{job['id']}/errors")).json()
    errors = errors["items"] if isinstance(errors, dict) else errors
    assert [(e["row_number"], e["field"]) for e in errors] == [(3, "reg_number")]

    body = "first_name,last_name,reg_number,guardian_name\nAmna,Bibi,KMU-2026-0002,Gul Khan\n"
    r = await app_client.post("/api/v1/imports/students", files={"file": ("s.csv", body.encode(), "text/csv")})
    db_session.expire_all()
    job = (await app_client.get(f"/api/v1/imports/students/{r.json()['import_job_id']}")).json()
    assert job["status"] == "COMPLETED", job
    amna = (await db_session.execute(select(Student).where(Student.reg_number == "KMU-2026-0002"))).scalar_one()
    g = (await db_session.execute(select(StudentGuardian).where(StudentGuardian.student_id == amna.id))).scalar_one()
    assert amna.cnic is None and g.name == "Gul Khan" and g.cnic is None

    # without the guardian's CNIC there is no parent login, with a clear message
    parent = await app_client.get(f"/api/v1/parents/students/{amna.id}")
    assert parent.status_code == 422 and "CNIC is not on record" in parent.text

    # back to the original rules: the full set is required again
    reset = await app_client.put(f"{S}/field-rules", json={"rules": {k: f["default_required"] for k, f in rules.items() if not f["locked"]}})
    assert {f["key"] for f in reset.json() if f["required"]} == {k for k, f in rules.items() if f["required"]}


async def test_reg_numbers_page_one_by_one_and_from_a_csv(app_client, admin_user, db_session):
    await _login(app_client)
    await app_client.put(f"{S}/field-rules", json={"rules": {k: False for k in ("date_of_birth", "gender", "cnic", "contact_no", "marital_status",
                                                                                  "religion", "nationality", "guardian_name", "guardian_cnic",
                                                                                  "relation_with_guardian", "emergency_contact_no", "present_address",
                                                                                  "province", "district", "city")}})
    ids = []
    for first in ("Asma", "Bilal", "Danish"):
        r = await app_client.post(S, json={"first_name": first, "last_name": "Test"})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    rolls = {i: (await app_client.get(f"{S}/{i}")).json()["roll_number"] for i in ids}

    listed = (await app_client.get(f"{S}/reg-numbers", params={"missing_only": True})).json()
    assert {s["id"] for s in listed["students"]} >= set(ids) and listed["missing"] >= 3

    one = await app_client.put(f"{S}/{ids[0]}/reg-number", json={"reg_number": "  AUST/21/001 "})
    assert one.status_code == 200 and one.json()["reg_number"] == "AUST/21/001"
    clash = await app_client.put(f"{S}/{ids[1]}/reg-number", json={"reg_number": "aust/21/001"})
    assert clash.status_code == 409
    bad = await app_client.put(f"{S}/{ids[1]}/reg-number", json={"reg_number": "=cmd()"})
    assert bad.status_code == 422

    # a CSV: Asma and Bilal swap numbers, Danish gets one; dry run first, then saved
    csv_body = f"roll_number,name,reg_number\n{rolls[ids[0]]},Asma,AUST/21/002\n{rolls[ids[1]]},Bilal,AUST/21/001\n{rolls[ids[2]]},Danish,AUST/21/003\n"
    files = {"file": ("r.csv", csv_body.encode(), "text/csv")}
    dry = (await app_client.post(f"{S}/reg-numbers/import", params={"dry_run": True}, files=files)).json()
    assert dry["dry_run"] and not dry["error_count"] and dry["change_count"] == 3
    db_session.expire_all()
    assert (await db_session.get(Student, ids[2])).reg_number is None
    done = (await app_client.post(f"{S}/reg-numbers/import", params={"dry_run": False}, files=files)).json()
    assert done["applied"] == 3
    db_session.expire_all()
    assert [(await db_session.get(Student, i)).reg_number for i in ids] == ["AUST/21/002", "AUST/21/001", "AUST/21/003"]

    # a Reg No another student keeps is refused, and nothing is saved
    clash_csv = f"roll_number,reg_number\n{rolls[ids[2]]},AUST/21/001\nNO-SUCH-ROLL,X1\n"
    res = (await app_client.post(f"{S}/reg-numbers/import", params={"dry_run": False}, files={"file": ("r.csv", clash_csv.encode(), "text/csv")})).json()
    assert res["error_count"] == 2 and "applied" not in res
    db_session.expire_all()
    assert (await db_session.get(Student, ids[2])).reg_number == "AUST/21/003"

    # search by Reg No in the student directory
    found = (await app_client.get(S, params={"q": "aust/21/003"})).json()
    items = found["items"] if isinstance(found, dict) else found
    assert [s["id"] for s in items] == [ids[2]]
