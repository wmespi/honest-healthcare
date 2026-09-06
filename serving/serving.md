# Serving layer — FastAPI + DuckDB

*Read this when adding or changing an API route.* The API reads **only**
`data/serving/` — the build step's output (`make build`, [build/build.md](../build/build.md)).
There is no fallback to raw Parquet: a missing build is a `503` from `GET /`,
not a slower degraded mode. Tests: `test_api_contract.py` (every route,
hermetic — a synthetic raw fixture in `conftest.py` runs the real
`build.build.build()`, then binds a `TestClient`); `make test-api` also runs
`test_coverage.py` against the live API with the full `data/`. Add a route →
add a contract test.

Every route runs raw DuckDB SQL against `read_parquet(...)` on the serving
tables — no ORM, `?` placeholders, one process-wide connection (`db()`).

## Layout

| File | Holds |
|---|---|
| `main.py` | app + CORS + `include_router` + `GET /` (health **and** trust-bar context: `priceable_npis`, `networks`, `n_codes`, `as_of`, `built_at`/`partial_build` from the build's `manifest.json`, `reference_loaded` — which optional datasets were build inputs) |
| `data_sources.py` | the serving-table sources (`*_SRC`), `db()` (one process-wide DuckDB database, `cursor()` per call), `network_slug()`, `rate_filters()`, `have_build()`/`missing_build()`, `manifest()`/`built_with()` |
| `labels.py` | consumer presentation — `pos_bucket()` + `POS_LABELS`, `MODIFIER_LABELS`, `provider_card()` (one `provider_dim` row + `provider_affiliations`), `plausibility()` |
| `evidence.py` | provider↔procedure evidence, read off the build's `evidence` table (issue #14) — `did_bill()`, `billed_codes()`, `medicare_specialty()`, `typical_codes()` / `code_tiers()`. `available()` / `profiles_available()` check `built_with(...)`. |
| `benchmark.py` | `medicare_allowed(conn, code, type)` — the CMS Physician Fee Schedule allowed $ for a code in Georgia, off `code_dim`. `None` until MPFS was a build input. |
| `routers/rates.py` | `/rates/distribution`, `/rates/by_network`, `/rates/providers`, `/rates/quote` |
| `routers/providers.py` | `/providers/{npi}/procedures`, `/providers/search` (`specialty=` fuzzy label match **or** `service_line=` exact taxonomy-code allowlist, e.g. `service_line=pcp`), `/specialties`, `/providers/ga`, `/service_lines` (the curated allowlists, for the frontend to stop hand-syncing its own copy) |
| `service_lines.py` | Curated NUCC taxonomy-code allowlists per service line (issue #83) — `SERVICE_LINES = {"pcp": [...]}`. Also `build/build.py`'s source for `provider_dim.service_lines`. |
| `routers/reference.py` | `/networks`, `/billing_codes`, `/procedure_categories`, `/plans` |

SQL still lives inline in the route handlers — moving it into a `queries/` module
is a later step ([issue #13](https://github.com/wmespi/honest-healthcare/issues/13)).

`RATE_GROUPS_SRC` = `rates ⨝ group_sets` on `(file_id, group_set_id)` — the join
that expands a price row to its provider groups. Schema:
[../docs/schema.md](../docs/schema.md).

## The four consumer jobs

Every rate view is scoped to **outpatient professional fee-for-service dollar
rates** — `rates.scope = 'outpatient_prof'`, baked in at build time
(`data_sources.outpatient_scope()` is the definition build/build.py pins
against; `test_build.py` guards the two staying equal). This drops facility/
institutional lines, inpatient-only rates, `bundle`/`capitation`, and
`percentage`/`per diem`/`derived` types. Every dollar view also drops
`is_sentinel` rows — placeholder rates Anthem fills for not-separately-priced
codes.

Jobs 1 and 3 **require a `network_name`** (`400 {"detail": {"code":
"network_required"}}` otherwise) — a rate is only comparable within a plan,
kept as a product rule (not a scale guard — see the epic's checkpoint
decisions on [#100](https://github.com/wmespi/honest-healthcare/issues/100)).

| Route | Job | Returns |
|---|---|---|
| `/rates/quote?billing_code&npi&network_name` | **1** — one procedure at one provider | headline rate + breakdown by component (global / `-26` / `-TC`) and place of service, + `plausibility`, `medicare_utilization`, `tier`, `medicare_allowed`/`vs_medicare`. **Needs `network_name`.** |
| `/rates/by_network?billing_code` | **2** — same procedure across every network | one row per network off `cross_network_rollup` (a roster-weighted CDF over `rate_hist`), sorted cheapest median first |
| `/rates/providers?billing_code&network_name` | **3** — compare across providers | one row per **billing practice** (`tin_value`), ordered by price. **Rule 5** (AGENTS.md #5, #100): within a practice, a `plan_specific` row wins over `shared` for the same code; `summary` stays over the full (uncollapsed) group-rate distribution. **Needs `network_name`.** |
| `/providers/{npi}/procedures` | **4** — the provider "menu" | procedures this NPI has a rate for, `tier` (`billed`/`typical`/`group`) from `evidence`, falling back to the full list when the plausible tier would hide everything |

Supporting: `/rates/distribution` — the histogram + summary off `rate_hist`
(roster-weighted) for every no-code overview and a code without
`network_name`; the live per-code path (over `rates ⨝ group_sets`) runs only
with a `network_name` or an `npi`, and 400s on `npi`-without-`billing_code`.
Also `/networks`, `/providers/search`, `/specialties`, `/procedure_categories`,
`/billing_codes`, `/providers/ga`, `/plans`, `/service_lines`.

## Key helpers

- `rate_filters(...)` (`data_sources.py`) — the shared WHERE for `RATE_GROUPS_SRC`.
  `scope=True` (default) keeps `outpatient_prof`; `drop_sentinel=True` removes
  placeholders (jobs 1-3; the histogram keeps them so it can still show the
  full picture — known-gaps).
- `network_slug()` — must stay identical to
  `etl/extraction/partition.go:slugifyNetwork` (partition pruning depends on it).
- `provider_card(conn, npi)` (`labels.py`) — one `provider_dim` row +
  `hospital_affiliations` from `provider_affiliations`. `group_name` is the CMS
  Doctors & Clinicians identity; `org_name` is the raw NPPES entity name — the
  two are deliberately separate columns (a group affiliation must never shadow
  an individual's own name in a search result).
- `evidence.code_tiers(conn, npi, codes)` → `billed`/`typical`/`group` per code,
  a straight read of the build's `evidence` table — no per-request CMS join.
- `manifest()` / `built_with(input_name)` (`data_sources.py`) — what the current
  build was made from (`data/serving/manifest.json`); `GET /`'s
  `reference_loaded` and every `available()` check in `evidence.py`/
  `benchmark.py` read it instead of checking a raw file's existence.

## Known limits

- `db()` is one process-wide DuckDB database; each call returns a lightweight
  `cursor()` so Parquet metadata / zonemaps stay warm across requests instead
  of being rebuilt per connection.
- `/rates/providers ga_hospitals_only` filters the rows but not `summary`.
- See [../docs/known-gaps.md](../docs/known-gaps.md) for the sentinel ceiling,
  HCPCS drug-code outliers, and the `n_groups`-vs-`n_providers` distinction.
