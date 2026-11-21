#!/usr/bin/env python3
"""The server's health, for the admin panel (System -> Server health). Run by `sites.sh jobs` every minute, as root,
with the standard library only.

stdin:  {"sites": [{"name", "dir", "port", "domain", "enabled"}], "state": "<file>", "cache": "<file>"}
stdout: one line per institute: "<name>\t<json>". Each institute gets the shared server figures and ITS OWN site only;
        "vertex" (the server's owner) gets every institute.

Measured each minute: disk, memory, load, each site's /health/ready and version. Every 30 minutes (cached): each
site's HTTPS certificate expiry (asked from nginx itself, so it is the certificate really served) and the size of its
uploaded files. Memory / load "high since" times are kept in a small state file, so "high for 10 minutes" can be told.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

SLOW_EVERY = 30 * 60
MEM_HIGH = 0.90


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def meminfo() -> dict:
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                out[k] = int(v.strip().split()[0]) * 1024
    except Exception:
        pass
    total, avail = out.get("MemTotal", 0), out.get("MemAvailable", 0)
    return {"total": total, "available": avail, "used_pct": round((total - avail) / total * 100, 1) if total else None,
            "swap_total": out.get("SwapTotal", 0), "swap_used": out.get("SwapTotal", 0) - out.get("SwapFree", 0)}


def get(url: str, timeout: float = 5.0):
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(20000)
            code = r.status
    except urllib.error.HTTPError as e:
        body, code = e.read(20000), e.code
    except Exception as e:
        text = str(e)
        if "refused" in text:
            text = "not running (connection refused)"
        elif "timed out" in text.lower():
            text = f"no answer within {timeout:g} s"
        return None, None, round((time.monotonic() - t0) * 1000), text[:120]
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        data = None
    return code, data, round((time.monotonic() - t0) * 1000), None


def cert_days(domain: str):
    """Expiry of the certificate nginx serves for this name, asked locally (SNI)."""
    if not domain:
        return None, None
    try:
        p = subprocess.run(["openssl", "s_client", "-servername", domain, "-connect", "127.0.0.1:443"], input=b"",
                           capture_output=True, timeout=10)
        q = subprocess.run(["openssl", "x509", "-noout", "-enddate"], input=p.stdout, capture_output=True, timeout=10)
        m = re.search(r"notAfter=(.+)", q.stdout.decode())
        if not m:
            return None, "no certificate"
        end = datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return end.isoformat(timespec="seconds"), None
    except Exception as e:
        return None, str(e)[:120]


def du(path: str):
    try:
        p = subprocess.run(["du", "-sb", "--exclude=exports", path], capture_output=True, timeout=120)
        return int(p.stdout.split()[0])
    except Exception:
        return None


def last_backup(site_dir: str):
    """From the site's backup log: the last 'Backup OK' and the last failure (time stamps at the line starts)."""
    ok = failed = None
    try:
        with open(os.path.join(site_dir, "backups", "backup.log"), errors="replace") as f:
            lines = f.readlines()[-400:]
    except Exception:
        return None, None
    for line in lines:
        stamp = line[:19] if re.match(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", line) else None
        if "Backup OK" in line and stamp:
            ok = stamp
        elif ("FAILED" in line or "STOPPED" in line) and stamp:
            failed = stamp
    return ok, failed


def main() -> int:
    cfg = json.load(sys.stdin)
    sites = cfg["sites"]
    state = read_json(cfg["state"], {})
    cache = read_json(cfg["cache"], {})
    t = time.time()

    disk = shutil.disk_usage("/")
    mem = meminfo()
    load1, load5, load15 = os.getloadavg()
    cpus = os.cpu_count() or 1
    try:
        with open("/proc/uptime") as f:
            uptime = int(float(f.read().split()[0]))
    except Exception:
        uptime = None

    # "high since": kept between runs
    mem_high = mem["used_pct"] is not None and mem["used_pct"] >= MEM_HIGH * 100
    load_high = load5 >= cpus * 1.5
    for key, high in (("memory_high_since", mem_high), ("load_high_since", load_high)):
        if high:
            state.setdefault(key, now_iso())
        else:
            state.pop(key, None)

    slow_due = t - cache.get("at", 0) > SLOW_EVERY
    if slow_due:
        cache = {"at": t, "certs": {}, "storage": {}}
    rows = []
    for s in sites:
        code, ready, ms, err = (None, None, None, "switched off") if not s.get("enabled", True) else get(f"http://127.0.0.1:{s['port']}/health/ready")
        _c, ver, _m, _e = get(f"http://127.0.0.1:{s['port']}/api/v1/version") if s.get("enabled", True) else (None, None, None, None)
        if slow_due:
            cache["certs"][s["name"]] = cert_days(s.get("domain") or "")
            cache["storage"][s["name"]] = du(os.path.join(s["dir"], "var", "storage"))
        cert_end, cert_err = (cache.get("certs", {}).get(s["name"]) or [None, None])
        ok_at, failed_at = last_backup(s["dir"])
        rows.append({
            "name": s["name"], "domain": s.get("domain"), "enabled": s.get("enabled", True),
            "up": code == 200 and isinstance(ready, dict) and ready.get("status") == "ok",
            "database": (ready or {}).get("database") if isinstance(ready, dict) else None,
            "http_status": code, "response_ms": ms, "error": err,
            "version": (ver or {}).get("version") if isinstance(ver, dict) else None,
            "build": (ver or {}).get("build") if isinstance(ver, dict) else None,
            "updated_at": (ver or {}).get("updated_at") if isinstance(ver, dict) else None,
            "cert_expires": cert_end, "cert_error": cert_err,
            "storage_bytes": cache.get("storage", {}).get(s["name"]),
            "backup_ok_at": ok_at, "backup_failed_at": failed_at,
        })
    write_json(cfg["state"], state)
    write_json(cfg["cache"], cache)

    server = {"taken_at": now_iso(), "disk_total": disk.total, "disk_used": disk.used, "disk_free": disk.free,
              "disk_used_pct": round(disk.used / (disk.used + disk.free) * 100, 1) if (disk.used + disk.free) else None,   # as df shows it
              "memory": mem, "load": [round(load1, 2), round(load5, 2), round(load15, 2)], "cpus": cpus, "uptime_s": uptime,
              "memory_high_since": state.get("memory_high_since"), "load_high_since": state.get("load_high_since"),
              "slow_checked_at": datetime.fromtimestamp(cache.get("at", t), timezone.utc).isoformat(timespec="seconds")}
    for s in sites:
        mine = rows if s["name"] == "vertex" else [r for r in rows if r["name"] == s["name"]]
        payload = {"server": server, "sites": mine, "owner": s["name"] == "vertex", "self": s["name"]}
        print(s["name"] + "\t" + json.dumps(payload, separators=(",", ":")).replace("$", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
