# Testing

*Read this before changing test setup, adding a fixture, or wiring CI.*

Three suites, each a `make` target (`make help` is the target list):

| Command | Covers | Stack? |
|---|---|---|
| `make check` | docs drift (`scripts/check_docs.py`) + gofmt + vet + build + Go unit tests | etl up |
| `make test` | `check` + serving pytest (contract, reference builders, build; golden excluded) + vitest + the ETL e2e / NPPES fixture scripts | full stack |
| `make test-live` | golden answers (`serving/tests/test_golden.py`) + the named persona journeys ([journeys.md](journeys.md)) against the live API and real corpus | serving up, **real corpus** |

`LOCAL=1` runs `check` / `test` on host toolchains with **no Docker** (go, `.venv`,
node via `scripts/dev-setup.sh`; the e2e fixtures are skipped) — the worktree gate
([worktrees](../AGENTS.md#parallel--worktree-development)). Run `make test LOCAL=1` before every commit. `test-live` is
never part of the gate: it needs the real corpus and skips cleanly (not fails) when
the target network isn't loaded or the API isn't reachable.

## Test isolation (`TEST=1` / `-test`)

Swaps two things: the **DB** (`TEST_DATABASE_URL`, `search_path=test`, so queue and
`coverage_log` writes hit `test.*`) and the **Parquet root** (`data-test/…`). Both are
safe to truncate at any time; a test run never touches `public.*` or `data/`
(Critical Rule 4). Discovery in test mode caps at 100 reporting structures, parsing
at 1 file (`LIMIT=` overrides).

The `test` schema is created from `public` via `LIKE … INCLUDING ALL`, which drifts
when `public` changes and never copies foreign keys; the newest
`db/migrations/*.sql` drops and recreates it — run `make migrate` after any schema change.

## Fixtures (committed)

- `etl/extraction/testdata/synthetic_mrf.json` — hand-written MRF exercising every
  parser branch; `etl/extraction/stream_test.go` runs it hermetically.
- `etl/extraction/testdata/fixtures/*.json.gz` — real, truncated MRFs from
  `make fixture`; `synthetic.json.gz` drives the e2e script, the rest are regression
  guards (`TestFixtures_Parse`). **Add one only for a genuinely new file shape.**
- `etl/nppes/testdata/nppes_sample.csv` — NPPES GA extractor sample.
- `reference/testdata/*` — small CMS / MPFS / DAC / geocode samples; each
  `serving/tests/test_<builder>.py` runs its `reference/` builder against them in test
  isolation.
- `serving/tests/conftest.py` (`api` fixture) — writes a small coherent RAW Parquet set
  under `data-test/apifix/`, runs the real `build.build.build()` on it, and binds a
  FastAPI `TestClient` to the result. `test_api_contract.py` covers every route this
  way, with no live server and the same build path `make build` runs. Schemas track
  [schema.md](schema.md).
- `serving/tests/test_golden.py` — **no fixture**: real answers (a quote, a rollup, a
  provider ranking, counts) pinned against the live API (`API_URL`, default
  `http://localhost:8000`).

## E2E scripts (with teardown)

`scripts/etl_e2e_test.sh` parses `synthetic.json.gz` in the `test` schema, asserts row
counts, `network_name` and a `coverage_log` row, then truncates `test.*` and removes
`data-test/anthem`. `scripts/nppes_test.sh` does the same for the NPPES extractor.
`etl_e2e_test.sh` needs `data-test/nppes/ga_providers.parquet` absent (a stale copy
makes the GA NPI filter drop every synthetic row) — every test tears down after itself,
so `rm -rf data-test/*` is only needed after a killed run.

## CI (`.github/workflows/ci.yml`)

Runs on every PR and push to `main`. `changes` runs the docs drift check and sets
`run` — `false` for a docs-only or draft PR, `true` otherwise — and the heavy jobs
gate on it. `CI Gate` (`if: always()`, `needs` all jobs) is the **single required
status check**: it passes only when every job succeeded or was legitimately skipped.

| Job | Covers |
|---|---|
| `changes` | docs drift check; diffs the PR → `run` |
| `go` | gofmt + vet + build + `go test ./...` (native `setup-go`, no stack) |
| `web` | `npx vitest run` |
| `integration` | `docker compose up db etl serving` + `make migrate`; contract + builder tests, `test_golden.py` (skips — no corpus), then the e2e scripts |
| `images` | builds the three `prod` Dockerfile targets and smokes each |
| `gate` | no upstream job failed or was cancelled |

JS lint (`npm run lint`) has pre-existing errors and is not gated.
