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

**Current focus** *(update as the active work changes)* — executing epic
[#95](https://github.com/wmespi/honest-healthcare/issues/95), the plan of record, one
PR per step; product direction in [docs/direction.md](docs/direction.md). Don't
restructure `etl/`, `serving/`, or `frontend/` without a reason (rule 7).

---

## Architecture

Five stages, one direction: **discovery** (Go) queues MRF URLs and keeps the
plan→file link → **extraction** (Go) streams the queued files to raw Parquet, a
provider probe aborting empty downloads before `in_network` → **reference** (Go +
Python/DuckDB) lands the public datasets → **build** (Python/DuckDB, `build/`) turns
raw + reference into the serving tables and is where every product decision lives →
**serving** (Python/DuckDB + FastAPI, `localhost:8000`) queries the Parquet in-process
for the **frontend** (React, `localhost:5173`). Storage is Parquet+ZSTD under `data/`;
Postgres holds only the queue. Layer table and diagrams:
**[docs/architecture.md](docs/architecture.md)**; on-disk layout:
**[docs/schema.md](docs/schema.md)**; images and ports: [deploy/](deploy/README.md).

---

## The language principle

**Go** = single-pass streaming acquisition of large raw sources that can't be held
in memory (MRF JSON, the NPPES CSV). Hand-rolled streaming parser, tight
memory control.

**Python (over DuckDB)** = relational reshaping — joins, enrichment, aggregation
against data already landed. SQL-shaped glue where DuckDB / pyarrow does the work
in C++.

The dividing line is *"hand-rolled streaming parser vs. SQL-shaped transform"*, not
extract-vs-serve. NPPES stays in Go even though it writes a dimension table,
because the work is the stream. RBCS/NUCC/CMS-utilization are Python even though
they're "extraction", because the work is a filter/join over a CSV that DuckDB's
parallel C++ reader handles, not a hand-rolled streaming parse.

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
6. **Give regular status updates — the most-missed rule here.** On any multi-step
   task, post a short progress note *as each step lands* — what's done, what's
   next, anything that changed — not just a summary at the end. If you've run
   several tool calls without saying anything to the user, stop and post one. A
   plan, a finding that changes direction, a merged PR, a failing test: each is
   its own note, in the moment, not folded into a later recap. Err toward
   over-communicating.
7. **No structural churn during a feature push** — batch pure renames /
   reorganizations into a deliberate tidy window, land them, then update docs +
   memory once. A restructure PR mid-feature-run competes for review attention
   and rebases badly against the open feature branches.

---

## Development commands

`make help` lists everything. One name per workflow — the `make` target, the
`etl` subcommand, and the helper doc all share it. Test isolation and
single-item selection are variables:

```bash
make start                  # Docker Desktop (if needed) + all containers
make up / make down / make logs

make discover               # Phase 1 — sync the master index into index_files + index_file_plans, then backfill sizes
make discover SCHEMA=1      #   stream only, write index_schema.json, no DB
make reference              # Phase 3 — NPPES, RBCS/NUCC labels, CMS utilization + profiles, MPFS, Doctors & Clinicians, geocode (skips what exists)
make reference STEP=mpfs    #   one builder, always runs (ARGS="--year 2025" passes through; FORCE=1 rebuilds all)
make parse                  # Phase 2 — stream pending files serving a target plan → Parquet (needs the NPPES reference first)
make parse ID=21057         #   one file by index_files.id (bypasses target selection)
make parse TARGETS=<path>   #   a different target-plan list (default etl/targets.yaml)
make parse TEST=1           #   test isolation (test schema + data-test/)
make build                  # Phase 4 — raw + reference parquet → data/serving/ tables
make build NET=<slug,slug>  #   a subset of network partitions
make refresh                # the monthly job: discover → reference → parse → code-labels → build

make check                  # pre-commit gate (container): fmt + vet + build + Go unit tests
make check LOCAL=1          #   same gate on host toolchains, NO Docker
make test                   # every hermetic suite: check + serving pytest + vitest (+ ETL e2e)
make test LOCAL=1           #   host toolchains, no Docker — the worktree gate; works in any checkout but fails with
                             #   "no host 'go'" / "no .venv" / "no frontend/node_modules" until scripts/dev-setup.sh has been run
make test-live              # golden answers + user journeys against the live API + real corpus (docs/journeys.md)

make worktree TOPIC=x       # new sibling worktree + branch off main, set up (GH #59)
make worktree-rm TOPIC=x    # remove it once its PR merges
make footprint / make clean # disk report / reclaim regenerable artifacts
make psql / make migrate    # DB shell / apply db/migrations/*.sql
make db-reset WHAT=processing|failed
make db-snapshot / db-restore  # pg_dump the queue tables before/after a risky migration
make fixture ID=5043        # truncated *.json.gz fixture from a file id
make sh S=serving           # shell into a container
```

**Parallel / worktree development** (GH #59). The canonical checkout runs the one
always-up stack on `main`. Feature work happens in sibling worktrees
(`../hh-<topic>`, branch `espinoza/<type>/<topic>`, one per session) made by
`make worktree TOPIC=x [TYPE=feat]`, which runs `scripts/dev-setup.sh` (`.venv`,
`npm ci`, a gitignored `.env` with its own ports and `HH_DATA_ROOT`). Host toolchains
are pinned by `.mise.toml` (`brew install mise` once, add its shell hook); run
`scripts/dev-setup.sh` once in the canonical checkout. Test with `make test LOCAL=1`
(no Docker), merge to `main` serially (trunk, no `develop`), then `make worktree-rm
TOPIC=x` — a lingering worktree is hundreds of MB.

- A worktree runs its own stack (`make start`) only for a live check; it **reads** the
  shared Parquet store and must never write `data/` — use `TEST=1`, or
  `SERVING_DIR=/app/data-local/serving` to rebuild serving tables locally.
- Any ad-hoc `duckdb.connect()` must `SET temp_directory` (or go through `db()` in
  `serving/data_sources.py`), or a spilling query fills the repo's `.tmp/`.
  `make footprint` reports disk use; `make clean` reclaims regenerable artifacts
  (`DOCKER=1` also prunes Docker).
- One branch per worktree; after a manual `rm -rf` run `git worktree prune`; parallel
  branches adding the same `db/migrations/NNN_*.sql` number conflict — renumber the
  later one. Gitignored files (`.env`, `.venv/`, `data/`) are normal files — read them
  by path; repo-wide search skips them.

---

## Where to look

| Working on… | Read |
|---|---|
| The source-file shape, plan/network/file/provider model, conflict resolution | [etl/mrf-model.md](etl/mrf-model.md) |
| Discovery — monthly index sync, the plan→file link, the queue, monthly churn | [etl/discover.md](etl/discover.md) |
| Extraction — target selection, the parser, network attribution, GA NPI filter, Parquet writers | [etl/parse.md](etl/parse.md) |
| Which plans we parse files for | [etl/targets.yaml](etl/targets.yaml) |
| Queue selection, ordering, recovering stuck rows | [etl/queue.md](etl/queue.md) |
| NPPES Georgia provider subset | [etl/nppes.md](etl/nppes.md) |
| RBCS procedure labels / NUCC specialty labels | [reference/code-labels.md](reference/code-labels.md) · [reference/taxonomy-labels.md](reference/taxonomy-labels.md) |
| Provider↔procedure evidence (CMS utilization `did_bill`; specialty profiles; menu tiers) | [reference/cms-utilization.md](reference/cms-utilization.md) · [reference/specialty-profiles.md](reference/specialty-profiles.md) |
| Medicare Physician Fee Schedule benchmark (`medicare_allowed` / `vs_medicare` on a quote) | [reference/mpfs.md](reference/mpfs.md) |
| Provider lat/long for distance ranking (Flow A build sequence step 1) | [reference/geocode.md](reference/geocode.md) |
| Real practice identity + hospital-affiliation (CCN↔NPI) bridge — CMS Doctors & Clinicians | [reference/doctors-clinicians.md](reference/doctors-clinicians.md) |
| The build step — raw + reference → serving tables, `scope` / `is_sentinel` / benchmark / rule 5 | [build/build.md](build/build.md) |
| The three standing architecture diagrams (data flow, serving entities, runtime) | [docs/architecture.md](docs/architecture.md) |
| API routes, the four consumer jobs, query-layer notes | [serving/serving.md](serving/serving.md) |
| On-disk schema (Parquet + what Postgres holds) | [docs/schema.md](docs/schema.md) |
| Container images, ports, parallel stacks, sending a tester the Tailscale link | [deploy/README.md](deploy/README.md) |
| The three test suites, isolation, fixtures, CI jobs | [docs/testing.md](docs/testing.md) |
| User journeys — the persona clickpaths `make test-live` asserts | [docs/journeys.md](docs/journeys.md) |
| What's wrong / missing / deferred | [docs/known-gaps.md](docs/known-gaps.md) |
| Where the product is headed — the two flows, the navigator direction, the data roadmap | [docs/direction.md](docs/direction.md) |
| CMS spec | https://github.com/CMSgov/price-transparency-guide |

**Doc rules** (`scripts/check_docs.py`, run by `make check` and CI, enforces links, `make`
targets, file paths and reachability).
1. One fact, one home — link, don't restate.
2. No hand-typed numbers (counts, sizes, latencies): say "run X", with a command that exists.
3. Describe the current state, never history (no "was", "used to", "since #NN" — that is git).
4. A doc not reachable from the map above doesn't exist — link it or delete it.
5. Helper docs cap at about 100 lines; a fixed gap is deleted from `docs/known-gaps.md`.

**Doc naming.** ALL-CAPS is reserved for repo-meta files (`README.md`, `AGENTS.md`,
`LICENSE`). Topic and helper docs are lowercase and — where a workflow exists —
share the name of its `make` target / `etl` subcommand (`parse` → `parse.md`).
