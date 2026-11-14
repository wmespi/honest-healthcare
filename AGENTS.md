# Honest Healthcare - Agent Charter

Price transparency tooling for Anthem Machine-Readable Files (MRFs).
It streams multi-GB negotiated-rate files in one pass, stores them as Parquet, and serves a find-care screen that shows the cheapest place nearby for a service on your plan.

**Primary use case:** rates for `BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM`, an individual HMO on Anthem's Blue Value network in Georgia (exchange network `45334-GAN001`).
Georgia's exchange plan file gives each plan its network by HIOS plan ID ([ingestion/reference/exchange-plans.md](ingestion/reference/exchange-plans.md)), and the rates are keyed on that network ID.
The `network_name` label in Anthem's file (`GA Blue Value HIX Individual Network`) only tells parse which file and which rows are the network's.
[ingestion/etl/targets.yaml](ingestion/etl/targets.yaml) lists every network we price.

This file is the charter: read it first, then follow the [doc map](#where-to-look) to the detail for whatever you're touching.
Keep it thin; detail lives next to the code.

**Current focus** *(update as the active work changes)*: epic [#191](https://github.com/wmespi/honest-healthcare/issues/191), pricing any shoppable service at any place with a quality signal, in the order of work on the issue.
Deploys from GitHub Actions ([#180](https://github.com/wmespi/honest-healthcare/issues/180)) follow the Cloud Run deploy for invited testers ([#17](https://github.com/wmespi/honest-healthcare/issues/17)).
The find-care tables' data model is the ERD on [#135](https://github.com/wmespi/honest-healthcare/issues/135), whose order of work is done.
Product direction is in [docs/direction.md](docs/direction.md).
Don't restructure `ingestion/`, `integration/` or `application/` without a reason (rule 7).

---

## Architecture

Three layers, one direction, each in its own container and reading only what the layer before it delivers:

1. **Ingestion** (`ingestion/`) reads each source and keeps what matters.
   **Discovery** (Go) lists this month's MRF URLs and the plan→file link.
   **Extraction** (Go) streams the files `ingestion/etl/targets.yaml` names to raw Parquet, with a provider probe that aborts empty downloads before `in_network`.
   **Publishing** (Python/DuckDB, `ingestion/anthem_rates.py`, the last step of `make parse`) keys each target network's rates on its exchange network ID.
   **Reference** (Go + Python/DuckDB) lands the public datasets.
   Ingestion keeps its own files in `data/raw/` and delivers what integration reads to `data/ingestion/`.
2. **Integration** (`integration/`, Python/DuckDB, `make build`) turns that into the find-care tables in `data/serving/`, and is where every product decision lives.
3. **Application** (`application/`, `localhost:8000`): **serving** (FastAPI + DuckDB) queries those tables in-process under `/api`, and serves the built **frontend** (React) at every other path.

Storage is Parquet+ZSTD under `data/`, with no database.
Layer table and diagrams: **[docs/architecture.md](docs/architecture.md)**; on-disk layout: **[docs/schema.md](docs/schema.md)**; where it runs (images, ports, tester access, Cloud Run): [infra/](infra/README.md).

---

## The language principle

**Go** = single-pass streaming acquisition of large raw sources that can't be held in memory (MRF JSON, the NPPES CSV).
Hand-rolled streaming parser, tight memory control.

**Python (over DuckDB)** = relational reshaping: joins, enrichment, aggregation against data already landed.
SQL-shaped glue where DuckDB / pyarrow does the work in C++.

The dividing line is *"hand-rolled streaming parser vs. SQL-shaped transform"*, not extract-vs-serve.
NPPES stays in Go even though it writes a dimension table, because the work is the stream.
NUCC/CMS-utilization are Python even though they're "extraction", because the work is a filter/join over a CSV that DuckDB's parallel C++ reader handles, not a hand-rolled streaming parse.
Publishing the payer rates is Python for the same reason: it joins Parquet that extraction already wrote.

---

## Critical rules

1. **`exec` not `run --rm`**: always `docker compose exec <service>` so output streams to Docker Desktop logs.
   The `make` targets already do this.
2. **Look at a stopped or failed parse before retrying it**: a `failed` parse record or a leftover `data/raw/anthem/.inflight/<name>/` means investigate first.
   Repeated failures on one file mean a bad file, not a transient error.
3. **No full-file buffering in extraction**: the Go parser must stream; MRFs can exceed 10 GB.
4. **Test mode writes only `data-test/`**: it's safe to delete at any time, and a test run must never touch `data/`.
5. **One plan, one contract**: a plan a person can pick is priced by one file, so nothing combines or overrides prices across files.
   `ingestion/etl/targets.yaml` names the files to parse, and parse stops unless the index links each one to a plan on Georgia's exchange networks, which keeps out `GA_JBKEMED0001`: it carries Blue Value's network label but prices an older plan that isn't sold any more.
   [ingestion/etl/mrf-model.md](ingestion/etl/mrf-model.md#one-plan-one-contract).
6. **Give regular status updates - the most-missed rule here.**
   On any multi-step task, post a short progress note *as each step lands*: what's done, what's next, anything that changed, not just a summary at the end.
   If you've run several tool calls without saying anything to the user, stop and post one.
   A plan, a finding that changes direction, a merged PR, a failing test: each is its own note, in the moment, not folded into a later recap.
   Err toward over-communicating.
7. **No structural churn during a feature push**: batch pure renames and reorganizations into a deliberate tidy window, land them, then update docs and memory once.
   A restructure PR mid-feature-run competes for review attention and rebases badly against the open feature branches.

---

## Development commands

`make help` lists everything.
One name per workflow: the `make` target, the `etl` subcommand, and the helper doc all share it.
Test isolation and single-item selection are variables.
The pipeline commands below are in the order they run, which `make refresh` follows:

```bash
make start                  # Docker Desktop (if needed) + all containers
make up / make down / make logs

make discover               # this month's file list + plan→file link (index_files, index_file_plans Parquet) from Anthem's index
make discover SCHEMA=1      #   stream only, write index_schema.json and nothing else
make reference              # NPPES, NUCC labels, CMS utilization, MPFS, OPPS, shoppable items, Doctors & Clinicians, geocode, MIPS, ZIP centroids, exchange plans, hospitals,
                            #   hospital price files; skips each whose output exists, except geocode, which runs every time and looks up only new
                            #   addresses, and hospital price files, which fetch only the hospitals with none (STEP=hospital-rates fetches all)
make reference STEP=mpfs    #   one builder, always runs (ARGS="--year 2025" passes through; FORCE=1 rebuilds all)
make parse                  # stream the files ingestion/etl/targets.yaml names → Parquet when new or updated, then publish their rates by Georgia network (needs the NPPES and exchange-plans references first)
make parse FILE=GA_JBNKMED0001.json.gz  #   one file by name, now (bypasses target selection and its parse record)
make parse TARGETS=<path>   #   a different target-plan list (default ingestion/etl/targets.yaml)
make parse TEST=1           #   test isolation (data-test/)
make build                  # what ingestion delivered (data/ingestion/) → the find-care tables in data/serving/
make coverage               # what the build keeps at each step, from rates on file to rows shown; writes nothing (BY=service, TEST=1)
make refresh                # the monthly job: discover → reference → parse → reference STEP=geocode → build
make deploy                 # ship the committed code and data/serving/'s tables to Cloud Run behind Google sign-in (infra/deploy.md)

make check                  # pre-commit gate (container): docs check + gofmt + vet + build + Go unit tests; gofmt rewrites files here
make check LOCAL=1          #   same gate on host toolchains, NO Docker; gofmt only lists the files, as CI does
make test                   # every hermetic suite: check + ETL e2e + each layer's pytest in its own container + the screen's eslint and vitest
make test LOCAL=1           #   host toolchains, no Docker, no ETL e2e; works in any checkout but fails with
                            #   "no host 'go'" / "no .venv" / "no application/frontend/node_modules" until scripts/dev-setup.sh has been run
make test-live              # golden answers + user journeys against the live API + real corpus (docs/journeys.md)

make worktree TOPIC=x       # only for parallel work: new sibling worktree + branch off main, set up (GH #59)
make worktree-rm TOPIC=x    # remove it once its PR merges
make footprint / make clean # disk report / reclaim regenerable artifacts
make fixture FILE=<name>    # truncated *.json.gz fixture from a file in this month's index
make sh S=integration       # shell into a container: ingestion, integration or application
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
- Any ad-hoc `duckdb.connect()` must `SET temp_directory` (or go through `db()` in `application/serving/data_sources.py`), or a spilling query fills the repo's `.tmp/`.
  `make footprint` reports disk use; `make clean` reclaims regenerable artifacts (`DOCKER=1` also prunes Docker).
- Gitignored files (`.env`, `.venv/`, `data/`) are normal files, so read them by path; repo-wide search skips them.

---

## Where to look

| Working on… | Read |
|---|---|
| The source-file shape, plan/network/file/provider model, conflict resolution | [ingestion/etl/mrf-model.md](ingestion/etl/mrf-model.md) |
| Discovery - the monthly file list, the plan→file link, a file's ID, monthly churn | [ingestion/etl/discover.md](ingestion/etl/discover.md) |
| Extraction - target selection, when a file is downloaded again, recovering a failed or stopped parse, the parser, network attribution, GA NPI filter, Parquet writers, publishing the rates by network | [ingestion/etl/parse.md](ingestion/etl/parse.md) |
| Which plans we parse files for | [ingestion/etl/targets.yaml](ingestion/etl/targets.yaml) |
| The families of services the find-care screen offers: each one's codes, bills, benefits, kinds of place and what the screen says | [integration/families.yaml](integration/families.yaml) |
| The services in each family, by our own names | [integration/services.yaml](integration/services.yaml) |
| NPPES Georgia provider subset | [ingestion/etl/nppes.md](ingestion/etl/nppes.md) |
| NUCC specialty labels | [ingestion/reference/taxonomy-labels.md](ingestion/reference/taxonomy-labels.md) |
| Provider↔procedure evidence (CMS utilization) | [ingestion/reference/cms-utilization.md](ingestion/reference/cms-utilization.md) |
| Medicare Physician Fee Schedule rate (`services.medicare_rate`) | [ingestion/reference/mpfs.md](ingestion/reference/mpfs.md) |
| Georgia's hospitals: CCN, name, address, phone and Medicare star rating | [ingestion/reference/hospitals.md](ingestion/reference/hospitals.md) |
| Hospitals' own price files - which hospitals and payers (`ingestion/etl/hospitals.yaml`), the CSV and JSON formats, the `hospital_rates` rows | [ingestion/etl/hospital-rates.md](ingestion/etl/hospital-rates.md) |
| Georgia's individual-market plans: real names, metal level, referral rule, network and premiums by HIOS plan ID | [ingestion/reference/exchange-plans.md](ingestion/reference/exchange-plans.md) |
| Medicare hospital-outpatient (OPPS) rate (`services.medicare_facility_rate`) - the rough guide the find-care screen gives at a hospital | [ingestion/reference/opps.md](ingestion/reference/opps.md) |
| Which codes are candidates for a service: CMS's 500 shoppable items, each with CMS's classification (RBCS) | [ingestion/reference/shoppable.md](ingestion/reference/shoppable.md) |
| Where each biller and hospital is on the map, and how far it is from a ZIP code | [ingestion/reference/geocode.md](ingestion/reference/geocode.md) |
| CMS MIPS quality score per clinician; ZIP → lat/lon for `?zip=` distance | [ingestion/reference/mips.md](ingestion/reference/mips.md) · [ingestion/reference/zip-centroids.md](ingestion/reference/zip-centroids.md) |
| Real practice identity - the CMS group a doctor's own billing NPI is named after (CMS Doctors & Clinicians) | [ingestion/reference/doctors-clinicians.md](ingestion/reference/doctors-clinicians.md) |
| The build step: payer rates + reference → the find-care tables, the checks on its inputs, and what each step keeps (`make coverage`) | [integration/build.md](integration/build.md) |
| The layers and what each delivers, and the three standing diagrams (data flow, serving entities, runtime) | [docs/architecture.md](docs/architecture.md) |
| API routes, query-layer notes | [application/serving/serving.md](application/serving/serving.md) |
| On-disk schema (Parquet) | [docs/schema.md](docs/schema.md) |
| Container images, ports, parallel stacks, sending a tester the Tailscale link | [infra/README.md](infra/README.md) |
| Deploying to Cloud Run, who can sign in, the spend cap, rolling back | [infra/deploy.md](infra/deploy.md) |
| The three test suites, isolation, fixtures, CI jobs | [docs/testing.md](docs/testing.md) |
| User journeys - the persona clickpaths `make test-live` asserts | [docs/journeys.md](docs/journeys.md) |
| What's wrong / missing / deferred | [docs/known-gaps.md](docs/known-gaps.md) |
| Where the product is headed - the two flows, the navigator direction, the data roadmap | [docs/direction.md](docs/direction.md) |
| CMS spec | https://github.com/CMSgov/price-transparency-guide |

**Doc rules** (`scripts/check_docs.py`, run by `make check` and CI, enforces links, `make` targets, file paths and reachability, but not that what a doc says is still true).
1. One fact, one home - link, don't restate.
2. No hand-typed numbers (counts, sizes, latencies): say "run X", with a command that exists.
3. Describe the current state, never history (no "was", "used to", "since #NN": that is git).
4. A doc not reachable from the map above doesn't exist - link it or delete it.
5. Helper docs cap at about 100 lines; a fixed gap is deleted from `docs/known-gaps.md`.

**Doc naming.** ALL-CAPS is reserved for repo-meta files (`README.md`, `AGENTS.md`, `CLAUDE.md`) and the `SKILL.md` that Claude Code requires in each `.claude/skills/` folder.
Topic and helper docs are lowercase and, where a workflow exists, share the name of its `make` target / `etl` subcommand (`parse` → `parse.md`).
