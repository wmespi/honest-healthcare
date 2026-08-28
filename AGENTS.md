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
| Storage | Parquet + ZSTD | `data/anthem/prices/`, `group_sets/`, `providers/`, `codes/`, `npi_lookup.parquet`; `data/nppes/ga_providers.parquet` |
| Backend | Python + DuckDB | Queries the Parquet globs in-process via FastAPI (`localhost:8000`) |
| Frontend | React + Vite | Rate explorer: histogram, filters, NPI search — `localhost:5173` |
| Discovery DB | Postgres 15 + PostGIS | `index_files` queue (URLs, status, sizes, `market_types`, `hios_issuer_ids`, `plan_states`) + `coverage_log` |

Docker services: `db`, `etl_go`, `backend`, `frontend`.

**Where data actually lands:**

- **Parquet** (`data/anthem/…`, `data/nppes/…`) — price rows + group-set rosters, provider rows, billing-code rows, NPI lookup, and the NPPES Georgia provider subset. This is what the backend reads.
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

# Reference data
make code-labels             # build data/reference/code_labels.parquet (RBCS categories + synonyms per parsed code)
make taxonomy-labels         # build data/reference/nucc_taxonomy.parquet (NUCC specialty labels for provider taxonomy codes)

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
| `-networks "GA *"` | network_name allowlist (comma-sep, trailing `*` = prefix). Default `GA *`, applied to BlueCard-mirror / other-state files only — **skipped for `anthem/GA_*` plan-specific files** (trusted by filename; their network labels vary — see Known gaps). A user-set value applies everywhere. |
| `-all-networks` | Disable the network_name allowlist entirely |
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
>
> The 8.7 GB `index_cache.json.gz` is only needed *during* a discovery run — safe to delete between
> monthly refreshes (`-discover` re-downloads it).

### Phase 2 — Parsing (`-parse`)

For each `index_files` row with `status = 'pending'` (ordered `file_size_bytes ASC NULLS LAST, id`,
or `gaPriorityExpr DESC, …` with `-priority`):

1. Marks the row `processing`, GETs the gzipped file (or reads `-fixture PATH`), streams it once
   via `streamMRF` (the shared token scanner; `provider_references` must precede `in_network`).
2. Builds a `provider_group_id → network_name` map from `provider_references[].network_name`
   (a structured array, e.g. `["GA Blue Value HIX Individual Network"]`) and stamps `network_name`
   onto every provider row and price row — structured attribution, no string matching.
   **GA NPI filter (default on when `data/nppes/ga_providers.parquet` exists):** a provider row is
   kept only if its NPI is a Georgia NPPES NPI; a provider group with no GA NPI is dropped entirely,
   and every price row whose whole roster it was goes too. `-all-npis` disables this. For
   GA-plan-specific files the loss is small (~0.4% of prices, ~23% of provider rows for
   `GA_JBNKMED0001`); for BlueCard-mirror / out-of-state files it drops 85–100% (many parse to zero
   rows). The `coverage_log.notes` column records the drop counts.
3. For each `negotiated_rate` block, buckets its provider references by network, fingerprints each
   network-scoped roster (`hashGroupSet`), and — first time that roster is seen in the file —
   writes its membership edges to `group_sets`. Emits one `prices` row per `(network × price)`
   pointing at the `group_set_id`. Parquet files keyed by `index_files.id`:
   `prices/net=<slug>/{id}.parquet`, `group_sets/{id}.parquet`, `providers/{id}.parquet`,
   `codes/{id}.parquet` (all ZSTD).
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
    NPI lists capped at 10, first 25 in-network items that touch a kept group, rates/prices capped).
    `synthetic.json.gz` drives `make etl-test`; the rest are regression guards run by
    `TestFixtures_Parse`. **Add a fixture only when a file has a genuinely new shape** (a GA plan
    file, a vision/dental file, a file that failed) — not one per file; near-duplicate BlueCard
    shards add nothing.
- **`make etl-test`** (`scripts/etl_e2e_test.sh`) — parses `fixtures/synthetic.json.gz` in the `test`
  schema, asserts row counts + `network_name` + a `coverage_log` row, then **tears everything down**
  on exit (`TRUNCATE test.*`, `rm -rf data-test/anthem`). Leaves zero residue.
- **`make nppes-test`** (`scripts/nppes_test.sh`) — same shape for the NPPES GA extractor.

---

## Parquet Schema

```
data/anthem/
  prices/net=<slug>/{id}.parquet   file_id | group_set_id | network_name
                          billing_code_type | billing_code | negotiation_arrangement
                          negotiated_type | negotiated_rate | expiration_date
                          service_code | billing_class | modifier | setting
                          ← One row per (network × negotiated price) — NOT fanned out per
                            provider group. `modifier` is the sorted "|"-joined
                            billing_code_modifier array ("26" = professional / physician
                            work, "TC" = technical / equipment+facility, "" = global);
                            ~11% of Blue Value rows carry one. `(billing_code, modifier,
                            service_code, setting)` is what pins a rate for a patient.
                            provider group. Hive-partitioned by network_name (slug =
                            etl-go/partition.go:slugifyNetwork == backend network_slug());
                            a network-filtered query adds `net = ?` and DuckDB prunes to
                            the one directory. Join to group_sets on (file_id, group_set_id)
                            to expand a price to its provider groups.
  group_sets/{id}.parquet  file_id | group_set_id | provider_group_id
                          ← The deduplicated provider-group rosters. group_set_id =
                            FNV-64a hash of a block's sorted provider_reference ids
                            (etl-go/mrf.go:hashGroupSet); written once per distinct
                            roster per file. File 21057: 199 rosters for 15,560 codes.
  providers/{id}.parquet  file_id | provider_group_id | network_name | npi | tin_type | tin_value
  codes/{id}.parquet      billing_code_type | billing_code | name | description
  npi_lookup.parquet      npi | tin_value
data/nppes/
  ga_providers.parquet    npi | entity_type | org_name | last_name | first_name
                          taxonomy_code | taxonomy_group | is_hospital | is_clinic
                          address_line1 | address_line2 | city | state | postal_code
                          ← taxonomy_group is a coarse bucket; join taxonomy_code to
                            nucc_taxonomy.parquet for the real specialty label
data/reference/
  rbcs_taxonomy_ry26.csv  raw CMS RBCS download (cache)
  code_labels.parquet     billing_code_type | billing_code | short_name
                          rbcs_category | rbcs_subcategory | rbcs_family | rbcs_is_major
                          label | search_text     ← consumer label + search blob
  nucc_taxonomy.csv       raw NUCC taxonomy download (cache)
  nucc_taxonomy.parquet   taxonomy_code | grouping | classification | specialization
                          display_name | specialty | is_individual   ← `make taxonomy-labels`
```

`{id}` is `index_files.id`, and `file_id` on every row carries it — `provider_group_id` is the
MRF's *file-local* `provider_reference.id`, so all cross-file joins key on `(file_id,
provider_group_id)`. `service_code` is the `|`-joined place-of-service array. While a parse runs,
everything for the file is written under `anthem/.inflight/{id}/` and promoted together (atomic
rename) only on a clean stream — so the backend never reads a half-written or zero-byte file.

**Why the split.** The MRF lists every participating provider group under nearly every billing
code, so the old flat `rates/` layout fanned out to one row per `(code × price × group ×
network)` — file 28947 alone was 723M rows. `prices` + `group_sets` stores each roster once:
file 21057 went 682k → 76k price rows + 2.8k roster edges (~9×), and the ratio grows with file
size since roster count stays ~flat as codes scale. The backend's `PRICE_GROUPS_SRC` re-joins
them; `prices ⨝ group_sets` reproduces every original `(code, rate, provider_group)` tuple exactly.

**`code_labels.parquet`** (`make code-labels`, script `scripts/build_code_labels.py`) is the
consumer-friendly procedure layer, from **public data only**: CMS RBCS (185 families →
`rbcs_family`, e.g. "Arthroplasty - Knee"; `rbcs_subcategory` covers ~84% of rate volume as a
fallback) + a hand-curated `FAMILY_SYNONYMS` map so "colonoscopy" / "mri back" / "blood test"
resolve. No AMA CPT descriptors (licensed). Needed because the Georgia MRF's own `codes.name`
is near-useless ("Medical", "Surgery"). `/billing_codes` searches `search_text`;
`/procedure_categories` powers browse-by-category.

**`network_name`** is the real, structured network label for the price — one member of the
`provider_references[].network_name` array (e.g. `"GA Blue Value HIX Individual Network"`), a
single value per price row (and equal to its `net` partition). This is the reliable filter for
the target plan. A provider group in two networks lands in both partitions.

**No `plan_name`.** The old layout stamped rows with the source file's `|`-joined `market_types`
(`"individual | group"`) — never a real plan name. It's gone; `/plans` returns `[]`. Filter by
`network_name`. See [Known gaps](#known-gaps).

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

The test passes when `prices/` and `group_sets/` parquet appear under `data-test/anthem/` and
every price row's `group_set_id` resolves to at least one membership edge.

### Frontend component tests (`make frontend-test`)

`frontend/src/App.test.jsx` — vitest + Testing Library, hermetic (`vi.mock('./api')`,
jsdom). Covers the rate-explorer state machine: the default network-overview load, and
the regression that a **provider selected with no procedure** shows the `/providers/{npi}/procedures`
menu and never fires an npi-only `/rates/distribution` (which full-scans and hangs).
Config in `frontend/vite.config.js` (`test:` block) + `frontend/src/test/setup.js`.

`make test-all` runs the full sweep: `etl-check` + `etl-test` + `backend-test` + `frontend-test`
(stack must be up). `make check` stays the fast static-only gate.

### Future test layers (not yet implemented)

| Layer | Tool | What it would cover |
|---|---|---|
| Frontend E2E | Playwright | Real browser: histogram render, filter chips, mobile layout |
| ETL conflict-resolution | `go test ./...` | Plan-specific-file-wins rate override (Critical Rule 5) |

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
| `etl-go/mrf.go` | `streamMRF` — the shared single-pass token scanner + pure `buildPriceRows` (group-set dedup) / `buildProviderRows`, `hashGroupSet` |
| `etl-go/parse.go` | Phase 2: I/O around `streamMRF` — Parquet writers, billing-code upsert, status, `coverage_log` |
| `etl-go/fixture.go` | `-make-fixture` — truncated `*.json.gz` generator |
| `etl-go/priority.go` | `gaPriorityExpr` — the deterministic GA/individual ranking |
| `etl-go/nppes.go` | `-nppes` — NPPES national zip → GA subset Parquet (`extractNPPESGeorgia`, `classifyTaxonomy`) |
| `etl-go/size.go` | Concurrent HEAD requests to backfill `file_size_bytes` |
| `etl-go/progress.go` | `ProgressReader` — byte tracking + ETA |
| `etl-go/types.go` | Shared structs, global vars, DB URL / output path init |
| `etl-go/*_test.go` | Hermetic unit tests over the committed fixtures |
| `backend/main.py` | FastAPI routes + DuckDB queries over the Parquet globs. Job endpoints: `/rates/quote` (job 1 — one procedure × one provider → headline + component/POS breakdown + `plausibility`), `/rates/by_network` (job 2 — a procedure priced across every network, p10/p90 spread), `/rates/providers` (job 3 — compare-across-providers, `component=global`), `/providers/{npi}/procedures` (job 4 — the provider "menu"), `/rates/distribution` (histogram; 400s on npi-without-code). Plus `/networks`, `/providers/search` (+ `specialty=`), `/procedure_categories`. `_pos_bucket` / `_MODIFIER_LABELS` label raw `service_code` / `modifier`; `_nucc_bits` / `_provider_card` join NUCC specialty + practice address; `_plausibility` is a coarse specialty↔code check (real fix: GH #14). |
| `backend/tests/test_coverage.py` | Contract + coverage pytest (run via `make backend-test`) |
| `scripts/build_code_labels.py` | Build `data/reference/code_labels.parquet` (RBCS + synonyms) — `make code-labels` |
| `scripts/build_taxonomy_labels.py` | Build `data/reference/nucc_taxonomy.parquet` (NUCC specialty labels) — `make taxonomy-labels` |
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
- **`network_name` is NOT uniform across files.** `GA_JBNKMED0001` (the target plan's only source,
  1.1 MB, id 21057, 682k rows) uses the clean `"GA Blue Value HIX Individual Network"`; other
  `anthem/GA_*` files use config-style labels like `"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL"`.
  That's why the `-networks "GA *"` default is **skipped for `anthem/GA_*` files** (`isGAPlanSpecific`
  trusts the filename). Every other big `anthem/GA_*` file is a *different* GA individual plan
  (Pathway/Gatekeeper HMO, etc.), not Blue Value — parsing them broadens GA coverage but adds nothing
  to the target plan.
- **Browse-layer aggregates still full-scan.** `/networks`, `/billing_codes`, `/procedure_categories`
  aggregate all of `prices ⨝ group_sets` (`VOL_CTE` in `backend/main.py`). Fine now (76k price rows),
  but a precomputed summary table is the next step for many-file / multi-payor scale — see GitHub
  issue #10. The detail endpoints (`/rates/distribution`, `/rates/providers` with a network filter)
  partition-prune and stay fast regardless.
- **The backend opens a fresh `duckdb.connect()` per request** — no connection reuse, no
  `memory_limit`, no spill dir. A heavy query can OOM-kill the process rather than degrade. Part of
  issue #10.
- **`coverage_log.n_ga_hospital_npis`** is never populated by the parser (the NPPES join happens at
  query time). Backfill it with a post-batch `providers ⨝ ga_providers` query if the number is wanted
  in the log.
- **Monthly index churn.** `location` is a signed URL with a `YYYY-MM_` path prefix, so it is not a
  cross-month key — re-discover monthly and prune the prior month (see Phase 1 note). A query-stripped
  `url_path` column would fix this.
- **Large GA files.** The `anthem/GA_*` plan-specific files above ~1 MB (e.g. `GA_HXRCMED0001` at
  2.1 GB, `GA_AHPPMEDGAHF*` at 3–7 GB) are the richest source for the target plan. The `prices` +
  `group_sets` split makes them tractable now (28947: 723M flat rows → far fewer), but still parse
  them individually and watch `du -sh data`.
- **Plan-name attribution — no real plan name in the pipeline.** The old `plan_name` column (source
  file `market_types`) is gone; nothing carries a real plan name. The frontend and probes filter by
  `network_name`. Revisit: map a member's plan name → network via HIOS `plan_id` + the CMS registry.
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
