-- Runs once, when the db volume is first created (docker-compose.yml).
-- The postgis image already enables postgis in POSTGRES_DB; the rest is what the backend needs.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() for the PRD Section 13.1 tables
