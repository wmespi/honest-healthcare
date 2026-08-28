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
| Go (`etl/**`) | `make check` | `etl` container up |
| Go parser behaviour / MRF handling | `make check` **and** `make test-e2e` | full stack |
| NPPES extraction (`nppes.go`) | `make check` **and** `make test-e2e` | full stack |
| Serving layer (`serving/**`) | `make test-api` | serving container running the new code (see restart note) |
| Frontend (`frontend/src/**`) | `make test-web` | `frontend` container up |
| SQL migrations (`db/migrations/*.sql`) | `make migrate` then re-run it (must be idempotent) | `db` up |
| `db/init.sql` | apply it to a scratch database and diff `\dt` | `db` up |
| Docs / Makefile / scripts only | `make check` is enough | `etl` up |
| Anything non-trivial before a PR | `make test-all` | full stack |

`make check` = `fmt` + `lint` (vet + build) + Go unit tests. It's the pre-commit
gate — always run it. `make help` lists every target.

## Footguns

- **Backend module changes need the container to reload.** `uvicorn --reload`
  usually picks up edits, but after adding/moving a module run
  `docker compose restart serving` before `make test-api`, and clear stale
  `serving/__pycache__` if imports act strange.
- **Ad-hoc DuckDB queries spill into the repo.** Any `duckdb.connect()` you write
  for a one-off check (not through `serving/data_sources.py:db()`) must
  `SET temp_directory='/tmp/dsp'` first — otherwise a big aggregate spills to
  `./.tmp/` in the repo root and breaks `git add`. Prefer a network-partition-
  pruned path (`read_parquet('data/anthem/prices/net=<slug>/*.parquet')`) over
  scanning all of `prices ⨝ group_sets`.
- **`make nppes` is not atomic.** It rewrites `data/nppes/ga_providers.parquet`
  in place; mid-run the file is 0 bytes and serving-layer queries that touch it 500.
  Don't run it while relying on the API; wait for it to finish, then
  `docker compose restart serving`.
- **`make test-e2e` and `make test-api` hit different data.** e2e runs in the
  `test` schema against committed fixtures with teardown; `test-api` runs against
  the live `public` data in the running stack. A green e2e does not prove a
  serving-layer change works on real volume — run `test-api` too.
- **`make test-web` proves nothing about the serving layer.** It mocks `./api`
  entirely. For real integration use `make smoke-web` (hits live endpoints).
- **Squash-merged base branch → cherry-pick, don't rebase.** When a stacked PR's
  base was squash-merged into `main`, `git rebase origin/main` replays the
  already-merged commits and conflicts. Instead:
  `git checkout -B <branch> origin/main && git cherry-pick <your-commit>`, then
  retarget the PR base to `main` (`gh pr edit <n> --base main`).

## Reporting

State what you ran and the actual result — "`make test-api`: 21 passed, 1
xfailed", "`make check`: green". If you skipped a layer that the change touches,
say so.
