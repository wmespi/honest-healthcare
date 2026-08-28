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
ALTER TABLE test.index_files   ADD COLUMN IF NOT EXISTS plan_states TEXT[] DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_index_files_plan_states      ON public.index_files USING GIN(plan_states);
CREATE INDEX IF NOT EXISTS idx_index_files_plan_states_test ON test.index_files   USING GIN(plan_states);

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

CREATE TABLE IF NOT EXISTS test.coverage_log (LIKE public.coverage_log INCLUDING ALL);
