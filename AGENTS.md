# Honest Healthcare — Agent Charter

Price transparency tooling for Anthem Machine-Readable Files (MRFs). Streams multi-GB
negotiated rate files in one pass, stores them as Parquet, and exposes a rate explorer UI.

**Primary use case:** rates for `BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM` (an
individual HMO on Anthem's Blue Value network in Georgia). The reliable filter is the structured
`network_name` **`GA Blue Value HIX Individual Network`** (from `provider_references`), now captured
end-to-end. Mapping the free-text plan *name* → network still isn't wired — see [Known gaps](#known-gaps).

---

## Architecture

| Layer | Tech | Purpose |
|---|---|---|
| ETL | Go (streaming JSON) | Parses gzipped MRF files in one pass; writes Parquet + a little Postgres |
| Storage | Parquet + ZSTD | `data/anthem/rates/`, `providers/`, `codes/`, `npi_lookup.parquet`; `data/nppes/ga_providers.parquet` |
| Backend | Python + DuckDB | Queries the Parquet globs in-process via FastAPI (`localhost:8000`) |
| Frontend | React + Vite | Rate explorer: histogram, filters, NPI search — `localhost:5173` |
| Discovery DB | Postgres 15 + PostGIS | `index_files` queue (URLs, status, sizes, `market_types`, `hios_issuer_ids`, `plan_states`) + `coverage_log` |

Docker services: `db`, `etl_go`, `backend`, `frontend`.

**Where data actually lands:**

- **Parquet** (`data/anthem/…`, `data/nppes/…`) — rate rows, provider rows, billing-code rows, NPI lookup, and the NPPES Georgia provider subset. This is what the backend reads.
- **Postgres** — `index_files` (the discovery/parse queue), `billing_codes` (a reference upsert), and `coverage_log` (one observational row per parsed file). The `negotiated_rates`, `provider_mappings`, `place_of_service_codes` tables and the `vw_rates_detailed` view exist in `db/init.sql` but are **not currently written or read** by any service; treat them as legacy until re-adopted.

---

## Development Commands

All commands wrap `docker compose`. **Always use `exec` (not `run --rm`)** so logs appear in Docker Desktop.

```bash
make help                    # list all available targets

# Infrastructure
make start                   # launch Docker Desktop (if needed) + start all containers
make up                      # start containers, attached (live logs)
make down                    # stop all containers
make logs                    # follow logs from all services

# ETL — Discovery
make etl-discover            # Phase 1: populate index_files from the Anthem master index
make etl-discover-test       # Phase 1 in test isolation
make etl-index-schema        # stream master index, write index_schema.json (no DB writes)

# ETL — Parsing
make etl-parse               # Phase 2: stream pending files into Parquet (add -priority via the CLI for GA-first)
make etl-parse-test          # Phase 2 in test isolation
make etl-parse-file ID=10065 # parse a single file by index_files.id
make etl-size                # backfill file_size_bytes via concurrent HEAD requests
make etl-fixture ID=5043 NAME=ga_small   # write a truncated *.json.gz fixture to etl-go/testdata/fixtures/

# ETL — NPPES (Georgia provider subset)
make nppes                   # download NPPES national file, write data/nppes/ga_providers.parquet (GA only)
make nppes URL="…_V3.zip"    # override the monthly URL (CMS re-cuts with _V<n> suffixes)
make nppes-test              # hermetic GA-extraction test on the committed CSV fixture, with teardown

# ETL — Quality (stack must be running)
make etl-fmt                 # gofmt -w on etl-go source
make etl-vet                 # go vet ./... static analysis
make etl-build               # verify etl-go compiles cleanly
make etl-unit                # go test ./... — hermetic, fixture-driven
make etl-check               # fmt + vet + build + unit
make etl-test                # hermetic e2e: parse a committed fixture in test isolation, WITH teardown
make check                   # top-level gate — run before every commit

# Coverage feedback loop
make coverage-probe LABEL=before   # ~40-code scorecard for the target plan → data/anthem/coverage_scorecard.json
make coverage-report               # aggregate coverage_log — what each parsed file contributed
make backend-test                  # FastAPI contract + coverage tests (pytest)

# Database
make db-psql                 # open psql shell on honest_healthcare
make db-migrate              # apply db/migrations/*.sql to a running DB (init.sql only runs on a fresh volume)
make db-reset-processing     # reset stale 'processing' rows → 'pending'
make db-reset-failed         # reset TRANSIENTLY-failed rows → 'pending' (keeps bad-gzip/EOF/4xx failures)

# Shells
make sh-etl                  # shell into etl_go container
make sh-backend              # shell into backend container
```

Direct `docker compose exec` (when not using make):

```bash
docker compose exec etl_go go run . -discover
docker compose exec etl_go go run . -index-schema
docker compose exec etl_go go run . -parse -file-ids 10065
docker compose exec db psql -U postgres -d honest_healthcare
```

### Go CLI flags (`etl-go`)

| Flag | Effect |
|---|---|
| `-discover` | Phase 1 — fetch master index, upsert file URLs into `index_files` |
| `-index-schema` | Stream the index, write a truncated schema example to `data/anthem/index_schema.json`, no DB |
| `-parse` | Phase 2 — stream `pending` files into Parquet |
| `-priority` | With `-parse`: order the queue by `gaPriorityExpr` (GA/individual first) then size |
| `-all-npis` | Disable the GA NPI filter — keep every provider/rate (default filters when `ga_providers.parquet` exists) |
| `-size` | HEAD every `index_files` row with a NULL `file_size_bytes`, fill it in (concurrent) |
| `-file-ids 1,2,3` | Parse specific IDs, bypassing the queue order |
| `-make-fixture` | Stream one MRF, write a truncated `*.json.gz` to `etl-go/testdata/fixtures/` (with `-file-ids N` or `-fixture-url`, `-fixture-name`) |
| `-fixture PATH` | With `-parse -file-ids N`: read a local `*.json.gz` instead of downloading (offline) |
| `-nppes` | Download the NPPES national file, write the GA subset to `data/nppes/ga_providers.parquet` (`-nppes-url`, `-nppes-file` override) |
| `-limit N` | Cap reporting structures (discover) or files (parse); defaults to 100 in test mode |
| `-index-url URL` | Override the monthly master-index URL |
| `-test` | Isolated mode — `TEST_DATABASE_URL` (`search_path=test`) + write Parquet under `../data-test/` |
| `-dry-run` | Stream and capture schema, skip all writes |
| `-no-cache` | Force re-download of the master index even if a local cache exists |

---

## ETL Workflow

### Phase 1 — Discovery (`-discover`)

1. Downloads the Anthem master index to a local gzip cache (`data/anthem/index_cache.json.gz`),
   using parallel HTTP Range requests. Subsequent runs on the same monthly URL skip the download
   (`-no-cache` forces a refresh).
2. Streams the JSON, walks every `reporting_structure`, and for each `in_network_files` entry
   accumulates, per unique file URL:
   - `market_types` — set of `plan_market_type` values (`individual` / `group`)
   - `hios_issuer_ids` — set of 5-digit HIOS issuer IDs (first 5 chars of each HIOS `plan_id`; maps to state)
   - `plan_states` — set of 2-letter state codes from HIOS `plan_id[5:7]` (positional, deterministic — the
     no-regex GA signal)
   - `reporting_entity_name` / `reporting_entity_type` — from the index root ("Anthem Inc"); the parser
     later overwrites these with the more specific per-file value ("Anthem Blue Cross and Blue Shield Georgia")
   - `network_entity` — the prefix before `" : "` in the file description (BlueCard files only; else NULL)
   - `description`, `location`

   Plan **names** are intentionally *not* stored — the plans × files cross-product blows the heap
   (400k+ unique plans, 10k+ files). `market_types` + `hios_issuer_ids` + `plan_states` cover the filtering
   needs; `network_name` (from `provider_references`) is the per-rate network label written at parse time.
3. Writes a compact schema example to `data/anthem/index_schema.json`.
4. Bulk-loads via `COPY` into a `TEMP` staging table, then set-based
   `UPDATE index_files … FROM _idx_stage` (existing rows) + `INSERT … LEFT JOIN … WHERE t.id IS NULL`
   (new rows). GIN indexes on `market_types` / `hios_issuer_ids` / `plan_states` are dropped before the
   write and rebuilt once after. Re-running is safe — `location` is the natural key **within a month**.

Monthly index URL pattern: `YYYY-MM-01_anthem_index.json.gz` (built from the current month). Override:

```bash
docker compose exec etl_go go run . -discover -no-cache -index-url "https://…/2026-08-01_anthem_index.json.gz"
```

> **Signed-URL expiry (important).** Every file `location` is a CloudFront-signed URL with
> `?Expires=…&Signature=…` that dies in ~30 days, and the monthly index's file paths carry a
> `YYYY-MM_` prefix — so each month's files are genuinely new rows, not updates to last month's.
> Practically: **re-run `-discover -no-cache` at the start of each month**, then prune the dead
> prior-month rows (`DELETE FROM index_files WHERE location LIKE '%/PREV-MM\_%' AND status IN
> ('pending','failed')`). `location` is not a cross-month key — a future improvement is to store the
> query-stripped path and dedupe on that.

### Phase 2 — Parsing (`-parse`)

For each `index_files` row with `status = 'pending'` (ordered `file_size_bytes ASC NULLS LAST, id`,
or `gaPriorityExpr DESC, …` with `-priority`):

1. Marks the row `processing`, GETs the gzipped file (or reads `-fixture PATH`), streams it once
   via `streamMRF` (the shared token scanner; `provider_references` must precede `in_network`).
2. Builds a `provider_group_id → network_name` map from `provider_references[].network_name`
   (a structured array, e.g. `["GA Blue Value HIX Individual Network"]`) and stamps `network_name`
   onto every provider row and rate row — structured attribution, no string matching.
   **GA NPI filter (default on when `data/nppes/ga_providers.parquet` exists):** a provider row is
   kept only if its NPI is a Georgia NPPES NPI; a provider group with no GA NPI is dropped entirely,
   and every rate row referencing it goes too. `-all-npis` disables this. For GA-plan-specific files
   the loss is small (~0.4% of rates, ~23% of provider rows for `GA_JBNKMED0001`); for
   BlueCard-mirror / out-of-state files it drops 85–100% (many parse to zero rows). The
   `coverage_log.notes` column records the drop counts.
3. Writes three Parquet files keyed by `index_files.id`:
   `rates/{id}.parquet`, `providers/{id}.parquet`, `codes/{id}.parquet` (all ZSTD).
4. Upserts each new billing code into the Postgres `billing_codes` table
   (`ON CONFLICT (billing_code) DO NOTHING`).
5. Writes one `coverage_log` row per completed file (rate/provider row counts, new codes/NPIs/TINs,
   distinct networks/settings/billing-classes) — observational, never read by the ETL.
6. After the whole run, writes/overwrites `npi_lookup.parquet` (dedup NPI → TIN value across all files parsed this run).
7. Marks the row `completed` (+ `completed_at`, + per-file `reporting_entity_*`), or `failed`
   (+ `failure_reason`) on any unrecoverable error.

### GA prioritization (`-priority`)

`gaPriorityExpr` (in `etl-go/priority.go`) scores each pending row 0–3 for the primary use case
(individual-market Georgia rates). Signals are all deterministic/structural — no regex, no plan-name
matching: `market_types ∋ 'individual'`, `plan_states ∋ 'GA'`, `hios_issuer_ids ∩ {49046,45334,44113}`,
and `location` being an `…amazonaws.com/anthem/GA_…` plan-specific file. The `anthembcbsga.mrf.bcbs.com`
host is deliberately **not** a signal — it is the BlueCard mirror and serves every Blues plan's files.
Tiers: 3 = individual AND a GA signal; 2 = individual; 1 = a GA signal; 0 = the rest.

`Content-Length` from the GET is written to `index_files.file_size_bytes` on every parse of a file
(no separate HEAD needed); `make size` backfills it ahead of time so the queue can be size-ordered.

Status lifecycle: `pending → processing → completed | failed`.

### Recovery

```sql
-- Reset stale rows after a crash (or use make reset-processing)
update index_files set status = 'pending' where status = 'processing';
```

Do not auto-reset on startup — investigate repeated failures on specific files first.

### Test isolation (`-test`)

`-test` swaps two things:

- **DB** — connects via `TEST_DATABASE_URL` (`search_path=test`); `billing_codes` / `index_files` /
  `coverage_log` writes hit `test.*`.
- **Parquet** — output dirs move to `../data-test/anthem/…` (rates, providers, codes, `npi_lookup.parquet`,
  `mrf_example.json`) and `../data-test/nppes/ga_providers.parquet`.

Both `test.*` and everything under `data-test/` are safe to truncate/delete at any time. Discovery
in test mode caps at 100 reporting structures; parsing caps at 1 file (override with `-limit`).

> The `test` schema is created once from `public` via `LIKE … INCLUDING ALL`, so it **drifts** when a
> public column is added. `db/migrations/001_ga_coverage.sql` drops and recreates the whole `test`
> schema from `public` — run `make db-migrate` after any schema change.

### Test harness (fixtures + teardown)

- **Fixtures** live in `etl-go/testdata/` and are committed:
  - `synthetic_mrf.json` — hand-written MRF exercising every parser branch; `mrf_test.go` runs
    `streamMRF` over it (hermetic — no network, no DB).
  - `nppes_sample.csv` — ~14 rows for the NPPES GA extractor.
  - `fixtures/*.json.gz` — real, heavily-truncated MRFs from `-make-fixture` (first 25 provider refs,
    first 25 in-network items that touch a kept group, prices capped). Used by `-parse -fixture`.
- **`make etl-test`** (`scripts/etl_e2e_test.sh`) — parses `fixtures/synthetic.json.gz` in the `test`
  schema, asserts row counts + `network_name` + a `coverage_log` row, then **tears everything down**
  on exit (`TRUNCATE test.*`, `rm -rf data-test/anthem`). Leaves zero residue.
- **`make nppes-test`** (`scripts/nppes_test.sh`) — same shape for the NPPES GA extractor.

---

## Parquet Schema

```
data/anthem/
  rates/{id}.parquet      provider_group_id | plan_name | network_name | billing_code_type | billing_code
                          negotiation_arrangement | negotiated_type | negotiated_rate
                          expiration_date | service_code | billing_class | setting
  providers/{id}.parquet  provider_group_id | network_name | npi | tin_type | tin_value
  codes/{id}.parquet      billing_code_type | billing_code | name | description
  npi_lookup.parquet      npi | tin_value
data/nppes/
  ga_providers.parquet    npi | entity_type | org_name | last_name | first_name
                          taxonomy_code | taxonomy_group | is_hospital | is_clinic
                          city | state | postal_code
```

`{id}` is `index_files.id`. `service_code` is the `|`-joined place-of-service array.

**`network_name`** is the real, structured network label for the rate — the `|`-joined
`provider_references[].network_name` array (e.g. `"GA Blue Value HIX Individual Network"`). This is
the reliable filter for the target plan. Old parquet files (pre-migration) lack the column; the
backend reads every glob with `union_by_name=true` and guards `network_name` paths, so they show as
`NULL` and `/networks` stays empty until a post-migration parse lands.

**`plan_name` caveat:** the parser stamps each rate row with the `|`-joined `market_types` of its
source file (e.g. `"individual | group"`), *not* an actual plan name (a few older parquet files carry
a different, real string). Prefer `network_name`. See [Known gaps](#known-gaps).

---

## Testing

### Pre-commit (static — no stack required)

```bash
make check    # etl-fmt + etl-vet + etl-build
```

Run before every commit. Fast — no containers need to be up beyond `etl_go`.

### ETL end-to-end (`make etl-test`)

Runs the full pipeline in test isolation: discover → parse → verify Parquet output exists.

```bash
make up       # stack must be running
make etl-test
```

Isolation guarantees:
- **DB** writes go to `test.*` schema (`search_path=test` via `TEST_DATABASE_URL`)
- **Parquet** output goes to `data-test/anthem/` (rates, providers, codes, npi_lookup)
- Discovery caps at 100 reporting structures; parsing caps at 1 file
- Safe to run at any time — `test.*` and `data-test/` can be truncated/deleted freely

The test passes when at least one `.parquet` file appears in `data-test/anthem/rates/`.

### Future test layers (not yet implemented)

| Layer | Tool | What it would cover |
|---|---|---|
| Backend API | pytest + httpx | `/rates`, `/plans`, `/providers` endpoint contracts |
| Frontend | Playwright | Rate explorer filter + histogram interactions |
| ETL unit | `go test ./...` | Parser struct validation, conflict-resolution logic |

Add `make backend-test` and `make frontend-test` targets when these are built, then wire them into `make check` or a separate `make test-all`.

---

## Code Conventions

### Editor (Neovim / kickstart.nvim)

- **Indentation:** 2 spaces, `expandtab` — `vim-sleuth` detects per-file convention
- **Go files** use tabs (`gofmt` standard); detected automatically
- **Auto-format on save** via `conform.nvim`; LSP fallback for Go (`gopls`), Python, JS
- **Treesitter** parsers: `go`, `python`, `javascript`, `tsx`, `sql`, `yaml`, `json`, `markdown`
- No trailing whitespace

### Go (etl-go)

- `gofmt`-formatted; run `gofmt -w .` or let `gopls` handle it
- Error strings lowercase, no trailing punctuation
- Shared structs and global vars (incl. DB URLs, output paths) in `types.go`; CLI flags and routing in `main.go`
- Prefer `pgx/v5` `COPY` (or `CopyFrom`) for bulk inserts; avoid individual `INSERT` round-trips
- Stream JSON — never buffer an entire MRF file in memory

### Python (backend)

- FastAPI + DuckDB; routes and queries in `main.py`, models in `models.py`, connection in `database.py`
- 2-space indentation; black-compatible if formatted
- No ORM — raw DuckDB SQL against `read_parquet(...)` globs; parameterize with `?` placeholders

### JavaScript / React (frontend)

- React + Vite; components in `frontend/src/`
- 2-space indentation, `.jsx` files
- API calls centralized in `api.js` — no direct `fetch` inside components

### SQL

- Lowercase keywords, 2-space indentation
- Bulk upserts: `COPY`/`CopyFrom` into a `TEMP` staging table, then one set-based
  `UPDATE … FROM` + `INSERT … WHERE NOT EXISTS` (see `discover.go`)
- `ON CONFLICT DO NOTHING` for idempotent single-row upserts (see `billing_codes`)

---

## Critical Rules

1. **`exec` not `run --rm`** — always `docker compose exec <service>` so output streams to Docker Desktop logs
2. **Never auto-reset `processing` rows** — investigate first; repeated failures on one file mean a bad file, not a transient error
3. **No full-file buffering in ETL** — the Go parser must stream; MRF files can exceed 10 GB
4. **Test mode is isolated** — `test.*` tables and `data-test/` are safe to truncate; never touch `public.*` or `data/` in a test run
5. **Plan-specific file wins on rate conflict** *(target design, not yet enforced in code)* — a single-plan
   file's rate should override a shared-network file's rate for the same `(billing_code + provider_group)`,
   and the lower rate wins between two shared files. Implementing this needs `source_file_id` / `plan_count`
   on rate rows — see [ETL.md](etl-go/ETL.md#conflict-resolution-strategy).

---

## File Map

| Path | Purpose |
|---|---|
| `etl-go/main.go` | CLI entry point, flag routing, test/prod path selection |
| `etl-go/discover.go` | Phase 1: master index download, stream, staging-table upsert (+ `plan_states`, `hiosStateCode`) |
| `etl-go/mrf.go` | `streamMRF` — the shared single-pass token scanner + pure `buildRateRows`/`buildProviderRows` |
| `etl-go/parse.go` | Phase 2: I/O around `streamMRF` — Parquet writers, billing-code upsert, status, `coverage_log` |
| `etl-go/fixture.go` | `-make-fixture` — truncated `*.json.gz` generator |
| `etl-go/priority.go` | `gaPriorityExpr` — the deterministic GA/individual ranking |
| `etl-go/nppes.go` | `-nppes` — NPPES national zip → GA subset Parquet (`extractNPPESGeorgia`, `classifyTaxonomy`) |
| `etl-go/size.go` | Concurrent HEAD requests to backfill `file_size_bytes` |
| `etl-go/progress.go` | `ProgressReader` — byte tracking + ETA |
| `etl-go/types.go` | Shared structs, global vars, DB URL / output path init |
| `etl-go/*_test.go` | Hermetic unit tests over the committed fixtures |
| `backend/main.py` | FastAPI routes + DuckDB queries over the Parquet globs (`/networks`, `/providers/ga`, network filters) |
| `backend/tests/test_coverage.py` | Contract + coverage pytest (run via `make backend-test`) |
| `scripts/coverage_probe.py` | ~40-code scorecard for the target plan |
| `scripts/coverage_report.py` | Aggregate `coverage_log` |
| `scripts/etl_e2e_test.sh`, `scripts/nppes_test.sh` | Hermetic e2e tests with teardown |
| `db/init.sql` | Postgres schema (public + test) — note legacy tables above |
| `db/migrations/` | Idempotent migrations for running DBs (`make db-migrate`) |
| `docker-compose.yml` | Service definitions |
| `deploy/` | Dockerfiles per service |
| `etl-go/ETL.md` | Deep-dive: MRF data model, conflict resolution, known issues |
| `coverage/` | Committed before/after coverage scorecards + reports |

---

## Known gaps

- **Network attribution — done; plan-name attribution — still partial.** `network_name` is now
  captured from `provider_references` and is the reliable filter (e.g. `GA Blue Value HIX Individual
  Network`). The free-text plan *name* (`BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM`) still never
  enters the pipeline; `index_files.plan_names` / `idx_index_files_plan` remain unused. Mapping a
  member's plan name → network_name(s) is the remaining piece (HIOS `plan_id` + the CMS registry, or
  the index's `reporting_plans`).
- **`coverage_log.n_ga_hospital_npis`** is never populated by the parser (the NPPES join happens at
  query time). Backfill it with a post-batch `providers ⨝ ga_providers` query if the number is wanted
  in the log.
- **Monthly index churn.** `location` is a signed URL with a `YYYY-MM_` path prefix, so it is not a
  cross-month key — re-discover monthly and prune the prior month (see Phase 1 note). A query-stripped
  `url_path` column would fix this.
- **Large GA files deferred.** The `anthem/GA_*` plan-specific files above ~1 MB (e.g.
  `GA_HXRCMED0001` at 2.1 GB, `GA_AHPPMEDGAHF*` at 3–7 GB) are the richest source for the target plan
  but were left unparsed to stay inside the disk budget for this pass.
- **Legacy Postgres tables.** `negotiated_rates`, `provider_mappings`, `place_of_service_codes`,
  `vw_rates_detailed` in `db/init.sql` are neither written nor read. `db/SCHEMA.md` still describes the
  old Postgres-centric model.
- **Conflict resolution** (Critical Rule 5) is documented but not implemented.
- **`etl-go/ETL.md` and `README.md`** predate the Parquet migration in places (they mention `UNNEST`
  upserts and Postgres rate tables).

---

## References

- [CMS Price Transparency schema](https://github.com/CMSgov/price-transparency-guide)
- [ETL deep-dive](etl-go/ETL.md)
- [Firstmate agent distro](https://github.com/kunchenguid/firstmate) — captain/crew architecture this charter mirrors
