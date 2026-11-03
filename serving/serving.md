# Serving layer — FastAPI + DuckDB

*Read this when adding or changing an API route.* The API reads **only**
`data/serving/` — the build step's output (`make build`, [build/build.md](../build/build.md)).
There is no fallback to raw Parquet: a missing build is a `503` from `GET /`,
not a slower degraded mode. Tests: `test_api_contract.py` (every route,
hermetic — the `api` fixture in `conftest.py` runs the real `build.build.build()`
on a synthetic raw set, then binds a `TestClient`); `make test-live` runs the golden
answers against the live API. Add a route → add a contract test.

Every route runs raw DuckDB SQL against `read_parquet(...)` on the serving
tables — no ORM, `?` placeholders, one process-wide connection (`db()`).

## Layout

| File | Holds |
|---|---|
| `main.py` | app + CORS + `include_router` + `GET /` (health **and** trust-bar context: `priceable_npis`, `networks`, `n_codes`, `as_of`, `built_at`/`partial_build` from the build's `manifest.json`, `reference_loaded` — which optional datasets were build inputs) |
| `data_sources.py` | the serving-table sources (`*_SRC`), `db()` (one process-wide DuckDB database, `cursor()` per call), `network_slug()`, `rate_filters()`, `have_build()`/`missing_build()`, `manifest()`/`built_with()` |
| `benchmark.py` | `opps_payment(conn, code, type)` — Medicare's national hospital-outpatient payment for a code, off `code_dim`. `None` until OPPS was a build input. |
| `ranking.py` | `haversine_sql()` — distance in SQL. |
| `service_lines.py` | Curated NUCC taxonomy-code allowlists per service line — `SERVICE_LINES = {"pcp": [...], "imaging": [...]}`, plus the imaging scan menu (`IMAGING_MENU`). Also `build/build.py`'s source for `provider_dim.service_lines`. |
| `routers/imaging.py` | `/imaging/providers?billing_code&network_name` — a labelled Medicare `facility_estimate` plus one row per billing practice for one scan on the curated `IMAGING_MENU`, priced global / technical (`TC`) / professional (`26`) with typical and min–max across the practice's contracts. A row says its `basis` (`global`, `split`, `professional_only`, `technical_only`) so a partial price is never shown as a whole one. A practice qualifies on CMS-billed evidence for that scan or an imaging-taxonomy billing ID, because Anthem fans a group's price out to every clinician on its roster. Only price lines that apply in an office (place of service `11`, or all places) count; facility fees are not in this file. **Needs `network_name`.** A row's name and `city` come from its billing ID's own Georgia NPPES record (the organization, or the doctor billing under their own NPI); a billing ID with no Georgia record has no row. `zip=` + `radius_mi=` add distance from the practice's own address, or its ZIP's center when the geocoder couldn't place it. |
| `routers/reference.py` | `/networks`, `/plans`, `/service_lines` (the curated allowlists), `/plans/cost_sharing?network_name` — the sourced plan versions for a network, read from `plan_cost_sharing.json` (deductible, out-of-pocket max, diagnostic and imaging cost share, each with its Anthem SBC link). Hand-copied from the SBCs because Georgia's individual market is not in the CMS plan-attributes file. **Needs `network_name`.** |

SQL lives inline in the route handlers
([#13](https://github.com/wmespi/honest-healthcare/issues/13) tracks a `queries/` module).
`main.py` is wiring only — don't add routes there; a genuinely new area gets
`routers/<x>.py` (`router = APIRouter()`, `@router.get`) plus `app.include_router`.

`RATE_GROUPS_SRC` = `rates ⨝ group_sets` on `(file_id, group_set_id)` — the join
that expands a price row to its provider groups. Schema:
[../docs/schema.md](../docs/schema.md).

## What a price is

`/imaging/providers` is scoped to **outpatient professional fee-for-service dollar
rates** — `rates.scope = 'outpatient_prof'`, baked in at build time
(`outpatient_scope()` in `data_sources.py` is the definition `build/build.py` pins
against; `test_build.py` guards the two staying equal). This drops facility/
institutional lines, inpatient-only rates, `bundle`/`capitation`, and
`percentage`/`per diem`/`derived` types. It also drops `is_sentinel` rows —
placeholder rates Anthem fills for not-separately-priced codes.

It **requires a `network_name`** (`400 {"detail": {"code": "network_required"}}`
otherwise) — a rate is only comparable within a plan, a product rule, not a scale
guard.

## Key helpers

- `rate_filters(...)` (`data_sources.py`) — the shared WHERE for `RATE_GROUPS_SRC`:
  `outpatient_prof`, one code, one network. `drop_sentinel=True` removes placeholders.
- `network_slug()` — must stay identical to `slugifyNetwork` in
  `etl/extraction/partition.go` (partition pruning depends on it).
- `manifest()` / `built_with(input_name)` (`data_sources.py`) — what the current
  build was made from (`data/serving/manifest.json`); `GET /`'s
  `reference_loaded` and `benchmark.py` read it instead of checking a raw file's
  existence.

## Query rules — get these wrong and it hangs

- **Always go through `db()`** (sets `memory_limit` and the spill dir); never a bare
  `duckdb.connect()`.
- **A `network_name` filter must partition-prune**: add `pg.net = ?` with
  `network_slug(network_name)`. A filter on the `network_name` column does not prune.
- **Expand to provider groups (`RATE_GROUPS_SRC`) only after a selective filter**
  (`billing_code`); over the whole store it is far too large. Browse aggregates read
  `rate_hist` instead.
- Name the columns in `GROUP BY`, never positions. When an alias shares a column's name,
  group by the full expression, because the column wins.
- Parameterise with `?`; never f-string user input into SQL (module-constant table and
  column names are fine).

## Finishing a route

Add the contract test (`test_api_contract.py`: 200 + shape, and the intended error
codes); update `frontend/src/api.js` and add a vitest case if the frontend calls it;
`docker compose restart serving`, `make test`, then hit the route against live data.

## Known limits

[../docs/known-gaps.md](../docs/known-gaps.md) holds the sentinel ceiling and how
practices are grouped.
