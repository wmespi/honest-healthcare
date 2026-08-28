# ETL Pipeline — Anthem Machine-Readable Files

## CMS Specification

These files are federally mandated under the Transparency in Coverage rule (45 CFR § 147.210). CMS publishes the full technical schema, valid field values, and file format requirements on GitHub:

- **Schema reference:** https://github.com/CMSgov/price-transparency-guide
- **In-network rates schema:** https://github.com/CMSgov/price-transparency-guide/tree/master/schemas/in-network-rates
- **Table of contents schema:** https://github.com/CMSgov/price-transparency-guide/tree/master/schemas/table-of-contents

This is the authoritative source for questions like "what are all valid `negotiated_type` values?" and "is `setting` always present?"

---

## MRF Data Model

### How Plan, Network, File, and Provider Relate

```
┌─────────────────────────────────────────────────────────────────┐
│                  ANTHEM MASTER INDEX (TOC)                      │
│          2026-07-01_anthem_index.json.gz                        │
└─────────────────────────────────────────────────────────────────┘
         │ reporting_structure[]
         │ Each entry = one PLAN
         ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  PLAN                                                       │
  │  plan_name: "BLUE VALUE IND NETWORK HMO - ANTHEM"          │
  │  plan_id:   "..."    plan_market_type: "individual"         │
  └─────────────────────────────────────────────────────────────┘
         │ in_network_files[]
         │ A plan may link to MANY rate files
         ├──────────────────────────────────────────┐
         ▼                                          ▼
  ┌──────────────────────┐              ┌──────────────────────────┐
  │ PLAN-SPECIFIC FILE   │              │   SHARED NETWORK FILE    │
  │ GA_JBKEMED0001.gz    │              │   ~244 other files       │
  │ 1 plan only          │              │   Up to 144k plans each  │
  └──────────────────────┘              └──────────────────────────┘
         │                                          │
         │ Both files share the same internal schema│
         └──────────────────┬───────────────────────┘
                            ▼

┌─────────────────────────────────────────────────────────────────┐
│  RATE FILE (in-network JSON)                                    │
│                                                                 │
│  in_network[]          ◄── one entry per billing code          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  BILLING CODE ITEM                                      │   │
│  │  billing_code:      "99213"                             │   │
│  │  billing_code_type: "CPT"                               │   │
│  │  name:              "Office or other outpatient visit"  │   │
│  │                                                         │   │
│  │  negotiated_rates[]  ◄── one entry per rate/group combo │   │
│  │  ┌───────────────────────────────────────────────────┐ │   │
│  │  │  provider_references: [1020000797660]  ◄── ID ref │ │   │
│  │  │                                                   │ │   │
│  │  │  negotiated_prices[]                              │ │   │
│  │  │  ┌─────────────────────────────────────────────┐ │ │   │
│  │  │  │  negotiated_rate:  89.45                    │ │ │   │
│  │  │  │  negotiated_type: "fee schedule"            │ │ │   │
│  │  │  │  billing_class:   "professional"            │ │ │   │
│  │  │  │  setting:         "outpatient"              │ │ │   │
│  │  │  │  expiration_date: "9999-12-31"              │ │ │   │
│  │  │  └─────────────────────────────────────────────┘ │ │   │
│  │  └───────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  provider_references[] ◄── NPI lookup table (separate section) │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  PROVIDER REFERENCE                                     │   │
│  │  provider_group_id: 1020000797660    ◄── matches above  │   │
│  │  network_name: ["Blue Value Individual Commercially..."] │   │
│  │                                                         │   │
│  │  provider_groups[]                                      │   │
│  │  ┌───────────────────────────────────────────────────┐ │   │
│  │  │  PROVIDER GROUP (= billing entity / TIN)          │ │   │
│  │  │  tin: { type: "npi", value: "1902943590" }        │ │   │
│  │  │  npi: [1841337524, 1902943590]  ◄── individual    │ │   │
│  │  │                                     providers      │ │   │
│  │  └───────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘


ENTITY RELATIONSHIPS
════════════════════

PLAN (from index)
  │  1 plan links to N rate files
  ▼
RATE FILE
  │  1 file has N billing codes
  ▼
BILLING CODE  (billing_code + billing_code_type = unique procedure)
  │  1 code has N negotiated_rates entries
  ├───────────────────────────────────────────────────────┐
  ▼                                                       ▼
NEGOTIATED PRICE                             PROVIDER_REFERENCE (by ID)
  negotiated_rate                                │
  negotiated_type ("fee schedule", "derived"...) │ resolves via provider_references[]
  billing_class ("professional", "institutional")▼
  setting ("outpatient", "inpatient")         PROVIDER GROUP (TIN)
                                              tin_type | tin_value
                                                 │
                                                 │ 1 group has N NPIs
                                                 ▼
                                               NPI[]
                                            (individual clinicians or
                                             locations under same TIN)


OUR PARQUET SCHEMA
══════════════════

  rates/{fileID}.parquet
    provider_group_id | plan_name | billing_code | billing_code_type
    negotiated_rate   | negotiated_type | expiration_date | service_code

  providers/{fileID}.parquet
    provider_group_id | npi | tin_type | tin_value

  codes/{fileID}.parquet
    billing_code | billing_code_type | name | description
```

### Why the Same Rate Can Appear in Multiple Files for the Same Plan

The CMS rule requires insurers to publish **all** rate files that a plan participates in. Anthem structures its provider relationships in layers:

- **Network-level rates** (shared files): Anthem negotiates a base contract for an entire network — e.g., "all providers in the GA Blue Value network get fee schedule X." That one contract applies across thousands of plans, so the rates live in a shared file that all those plans link to.
- **Plan-level rates** (plan-specific files): For certain plans, Anthem negotiates an additional discount or a custom tier on top of the network rate. Those rates live in a plan-specific file.

The result: the same `(billing_code + provider_group + plan)` combination can legitimately appear in both a plan-specific file and one or more shared files, with potentially different rates.

**The CMS spec provides no deduplication mechanism** — it defines the file schema but leaves conflict resolution entirely to the consumer. This means a patient or researcher trying to find their actual rate must download terabytes of files, join them all, and infer the right rate without any official guidance on precedence. The complexity is a consequence of a regulation written for policy goals rather than data engineering reality, but the practical effect is significant obfuscation of the real rate.

### Conflict Resolution Strategy

When parsing multiple files for the same plan:

| Scenario | What to do |
|---|---|
| Same code + provider group in plan-specific AND shared file | **Plan-specific wins** — it represents the most directly negotiated rate for that plan |
| Code exists in shared file but NOT in plan-specific file | **Include it** — plan members can access those providers/codes through the shared network |
| Same code + provider group in two shared files | **Take the lower rate** — both are network-level rates; lower is what a member should be charged |

**Practical implementation:** stamp `source_file_id` and `plan_count` (number of plans that file serves) on every rate row. When querying for a specific plan, rank rows from single-plan files above rows from multi-plan files on tie-break.

---

## Overview

This Go service ingests Anthem's CMS-mandated Machine-Readable Files (MRFs) — multi-gigabyte gzipped JSON files listing every in-network negotiated rate. It runs as a Docker container (`etl_go`) alongside the database.

The pipeline has two sequential phases, each triggered by a CLI flag:

```
go run . -discover    # Phase 1: populate index_files with all rate file URLs
go run . -parse       # Phase 2: stream each pending file → Parquet (+ a little Postgres)
```

> **This doc lags the code in places** (it still describes CSV output and direct
> `negotiated_rates` inserts). Authoritative state:
> - Phase 2 writes **Parquet** (`data/anthem/{rates,providers,codes}/{id}.parquet`, ZSTD) plus a
>   `billing_codes` upsert and one `coverage_log` row per file. It does not write `negotiated_rates`.
> - The streaming core is `streamMRF` in `mrf.go`, shared by `-parse` and the unit tests.
> - **Structured attribution (no regex):** `provider_references[].network_name` (e.g.
>   `["GA Blue Value HIX Individual Network"]`) is mapped `provider_group_id → network_name` during
>   the provider pass and stamped onto every provider and rate row. `discover.go` also captures
>   `plan_states` from HIOS `plan_id[5:7]` (positional state code) into `index_files`.
> - **GA priority:** `-parse -priority` orders by `gaPriorityExpr` (`priority.go`) — individual-market
>   Georgia files first. Signals: `market_types ∋ individual`, `plan_states ∋ GA`, GA issuer IDs
>   `{49046,45334,44113}`, `anthem/GA_*` path. The `anthembcbsga.mrf.bcbs.com` host is the BlueCard
>   mirror and is **not** a GA signal.
> - **Fixtures:** `-make-fixture` writes a truncated `*.json.gz` to `testdata/fixtures/`; `-parse
>   -fixture PATH` reads one offline. `make etl-test` / `make nppes-test` run hermetic e2e tests with
>   full teardown.
> - **Signed URLs expire (~30 days) and the index path carries a `YYYY-MM_` prefix** — re-run
>   `-discover -no-cache` monthly and prune the prior month's rows.
> - **NPPES:** `-nppes` streams the national dissemination zip and writes the GA-only subset to
>   `data/nppes/ga_providers.parquet` (hospital = taxonomy `28x`, clinic = `261Q`).

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
