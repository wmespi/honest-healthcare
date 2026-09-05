-- Postgres schema for Honest Healthcare.
--
-- Postgres holds only the discovery queue + two small reference/log tables; the
-- rate data lives in Parquet and is read by DuckDB in the backend. See
-- ../docs/schema.md. This file runs once, on a fresh volume — for changes to a
-- running database use db/migrations/*.sql (`make migrate`).

-- ── billing_codes — reference upsert (Phase 2) ──────────────────────────────
CREATE TABLE IF NOT EXISTS billing_codes (
    billing_code_type VARCHAR(50),
    billing_code VARCHAR(100) PRIMARY KEY,
    name TEXT,
    description TEXT
);

-- ── index_files — the parse queue (Phase 1 writes, Phase 2 advances) ────────
-- One row per unique MRF URL. The plans a file serves are deliberately NOT a
-- column here: a rate file is shared across many plans, and the per-file array
-- is the shape that blew the heap (400k+ plans × 10k files). They live one row
-- per (file, plan) in index_file_plans below.
CREATE TABLE IF NOT EXISTS index_files (
    id SERIAL PRIMARY KEY,
    reporting_entity_name TEXT,
    reporting_entity_type TEXT,
    market_types TEXT[],
    hios_issuer_ids TEXT[],
    -- 2-letter state codes from HIOS plan_id[5:7] (positional, deterministic — no regex).
    plan_states TEXT[] DEFAULT '{}',
    network_entity TEXT,
    description TEXT,
    location TEXT NOT NULL,
    file_size_bytes BIGINT,
    status VARCHAR(20) DEFAULT 'pending',
    failure_reason TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    CONSTRAINT uq_index_files_location UNIQUE (location)
);

CREATE INDEX IF NOT EXISTS idx_index_files_status ON index_files(status);
CREATE INDEX IF NOT EXISTS idx_index_files_market ON index_files USING GIN(market_types);
CREATE INDEX IF NOT EXISTS idx_index_files_hios ON index_files USING GIN(hios_issuer_ids);
CREATE INDEX IF NOT EXISTS idx_index_files_plan_states ON index_files USING GIN(plan_states);

-- ── index_file_plans — the plan → file link the master index publishes ──────
-- One row per (file, plan) from reporting_structure[]: reporting_plans[] ×
-- in_network_files[]. This is what makes "which files serve plan X" answerable,
-- and what `etl parse -targets` selects on (etl/targets.yaml) instead of
-- guessing Georgia files from their filename.
CREATE TABLE IF NOT EXISTS index_file_plans (
    file_id      INTEGER NOT NULL REFERENCES index_files(id) ON DELETE CASCADE,
    plan_id      TEXT NOT NULL DEFAULT '',
    plan_id_type TEXT NOT NULL DEFAULT '',
    plan_name    TEXT NOT NULL DEFAULT '',
    market_type  TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_index_file_plans UNIQUE (file_id, plan_id, plan_name, market_type)
);

-- No index on plan_name: "which files serve plan X" matches with
-- ILIKE '%substring%' (docs/discover.md), which no btree can use regardless
-- of case-folding — an index here would just be dead weight.
CREATE INDEX IF NOT EXISTS idx_index_file_plans_plan_id
    ON index_file_plans (plan_id text_pattern_ops);

-- ── coverage_log — one observational row per parsed file (Phase 2) ──────────
-- What did this file contribute? Feeds `make cov-report`. Never read by the ETL.
-- file_id is UNIQUE (migration 004): the one-row-per-file invariant `cov-report`
-- keys on is the schema's job, and writeCoverageLog upserts on it. That
-- constraint's own index serves the file_id lookups, so no separate index.
CREATE TABLE IF NOT EXISTS coverage_log (
    id SERIAL PRIMARY KEY,
    file_id INTEGER UNIQUE,
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

-- ── test schema — mirrors public for isolated ETL test runs ────────────────
-- Connected via search_path=test in TEST_DATABASE_URL. Safe to TRUNCATE or DROP.
-- `LIKE ... INCLUDING ALL` is a one-time copy — when a public column is added,
-- rebuild the test schema via the newest db/migrations/*.sql (test.* is
-- disposable by charter).
CREATE SCHEMA IF NOT EXISTS test;

CREATE TABLE IF NOT EXISTS test.billing_codes    (LIKE public.billing_codes    INCLUDING ALL);
CREATE TABLE IF NOT EXISTS test.index_files      (LIKE public.index_files      INCLUDING ALL);
CREATE TABLE IF NOT EXISTS test.coverage_log     (LIKE public.coverage_log     INCLUDING ALL);
CREATE TABLE IF NOT EXISTS test.index_file_plans (LIKE public.index_file_plans INCLUDING ALL);

-- LIKE never copies foreign keys, so the test mirror's file_id → index_files
-- link has to be re-declared or a TRUNCATE ... CASCADE would orphan plan rows.
ALTER TABLE test.index_file_plans DROP CONSTRAINT IF EXISTS index_file_plans_file_id_fkey;
ALTER TABLE test.index_file_plans
    ADD CONSTRAINT index_file_plans_file_id_fkey
    FOREIGN KEY (file_id) REFERENCES test.index_files(id) ON DELETE CASCADE;

-- discover.go manages these by exact name in test mode (search_path=test).
CREATE INDEX IF NOT EXISTS idx_index_files_plan_states ON test.index_files USING GIN(plan_states);
CREATE INDEX IF NOT EXISTS idx_index_files_market      ON test.index_files USING GIN(market_types);
CREATE INDEX IF NOT EXISTS idx_index_files_hios        ON test.index_files USING GIN(hios_issuer_ids);
