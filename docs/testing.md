# Testing

*Read this before changing test setup, adding a fixture, or wiring CI.*

Three suites, each a `make` target (`make help` is the target list):

| Command | Covers | Stack? |
|---|---|---|
| `make check` | docs drift (`scripts/check_docs.py`) + gofmt + vet + build + Go unit tests | etl up |
| `make test` | `check` + `serving/tests` (contract, reference builders, build) + vitest + `tests/e2e` | full stack |
| `make test-live` | `tests/live`: golden answers (`test_golden.py`) + the named persona journeys ([journeys.md](journeys.md)) against the live API and real corpus | serving up, **real corpus** |

Unit tests live with their code: Go's `_test.go` files beside their package, the frontend's `*.test.js` and `*.test.jsx` files beside their code, and the Python tests (serving, build and reference) in `serving/tests/`.
Tests that drive the running stack across components live in the top-level `tests/`:

- `tests/e2e/` runs the real ETL against fixtures in test isolation, then tears down.
- `tests/live/` checks real answers against the live API and real corpus.

`LOCAL=1` runs `check` / `test` on host toolchains with **no Docker** (go, `.venv`,
node via `scripts/dev-setup.sh`; the e2e fixtures are skipped). Run `make test LOCAL=1` before every commit. `test-live` is
never part of the gate: it needs the real corpus and skips cleanly (not fails) when
the target network isn't loaded or the API isn't reachable.

## Test isolation (`TEST=1` / `-test`)

Swaps two things: the **DB** (`TEST_DATABASE_URL`, `search_path=test`, so queue and
`coverage_log` writes hit `test.*`) and the **Parquet root** (`data-test/…`). Both are
safe to truncate at any time; a test run never touches `public.*` or `data/`
(Critical Rule 4). Discovery in test mode caps at 100 reporting structures, parsing
at 1 file (`LIMIT=` overrides).

The `test` schema is created from `public` via `LIKE … INCLUDING ALL`, which drifts when `public` changes and never copies foreign keys or sequences.
`db/migrations/999_test_schema.sql`, which `make migrate` runs last, drops and recreates it with its own foreign keys and id sequences, so a test run never uses up a `public` id.
Run `make migrate` after any schema change.

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
- `serving/tests/conftest.py` (`corpus` fixture) writes a tiny copy of every source the find-care build reads in a temp folder, with methods to add or remove rows: insurer rates and rosters, the index's plan-to-file link, Georgia's networks, plans, premiums and hospitals, NPPES records, Census matches, Medicare's billing and rates, CMS groups and a services file.
  Its eight billers priced for a lower-back MRI each show one placement rule.
  The helpers queue their rows and write each file once, just before a build reads it, which keeps a test's setup to a few milliseconds.
  `serving/tests/inputs/` and `serving/tests/tables/` run the real find-care build (`build.tables.build`) on it and read what it wrote, as `make build` does, rather than calling one table's class.
  So each test also checks that its table is in the build order after the tables it reads, and that every shared check runs.
  A test imports a table's class only to change it, as `test_tables.py` does to check column drift.
- `serving/tests/test_contract.py` (**no fixture**) checks the find-care contract's own rules
  (`serving/contract.py`) on sample rows, and that FastAPI publishes it.
- `tests/live/test_golden.py` — **no fixture**: real answers (a quote, a rollup, a
  provider ranking, counts) pinned against the live API (`API_URL`, default
  `http://localhost:8000`).

## E2E tests (with teardown)

`tests/e2e/discover.sh` runs discover in the `test` schema on two months of a small index and checks the [monthly rebuild](../etl/discover.md#the-monthly-rebuild): ids kept, dropped files removed unless parsed, links replaced in the queue and in the build's link Parquet, neither changed by a limited read, and a stop on a `processing` file.
`tests/e2e/parse.sh` parses `synthetic.json.gz` in the `test` schema and asserts row counts, `network_name` and a `coverage_log` row.
It then checks that parse downloads only the file a targets file names, never another file the index links to the same plan, and downloads it again only once its `Last-Modified` changes.
`tests/e2e/nppes.sh` does the same for the NPPES extractor.
Each truncates `test.*` and removes what it wrote under `data-test/`; `discover.sh` and `parse.sh` also check that `public`'s id sequence didn't move.
`tests/e2e/lib.sh` holds their helpers, including the file server that stands in for Anthem: a Python server in the `serving` container, which the `etl` container reaches at `http://serving:<port>/`.
`parse.sh` needs `data-test/nppes/ga_providers.parquet` absent, because a stale copy makes the GA NPI filter drop every synthetic row.
Every test tears down after itself, so `rm -rf data-test/*` is only needed after a killed run.

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
| `integration` | `docker compose up db etl serving` + `make migrate`; every hermetic pytest file, as `make test` runs them, then `tests/live` (skips: no corpus), then `tests/e2e` |
| `images` | builds the three `prod` Dockerfile targets and smokes each |
| `gate` | no upstream job failed or was cancelled |

JS lint (`npm run lint`) has pre-existing errors and is not gated.
