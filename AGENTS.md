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

**Current focus** *(update as the active work changes)*: moving one owner-reviewed step at
a time through the [order of work on #135](https://github.com/wmespi/honest-healthcare/issues/135),
which also holds the target data model. The find-care screen at `/find-care` (find the
cheapest care nearby) is live, and step 12 finishes the three-layer layout below and the
audit's last actions.
Epic [#120](https://github.com/wmespi/honest-healthcare/issues/120) is on hold. Product
direction is in [docs/direction.md](docs/direction.md). Don't restructure `ingestion/`, `integration/`
or `application/` without a reason (rule 7).

---

## Architecture

Three layers, one direction, each in its own container and reading only what the layer before
it delivers:

1. **Ingestion** (`ingestion/`) reads each source and keeps what matters.
   **Discovery** (Go) lists this month's MRF URLs and the plan→file link, **extraction** (Go)
   streams the files `ingestion/etl/targets.yaml` names to raw Parquet, a provider probe aborting
   empty downloads before `in_network`, then publishes each target network's rates, and
   **reference** (Go + Python/DuckDB) lands the public datasets. It keeps its own files in
   `data/raw/` and delivers what integration reads to `data/ingestion/`.
2. **Integration** (`integration/`, Python/DuckDB, `make build`) turns that into the find-care
   tables in `data/serving/`, and is where every product decision lives.
3. **Application** (`application/`): **serving** (FastAPI + DuckDB, `localhost:8000`) queries
   those tables in-process for the **frontend** (React, `localhost:5173`).

Storage is Parquet+ZSTD under `data/`, with no database. Layer table and diagrams:
**[docs/architecture.md](docs/architecture.md)**; on-disk layout:
**[docs/schema.md](docs/schema.md)**; where it runs (images, ports, tester access): [infra/](infra/README.md).

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
because the work is the stream. NUCC/CMS-utilization are Python even though
they're "extraction", because the work is a filter/join over a CSV that DuckDB's
parallel C++ reader handles, not a hand-rolled streaming parse.

---

## Critical rules

1. **`exec` not `run --rm`** — always `docker compose exec <service>` so output
   streams to Docker Desktop logs. The `make` targets already do this.
2. **Look at a stopped or failed parse before retrying it** - a `failed` parse
   record or a leftover `data/raw/anthem/.inflight/<name>/` means investigate first.
   Repeated failures on one file mean a bad file, not a transient error.
3. **No full-file buffering in extraction** — the Go parser must stream; MRFs can
   exceed 10 GB.
4. **Test mode writes only `data-test/`** - it's safe to delete at any time, and a
   test run must never touch `data/`.
5. **One plan, one contract** — a plan a person can pick is priced by one file, so
   nothing combines or overrides prices across files. `ingestion/etl/targets.yaml` names
   the files to parse, and parse stops unless the index links each one to a plan on
   Georgia's exchange networks, which keeps out `GA_JBKEMED0001`: it carries Blue
   Value's network label but prices an older plan that isn't sold any more. [ingestion/etl/mrf-model.md](ingestion/etl/mrf-model.md#one-plan-one-contract).
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

make discover               # Phase 1 - this month's file list + plan→file link (index_files, index_file_plans Parquet) from Anthem's index
make discover SCHEMA=1      #   stream only, write index_schema.json and nothing else
make reference              # Phase 3 — NPPES, NUCC labels, CMS utilization, MPFS, OPPS, Doctors & Clinicians, geocode, MIPS, ZIP centroids, exchange plans, hospitals (skips what exists)
make reference STEP=mpfs    #   one builder, always runs (ARGS="--year 2025" passes through; FORCE=1 rebuilds all)
make parse                  # Phase 2 - stream the files ingestion/etl/targets.yaml names → Parquet when new or updated, then publish their rates by Georgia network (needs the NPPES and exchange-plans references first)
make parse FILE=GA_JBNKMED0001.json.gz  #   one file by name, now (bypasses target selection and its parse record)
make parse TARGETS=<path>   #   a different target-plan list (default ingestion/etl/targets.yaml)
make parse TEST=1           #   test isolation (data-test/)
make build                  # Phase 4 - what ingestion delivered (data/ingestion/) → the find-care tables in data/serving/
make refresh                # the monthly job: discover → reference → parse → geocode → build

make check                  # pre-commit gate (container): fmt + vet + build + Go unit tests
make check LOCAL=1          #   same gate on host toolchains, NO Docker
make test                   # every hermetic suite: check + each layer's pytest in its own container + vitest (+ ETL e2e)
make test LOCAL=1           #   host toolchains, no Docker; works in any checkout but fails with
                             #   "no host 'go'" / "no .venv" / "no application/frontend/node_modules" until scripts/dev-setup.sh has been run
make test-live              # golden answers + user journeys against the live API + real corpus (docs/journeys.md)

make worktree TOPIC=x       # only for parallel work: new sibling worktree + branch off main, set up (GH #59)
make worktree-rm TOPIC=x    # remove it once its PR merges
make footprint / make clean # disk report / reclaim regenerable artifacts
make fixture FILE=<name>    # truncated *.json.gz fixture from a file in this month's index
make sh S=integration       # shell into a container: ingestion, integration, serving or frontend
```

**Branches and worktrees** (GH #59).
Work on one branch at a time in the main checkout: `git switch -c espinoza/<type>/<topic>` from an up-to-date `main`, then merge to `main` serially (trunk, no `develop`).
Host toolchains are pinned by `.mise.toml` (`brew install mise` once, add its shell hook).
Run `scripts/dev-setup.sh` once, then test with `make test LOCAL=1` (no Docker).

- The Docker stack mounts this checkout's code, so a running stack serves whichever branch is checked out.
- A branch writes `data/` only when that is its purpose.
  Otherwise use `TEST=1`, or set `HH_SERVING_DIR=./data-local/serving` in `.env` to build and serve the find-care tables locally.
- Use a worktree only when two things must run at once, such as a second session or a stack that has to stay on `main`.
  `make worktree TOPIC=x [TYPE=feat]` creates `../hh-<topic>` and runs `scripts/dev-setup.sh` (`.venv`, `npm ci`, a gitignored `.env` with its own ports and `HH_DATA_ROOT`).
  Each session gets its own worktree, which **reads** the shared Parquet store, must never write it, and runs its own stack (`make start`) only for a live check.
  Remove it with `make worktree-rm TOPIC=x` once its PR merges, because a lingering worktree is hundreds of MB.
  After a manual `rm -rf`, run `git worktree prune`.
- Any ad-hoc `duckdb.connect()` must `SET temp_directory` (or go through `db()` in
  `application/serving/data_sources.py`), or a spilling query fills the repo's `.tmp/`.
  `make footprint` reports disk use; `make clean` reclaims regenerable artifacts
  (`DOCKER=1` also prunes Docker).
- Gitignored files (`.env`, `.venv/`, `data/`) are normal files, so read them by path; repo-wide search skips them.

---

## Where to look

| Working on… | Read |
|---|---|
| The source-file shape, plan/network/file/provider model, conflict resolution | [ingestion/etl/mrf-model.md](ingestion/etl/mrf-model.md) |
| Discovery - the monthly file list, the plan→file link, a file's ID, monthly churn | [ingestion/etl/discover.md](ingestion/etl/discover.md) |
| Extraction - target selection, when a file is downloaded again, recovering a failed or stopped parse, the parser, network attribution, GA NPI filter, Parquet writers, publishing the rates by network | [ingestion/etl/parse.md](ingestion/etl/parse.md) |
| Which plans we parse files for | [ingestion/etl/targets.yaml](ingestion/etl/targets.yaml) |
| The services the find-care screen offers, and what it says about each | [integration/services.yaml](integration/services.yaml) |
| NPPES Georgia provider subset | [ingestion/etl/nppes.md](ingestion/etl/nppes.md) |
| NUCC specialty labels | [ingestion/reference/taxonomy-labels.md](ingestion/reference/taxonomy-labels.md) |
| Provider↔procedure evidence (CMS utilization) | [ingestion/reference/cms-utilization.md](ingestion/reference/cms-utilization.md) |
| Medicare Physician Fee Schedule rate (`services.medicare_rate`) | [ingestion/reference/mpfs.md](ingestion/reference/mpfs.md) |
| Georgia's hospitals: CCN, name, address, phone and Medicare star rating | [ingestion/reference/hospitals.md](ingestion/reference/hospitals.md) |
| Georgia's individual-market plans: real names, metal level, referral rule, network and premiums by HIOS plan ID | [ingestion/reference/exchange-plans.md](ingestion/reference/exchange-plans.md) |
| Medicare hospital-outpatient (OPPS) rate - the labelled facility estimate on the imaging screen | [ingestion/reference/opps.md](ingestion/reference/opps.md) |
| Provider lat/long for distance ranking (Flow A build sequence step 1) | [ingestion/reference/geocode.md](ingestion/reference/geocode.md) |
| CMS MIPS quality score per clinician; ZIP → lat/lon for `?zip=` distance | [ingestion/reference/mips.md](ingestion/reference/mips.md) · [ingestion/reference/zip-centroids.md](ingestion/reference/zip-centroids.md) |
| Real practice identity + hospital-affiliation (CCN↔NPI) bridge - CMS Doctors & Clinicians | [ingestion/reference/doctors-clinicians.md](ingestion/reference/doctors-clinicians.md) |
| The build step: payer rates + reference → the find-care tables, and the checks on its inputs | [integration/build.md](integration/build.md) |
| The three standing architecture diagrams (data flow, serving entities, runtime) | [docs/architecture.md](docs/architecture.md) |
| API routes, query-layer notes | [application/serving/serving.md](application/serving/serving.md) |
| On-disk schema (Parquet) | [docs/schema.md](docs/schema.md) |
| Container images, ports, parallel stacks, sending a tester the Tailscale link | [infra/README.md](infra/README.md) |
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
