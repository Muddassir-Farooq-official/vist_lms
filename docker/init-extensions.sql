-- Runs automatically on first container start (docker-entrypoint-initdb.d).
-- Required for trigram-based student name search (Section 44).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
