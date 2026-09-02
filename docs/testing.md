# Testing

*Read this before changing test setup, adding a fixture, or wiring CI.*

| Command | Scope | Stack? |
|---|---|---|
| `make check` | Pre-commit gate — `fmt` + `lint` (vet + build) + Go unit tests | etl up |
| `make check-local` | Same gate + the hermetic `pytest` contract suite + `vitest`, on **host toolchains** | **none** (host go / `.venv` / node — `scripts/dev-setup.sh`) |
| `make test` | Go unit slice only — hermetic, fixture-driven | etl up |
| `make test-e2e` | Hermetic end-to-end: parse + NPPES fixtures in test isolation, with teardown | full stack |
| `make test-api` | Backend contract + coverage (pytest, against the running API) | full stack |
| `make test-web` | Rate-explorer component tests (vitest + Testing Library, mocks the API) | frontend up |
| `make test-all` | `check` + `test-e2e` + `test-api` + `test-web` | full stack |

Run `make check` (canonical checkout) or `make check-local` (a worktree, no
Docker — [worktrees.md](worktrees.md)) before every commit (Critical Rule — fast).
`check-local` covers everything CI gates *except* the live-data coverage sweep;
that (and `test-e2e`) still needs a stack.

---

## Test isolation (`-test` / `TEST=1`)

`-test` swaps two things:

- **DB** — connects via `TEST_DATABASE_URL` (`search_path=test`); `billing_codes`,
  `index_files`, `coverage_log` writes hit `test.*`.
- **Parquet** — output moves to `../data-test/anthem/…` and
  `../data-test/nppes/ga_providers.parquet`.

Both `test.*` and everything under `data-test/` are safe to truncate/delete at any
time (Critical Rule 4 — never touch `public.*` or `data/` in a test run). Discovery
in test mode caps at 100 reporting structures; parsing caps at 1 file
(`LIMIT=` overrides).

> The `test` schema is created once from `public` via `LIKE … INCLUDING ALL`, so it
> **drifts** when a `public` column is added. `db/migrations/001_ga_coverage.sql`
> drops and recreates the whole `test` schema from `public` — run `make migrate`
> after any schema change.

---

## Fixtures (committed)

- `etl/extraction/testdata/synthetic_mrf.json` — hand-written MRF exercising every
  parser branch; `extraction/stream_test.go` runs `streamMRF` over it (hermetic —
  no network, no DB).
- `etl/nppes/testdata/nppes_sample.csv` — ~14 rows for the NPPES GA extractor.
- `reference/testdata/cms_sample.csv` — 15 rows for the CMS utilization builder
  (12 GA + 1 FL + 1 TX + 1 corrupt-NPI). `serving/tests/test_cms_utilization.py`
  and `test_specialty_profiles.py` run the `reference/` builders against it in
  test isolation (`data-test/cms/`, `data-test/reference/`) — hermetic, picked up
  by `make test-api`.
- `reference/testdata/mpfs_sample.csv` + `mpfs_gpci_sample.csv` — a PPRRVU-shaped
  RVU file (plain code, 26/TC split, facility-`NA` code, bundled `B`,
  carrier-priced `C`) and a GPCI file (GA localities 01/99 + a Florida row to
  filter). `serving/tests/test_mpfs.py` runs `reference/mpfs.py` against them in
  test isolation (`data-test/reference/mpfs_ga.parquet`) — hermetic; checks the
  RVU formula, the fac/non-fac PE split, and status handling.
- `reference/testdata/dac_sample.csv` — 14 rows for the CMS Doctors & Clinicians
  builder (9 GA clinicians incl. one with two groups + two hospitals, 2
  out-of-state, 1 corrupt NPI), in the wide single-file layout so `--dac-file`
  builds both outputs offline. `serving/tests/test_doctors_clinicians.py` runs it
  in test isolation — hermetic, picked up by `make check-local` and `make test-api`.
- `serving/tests/conftest.py` (`api` fixture) — builds a small coherent Parquet
  dataset (2 networks, 5 CPT codes with `-26`/`-TC` splits, 5 providers incl. a
  hospital org NPI, + NPPES/NUCC/RBCS/CMS/profile/MPFS/DAC tables) under
  `data-test/apifix/` and binds a FastAPI `TestClient` to it. Drives
  `test_api_contract.py` — every route, hermetic, no live server or `data/` mount.
  Schemas track [schema.md](schema.md); teardown removes the dir.
- `etl/extraction/testdata/fixtures/*.json.gz` — real, heavily-truncated MRFs from `make fixture` (first 25
  provider refs, NPI lists capped at 10, first 25 in-network items that touch a
  kept group, rates/prices capped). `synthetic.json.gz` drives `make test-e2e`;
  the rest are regression guards run by `TestFixtures_Parse`.

  **Add a fixture only when a file has a genuinely new shape** — a GA plan file, a
  vision/dental file, a file that failed. Not one per file; near-duplicate BlueCard
  shards add nothing.

## E2E scripts (with teardown)

- `scripts/etl_e2e_test.sh` — parses `fixtures/synthetic.json.gz` in the `test`
  schema, asserts row counts + `network_name` + a `coverage_log` row, then
  `TRUNCATE test.*` + `rm -rf data-test/anthem` on exit. Zero residue.
- `scripts/nppes_test.sh` — same shape for the NPPES GA extractor.

`make test-e2e` runs both.

> **Ordering:** `etl_e2e_test.sh` only gets the clean "keep all NPIs" path when
> `data-test/nppes/ga_providers.parquet` is absent — a stale copy makes the GA NPI
> filter drop every synthetic row. Every test that writes `data-test/` now tears
> it down (the e2e scripts delete through the `etl` container; the serving
> fixtures `os.remove` on teardown), so `rm -rf data-test/*` is only needed if a
> run was killed mid-flight.

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every PR and on push to `main`. A `changes`
job diffs the PR and sets `run` — `false` for a docs-only change (every path is a
`*.md`, under `docs/`, or `LICENSE`) or a draft PR, `true` otherwise. The four
heavy jobs are gated on `run == 'true'`; `CI Gate` (`if: always()`, `needs` all
four) is the **single required status check** and passes only when every heavy
job succeeded or was legitimately skipped. That replaces the old `paths-ignore` —
which left the required checks stuck "pending" forever on a docs-only PR.

| Job | Covers | How |
|---|---|---|
| `changes` | diffs the PR → `run` output (docs-only / draft ⇒ `false`) | `git diff --name-only`, no deps |
| `go` | `gofmt -l` + `go vet` + `go build` + `go test ./...` | native `setup-go` (`etl/go.mod`), no stack |
| `web` | `npx vitest run` | native `setup-node` 20, `npm ci` |
| `integration` | `test_api_contract.py` (every route, hermetic) + the three reference-builder tests (CMS utilization, specialty profiles, MPFS), then `make test-e2e` (parse + NPPES fixtures) | `docker compose up db etl serving` + `make migrate` |
| `images` | builds the three `prod` Dockerfile targets and smokes each (`etl --help`, `serving` GET /, `nginx` GET /) | raw `docker build --target prod` — catches Dockerfile / dep drift the compose `dev` targets don't |
| `gate` (`CI Gate`) | asserts no upstream job failed / was cancelled | `join(needs.*.result)` |

**Not in CI yet:** `test_coverage.py`'s coverage-basket assertions
(`test_core_code_has_rates` etc.) run against a live API with the full `data/`
mounted — the *contract* half of that file is now covered hermetically by
`test_api_contract.py`. JS lint (`npm run lint`) has pre-existing errors — not
gated until they're cleared.

## Frontend — `frontend/src/App.test.jsx`

vitest + Testing Library, hermetic (`vi.mock('./api')`, jsdom). Covers the
rate-explorer state machine: the default network-overview load, and the regression
that a **provider selected with no procedure** shows the
`/providers/{npi}/procedures` menu and never fires an npi-only
`/rates/distribution` (which full-scans and hangs). Config in
`frontend/vite.config.js` (`test:` block) + `frontend/src/test/setup.js`.

## Not yet implemented

| Layer | Tool | Would cover |
|---|---|---|
| Frontend E2E | Playwright | Real browser: histogram render, filter chips, mobile layout |
| ETL conflict-resolution | `go test ./...` | Plan-specific-file-wins rate override (Critical Rule 5) |
| Live coverage basket in CI | GitHub Actions | `test_coverage.py`'s `test_core_code_has_rates` — needs the real `data/`, not the synthetic fixture |
