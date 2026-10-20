# build — raw + reference Parquet → the serving tables

*Read this when changing what the serving layer reads, or a product rule.*
`make build` (→ `build/build.py`) is the one cheap, re-runnable step between
the streaming ETL and the API, and the **only** thing the API reads — a missing
table is a `503` from `GET /`, not a fallback to raw Parquet.

## What it writes — `data/serving/` (entity model: docs/architecture.md 2b)

| Table | Grain | Notes |
|---|---|---|
| `rates/net=<slug>/part.parquet` | one row per price (the parser's grain) | `scope`, `is_sentinel`, `source_kind`, `medicare_allowed`, `vs_medicare` added. Hive-partitioned by `net`. No group fan-out — expansion to provider groups happens at query time after pruning on `net` + `billing_code`. Holds only the built networks (below). |
| `group_sets.parquet` | `(file_id, group_set_id, provider_group_id)` | a price's roster, from `anthem/group_sets`, kept only for the sets a price in `rates` points at. |
| `group_members.parquet` | `(file_id, provider_group_id, npi, tin_value)` | deduped from `anthem/providers`. |
| `group_networks.parquet` | `(file_id, provider_group_id, net, network_name)` | which networks a file-local group is attributed to — `group_members ⨝ group_networks` answers "does this NPI have a rate in network X" without touching `rates`. |
| `provider_dim.parquet` | one row per GA NPPES NPI | ⨝ NUCC specialty ⨝ CMS `provider_type` ⨝ DAC (`group_name`, `org_pac_id`, `grad_year`) ⨝ geocode (`lat`,`lon`) ⨝ MIPS (`mips_score`, `mips_source`, `mips_year`) + `service_lines`. **`org_name`** (raw NPPES entity name) and **`group_name`** (DAC's billing-group identity) are deliberately separate columns — a shared org affiliation must never overwrite an individual's own name. |
| `provider_affiliations.parquet` | `(npi, ccn, facility_name)` | the DAC hospital CCN↔NPI bridge, verbatim. |
| `code_dim.parquet` | one row per code used in `rates` | RBCS `label`/`category`/`rbcs_family`, MPFS `medicare_allowed` (global modifier), `shoppable`. |
| `evidence.parquet` | `(npi, billing_code, tier, ...)` | `billed` rows carry the Medicare utilization detail (`year`, `tot_srvcs`, `tot_benes`, `tot_bene_days`, `avg_mdcr_allowed`, `is_drug`); `typical` rows carry `prevalence` so the read layer can raise (never lower) the threshold. Scoped to NPIs in `group_members`. |
| `rate_hist.parquet` | `(net, code, setting, scope, modifier, is_sentinel, bucket)` | a $25 roster-weighted histogram — `n` sums each price's `group_set` size, `n_rates` is the raw price-row count. `is_sentinel` is a dimension (not a filter) so the histogram can still show the placeholder rows; the rollup below excludes them. |
| `cross_network_rollup.parquet` | `(code, network)` | `n_groups`, `min`/`p10`/`median`/`p90`/`max` off the `rate_hist` CDF (global modifier, outpatient-prof, non-sentinel). Read straight by `/rates/by_network`. |
| `zip_centroids.parquet` | one row per ZIP | A copy of `reference/zip_centroids.parquet` so the API reads only `serving/`. |
| `manifest.json` | one object | `built_at`, `networks` built, `scope` (`targets` | `all` | `custom`), `partial` (`scope != all`), `target_patterns` (the `etl/targets.yaml` patterns a `targets` build used), `inputs` (which optional datasets — nppes/nucc/cms_utilization/mpfs/dac/geocode/mips/zip_centroids/plan_link — were present), `rows` (per-table counts). `GET /`'s `reference_loaded` and every `evidence.py`/`benchmark.py` `available()` check read this instead of a raw file's existence. |

## The product rules

- **`scope`** — `outpatient_prof` mirrors `serving/data_sources.outpatient_scope`
  (professional · FFS · outpatient/both · fee-schedule/negotiated); everything
  else is `other`. `serving/tests/test_build.py` pins the two strings equal.
- **`is_sentinel`** — `rate ≤ max($1, 5% of the code's median rate)`, computed
  over the networks this build holds (pre-fan-out) with `approx_quantile`, so the
  same code can flag differently in a `targets` build and an `all` build.
- **`medicare_allowed` / `vs_medicare`** — GA median MPFS allowed $ for the
  code + modifier, non-facility; `vs_medicare` = `rate / allowed`.
- **Rule 5** (AGENTS.md #5, `etl/mrf-model.md#conflict-resolution-strategy`) —
  the build **keeps every row** and tags each `source_kind` (`plan_specific`
  when the file serves one GA-individual plan, else `shared`, from
  `index_file_plans`). It does **not** collapse across files —
  `provider_group_id` is file-local. The read layer resolves MRF redundancy at
  read time, per practice: a `plan_specific` row wins over `shared` for the
  same code. `source_kind` is `shared` for every row until `make discover` has
  populated `index_file_plans`.

## Scope

By default the build reads only the `net=` partitions whose network name matches a
`network_patterns` entry in [`etl/targets.yaml`](../etl/targets.yaml) — the same
patterns the parse probe uses — and removes any other `rates/net=*` directory from
the serving dir, so the store holds exactly the networks in the manifest. Raw
partitions for other networks stay on disk and a `NET=all` build serves them. A
build that matches nothing exits non-zero rather than writing an empty store.

## Running it

```
make build                 # the networks in etl/targets.yaml, in the serving container
make build NET=all         # every network partition
make build NET=<slug,slug> # a subset of network partitions
make build TEST=1          # data-test/
python -m build.build --data-dir <path> --serving-dir <path> [--networks <slugs>]
```

**Memory.** One partition at a time; the join pipelines straight into the COPY
(no aggregate, no full materialisation), so memory is bounded by the join
build sides (`group_sets`, `code_ceiling`, `mpfs`, `file_plan_count`), not the
fan-out. Reads the shared corpus read-only; writes only `SERVING_DIR` (a
worktree points it at `./data-local/serving`). Tunables:
`DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, `DUCKDB_TMP`.
