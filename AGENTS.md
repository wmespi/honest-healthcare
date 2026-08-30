# Honest Healthcare — Agent Charter

Price transparency tooling for Anthem Machine-Readable Files (MRFs). Streams
multi-GB negotiated-rate files in one pass, stores them as Parquet, and exposes a
consumer rate explorer.

**Primary use case:** rates for `BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM` — an
individual HMO on Anthem's Blue Value network in Georgia. The reliable filter is
the structured `network_name` **`GA Blue Value HIX Individual Network`** (from
`provider_references`), captured end-to-end. Mapping the free-text plan *name* →
network is still not wired — [docs/known-gaps.md](docs/known-gaps.md).

This file is the charter — read it first, then follow the [doc map](#where-to-look)
to the detail for whatever you're touching. Keep it thin; detail lives next to the
code.

---

## Architecture

| Layer | Tech | Dir | Purpose |
|---|---|---|---|
| Discovery | Go + Postgres | `etl/discovery/` | Monthly metadata sync of MRF URLs into the `index_files` queue |
| Extraction | Go (streaming JSON) | `etl/extraction/` | Parses gzipped MRFs in one pass → Parquet |
| Reference | Go (NPPES) + Python/DuckDB (RBCS, NUCC, CMS utilization) | `etl/nppes/`, `reference/` | External public datasets → dimension Parquet |
| Serving | Python + DuckDB | `serving/` | Queries the Parquet globs in-process via FastAPI (`localhost:8000`) |
| Frontend | React + Vite | `frontend/` | Rate explorer — `localhost:5173` |
| Storage | Parquet + ZSTD | `data/` | `data/anthem/{prices,group_sets,providers,codes,summary}/`, `npi_lookup.parquet`; `data/nppes/`, `data/reference/`, `data/cms/` |
| Queue DB | Postgres 15 + PostGIS | `db/` | `index_files` + `billing_codes` + `coverage_log` |

The Go CLI (`etl/`, one module) dispatches from `main.go` to the `discovery` /
`extraction` / `nppes` / `fixture` packages; shared structs, config, and the
progress reader live in `etl/core/`.

Docker services: `db`, `etl`, `serving`, `frontend` — each from a multi-stage
Dockerfile in [deploy/](deploy/README.md) (`prod` target = deployable artifact;
compose runs the `dev` target). Full on-disk layout: [docs/schema.md](docs/schema.md).

---

## The language principle

**Go** = single-pass streaming acquisition of large raw sources that can't be held
in memory (MRF JSON, the ~9 GB NPPES CSV). Hand-rolled streaming parser, tight
memory control.

**Python (over DuckDB)** = relational reshaping — joins, enrichment, aggregation
against data already landed. SQL-shaped glue where DuckDB / pyarrow does the work
in C++.

The dividing line is *"hand-rolled streaming parser vs. SQL-shaped transform"*, not
extract-vs-serve. NPPES stays in Go even though it writes a dimension table,
because the work is the stream. RBCS/NUCC/CMS-utilization are Python even though
they're "extraction" — and the CMS file is ~3 GB — because the work is a
filter/join over a CSV that DuckDB's parallel C++ reader chews through in
seconds, not a hand-rolled streaming parse.

---

## Critical rules

1. **`exec` not `run --rm`** — always `docker compose exec <service>` so output
   streams to Docker Desktop logs. The `make` targets already do this.
2. **Never auto-reset `processing` rows** — investigate first. Repeated failures on
   one file mean a bad file, not a transient error.
3. **No full-file buffering in extraction** — the Go parser must stream; MRFs can
   exceed 10 GB.
4. **Test mode is isolated** — `test.*` tables and `data-test/` are safe to
   truncate; a test run must never touch `public.*` or `data/`.
5. **Plan-specific file wins on rate conflict** *(target design, not yet in code)* —
   a single-plan file's rate overrides a shared-network file's for the same
   `(billing_code + provider_group)`; the lower rate wins between two shared files.
   [etl/mrf-model.md](etl/mrf-model.md#conflict-resolution-strategy).
6. **Give regular status updates.** On any multi-step task, post a short progress
   note as each step lands — what's done, what's next, anything that changed — not
   just a summary at the end.

---

## Development commands

`make help` lists everything. One name per workflow — the `make` target, the
`etl` subcommand, and the helper doc all share it. Test isolation and
single-item selection are variables:

```bash
make start                  # Docker Desktop (if needed) + all containers
make up / make down / make logs

make discover               # Phase 1 — sync the master index into index_files
make discover SCHEMA=1      #   stream only, write index_schema.json, no DB
make parse                  # Phase 2 — stream pending files → Parquet
make parse ID=21057         #   one file by index_files.id
make parse GA=1             #   GA / individual-market files first
make parse TEST=1           #   test isolation (test schema + data-test/)
make build-summary          # rebuild the browse-layer summary (after a parse batch)
make size                   # backfill index_files.file_size_bytes

make nppes                  # NPPES national file → data/nppes/ga_providers.parquet (GA)
make code-labels            # RBCS consumer procedure labels
make taxonomy-labels        # NUCC provider specialty labels
make cms-utilization        # CMS Medicare Part B — did this NPI bill this code
make specialty-profiles     #   ...and what's typical for each specialty (Tier 2)

make check                  # pre-commit gate: fmt + vet + build + Go unit tests
make test-all               # full sweep (stack must be up)
make test-api / test-web / test-e2e

make cov-probe LABEL=before # coverage scorecard for the target plan
make cov-report             # aggregate coverage_log
make data-size              # rows + bytes per Parquet table + Postgres queue tables
make psql / make migrate    # DB shell / apply db/migrations/*.sql
make db-reset WHAT=processing|failed
make db-snapshot / db-restore  # pg_dump the queue tables before/after a risky migration
make sh S=serving          # shell into a container
```

---

## Where to look

| Working on… | Read |
|---|---|
| The source-file shape, plan/network/file/provider model, conflict resolution | [etl/mrf-model.md](etl/mrf-model.md) |
| Discovery — monthly index sync, the queue, monthly churn | [etl/discover.md](etl/discover.md) |
| Extraction — the parser, network attribution, GA NPI filter, Parquet writers | [etl/parse.md](etl/parse.md) |
| Queue ordering, GA prioritization, recovering stuck rows | [etl/queue.md](etl/queue.md) |
| NPPES Georgia provider subset | [etl/nppes.md](etl/nppes.md) |
| RBCS procedure labels / NUCC specialty labels | [reference/code-labels.md](reference/code-labels.md) · [reference/taxonomy-labels.md](reference/taxonomy-labels.md) |
| Provider↔procedure evidence (CMS utilization `did_bill`; specialty profiles; menu tiers) | [reference/cms-utilization.md](reference/cms-utilization.md) · [reference/specialty-profiles.md](reference/specialty-profiles.md) |
| API routes, the four consumer jobs, query-layer notes | [serving/serving.md](serving/serving.md) |
| On-disk schema (Parquet + what Postgres holds) | [docs/schema.md](docs/schema.md) |
| Container images — dev/prod targets, ports, what CI builds | [deploy/README.md](deploy/README.md) |
| Test isolation, fixtures, e2e scripts, all test layers | [docs/testing.md](docs/testing.md) |
| What's wrong / missing / deferred | [docs/known-gaps.md](docs/known-gaps.md) |
| Where the product is headed — the two flows, the navigator direction, the data roadmap | [docs/direction.md](docs/direction.md) |
| CMS spec | https://github.com/CMSgov/price-transparency-guide |

`db/SCHEMA.md` covers what Postgres holds; [docs/schema.md](docs/schema.md) is the
authoritative on-disk schema.

**Doc naming.** ALL-CAPS is reserved for repo-meta files (`README.md`, `AGENTS.md`,
`LICENSE`). Topic and helper docs are lowercase and — where a workflow exists —
share the name of its `make` target / `etl` subcommand (`parse` → `parse.md`),
so an agent that ran `make help` can guess the filename.
