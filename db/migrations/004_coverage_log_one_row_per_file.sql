-- Migration 004 — coverage_log: enforce one row per file
--
-- coverage_log's own comment has always said "one observational row per parsed
-- file", and `make cov-report` trusts it (issue #52's suspicious-completion
-- check keys on file_id). Nothing enforced it: file_id was a plain nullable
-- column with only a non-unique index. writeCoverageLog tried to hold the
-- invariant with `WITH prior AS (DELETE …) INSERT`, but that is fragile — it
-- races two concurrent parses of the same file, and it never cleaned the rows
-- left by the plain-INSERT version that predated it (file 21057 carried four).
--
-- This makes file_id UNIQUE, so the invariant is the schema's job, and
-- writeCoverageLog becomes a plain `INSERT … ON CONFLICT (file_id) DO UPDATE`.
--
-- Applied to public and to test. Migration 003 rebuilds the test schema from
-- public on every `make migrate`, so on the second run test.coverage_log
-- already carries the LIKE-copied unique constraint under PostgreSQL's default
-- name (coverage_log_file_id_key); both possible names are dropped before ours
-- is added so a re-run never stacks duplicate indexes.
--
-- Idempotent. Run with `make migrate`.

\set ON_ERROR_STOP on

DO $$
DECLARE s text;
BEGIN
	FOREACH s IN ARRAY ARRAY['public', 'test'] LOOP
		IF to_regclass(s || '.coverage_log') IS NULL THEN
			CONTINUE;
		END IF;

		-- Keep the most recent row (highest id) per file_id, drop the rest.
		-- NULL file_id rows are left alone — UNIQUE permits many NULLs.
		EXECUTE format(
			'DELETE FROM %I.coverage_log a USING %I.coverage_log b
			   WHERE a.file_id = b.file_id AND a.file_id IS NOT NULL AND a.id < b.id', s, s);

		-- The plain index is redundant once file_id is unique. Drop it under
		-- both the name migration 001 gives it and the name a `LIKE … INCLUDING
		-- ALL` copy into the test schema renames it to.
		EXECUTE format('DROP INDEX IF EXISTS %I.idx_coverage_log_file', s);
		EXECUTE format('DROP INDEX IF EXISTS %I.coverage_log_file_id_idx', s);

		EXECUTE format('ALTER TABLE %I.coverage_log DROP CONSTRAINT IF EXISTS uq_coverage_log_file', s);
		EXECUTE format('ALTER TABLE %I.coverage_log DROP CONSTRAINT IF EXISTS coverage_log_file_id_key', s);
		EXECUTE format('ALTER TABLE %I.coverage_log ADD CONSTRAINT uq_coverage_log_file UNIQUE (file_id)', s);
	END LOOP;
END $$;
