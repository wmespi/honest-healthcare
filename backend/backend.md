# Serving layer — FastAPI + DuckDB

*Read this when adding or changing an API route. Tests: `make test-api`
(`backend/tests/`, contract + coverage, against the running API).*

FastAPI on `localhost:8000`. Every route runs raw DuckDB SQL against
`read_parquet(...)` globs — no ORM, `?` placeholders. Routes and queries currently
live inline in `main.py`; `models.py` / `database.py` are near-empty. Splitting
`main.py` into routers + a `queries/` module is [issue #13 item 2](../docs/known-gaps.md).

`PRICE_GROUPS_SRC` = `prices ⨝ group_sets` on `(file_id, group_set_id)` — the
join that expands a price row to its provider groups. Schema:
[../docs/schema.md](../docs/schema.md).

## The four consumer jobs

| Route | Job | Returns |
|---|---|---|
| `/rates/quote?billing_code&npi` | **1** — one procedure at one provider | headline rate + breakdown by component (global / `-26` professional / `-TC` technical) and place of service, + `plausibility` |
| `/rates/by_network?billing_code` | **2** — same procedure across every network | one row per network, `median` + p10/p90 spread, sorted cheapest median first |
| `/rates/providers?billing_code` | **3** — compare across providers | one row per provider group, `component=global` by default; `ROLLUP_THRESHOLD` folds the fee-schedule majority |
| `/providers/{npi}/procedures` | **4** — the provider "menu" | every procedure this NPI has a rate for, with the range; resolves NPI → group_sets first so it stays cheap |

Supporting: `/rates/distribution` (histogram — **400s on npi-without-code**, which
would full-scan), `/networks`, `/providers/search` (+ `specialty=`),
`/procedure_categories`, `/billing_codes`, `/providers/ga`.

## Helpers in `main.py`

- `network_slug()` — must stay identical to `etl-go/partition.go:slugifyNetwork`
  (partition pruning depends on it).
- `_pos_bucket(service_code)` → office / asc / er / inpatient / hosp_outpatient /
  any / unspecified / facility. `_MODIFIER_LABELS` labels raw `modifier`.
- `_nucc_bits()` / `_provider_card(conn, npi)` — LEFT JOIN NUCC specialty +
  NPPES practice address onto an NPI. `_nppes_cols(conn)` is module-cached from
  `DESCRIBE` so `address_line1/2` is referenced only when the file has it.
- `_plausibility(...)` — a **coarse** specialty ↔ code check. NOT proof a provider
  does or doesn't do a procedure; only flags that the code sits well outside the
  NUCC taxonomy, so the frontend presents the number as the *group's* rate, not the
  individual's. Real fix: [GH #14](https://github.com/wmespi/honest-healthcare/issues/14).

## Known limits

- A fresh `duckdb.connect()` per request — no reuse, no `memory_limit`, no spill
  dir. A heavy query can OOM-kill the process. Any ad-hoc query must
  `SET temp_directory='/tmp/dsp'`.
- Browse-layer aggregates (`/networks`, `/billing_codes`, `/procedure_categories`)
  full-scan `prices ⨝ group_sets` (`VOL_CTE`). Fine at 76k rows; a precomputed
  summary table is the next step — [issue #10](https://github.com/wmespi/honest-healthcare/issues/10).
- Detail endpoints with a network filter partition-prune and stay fast regardless.
