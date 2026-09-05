# build — raw + reference Parquet → the serving tables

*Read this when changing what the serving layer reads, or a product rule that
used to live in a serving-layer SQL string.* `make build` (→ `build/build.py`)
is the one cheap, re-runnable step between the dumb streaming ETL and the API.

## What it writes — `data/serving/` (entity model: docs/architecture.md 2b)

| Table | Grain | Notes |
|---|---|---|
| `rates/net=<slug>/part.parquet` | one `prices ⨝ group_sets` row | `scope`, `is_sentinel`, `source_kind`, `medicare_allowed`, `vs_medicare` added. Hive-partitioned by `net` like `prices`. Every expanded row is kept — `(file_id, provider_group_id, code, modifier, setting)` is **not** unique (POS variants, multi-roster rates). |
| `group_members.parquet` | `(file_id, provider_group_id, npi, tin_value)` | today's `providers`, deduped, scoped to files that reached `rates`. |
| `provider_dim.parquet` | one row per GA NPPES NPI | ⨝ NUCC specialty ⨝ CMS `provider_type` ⨝ DAC (`org_name`, `org_pac_id`) ⨝ geocode (`lat`, `lon`) + `service_lines` (comma list from `serving/service_lines.py`), address, `is_hospital`/`is_clinic`. The full NPI universe, not scoped to the built networks. |
| `code_dim.parquet` | one row per code used in `rates` | RBCS `label`/`category`/`rbcs_family`, MPFS `medicare_allowed` (global modifier), `shoppable` (a plain CPT procedure code). |
| `evidence.parquet` | `(npi, billing_code, tier)` | `billed` (this NPI billed it to Medicare) or `typical` (≥3% of its NUCC classification do). Scoped to NPIs in `group_members`. `serving/evidence.py`'s tiers. |
| `cross_network_rollup.parquet` | `(code, network)` | `n_groups, p10, median, p90` off `rates` (outpatient-prof, non-sentinel, global modifier). Replaces `/rates/by_network`'s live cross-network scan and `summary/rate_summary`. |

## The product rules, and where they came from

- **`scope`** — `outpatient_prof` mirrors `serving/data_sources.outpatient_scope`
  (professional · FFS · outpatient/both · fee-schedule/negotiated); everything
  else is `other`. `serving/tests/test_build.py` pins the two strings equal.
- **`is_sentinel`** — `rate ≤ max($1, 5% of the code's median rate)`, the
  `serving/routers/rates.py:_sentinel_ceiling` rule. Computed over **every**
  partition's price rows (pre-fan-out), not just the built ones, because the API
  computes it store-wide — a `NET=` build must not shift it. `approx_quantile`
  for the median vs. the API's $25-bucket CDF: within ±$13, and the same
  price-row population.
- **`medicare_allowed` / `vs_medicare`** — GA median MPFS allowed $ for the
  code + modifier, non-facility (`serving/benchmark.py`); `vs_medicare` =
  `rate / allowed`.
- **Rule 5** (AGENTS.md #5, `etl/mrf-model.md#conflict-resolution-strategy`) —
  the build **keeps every expanded row** and tags each with `source_kind`
  (`plan_specific` when the file serves one GA-individual plan, else `shared`,
  from Step 1's `index_file_plans`). It does **not** collapse across files:
  `provider_group_id` is file-local (`docs/schema.md`), so "the same provider
  group in a shared and a plan-specific file" is not an id join. MRF redundancy
  is resolved where the serving layer already resolves it on `prices` — at read
  time, with `DISTINCT` / `MIN` over the query-narrowed rows, now also
  preferring `source_kind='plan_specific'` and the lower shared rate
  ([#100](https://github.com/wmespi/honest-healthcare/issues/100)). Until
  `make discover` re-runs post-#108, `source_kind` is uniformly `shared`.

## Running it

```
make build                 # the targets.yaml networks, in the serving container
make build NET=ga-blue-value-hix-individual-network,<slug>   # a subset
make build ALL=1           # every partition (see the scale note)
make build TEST=1          # data-test/
python -m build.build --data-dir <path> --serving-dir <path> [--networks <slugs>]
```

**Scope.** With no `NET=`, `make build` builds only the partitions whose
`network_name` matches an `etl/targets.yaml` `network_patterns` glob — the parse
probe's signal. Deliberate: the current store's full `prices ⨝ group_sets`
fan-out is **~33 billion rows** (one off-exchange shard alone is ~2 B) because a
hospital/specialist roster can hold thousands of groups. The epic's "~0.6 M rows
per plan-network" holds for a compact dedicated network like Blue Value (70
groups → 593 k rows), not for those. If a target has no `network_patterns` the
build refuses to guess and asks for `--networks` / `--all-networks`. `ALL=1`
forces every partition ([#94](https://github.com/wmespi/honest-healthcare/issues/94)
removes the off-target ones).

**Memory.** One partition at a time; the join pipelines straight into the COPY
(no aggregate, no full materialisation), so memory is bounded by the join build
sides — this file's `group_sets` plus the small `code_ceiling` / `mpfs` /
`file_plan_count` tables — not the fan-out. Reads the shared corpus read-only;
writes only `SERVING_DIR` (a worktree points it at `./data-local/serving`).
Tunables: `DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, `DUCKDB_TMP` (a spill dir the
build owns is cleaned on entry and exit; a caller-set one is left alone).

## Not yet

`scripts/build_rate_summary.py` + `make build-summary` still build `summary/`,
which the serving layer reads. `cross_network_rollup` covers `rate_summary` and
`/rates/by_network`; `summary/rate_hist` (the sentinel ceiling + the
`/rates/distribution` histogram) and `code_rollup`'s group-volume hint have **no
equivalent here yet** — #100 decides whether they move too, and deletes
`build_rate_summary.py` then. This step does not touch `serving/` or the parser.
