-- Migration 001 — GA coverage + structured attribution
-- Applies the schema changes in init.sql to an already-running database.
-- Idempotent. Run it with:
--   make db-migrate
-- or directly:
--   docker compose exec -T db psql -U postgres -d honest_healthcare < db/migrations/001_ga_coverage.sql
--
-- (init.sql is only executed on a fresh volume, so existing stacks need this.)

\set ON_ERROR_STOP on

-- ── index_files: plan_states (HIOS plan_id[5:7], positional state code) ──────────
ALTER TABLE public.index_files ADD COLUMN IF NOT EXISTS plan_states TEXT[] DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_index_files_plan_states ON public.index_files USING GIN(plan_states);

-- Why a file failed — so retries can skip unrecoverable failures (bad gzip,
-- unexpected EOF) instead of re-downloading them forever. status 'failed'
-- stays retryable; the reason lets db-reset-failed be selective.
ALTER TABLE public.index_files ADD COLUMN IF NOT EXISTS failure_reason TEXT;

-- ── coverage_log: one row per parsed file ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.coverage_log (
    id SERIAL PRIMARY KEY,
    file_id INTEGER,
    location TEXT,
    parsed_at TIMESTAMP DEFAULT NOW(),
    compressed_bytes BIGINT,
    n_rate_rows BIGINT,
    n_provider_rows BIGINT,
    n_new_billing_codes INTEGER,
    n_total_billing_codes_after INTEGER,
    n_new_npis INTEGER,
    n_new_tins INTEGER,
    network_names TEXT[],
    plan_states TEXT[],
    hios_issuer_ids TEXT[],
    market_types TEXT[],
    distinct_settings TEXT[],
    distinct_billing_classes TEXT[],
    billing_code_types TEXT[],
    n_ga_hospital_npis INTEGER,
    parquet_retained BOOLEAN DEFAULT TRUE,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_coverage_log_file ON public.coverage_log(file_id);

-- ── test schema: rebuild from public ──────────────────────────────────────────
-- `CREATE TABLE test.x (LIKE public.x INCLUDING ALL)` is a one-time copy, so the
-- test mirror drifts every time a public column is added (it was already missing
-- market_types / hios_issuer_ids / network_entity). test.* is disposable by
-- charter — drop and recreate so isolation is trustworthy. Do NOT do this to
-- public.
DROP SCHEMA IF EXISTS test CASCADE;
CREATE SCHEMA test;

CREATE TABLE test.billing_codes (LIKE public.billing_codes INCLUDING ALL);
CREATE TABLE test.index_files   (LIKE public.index_files   INCLUDING ALL);
CREATE TABLE test.coverage_log  (LIKE public.coverage_log  INCLUDING ALL);
-- (legacy provider_mappings / negotiated_rates / place_of_service_codes dropped
--  in migration 002 — no longer mirrored here)

-- discover.go drops/recreates these by exact (schema-unqualified) name in test
-- mode; INCLUDING ALL copies public's indexes only under auto-generated names.
CREATE INDEX IF NOT EXISTS idx_index_files_plan_states ON test.index_files USING GIN(plan_states);
CREATE INDEX IF NOT EXISTS idx_index_files_market      ON test.index_files USING GIN(market_types);
CREATE INDEX IF NOT EXISTS idx_index_files_hios        ON test.index_files USING GIN(hios_issuer_ids);
