---
name: verify
description: >
  Validate a change in the honest-healthcare repo before committing or opening a
  PR. Use when the user asks to "check", "run the tests", "make sure this works",
  or is about to commit/push. Covers which make target maps to which change, and
  the repo's DuckDB / Docker / git footguns that don't show up in a normal test
  run.
---

# Verifying a change

## Pick the target by what changed

| Changed | Run | Needs |
|---|---|---|
| Go (`ingestion/etl/**`) | `make check` (or `make check LOCAL=1`, no Docker) | `ingestion` container up |
| Go parser behaviour / MRF handling, NPPES extraction | `make test` (runs the ETL e2e + NPPES fixtures) | full stack |
| Ingestion's Python (`ingestion/reference/**`, `ingestion/anthem_rates.py`) or the build (`integration/**`) | `make test` (or `make test LOCAL=1`) | full stack |
| Serving layer (`application/serving/**`) | `make test` (or `make test LOCAL=1`) | application container running the new code (see restart note) |
| Frontend (`application/frontend/src/**`) | `make test` (or `make test LOCAL=1`); `docker compose restart application` rebuilds the screen it serves | `application` container up |
| Docs / Makefile / scripts only | `make check` is enough | `ingestion` up |
| Anything non-trivial before a PR | `make test` | full stack |
| A change to a real answer (rates, rankings, labels) | `make test-live` | application up, **real corpus** |

`make check` = docs drift check + the Go gate; it's the pre-commit gate. `make help` lists every target.

## Footguns

- **Backend module changes need the container to reload.** `uvicorn --reload`
  usually picks up edits, but after adding/moving a module run
  `docker compose restart application` before `make test`, and clear stale
  `application/serving/__pycache__` if imports act strange.
- **Ad-hoc DuckDB queries spill into the repo.** A one-off `duckdb.connect()` (not
  through `application/serving/data_sources.py:db()`, which sets `DUCKDB_TMP`) must
  `SET temp_directory='/tmp/dsp'` first, or a big aggregate spills to `./.tmp/`
  and breaks `git add`. Read one `net=<slug>` partition of `data/raw/anthem/prices/`,
  not the whole store.
- **`make reference STEP=nppes` is not atomic** — mid-run the output file is 0
  bytes and queries that touch it 500. Let it finish, then restart `application`.
- **`make test` and `make test-live` hit different data.** `test` is hermetic:
  fixtures, `data-test/`, mocked `./api` in vitest. A green `test` does not
  prove a serving-layer change works on real volume — run `test-live` too.

## Reporting

State what you ran and the actual result — "`make test`: green", "`make check`: green". If you skipped a layer that the change touches,
say so.
