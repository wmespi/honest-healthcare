# Serving layer — FastAPI + DuckDB

*Read this when adding or changing an API route. Tests: `test_api_contract.py`
(every route, hermetic — synthetic Parquet fixture in `conftest.py`, runs in CI);
`make test-api` also runs `test_coverage.py`'s coverage basket against the live
API with the full `data/`. Add a route → add a contract test.*

FastAPI on `localhost:8000`. Every route runs raw DuckDB SQL against
`read_parquet(...)` globs — no ORM, `?` placeholders.

## Layout

| File | Holds |
|---|---|
| `main.py` | app + CORS + `include_router` + `/` (health **and** trust-bar context: `priceable_npis`, `networks`, `n_codes`, `as_of` = newest prices Parquet mtime) |
| `data_sources.py` | glob paths, the `*_SRC` / `PRICE_GROUPS_SRC` / `VOL_CTE` SQL fragments, `db()` (the bounded DuckDB connection), `network_slug()`, `price_filters()`, `have_prices()` / `has_parquet()` |
| `labels.py` | consumer presentation — `pos_bucket()` + `POS_LABELS`, `MODIFIER_LABELS`, `nucc_bits()` / `provider_card()`, `plausibility()` |
| `evidence.py` | provider↔procedure evidence from CMS Medicare utilization (issue #14) — `did_bill()`, `billed_codes()`, `medicare_specialty()`, `typical_codes()` / `code_tiers()`. All no-op until `make cms-utilization` / `make specialty-profiles` run. |
| `routers/rates.py` | `/rates/distribution`, `/rates/by_network`, `/rates/providers`, `/rates/quote` |
| `routers/providers.py` | `/providers/{npi}/procedures`, `/providers/search` (name/NPI **or** `specialty=`), `/specialties`, `/providers/ga` |
| `routers/reference.py` | `/networks`, `/billing_codes`, `/procedure_categories`, `/plans` |

SQL still lives inline in the route handlers — moving it into a `queries/` module
is a later step ([issue #13](https://github.com/wmespi/honest-healthcare/issues/13)).

`PRICE_GROUPS_SRC` = `prices ⨝ group_sets` on `(file_id, group_set_id)` — the join
that expands a price row to its provider groups. Schema:
[../docs/schema.md](../docs/schema.md).

## The four consumer jobs

| Route | Job | Returns |
|---|---|---|
| `/rates/quote?billing_code&npi` | **1** — one procedure at one provider | headline rate + breakdown by component (global / `-26` professional / `-TC` technical) and place of service, + `plausibility`, `medicare_utilization`, `tier` |
| `/rates/by_network?billing_code` | **2** — same procedure across every network | one row per network, `median` + p10/p90 spread, sorted cheapest median first |
| `/rates/providers?billing_code` | **3** — compare across providers | one row per provider group, `component=global` by default; `ROLLUP_THRESHOLD` folds the fee-schedule majority. `specialty=` scopes to groups containing a provider of that NUCC specialty (distinct from `npi=`, which drills to one). |
| `/providers/{npi}/procedures` | **4** — the provider "menu" | procedures this NPI has a rate for, with the range; resolves NPI → group_sets first so it stays cheap. `tier=plausible` (default) shows only codes the NPI billed to Medicare or that are typical for their specialty + a `group_count`; `tier=all` shows every contracted code tagged `billed`/`typical`/`group` |

Supporting: `/rates/distribution` (histogram — **400s on npi-without-code**, which
would full-scan), `/networks`, `/providers/search` (+ `specialty=`), `/specialties` (the "by specialty" typeahead),
`/procedure_categories`, `/billing_codes`, `/providers/ga`, `/plans` (curated
friendly-name → network map, `serving/plan_networks.json`, GH #33).

## Key helpers

- `price_filters(...)` (`data_sources.py`) — the shared WHERE for `price_groups`.
  `npi=` → groups containing that NPI; `specialty=` → groups containing any
  provider of that NUCC specialty (a coarse scope, cheap once a `billing_code`
  prunes prices).
- `network_slug()` (`data_sources.py`) — must stay identical to
  `etl/extraction/partition.go:slugifyNetwork` (partition pruning depends on it).
- `pos_bucket(service_code)` → office / asc / er / inpatient / hosp_outpatient /
  any / unspecified / facility. `MODIFIER_LABELS` labels raw `modifier`.
- `nucc_bits()` / `provider_card(conn, npi)` — LEFT JOIN NUCC specialty + NPPES
  practice address onto an NPI. `nppes_cols(conn)` is module-cached from
  `DESCRIBE` so `address_line1/2` is referenced only when the file has it.
- `plausibility(...)` — a **coarse** specialty ↔ code check (fallback heuristic).
  Superseded by the `evidence.py` tiers when the CMS files are built; still used
  where they're not, and feeds `medicare_type` from CMS when the NUCC taxonomy is
  vague.
- `evidence.code_tiers(conn, npi, specialty, codes)` → `billed` / `typical` /
  `group` per code. `billed` = this NPI billed it to Medicare Part B; `typical` =
  ≥3% of the NPI's specialty bills it (from
  `data/reference/specialty_procedure_profiles.parquet`, `make specialty-profiles`);
  `group` = reaches the provider only via a shared billing group. See
  [../reference/cms-utilization.md](../reference/cms-utilization.md) and
  [GH #14](https://github.com/wmespi/honest-healthcare/issues/14).

## Known limits

- `db()` opens a fresh connection per request (no pooling) but bounds it —
  `memory_limit` (`DUCKDB_MEMORY_LIMIT`, default 4GB) and a spill dir
  (`DUCKDB_TMP`, default `/tmp/duckdb_spill`). Any ad-hoc query written outside
  `db()` must set `temp_directory` itself or it can spill into the repo.
- Browse-layer aggregates (`/networks`, `/billing_codes`, `/procedure_categories`)
  full-scan `prices ⨝ group_sets` (`VOL_CTE`). Fine at ~76k rows for the target
  plan; a precomputed summary table is the next step —
  [issue #10](https://github.com/wmespi/honest-healthcare/issues/10).
- Detail endpoints with a network filter partition-prune and stay fast regardless.
