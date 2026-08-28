# Honest Healthcare — Agent Charter

Price transparency tooling for Anthem Machine-Readable Files (MRFs). Streams multi-GB
negotiated rate files in one pass, stores them as Parquet, and exposes a rate explorer UI.

**Primary use case:** rates for `BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM` (an
individual HMO on Anthem's Blue Value network in Georgia; ~245 MRF rate files reference it).
See [Known gaps](#known-gaps) — plan-name filtering is not fully wired through the pipeline yet.

---

## Architecture

| Layer | Tech | Purpose |
|---|---|---|
| ETL | Go (streaming JSON) | Parses gzipped MRF files in one pass; writes Parquet + a little Postgres |
| Storage | Parquet + ZSTD | `data/anthem/rates/`, `providers/`, `codes/`, `npi_lookup.parquet` |
| Backend | Python + DuckDB | Queries the Parquet globs in-process via FastAPI (`localhost:8000`) |
| Frontend | React + Vite | Rate explorer: histogram, filters, NPI search — `localhost:5173` |
| Discovery DB | Postgres 15 + PostGIS | Tracks the `index_files` queue (URLs, status, sizes, market types, HIOS issuer IDs) |

Docker services: `db`, `etl_go`, `backend`, `frontend`.

**Where data actually lands:**

- **Parquet** (`data/anthem/…`) — rate rows, provider rows, billing-code rows, NPI lookup. This is what the backend reads.
- **Postgres** — only `index_files` (the discovery/parse queue) and `billing_codes` (a reference upsert). The `negotiated_rates`, `provider_mappings`, `place_of_service_codes` tables and the `vw_rates_detailed` view exist in `db/init.sql` but are **not currently written or read** by any service; treat them as legacy until re-adopted.

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
make etl-parse               # Phase 2: stream pending files into Parquet
make etl-parse-test          # Phase 2 in test isolation
make etl-parse-file ID=10065 # parse a single file by index_files.id
make etl-size                # backfill file_size_bytes via concurrent HEAD requests

# ETL — Quality (stack must be running)
make etl-fmt                 # gofmt -w on etl-go source
make etl-vet                 # go vet ./... static analysis
make etl-build               # verify etl-go compiles cleanly
make etl-check               # fmt + vet + build
make etl-test                # full e2e pipeline in test isolation
make check                   # top-level gate — run before every commit

# Database
make db-psql                 # open psql shell on honest_healthcare
make db-reset-processing     # reset stale 'processing' rows → 'pending'
make db-reset-failed         # reset 'failed' rows → 'pending' for retry

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
| `-size` | HEAD every `index_files` row with a NULL `file_size_bytes`, fill it in (concurrent) |
| `-file-ids 1,2,3` | Parse specific IDs, bypassing the queue order |
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
   - `network_entity` — the prefix before `" : "` in the file description (BlueCard files only; else NULL)
   - `description`, `location`

   Plan **names** are intentionally *not* stored — the plans × files cross-product blows the heap
   (400k+ unique plans, 10k+ files). `market_types` + `hios_issuer_ids` cover the filtering needs.
3. Writes a compact schema example to `data/anthem/index_schema.json`.
4. Bulk-loads via `COPY` into a `TEMP` staging table, then set-based
   `UPDATE index_files … FROM _idx_stage` (existing rows) + `INSERT … LEFT JOIN … WHERE t.id IS NULL`
   (new rows). GIN indexes on `market_types` / `hios_issuer_ids` are dropped before the write and
   rebuilt once after. Re-running is safe — `location` is the natural key.

Monthly index URL pattern: `YYYY-MM-01_anthem_index.json.gz` (built from the current month). Override:

```bash
docker compose exec etl_go go run . -discover -index-url "https://…/2026-08-01_anthem_index.json.gz"
```

### Phase 2 — Parsing (`-parse`)

For each `index_files` row with `status = 'pending'` (ordered `file_size_bytes ASC NULLS LAST, id`):

1. Marks the row `processing`, GETs the gzipped file, streams it once.
2. Writes three Parquet files keyed by `index_files.id`:
   `rates/{id}.parquet`, `providers/{id}.parquet`, `codes/{id}.parquet` (all ZSTD).
3. Upserts each new billing code into the Postgres `billing_codes` table
   (`ON CONFLICT (billing_code) DO NOTHING`).
4. After the whole run, writes/overwrites `npi_lookup.parquet` (dedup NPI → TIN value across all files parsed this run).
5. Marks the row `completed` (+ `completed_at`), or `failed` on any unrecoverable error.

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

- **DB** — connects via `TEST_DATABASE_URL` (`search_path=test`); `billing_codes` / `index_files` writes hit `test.*`.
- **Parquet** — output dirs move to `../data-test/anthem/…` (rates, providers, codes, `npi_lookup.parquet`, `mrf_example.json`).

Both `test.*` and everything under `data-test/` are safe to truncate/delete at any time. Discovery
in test mode caps at 100 reporting structures; parsing caps at 1 file (override with `-limit`).

---

## Parquet Schema

```
data/anthem/
  rates/{id}.parquet      provider_group_id | plan_name | billing_code_type | billing_code
                          negotiation_arrangement | negotiated_type | negotiated_rate
                          expiration_date | service_code | billing_class | setting
  providers/{id}.parquet  provider_group_id | npi | tin_type | tin_value
  codes/{id}.parquet      billing_code_type | billing_code | name | description
  npi_lookup.parquet      npi | tin_value
```

`{id}` is `index_files.id`. `service_code` is the `|`-joined place-of-service array.

**`plan_name` caveat:** the parser stamps each rate row with the `|`-joined `market_types` of its
source file (e.g. `"individual | group"`), *not* an actual plan name — plan names never enter the
pipeline (see Phase 1). The backend's `/plans` endpoint and plan filter operate on these strings.
Real plan-name attribution is unfinished — see [Known gaps](#known-gaps).

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
| `etl-go/discover.go` | Phase 1: master index download, stream, staging-table upsert |
| `etl-go/parse.go` | Phase 2: stream rate files, write Parquet, upsert billing codes |
| `etl-go/size.go` | Concurrent HEAD requests to backfill `file_size_bytes` |
| `etl-go/progress.go` | `ProgressReader` — byte tracking + ETA |
| `etl-go/types.go` | Shared structs, global vars, DB URL / output path init |
| `backend/main.py` | FastAPI routes + DuckDB queries over the Parquet globs |
| `backend/models.py` | Pydantic response models |
| `backend/database.py` | DuckDB connection setup |
| `frontend/src/App.jsx` | Main rate explorer UI |
| `frontend/src/api.js` | Centralized API client |
| `db/init.sql` | Postgres schema (public + test schemas) — note legacy tables above |
| `docker-compose.yml` | Service definitions |
| `deploy/` | Dockerfiles per service |
| `etl-go/ETL.md` | Deep-dive: MRF data model, conflict resolution, known issues |

---

## Known gaps

- **Plan-name attribution.** Discovery drops plan names; parsing stamps market-type strings into the
  `plan_name` Parquet column. Filtering by the target plan string does not work end-to-end yet. The
  `index_files.plan_names TEXT[]` column and `idx_index_files_plan` index are unused.
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
