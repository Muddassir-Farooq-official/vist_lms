"""Several institutes on one server (docs/multi-institute.md): the deploy order and failure handling of
scripts/deploy_all.sh, the helpers of scripts/server/sites.sh, and that the compose file still gives Vertex exactly its
old set-up while another institute gets its own .env, files, port and project."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _fake_code_dir(tmp_path: Path, fail: set[str]) -> tuple[Path, Path]:
    code = tmp_path / "code"
    (code / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "deploy_all.sh", code / "scripts" / "deploy_all.sh")
    log = tmp_path / "calls.log"
    # a stand-in deploy.sh that records each call and fails for the names in FAIL
    (code / "scripts" / "deploy.sh").write_text(
        '#!/usr/bin/env bash\nname="${2:-vertex}"\necho "$name" >> "' + str(log) + '"\n'
        'case " $FAIL " in *" $name "*) exit 1;; esac\nexit 0\n')
    sites = tmp_path / "sites"
    for name, enabled in (("abc", 1), ("kcon", 1), ("old", 0)):
        (sites / name).mkdir(parents=True)
        (sites / name / "site.env").write_text(f"NAME={name}\nENABLED={enabled}\n")
    (sites / "notasite").mkdir()                                            # no site.env: ignored
    return code, log


def _run_all(tmp_path, fail=()):
    code, log = _fake_code_dir(tmp_path, set(fail))
    env = {**os.environ, "LMS_SITES_DIR": str(tmp_path / "sites"), "FAIL": " ".join(fail)}
    r = subprocess.run(["bash", str(code / "scripts" / "deploy_all.sh")], env=env, capture_output=True, text=True)
    return r, (log.read_text().split() if log.exists() else [])


def test_deploy_all_runs_vertex_first_then_each_enabled_institute(tmp_path):
    r, calls = _run_all(tmp_path)
    assert r.returncode == 0, r.stderr
    assert calls == ["vertex", "abc", "kcon"]                              # "old" is switched off, "notasite" is not one
    assert "OK: abc kcon" in r.stdout and "Switched off (skipped): old" in r.stdout


def test_a_failing_vertex_touches_no_other_institute(tmp_path):
    r, calls = _run_all(tmp_path, fail=["vertex"])
    assert r.returncode == 1 and calls == ["vertex"]
    assert "no other institute was touched" in r.stderr


def test_one_failing_institute_does_not_stop_the_others_but_fails_the_run(tmp_path):
    r, calls = _run_all(tmp_path, fail=["abc"])
    assert r.returncode == 1 and calls == ["vertex", "abc", "kcon"]
    assert "FAILED: abc" in r.stderr and "OK: kcon" in r.stdout


def _helpers(tmp_path: Path) -> Path:
    text = (ROOT / "scripts" / "server" / "sites.sh").read_text()
    start, end = text.index("envget() {"), text.index("obscure() {")
    f = tmp_path / "helpers.sh"
    f.write_text(text[start:end].replace("rclone_run() {", "rclone_unused() {"))
    return f


def test_sites_helpers_write_settings_without_echoing_them(tmp_path):
    f = _helpers(tmp_path)
    env_file = tmp_path / "e"
    env_file.write_text('APP_ENV=production\nAPP_NAME="Old name" # note\nPW=ab#cd\n')
    script = f'''
set -u; source {f}
V='p@ss w$rd=1'; setkey {env_file} SECRET V
V=ABC; setkey {env_file} APP_ENV V
echo "name=$(envget APP_NAME {env_file})|pw=$(envget PW {env_file})"
printf 'account=abc\\nkey=K/=+\\n' | rc_section lmsstore b2
printf 'account=NEW\\n' | rc_section lmsstore b2
'''
    r = subprocess.run(["bash", "-c", script], env={**os.environ, "RCLONE_DIR": str(tmp_path)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "p@ss" not in r.stdout                                          # nothing secret is printed
    assert "name=Old name|pw=ab#cd" in r.stdout
    lines = env_file.read_text().splitlines()
    assert "SECRET=p@ss w$rd=1" in lines and lines.count("APP_ENV=ABC") == 1 and "APP_ENV=production" not in lines
    conf = (tmp_path / "rclone.conf").read_text()
    assert conf.count("[lmsstore]") == 1 and "account = NEW" in conf and "key = K/=+" not in conf   # replaced, not duplicated
    assert oct((tmp_path / "rclone.conf").stat().st_mode)[-3:] == "600"


def test_rc_delete_removes_one_remote_and_keeps_the_rest(tmp_path):
    text = (ROOT / "scripts" / "server" / "sites.sh").read_text()
    f = _helpers(tmp_path)
    rc_delete = text[text.index("rc_delete() {"):text.index("# Move every institute's backups to Backblaze B2.")]
    f.write_text(f.read_text() + "\n" + rc_delete)
    script = f'''
set -u; source {f}
printf 'scope=drive\\ntoken=x\\n' | rc_section lmsstore drive
printf 'remote=lmsstore:LMS-backups/vertex\\npassword=p\\n' | rc_section lmsbk-vertex crypt
rc_delete lmsstore
'''
    r = subprocess.run(["bash", "-c", script], env={**os.environ, "RCLONE_DIR": str(tmp_path)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    conf = (tmp_path / "rclone.conf").read_text()
    assert "[lmsstore]" not in conf and "[lmsbk-vertex]" in conf and "password = p" in conf


def test_backup_uploads_only_changed_files_and_a_full_database_copy():
    text = (ROOT / "scripts" / "server" / "backup.sh").read_text()
    assert 'rclone sync /storage "${REMOTE}files/current" --backup-dir "${REMOTE}files/changed/$stamp"' in text
    assert 'rclone copy "/backups/nightly/$db" "${REMOTE}daily/"' in text
    assert "tar -czf" not in text                                          # no full archive of every file each night
    assert 'rclone mkdir "$REMOTE"' in text                                # a new institute's folder is made first


def test_temporary_address_and_nginx_site(tmp_path):
    text = (ROOT / "scripts" / "server" / "sites.sh").read_text()
    funcs = text[text.index("temp_suffix() {"):text.index("# ------------------------------------------------------------------ move to an official domain")]
    f = tmp_path / "f.sh"
    f.write_text(funcs)
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "curl").write_text("#!/bin/sh\necho 187.127.220.153\n")
    (fake / "curl").chmod(0o755)
    r = subprocess.run(["bash", "-c", f"source {f}; echo fims.$(temp_suffix); nginx_proxy_conf lms.fims.edu.pk 8003"],
                       env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0] == "fims.187-127-220-153.sslip.io"
    conf = "\n".join(lines[1:])
    assert "server_name lms.fims.edu.pk;" in conf and "proxy_pass http://127.0.0.1:8003;" in conf
    assert "proxy_set_header X-Forwarded-For   $remote_addr;" in conf      # SEC-02: never the client's own header


def test_sites_script_is_valid_bash_and_explains_itself():
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "server" / "sites.sh")]).returncode == 0
    r = subprocess.run(["bash", str(ROOT / "scripts" / "server" / "sites.sh")], capture_output=True, text=True)
    assert r.returncode == 2 and "sites.sh add <name> <domain>" in r.stdout and "sites.sh domain <name|vertex> <new-domain>" in r.stdout


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose not installed")
def test_compose_gives_vertex_its_old_setup_and_an_institute_its_own(tmp_path):
    shutil.copy(ROOT / "docker-compose.prod.yml", tmp_path / "docker-compose.prod.yml")
    (tmp_path / ".env").write_text("APP_ENV=production\nDATABASE_URL=vertex-db\n")
    site = tmp_path / "abc"
    (site / "var" / "storage").mkdir(parents=True)
    (site / ".env").write_text("APP_ENV=production\nDATABASE_URL=abc-db\n")
    (site / "compose.env").write_text(f"LMS_ENV_FILE={site}/.env\nLMS_STORAGE={site}/var/storage\nLMS_PORT=8001\nLMS_WEB_WORKERS=2\n")

    def config(*extra):
        r = subprocess.run(["docker", "compose", *extra, "-f", "docker-compose.prod.yml", "config", "--format", "json"],
                           cwd=tmp_path, capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"docker compose config unavailable: {r.stderr[:200]}")
        import json
        return json.loads(r.stdout)

    vertex = config()
    assert vertex["name"] == "vist-lms"
    app = vertex["services"]["app"]
    assert app["environment"]["DATABASE_URL"] == "vertex-db"
    assert app["ports"][0]["published"] == "8000" and app["ports"][0]["host_ip"] == "127.0.0.1"
    assert app["volumes"][0]["source"] == str(tmp_path / "var" / "storage")
    assert app["command"][app["command"].index("--workers") + 1] == "4"

    abc = config("-p", "lms-abc", "--env-file", str(site / "compose.env"), "--profile", "tools")
    assert abc["name"] == "lms-abc"
    for svc in ("app", "worker", "sms", "migrate"):
        assert abc["services"][svc]["environment"]["DATABASE_URL"] == "abc-db"
    app = abc["services"]["app"]
    assert app["ports"][0]["published"] == "8001"
    assert app["volumes"][0]["source"] == str(site / "var" / "storage")
    assert abc["services"]["worker"]["volumes"][0]["source"] == str(site / "var" / "storage")
    assert app["command"][app["command"].index("--workers") + 1] == "2"
