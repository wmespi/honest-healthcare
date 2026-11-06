# Testing

*Read this before changing test setup, adding a fixture, or wiring CI.*

Three suites, each a `make` target (`make help` is the target list):

| Command | Covers | Stack? |
|---|---|---|
| `make check` | docs drift (`scripts/check_docs.py`) + gofmt + vet + build + Go unit tests | ingestion up |
| `make test` | `check` + `tests/e2e` + each layer's pytest in its own container (`ingestion/`, `integration/`, `application/serving/`) + the screen's lint and vitest | full stack |
| `make test-live` | `tests/live`: golden answers (`test_golden.py`) + the named persona journeys ([journeys.md](journeys.md)) against the live API and real corpus | serving up, **real corpus** |

Unit tests live with their code: Go's `_test.go` files beside their package, the frontend's `*.test.js` and `*.test.jsx` files beside their code, and each Python layer's in its own `tests/` folder: `ingestion/tests/`, `ingestion/reference/tests/`, `integration/tests/` and `application/serving/tests/`.
`pytest.ini` at the root lists them, so `python -m pytest` from the root runs them all, which is how `LOCAL=1` runs them.
In the stack each container mounts only its own layer's code, so `make test` runs each layer's tests in that layer's container, and a test that reached into another layer would fail there.
Each layer also has a test that importing it loads nothing from the other two.
Tests that drive the running stack across components live in the top-level `tests/`:

- `tests/e2e/` runs the real ETL against fixtures in test isolation, then tears down.
- `tests/live/` checks real answers against the live API and real corpus.

`LOCAL=1` runs `check` / `test` on host toolchains with **no Docker** (go, `.venv`,
node via `scripts/dev-setup.sh`; the e2e fixtures are skipped). Run `make test LOCAL=1` before every commit. `test-live` is
never part of the gate: it needs the real corpus and skips cleanly (not fails) when
the target network isn't loaded or the API isn't reachable.

## Test isolation (`TEST=1` / `-test`)

Swaps the **Parquet root**: every path the ETL reads and writes moves from `data/` to `data-test/` (`core.ApplyTestMode`).
`data-test/` has the same layer folders as `data/` (`raw/`, `ingestion/`, `application/serving/`), mounted into the containers the same way.
`data-test/` is safe to delete at any time, and a test run never writes `data/` (Critical Rule 4).
Discovery in test mode caps at 100 reporting structures, parsing at 1 file (`LIMIT=` overrides).

## Fixtures (committed)

- `ingestion/etl/extraction/testdata/synthetic_mrf.json` - hand-written MRF exercising every
  parser branch; `ingestion/etl/extraction/stream_test.go` runs it hermetically.
- `ingestion/etl/extraction/testdata/fixtures/*.json.gz` - real, truncated MRFs from
  `make fixture`; `synthetic.json.gz` drives the e2e script, the rest are regression
  guards (`TestFixtures_Parse`). **Add one only for a genuinely new file shape.**
- `ingestion/etl/nppes/testdata/nppes_sample.csv` - NPPES GA extractor sample.
- `ingestion/reference/testdata/*` - small CMS / MPFS / DAC / geocode samples; each
  `ingestion/reference/tests/test_<builder>.py` runs its `ingestion/reference/` builder against them in test
  isolation. `test_common.py` runs the builders' download against a local HTTP server.
- `ingestion/tests/conftest.py` (`anthem` fixture) writes Anthem's files as parse and discover write them, with Georgia's networks and plans and a targets file, and `test_anthem_rates.py` publishes the rates by network from them.
- `integration/tests/app_tables.py` builds the build's default test corpus with the real
  `integration.build.build()` into `data-test/serving/`, and `make test` runs it before the
  app's tests, so they read tables integration wrote, handed over the way the real ones
  are. `application/serving/tests/conftest.py` (`api` fixture) binds a FastAPI `TestClient` to them,
  and fails, naming the command, if they're missing. `test_api_contract.py` covers every
  route this way, with no live server. Schemas track [schema.md](schema.md).
- `integration/tests/corpus.py` (the `corpus` fixture in `integration/tests/conftest.py`) writes a tiny copy of every dataset the find-care build reads in a temp folder, each with its declared columns, with methods to add or remove rows: payer rates and rosters by network, Georgia's networks, plans, premiums and hospitals, NPPES records, Census matches, Medicare's billing and rates, CMS groups and a services file.
  Its eight billers priced for a lower-back MRI each show one placement rule.
  The helpers queue their rows and write each file once, just before a build reads it, which keeps a test's setup to a few milliseconds.
  `integration/tests/inputs/` and `integration/tests/tables/` run the real find-care build (`integration.tables.build`) on it and read what it wrote, as `make build` does, rather than calling one table's class.
  So each test also checks that its table is in the build order after the tables it reads, and that every shared check runs.
  A test imports a table's class only to change it, as `test_tables.py` does to check column drift.
- `application/serving/tests/test_contract.py` (**no fixture**) checks the find-care contract's own rules
  (`application/serving/contract.py`) on sample rows, and that FastAPI publishes it.
- `tests/live/test_golden.py` — **no fixture**: real answers (a quote, a rollup, a
  provider ranking, counts) pinned against the live API (`API_URL`, default
  `http://localhost:8000/api`).

## E2E tests (with teardown)

`tests/e2e/discover.sh` runs discover in test mode on two months of a small index and checks each month's lists ([discover.md](../ingestion/etl/discover.md#a-files-id)): a file keeps its ID when only its signature changes, a dropped file is gone, the links are that month's and name listed files, the same index writes the same bytes, and the index download is deleted unless `-keep-index` keeps it.
`tests/e2e/parse.sh` parses `synthetic.json.gz` in test mode and asserts row counts, `network_name` and the parse record.
It then discovers a small served index and checks that parse downloads only the file a targets file names, never another file the index links to the same plan, and downloads it again only once its `Last-Modified` changes.
It also checks that a failed file waits until it's named with `-files`, and that a parse that stopped part way stops the next one ([parse.md](../ingestion/etl/parse.md#when-parse-downloads-a-file)).
`tests/e2e/nppes.sh` does the same for the NPPES extractor.
Each removes what it wrote under `data-test/`, and `discover.sh` and `parse.sh` also check that nothing under `data/` changed during the run.
`tests/e2e/lib.sh` holds their helpers, including the file server that stands in for Anthem: a Python server in the `ingestion` container, serving a folder under `data-test/raw/`, which `etl` reaches at `http://127.0.0.1:<port>/`.
`parse.sh` needs `data-test/ingestion/nppes/ga_providers.parquet` absent, because a stale copy makes the GA NPI filter drop every synthetic row.
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
| `web` | `npm run lint` + `npx vitest run` |
| `layers` | `docker compose up ingestion integration application`; `tests/e2e` and each layer's pytest in its own container, as `make test` runs them (`make _test-layers`), then `tests/live` (skips: no corpus) |
| `images` | builds the three `prod` Dockerfile targets, the application's on the app's test tables, and smokes each (`infra/smoke.py` for the application, with an invite the image makes) |
| `gate` | no upstream job failed or was cancelled |

