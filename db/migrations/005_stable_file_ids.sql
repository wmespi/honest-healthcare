-- Migration 005 — index_files: a stable ID per file, and which update of it we parsed
--
-- A file's address is its location without the query a signed URL carries.
-- Discover matches each month's index to the queue on it, so a file keeps its ID
-- from month to month and only its signed location is refreshed. Parsed Parquet
-- is keyed by that ID (prices/net=<slug>/<id>.parquet), so a re-parse replaces the
-- file's old output instead of adding a second copy. A unique address implies a
-- unique location, so the location constraint goes (init.sql declared it; a
-- database older than init.sql's version never had it).
--
-- source_updated_at is when Anthem last updated the file (its Last-Modified), as
-- parse last saw it. parsed_updated_at is the Last-Modified of the copy the last
-- finished parse read. Parse downloads a parsed file again when they differ
-- (etl/queue.md).
--
-- Idempotent. Run with `make migrate`.

\set ON_ERROR_STOP on

ALTER TABLE public.index_files
    ADD COLUMN IF NOT EXISTS address TEXT GENERATED ALWAYS AS (split_part(location, '?', 1)) STORED;

ALTER TABLE public.index_files DROP CONSTRAINT IF EXISTS uq_index_files_location;
ALTER TABLE public.index_files DROP CONSTRAINT IF EXISTS uq_index_files_address;
ALTER TABLE public.index_files ADD CONSTRAINT uq_index_files_address UNIQUE (address);

ALTER TABLE public.index_files ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ;
ALTER TABLE public.index_files ADD COLUMN IF NOT EXISTS parsed_updated_at TIMESTAMPTZ;
