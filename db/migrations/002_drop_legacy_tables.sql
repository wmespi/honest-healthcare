-- Migration 002 — drop the pre-Parquet legacy tables
--
-- negotiated_rates / provider_mappings / place_of_service_codes and the
-- vw_rates_detailed view predate the Parquet migration. No service writes or
-- reads them — the Go parser only touches billing_codes / index_files /
-- coverage_log, and the backend reads Parquet. See docs/schema.md.
-- Idempotent. Run with `make migrate`.

\set ON_ERROR_STOP on

DROP VIEW  IF EXISTS public.vw_rates_detailed;
DROP TABLE IF EXISTS public.negotiated_rates;
DROP TABLE IF EXISTS public.provider_mappings;
DROP TABLE IF EXISTS public.place_of_service_codes;

-- Rebuild the test schema without the legacy mirrors. test.* is disposable by
-- charter — drop and recreate so isolation stays trustworthy. Never do this to
-- public.
DROP SCHEMA IF EXISTS test CASCADE;
CREATE SCHEMA test;

CREATE TABLE test.billing_codes (LIKE public.billing_codes INCLUDING ALL);
CREATE TABLE test.index_files   (LIKE public.index_files   INCLUDING ALL);
CREATE TABLE test.coverage_log  (LIKE public.coverage_log  INCLUDING ALL);

-- discover.go manages these by exact (schema-unqualified) name in test mode.
CREATE INDEX IF NOT EXISTS idx_index_files_plan_states ON test.index_files USING GIN(plan_states);
CREATE INDEX IF NOT EXISTS idx_index_files_market      ON test.index_files USING GIN(market_types);
CREATE INDEX IF NOT EXISTS idx_index_files_hios        ON test.index_files USING GIN(hios_issuer_ids);
