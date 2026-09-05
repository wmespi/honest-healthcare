-- Migration 003 — index_file_plans: keep the plan → file link discovery publishes
--
-- The master index gives `reporting_structure[] = reporting_plans[] ×
-- in_network_files[]`. Discovery used to collapse that to per-file *sets*
-- (market_types / hios_issuer_ids / plan_states) and throw the plan identity
-- away, so "which files serve plan X" was unanswerable and `parse` had to guess
-- at Georgia files from their filename. This table keeps the pair.
--
-- One row per (file, plan). `index_files.plan_names TEXT[]` — the per-file array
-- that was reserved for this and never populated (it is the shape that blew the
-- heap: 400k+ plans × 10k files as arrays) — is dropped here; the relational
-- form carries strictly more (plan_id, plan_id_type, market_type) and lets
-- Postgres do the deduplication.
--
-- discovery.go stages a row only for Georgia individual-market plans
-- (market_type == "individual" or HIOS plan_id[5:7] == "GA") — not this
-- table's schema, but worth knowing here: unfiltered, the full
-- reporting_plans[] x in_network_files[] cross-product runs to ~180M
-- rows/month, ~40-50 GB of Postgres. See etl/discover.md.
--
-- Idempotent. Run with `make migrate`.

\set ON_ERROR_STOP on

-- ── index_file_plans — one row per (file, plan) ─────────────────────────────
-- plan_id / plan_id_type / market_type are NOT NULL DEFAULT '' rather than
-- nullable so the uniqueness constraint actually constrains: NULLs would let
-- the same pair be inserted repeatedly on every monthly re-run.
CREATE TABLE IF NOT EXISTS public.index_file_plans (
    file_id      INTEGER NOT NULL REFERENCES public.index_files(id) ON DELETE CASCADE,
    plan_id      TEXT NOT NULL DEFAULT '',
    plan_id_type TEXT NOT NULL DEFAULT '',
    plan_name    TEXT NOT NULL DEFAULT '',
    market_type  TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_index_file_plans UNIQUE (file_id, plan_id, plan_name, market_type)
);

-- Lookups run in both directions: "which plans does this file serve" (file_id,
-- covered by the unique constraint's leading column) and "which files serve a
-- plan matching X" — a plan_id prefix, which text_pattern_ops makes an
-- index-scannable LIKE 'prefix%'. No index on plan_name: "which files serve
-- plan X" matches with ILIKE '%substring%' (docs/discover.md), which a btree —
-- text_pattern_ops or otherwise — can't use regardless of case-folding, so an
-- index here would just be dead weight at the row counts this table reaches.
CREATE INDEX IF NOT EXISTS idx_index_file_plans_plan_id
    ON public.index_file_plans (plan_id text_pattern_ops);

-- ── index_files: drop the never-populated plan_names array ──────────────────
DROP INDEX IF EXISTS public.idx_index_files_plan;
ALTER TABLE public.index_files DROP COLUMN IF EXISTS plan_names;

-- ── test schema: rebuild from public ───────────────────────────────────────
-- `CREATE TABLE test.x (LIKE public.x INCLUDING ALL)` is a one-time copy, so the
-- test mirror drifts every time a public column or table is added. test.* is
-- disposable by charter (Critical Rule 4) — drop and recreate so isolation stays
-- trustworthy. Never do this to public.
DROP SCHEMA IF EXISTS test CASCADE;
CREATE SCHEMA test;

CREATE TABLE test.billing_codes    (LIKE public.billing_codes    INCLUDING ALL);
CREATE TABLE test.index_files      (LIKE public.index_files      INCLUDING ALL);
CREATE TABLE test.coverage_log     (LIKE public.coverage_log     INCLUDING ALL);
CREATE TABLE test.index_file_plans (LIKE public.index_file_plans INCLUDING ALL);

-- LIKE copies the CHECK/UNIQUE/index definitions but never the foreign keys, so
-- the test mirror's file_id → index_files link has to be re-declared. Without it
-- a `TRUNCATE test.index_files CASCADE` would leave orphaned plan rows behind.
ALTER TABLE test.index_file_plans
    ADD CONSTRAINT index_file_plans_file_id_fkey
    FOREIGN KEY (file_id) REFERENCES test.index_files(id) ON DELETE CASCADE;

-- discover.go drops/recreates these by exact (schema-unqualified) name in test
-- mode; INCLUDING ALL copies public's indexes only under auto-generated names.
CREATE INDEX IF NOT EXISTS idx_index_files_plan_states ON test.index_files USING GIN(plan_states);
CREATE INDEX IF NOT EXISTS idx_index_files_market      ON test.index_files USING GIN(market_types);
CREATE INDEX IF NOT EXISTS idx_index_files_hios        ON test.index_files USING GIN(hios_issuer_ids);
