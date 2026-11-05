# Testing

*Read this before changing test setup, adding a fixture, or wiring CI.*

Three suites, each a `make` target (`make help` is the target list):

| Command | Covers | Stack? |
|---|---|---|
| `make check` | docs drift (`scripts/check_docs.py`) + gofmt + vet + build + Go unit tests | etl up |
| `make test` | `check` + pytest (`ingestion/tests`, `reference/tests`, `build/tests`, `serving/tests`) + vitest + `tests/e2e` | full stack |
| `make test-live` | `tests/live`: golden answers (`test_golden.py`) + the named persona journeys ([journeys.md](journeys.md)) against the live API and real corpus | serving up, **real corpus** |

Unit tests live with their code: Go's `_test.go` files beside their package, the frontend's `*.test.js` and `*.test.jsx` files beside their code, and each Python layer's in its own `tests/` folder: `ingestion/tests/`, `reference/tests/`, `build/tests/` and `serving/tests/`.
`pytest.ini` at the root lists them, so `python -m pytest` from the root runs them all.
Tests that drive the running stack across components live in the top-level `tests/`:

- `tests/e2e/` runs the real ETL against fixtures in test isolation, then tears down.
- `tests/live/` checks real answers against the live API and real corpus.

`LOCAL=1` runs `check` / `test` on host toolchains with **no Docker** (go, `.venv`,
node via `scripts/dev-setup.sh`; the e2e fixtures are skipped). Run `make test LOCAL=1` before every commit. `test-live` is
never part of the gate: it needs the real corpus and skips cleanly (not fails) when
the target network isn't loaded or the API isn't reachable.

## Test isolation (`TEST=1` / `-test`)

Swaps the **Parquet root**: every path the ETL reads and writes moves from `data/` to `data-test/` (`core.ApplyTestMode`).
`data-test/` is safe to delete at any time, and a test run never writes `data/` (Critical Rule 4).
Discovery in test mode caps at 100 reporting structures, parsing at 1 file (`LIMIT=` overrides).

## Fixtures (committed)

- `etl/extraction/testdata/synthetic_mrf.json` — hand-written MRF exercising every
  parser branch; `etl/extraction/stream_test.go` runs it hermetically.
- `etl/extraction/testdata/fixtures/*.json.gz` — real, truncated MRFs from
  `make fixture`; `synthetic.json.gz` drives the e2e script, the rest are regression
  guards (`TestFixtures_Parse`). **Add one only for a genuinely new file shape.**
- `etl/nppes/testdata/nppes_sample.csv` — NPPES GA extractor sample.
- `reference/testdata/*` — small CMS / MPFS / DAC / geocode samples; each
  `reference/tests/test_<builder>.py` runs its `reference/` builder against them in test
  isolation.
- `ingestion/tests/conftest.py` (`anthem` fixture) writes Anthem's files as parse and discover write them, with Georgia's networks and plans and a targets file, and `test_anthem_rates.py` publishes the rates by network from them.
- `serving/tests/conftest.py` (`api` fixture) writes the build's default test corpus
  under `data-test/apifix/`, runs the real `build.build.build()` on it, and binds a
  FastAPI `TestClient` to the result. `test_api_contract.py` covers every route this
  way, with no live server and the same build path `make build` runs. Schemas track
  [schema.md](schema.md).
- `build/tests/corpus.py` (the `corpus` fixture in `build/tests/conftest.py`) writes a tiny copy of every dataset the find-care build reads in a temp folder, each with its declared columns, with methods to add or remove rows: payer rates and rosters by network, Georgia's networks, plans, premiums and hospitals, NPPES records, Census matches, Medicare's billing and rates, CMS groups and a services file.
  Its eight billers priced for a lower-back MRI each show one placement rule.
  The helpers queue their rows and write each file once, just before a build reads it, which keeps a test's setup to a few milliseconds.
  `build/tests/inputs/` and `build/tests/tables/` run the real find-care build (`build.tables.build`) on it and read what it wrote, as `make build` does, rather than calling one table's class.
  So each test also checks that its table is in the build order after the tables it reads, and that every shared check runs.
  A test imports a table's class only to change it, as `test_tables.py` does to check column drift.
- `serving/tests/test_contract.py` (**no fixture**) checks the find-care contract's own rules
  (`serving/contract.py`) on sample rows, and that FastAPI publishes it.
- `tests/live/test_golden.py` — **no fixture**: real answers (a quote, a rollup, a
  provider ranking, counts) pinned against the live API (`API_URL`, default
  `http://localhost:8000`).

## E2E tests (with teardown)

`tests/e2e/discover.sh` runs discover in test mode on two months of a small index and checks each month's lists ([discover.md](../etl/discover.md#a-files-id)): a file keeps its ID when only its signature changes, a dropped file is gone, the links are that month's and name listed files, the same index writes the same bytes, and the index download is deleted unless `-keep-index` keeps it.
`tests/e2e/parse.sh` parses `synthetic.json.gz` in test mode and asserts row counts, `network_name` and the parse record.
It then discovers a small served index and checks that parse downloads only the file a targets file names, never another file the index links to the same plan, and downloads it again only once its `Last-Modified` changes.
It also checks that a failed file waits until it's named with `-files`, and that a parse that stopped part way stops the next one ([parse.md](../etl/parse.md#when-parse-downloads-a-file)).
`tests/e2e/nppes.sh` does the same for the NPPES extractor.
Each removes what it wrote under `data-test/`, and `discover.sh` and `parse.sh` also check that nothing under `data/` changed during the run.
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
| `integration` | `docker compose up etl serving`; every hermetic pytest file, as `make test` runs them, then `tests/live` (skips: no corpus), then `tests/e2e` |
| `images` | builds the three `prod` Dockerfile targets and smokes each |
| `gate` | no upstream job failed or was cancelled |

JS lint (`npm run lint`) has pre-existing errors and is not gated.
