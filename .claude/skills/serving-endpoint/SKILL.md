---
name: serving-endpoint
description: >
  Add or modify a FastAPI route in the honest-healthcare serving layer. Use when the
  user wants a new API endpoint, a change to an existing one, or a new query over
  the rate/provider data. Covers the router layout, the shared query helpers,
  partition-pruning, and the query shapes that hang if you get them wrong.
---

# Adding / changing a serving endpoint

Full context: `serving/serving.md`. Schema: `docs/schema.md`.

## Where it goes

| Concern | File |
|---|---|
| `/rates/*` | `serving/routers/rates.py` |
| `/providers/*` | `serving/routers/providers.py` |
| `/networks`, `/billing_codes`, `/procedure_categories`, `/plans` | `serving/routers/reference.py` |
| a genuinely new area | new `serving/routers/<x>.py` + `app.include_router` in `main.py` |

Each router is `router = APIRouter()`; handlers are `@router.get(...)`.
`main.py` is wiring only — don't add routes there.

## Use the shared pieces (`serving/data_sources.py`)

- `db()` — the DuckDB connection. Always use it; it sets `memory_limit` and a
  spill dir. Never bare `duckdb.connect()`.
- `PRICES_SRC`, `GROUP_SETS_SRC`, `PROVIDERS_SRC`, `PRICE_GROUPS_SRC` (= prices ⨝
  group_sets), `VOL_CTE` — the parquet sources. Don't re-glob.
- `price_filters(billing_code, billing_code_type, network_name, setting, npi)` →
  `(where_sql, params)` for the `pg`-aliased price-groups source.
- `network_slug(name)` — turns a `network_name` into its Hive partition key.
  **Must stay identical to `etl-go/partition.go:slugifyNetwork`.**

Consumer labels live in `serving/labels.py` (`pos_bucket`, `MODIFIER_LABELS`,
`provider_card`, `nucc_bits`, `plausibility`).

## Query rules — get these wrong and it hangs

- **Never filter by `npi` without a `billing_code`.** Nothing prunes the code
  axis, so it full-scans ~12M price rows. `/rates/distribution` returns **400**
  for exactly this. The "everything at this provider" view is
  `/providers/{npi}/procedures`, which is affordable because it resolves the NPI
  to its `(file_id, group_set_id)` sets *first* (small), then joins `prices`.
- **A `network_name` filter should partition-prune.** Add `pg.net = ?` with
  `network_slug(network_name)` — DuckDB then reads one directory. A filter on
  `pg.network_name` (the column) does not prune.
- **Expand to provider groups only when the filter is selective.** `PRICE_GROUPS_SRC`
  (prices ⨝ group_sets) is fine after a `billing_code` or `npi` filter; over the
  whole dataset it's tens of millions of rows. Browse aggregates use `VOL_CTE`
  (roster sizes summed once) instead — see issue #10.
- Parameterise with `?`. Never f-string user input into SQL. Table/column
  *names* from the module constants are fine to interpolate.

## Finish

1. Add a contract test to `serving/tests/test_coverage.py` — 200 + expected
   shape. If it resolves an NPI, add it to the `npi_with_rates` fixture flow.
2. If the endpoint changes the rate-explorer state machine, add a
   `frontend/src/App.test.jsx` case (and update `api.js`).
3. `docker compose restart serving`, then `make test-api`. Hit the new route by
   hand against live data for the 200 and the intended error codes.
4. Update `serving/serving.md` if you added a route or a helper.
