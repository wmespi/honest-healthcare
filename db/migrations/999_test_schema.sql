-- Migration 999 — rebuild the test schema from public (always runs last)
--
-- `CREATE TABLE test.x (LIKE public.x INCLUDING ALL)` is a one-time copy, so the
-- test mirror drifts every time a public column or table changes. test.* is
-- disposable by charter (Critical Rule 4), so `make migrate` drops and recreates
-- it after every other migration; the number keeps it last. Never do this to
-- public. db/init.sql builds the same mirror on a fresh volume.
--
-- Idempotent. Run with `make migrate`.

\set ON_ERROR_STOP on

DROP SCHEMA IF EXISTS test CASCADE;
CREATE SCHEMA test;

CREATE TABLE test.billing_codes    (LIKE public.billing_codes    INCLUDING ALL);
CREATE TABLE test.index_files      (LIKE public.index_files      INCLUDING ALL);
CREATE TABLE test.coverage_log     (LIKE public.coverage_log     INCLUDING ALL);
CREATE TABLE test.index_file_plans (LIKE public.index_file_plans INCLUDING ALL);

-- LIKE copies a SERIAL column's default, nextval('<table>_id_seq'), still naming
-- public's sequence, so every test insert would use up a public ID. Each test
-- table gets its own.
CREATE SEQUENCE test.index_files_id_seq OWNED BY test.index_files.id;
ALTER TABLE test.index_files ALTER COLUMN id SET DEFAULT nextval('test.index_files_id_seq');
CREATE SEQUENCE test.coverage_log_id_seq OWNED BY test.coverage_log.id;
ALTER TABLE test.coverage_log ALTER COLUMN id SET DEFAULT nextval('test.coverage_log_id_seq');

-- LIKE never copies foreign keys, so the file_id → index_files link has to be
-- re-declared. Without it a `TRUNCATE test.index_files CASCADE` would leave
-- orphaned plan rows behind.
ALTER TABLE test.index_file_plans
    ADD CONSTRAINT index_file_plans_file_id_fkey
    FOREIGN KEY (file_id) REFERENCES test.index_files(id) ON DELETE CASCADE;

-- Discover drops and recreates the GIN indexes by their exact, schema-unqualified
-- names. LIKE copies them under generated names, so swap those for the real ones
-- rather than keep two of each.
DROP INDEX IF EXISTS test.index_files_market_types_idx;
DROP INDEX IF EXISTS test.index_files_hios_issuer_ids_idx;
DROP INDEX IF EXISTS test.index_files_plan_states_idx;
CREATE INDEX idx_index_files_market      ON test.index_files USING GIN(market_types);
CREATE INDEX idx_index_files_hios        ON test.index_files USING GIN(hios_issuer_ids);
CREATE INDEX idx_index_files_plan_states ON test.index_files USING GIN(plan_states);
