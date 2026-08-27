-- Runs automatically on first container start (empty data directory) via
-- Postgres's /docker-entrypoint-initdb.d/ convention. Migration
-- 0003_publication_governance requires pgcrypto's digest(bytea, text) for
-- its integrity checks and fails closed with PGCRYPTO_REQUIRED without it —
-- a fresh `docker compose up` against an empty database had no path to
-- install this extension before this script existed.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
