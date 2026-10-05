#!/usr/bin/env bash
# One-command sandbox for VIST LMS.  Usage: scripts/sandbox.sh [setup|reset|test|run|demo]
# Needs local PostgreSQL 16 + Redis (or `docker compose up -d db redis`).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python}
DB_USER=${DB_USER:-vist}; DB_PASS=${DB_PASS:-vist}
SANDBOX_DB=vist_lms_sandbox; TEST_DB=vist_lms_test

pg() { PGPASSWORD=$DB_PASS psql -h localhost -U $DB_USER -d postgres -v ON_ERROR_STOP=1 -qtAc "$1"; }
mkdb() { pg "SELECT 1 FROM pg_database WHERE datname='$1'" | grep -q 1 || pg "CREATE DATABASE $1"
         PGPASSWORD=$DB_PASS psql -h localhost -U $DB_USER -d "$1" -qc 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'; }
export DATABASE_URL="postgresql+asyncpg://$DB_USER:$DB_PASS@localhost:5432/$SANDBOX_DB"
export DATABASE_URL_SYNC="postgresql+psycopg://$DB_USER:$DB_PASS@localhost:5432/$SANDBOX_DB"
export APP_ENV=${APP_ENV:-development} COOKIE_SECURE=false
export JWT_SECRET=${JWT_SECRET:-sandbox-secret-sandbox-secret-sandbox-1234}

case "${1:-setup}" in
  setup) mkdb $SANDBOX_DB; mkdb $TEST_DB
         alembic upgrade head
         $PY scripts/seed_permissions.py ;;
  reset) pg "DROP DATABASE IF EXISTS $SANDBOX_DB"; rm -rf var/storage; "$0" setup ;;
  demo)  $PY scripts/seed_demo_data.py ;;
  test)  pytest tests -q -p no:cacheprovider "${@:2}" ;;   # uses $TEST_DB via conftest
  run)   uvicorn app.main:app --reload --port 8000 ;;
  *) echo "usage: $0 [setup|reset|demo|test|run]"; exit 1 ;;
esac