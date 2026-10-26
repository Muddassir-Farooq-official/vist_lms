-- Least-privilege database users for VIST LMS (audit SEC-05 / DB-07).
--
-- Today the app connects as the PostgreSQL superuser, so any SQL bug would have full control of the server. After this:
--   vist_owner : owns the tables; used ONLY to run migrations (MIGRATION_DATABASE_URL).
--   vist_app   : what the running app uses (DATABASE_URL / DATABASE_URL_SYNC): read and write rows, nothing else.
--                It cannot create, drop or alter tables, create extensions, or read other databases.
--
-- Run ONCE as the superuser, in a maintenance window, after a backup:
--   psql "postgresql://postgres@HOST:5432/vist_lms" -v owner_pw="'...'" -v app_pw="'...'" -f scripts/db_roles.sql
-- Then change .env (see docs in CHANGES.md) and restart. Use long random passwords; never commit them.
\set ON_ERROR_STOP on

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vist_owner') THEN CREATE ROLE vist_owner LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vist_app') THEN CREATE ROLE vist_app LOGIN; END IF;
END $$;
ALTER ROLE vist_owner WITH PASSWORD :owner_pw NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE vist_app   WITH PASSWORD :app_pw   NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 60;

-- The owner takes over the database objects (the app keeps working during this; it only changes ownership).
ALTER DATABASE vist_lms OWNER TO vist_owner;
ALTER SCHEMA public OWNER TO vist_owner;
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO vist_owner', r.tablename); END LOOP;
  FOR r IN SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO vist_owner', r.sequence_name); END LOOP;
  FOR r IN SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'public' AND t.typtype = 'e' LOOP
    EXECUTE format('ALTER TYPE public.%I OWNER TO vist_owner', r.typname); END LOOP;
END $$;

-- The app: rows only.
REVOKE ALL ON DATABASE vist_lms FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE vist_lms TO vist_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO vist_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vist_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO vist_app;
-- ...and the same on every table a future migration creates.
ALTER DEFAULT PRIVILEGES FOR ROLE vist_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vist_app;
ALTER DEFAULT PRIVILEGES FOR ROLE vist_owner IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO vist_app;

-- Audit history is never edited or deleted by the app.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM vist_app;
