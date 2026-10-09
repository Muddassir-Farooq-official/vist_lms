"""Show which database the app will use (password hidden) and test the connection.

Usage:
    python scripts/check_db.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def masked(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        creds, host = netloc.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        netloc = f"{user}:****@{host}"
    return parts._replace(netloc=netloc).geturl()


def main() -> int:
    for name in ("DATABASE_URL", "DATABASE_URL_SYNC"):
        if os.environ.get(name):
            print(f"WARNING: Windows environment variable {name} is set and overrides .env:")
            print(f"   {masked(os.environ[name])}")

    from app.core.config import settings

    print("App uses (async):", masked(settings.database_url))
    print("App uses (sync): ", masked(settings.database_url_sync))

    parts = urlsplit(settings.database_url)
    host, port = parts.hostname, parts.port or 5432
    print(f"\n1) TCP test to {host}:{port} ...", end=" ", flush=True)
    try:
        with socket.create_connection((host, port), timeout=5):
            print("OK, port is reachable")
    except OSError as exc:
        print(f"FAILED: {exc}")
        if host in ("localhost", "127.0.0.1"):
            print("   The SSH tunnel is not running. Start it in another window and leave it open:")
            print("   ssh -N -L 5433:localhost:5432 root@srv1882304.hstgr.cloud")
        else:
            print("   The server blocks this port from the internet. Use the SSH tunnel and set")
            print("   host=localhost, port=5433 and ssl=disable in .env.")
        return 1

    print("2) Database login ...", end=" ", flush=True)
    from sqlalchemy import text

    from app.core.database import engine

    async def probe() -> None:
        async with engine.connect() as conn:
            version = (await conn.execute(text("select version()"))).scalar_one()
            db = (await conn.execute(text("select current_database()"))).scalar_one()
            tables = (
                await conn.execute(
                    text("select count(*) from information_schema.tables where table_schema='public'")
                )
            ).scalar_one()
            try:
                rev = (await conn.execute(text("select version_num from alembic_version"))).scalar_one()
            except Exception:
                rev = "none (run: alembic upgrade head)"
        await engine.dispose()
        print("OK")
        print(f"   database: {db}\n   tables:   {tables}\n   alembic:  {rev}\n   server:   {version[:60]}")

    try:
        asyncio.run(probe())
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())