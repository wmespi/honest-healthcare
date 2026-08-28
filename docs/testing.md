# Testing

*Read this before changing test setup, adding a fixture, or wiring CI.*

| Command | Scope | Stack? |
|---|---|---|
| `make check` | Pre-commit gate — `fmt` + `lint` (vet + build) + Go unit tests | etl_go up |
| `make test` | Go unit slice only — hermetic, fixture-driven | etl_go up |
| `make test-e2e` | Hermetic end-to-end: parse + NPPES fixtures in test isolation, with teardown | full stack |
| `make test-api` | Backend contract + coverage (pytest, against the running API) | full stack |
| `make test-web` | Rate-explorer component tests (vitest + Testing Library, mocks the API) | frontend up |
| `make test-all` | `check` + `test-e2e` + `test-api` + `test-web` | full stack |

Run `make check` before every commit (Critical Rule — fast, no full stack).

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

## Fixtures — `etl-go/testdata/` (committed)

- `synthetic_mrf.json` — hand-written MRF exercising every parser branch;
  `mrf_test.go` runs `streamMRF` over it (hermetic — no network, no DB).
- `nppes_sample.csv` — ~14 rows for the NPPES GA extractor.
- `fixtures/*.json.gz` — real, heavily-truncated MRFs from `make fixture` (first 25
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
| CI | GitHub Actions | `make check` on every push (no workflow exists yet) |
