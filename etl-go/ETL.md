# ETL Pipeline — Anthem Machine-Readable Files

## Overview

This Go service ingests Anthem's CMS-mandated Machine-Readable Files (MRFs) — multi-gigabyte gzipped JSON files listing every in-network negotiated rate — and loads them into Postgres. It runs as a Docker container (`etl_go`) alongside the database.

The pipeline has two sequential phases, each triggered by a CLI flag:

```
go run . -discover    # Phase 1: populate index_files with all rate file URLs
go run . -parse       # Phase 2: stream each pending file directly into Postgres
```

---

## Phase 1: Discovery (`-discover`)

**What it does:** Fetches the Anthem master index (`_anthem_index.json.gz`), walks every `reporting_structure` entry, and extracts **all** `InNetworkFiles` entries — no URL pattern filter. Every file URL Anthem lists is captured, including individual market plans and any non-standard path patterns.

**Upsert strategy:** Rather than 150k individual `INSERT` round-trips, all candidates are collected in memory during streaming and then written in a single query using `UNNEST`:

```sql
INSERT INTO index_files (plan_name, description, location)
SELECT * FROM unnest($1::text[], $2::text[], $3::text[])
ON CONFLICT ON CONSTRAINT uq_index_files_location DO NOTHING
```

One round-trip regardless of row count. Re-running discovery never creates duplicates.

**Index schema capture (`-index-schema`):** A separate flag that streams the same index file and writes a compact JSON example to `data/anthem/index_schema.json`. Arrays are truncated to 1 item so the output is human-readable. No DB connection required — useful for inspecting the index structure before running discovery.

---

## Phase 2: Parsing (`-parse`)

**What it does:** For each file in `index_files WHERE status = 'pending'`, streams the gzipped JSON directly and inserts rows into `provider_mappings`, `negotiated_rates`, and `billing_codes`. The `Content-Length` from the HTTP GET response is immediately written to `index_files.file_size_bytes` — no separate HEAD request needed. Sizes are `NULL` until a file is first parsed; the parse queue orders by `file_size_bytes ASC NULLS LAST` so that once sizes are known, smaller files are prioritized.

**Status lifecycle per file:**

```
pending → processing → completed
                   ↘ failed
```

- `pending`: discovered, not yet processed
- `processing`: currently being streamed (or crashed mid-run — see Recovery below)
- `completed`: fully ingested; skipped on future parse runs
- `failed`: encountered an unrecoverable error; can be manually reset to `pending` to retry

**Current state:** Parsing writes to CSV files and does not yet talk to Postgres. Migration is the next major task.

---

## Idempotency

Idempotency is enforced at two levels:

**1. Discovery idempotency** — `INSERT INTO index_files ... ON CONFLICT (location) DO NOTHING`. Running `-discover` twice against the same Anthem index produces the same set of rows.

**2. Parse idempotency** — The parser only selects `WHERE status = 'pending'`. Files already marked `completed` are never re-fetched or re-inserted. All inserts into `negotiated_rates` and `provider_mappings` use `ON CONFLICT DO NOTHING` backed by appropriate unique constraints, so a partial re-run of any single file is safe.

---

## Recovery from Crashes

If the process dies while a file is `processing`, those rows stay in `processing` indefinitely. On startup (or manually), reset them:

```sql
UPDATE index_files SET status = 'pending' WHERE status = 'processing';
```

This is intentional — automatic reset-on-startup would mask repeated failures on a specific bad file. Investigate first, then reset.

---

## Test Mode (`-test`)

Test mode is designed to never touch production data. The strategy:

- **Schema isolation:** Test runs connect to the same `honest_healthcare` database but with `search_path=test` appended to the connection URL. All writes go to the `test` schema (e.g. `test.negotiated_rates`), which mirrors the production `public` schema structure exactly — same tables, indexes, and constraints. The `test` schema is defined in `db/init.sql` via `LIKE public.tablename INCLUDING ALL`.
- **Smaller limits:** Test mode caps discovery at 100 reporting structures and parsing at 1 file (overridable with `-limit`).
- **Safe to reset:** `TRUNCATE test.negotiated_rates, test.provider_mappings, ...` at any time with no production impact.
- **No cross-contamination:** Switching between modes is controlled entirely by the connection URL (`TEST_DATABASE_URL` vs `DATABASE_URL`) — no code paths are shared.

Running tests:
```bash
docker compose run etl_go go run . -discover -test
docker compose run etl_go go run . -parse -test
```

---

## Data Refresh Strategy

**We do not maintain historical rates.** When Anthem publishes a new monthly index, the entire dataset is replaced:

1. TRUNCATE `negotiated_rates`, `provider_mappings`, `billing_codes`, `index_files` (in that order, respecting foreign keys if added later).
2. Re-run `-discover` against the new index URL to repopulate `index_files` with `status = 'pending'`.
3. Re-run `-parse` to stream the new data in.

The monthly index URL follows the pattern `YYYY-MM-01_anthem_index.json.gz`. Pass it explicitly:

```bash
go run . -discover -index-url "https://...2026-08-01_anthem_index.json.gz"
```

**Why no history?** Storage cost grows proportionally with each month's full dataset (tens of GBs). Rate changes between months are typically incremental and not yet surfaced in the UI. If point-in-time comparisons become a product requirement, we'll revisit — but the `completed_at` timestamp on `index_files` provides a lightweight audit trail of when data was ingested.

---

## Progress Logging

The parser logs progress with live DB size and ETA in the same format as the download progress bar:

```
⚙️  Loaded 500,000 provider rows...   | DB Size: 1.2 GB | [████████░░░░░░░] 53.2% | ETA: 3m22s (2:45 PM ET)
⚙️  Loaded 1,000,000 provider rows... | DB Size: 2.4 GB | [██████████████░] 94.1% | ETA: 0m18s (2:48 PM ET)
```

DB size is sampled via `SELECT pg_database_size('honest_healthcare')` every 500k rows to avoid hammering the DB with system queries.

---

## File Map

| File | Purpose |
|------|---------|
| `main.go` | CLI entry point, flag routing, test/prod path selection |
| `discover.go` | Phase 1: fetch master index, extract all file URLs; also `captureIndexSchema` |
| `parse.go` | Phase 2: stream individual rate files, extract rows, capture file sizes |
| `types.go` | Shared structs, global vars, `DATABASE_URL` init |
| `progress.go` | `ProgressReader` — wraps HTTP body to track bytes + ETA |

**CLI flags:**

| Flag | Description |
|------|-------------|
| `-discover` | Phase 1: populate `index_files` from master index |
| `-parse` | Phase 2: process pending files from `index_files` |
| `-index-schema` | Stream master index, write compact schema example to `data/anthem/index_schema.json` (no DB) |
| `-test` | Write to `test` schema instead of `public` |
| `-limit N` | Cap number of reporting structures (discover) or files (parse) |
| `-index-url URL` | Override the master index URL |

---

## Roadmap

- [x] Migrate discovery output from JSON file → `index_files` table (upsert)
- [x] Migrate parse output from CSV files → direct Postgres streaming inserts
- [x] Add live DB size to progress logger
- [x] Add test schema isolation (`search_path=test`)
- [ ] Add startup check: reset stale `processing` rows older than N hours
- [ ] Add `-reset` flag to mark all `completed` rows back to `pending` for a full refresh
- [ ] Parallelize file parsing (2–4 concurrent workers)
- [ ] Add retry with exponential backoff before marking files `failed`

---

## Known Issues

These problems don't affect a single-operator sequential run but will cause correctness or operational issues at scale.

### Critical

**No deduplication on `negotiated_rates` / `provider_mappings`**
These tables have no unique constraints. If a failed file is reset to `pending` and re-run, its rows are inserted again — silently creating duplicates. The `pgx` COPY protocol can't use `ON CONFLICT`, so the fix requires either a temp-table + merge pattern, or wrapping each file's parse in a transaction that rolls back on failure.

**`pending → processing` status transition is not atomic**
`parse.go` SELECTs pending files and UPDATEs their status in two separate operations. If two parse containers ever run simultaneously, they can race and double-process the same file. Fix: use `SELECT ... FOR UPDATE SKIP LOCKED` inside a transaction to atomically claim a file before marking it `processing`.

**Partial data survives failed files**
If parsing fails after some COPY batches have already flushed, those rows persist in the DB. The file is marked `failed` but the partial rows are not cleaned up. Combined with the deduplication gap above, retrying a failed file appends duplicate rows. Fix: use per-file transactions or track a `parse_attempt_id` and delete partial rows on failure.

### Significant

**No retry on transient errors**
A network timeout or 5xx response mid-stream permanently marks a file `failed`. For a multi-hour, 9,000-file run, dozens of files will fail this way. Manual reset-to-`pending` is the only recovery path today. Fix: exponential backoff with 3 retries before marking `failed`.

**Single `pgx.Conn` instead of connection pool**
`main.go` uses `pgx.Connect()` — a single connection. Adequate for sequential processing, but will serialize all DB calls if file parsing is ever parallelized. Fix: switch to `pgxpool.New()` now while the callsites are few.

**Sequential file processing**
Files are processed one at a time. The parser blocks on HTTP download I/O for most of each file's duration. Running 2–4 files concurrently would meaningfully reduce total pipeline runtime with minimal memory overhead.

### Minor

**`isFirstFile` schema capture is fragile**
The first file parsed is used to capture a schema snapshot written to `mrf_example.json`. To do this, the code decodes the first item in each array into a `map[string]interface{}`, marshals it to JSON bytes, then unmarshals those bytes back into the typed struct (e.g. `ProviderReference`). This double-conversion is wasteful, and if the first file has missing or unusual fields, the typed struct silently gets zero-valued fields instead of an error — potentially writing empty rows to the DB for the entire first file.

**`file_size_bytes` unused for scheduling**
File sizes are fetched via concurrent HEAD requests during discovery and stored in `index_files`, but the parse queue orders by `id` (insertion order). Smallest files first would give faster feedback; very large files (>5 GB) could be deprioritized.
