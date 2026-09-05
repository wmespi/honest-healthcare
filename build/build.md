# build — raw + reference Parquet → the serving tables

*Read this when changing what the serving layer reads, or a product rule that
used to live in a serving-layer SQL string.* `make build` (→ `build/build.py`)
is the one cheap, re-runnable step between the dumb streaming ETL and the API.
Every product decision lives here now, not in `serving/`.

## What it writes — `data/serving/` (entity model: docs/architecture.md 2b)

| Table | Grain | Notes |
|---|---|---|
| `rates/net=<slug>/part.parquet` | one `prices ⨝ group_sets` row | `scope`, `is_sentinel`, `source_kind`, `medicare_allowed`, `vs_medicare` added; rule 5 applied. Hive-partitioned by `net` like `prices`. |
| `group_members.parquet` | `(file_id, provider_group_id, npi, tin_value)` | today's `providers`, deduped, scoped to files that survived to `rates`. |
| `provider_dim.parquet` | one row per GA NPPES NPI | ⨝ NUCC specialty ⨝ DAC (`org_name`, `org_pac_id`) ⨝ geocode (`lat`, `lon`) + `service_lines` (comma list from `serving/service_lines.py`). |
| `code_dim.parquet` | one row per code used in `rates` | RBCS `label`/`category`, MPFS `medicare_allowed` (global modifier), `shoppable` (CPT, 5-digit, not J/Q). |
| `evidence.parquet` | `(npi, billing_code, tier)` | `billed` (this NPI billed it to Medicare) or `typical` (≥3% of its NUCC classification do). Scoped to NPIs in `group_members`. `serving/evidence.py`'s tiers. |
| `cross_network_rollup.parquet` | `(code, net)` | `n_groups, p10, median, p90` off `rates` (outpatient-prof, non-sentinel, global modifier). Replaces `summary/` + `/rates/by_network`'s live scan. |

## The product rules, and where they came from

- **`scope`** — `outpatient_prof` mirrors `serving/data_sources.outpatient_scope`
  (professional · FFS · outpatient/both · fee-schedule/negotiated); everything
  else is `other`. Kept in sync by hand — no shared module across the stack.
- **`is_sentinel`** — `rate ≤ max($1, 5% of the code's scoped median)`, the
  `serving/routers/rates.py:_sentinel_ceiling` rule. Median is over price rows
  (pre-fan-out) so it matches the histogram the API computes it from. On a
  `NET=` subset the ceiling is network-local.
- **`medicare_allowed` / `vs_medicare`** — GA median MPFS allowed $ for the
  code + modifier, non-facility (`serving/benchmark.py`); `vs_medicare` =
  `rate / allowed`.
- **Rule 5** (AGENTS.md #5, `etl/mrf-model.md#conflict-resolution-strategy`) —
  build-time it collapses only rows that are **identical in every column except
  `file_id`** (the redundancy CMS's "publish every file a plan touches" mandate
  creates); of a collapsed set the `plan_specific` file's row survives, else the
  lowest `file_id`. Genuinely different rates for one billable line — reached
  through different rosters, or a real plan discount — are **all kept**, each
  tagged with `source_kind`. The "plan-specific beats shared / lower shared
  wins" *selection* then happens at read time ([#100](https://github.com/wmespi/honest-healthcare/issues/100))
  off `source_kind`; collapsing different-rate rows here would flatten the spread
  `/rates/quote` and `/rates/providers` aggregate over (MIN/MEDIAN/MAX). A
  single-file partition can't have a cross-file collision, so it skips the
  aggregate entirely. `source_kind` needs Step 1's `index_file_plans`; until
  `make discover` has re-run it is uniformly `shared`.

## Running it

```
make build                 # the targets.yaml networks, in the serving container
make build NET=ga-blue-value-hix-individual-network,<slug>   # a subset
make build ALL=1           # every partition (see the scale note below)
make build TEST=1          # data-test/
python -m build.build --data-dir <path> --serving-dir <path> [--networks <slugs>]
```

**Scope.** `make build` with no `NET=` builds only the partitions whose
`network_name` matches an `etl/targets.yaml` `network_patterns` glob — the same
signal the parse probe uses. That is deliberate: the current store's full
`prices ⨝ group_sets` fan-out is **~33 billion rows** (one off-exchange shard
alone is ~2 B), because a hospital/specialist roster can hold thousands of
groups. The epic's "~0.6 M rows per plan-network" holds for a compact dedicated
network like Blue Value (70 groups → 593 k rows), not for those. `ALL=1` forces
every partition and can exhaust memory on the largest multi-file ones —
[#94](https://github.com/wmespi/honest-healthcare/issues/94) removes them.

**Memory.** One partition at a time. A single-file partition (the target, most
others) has no cross-file conflict — the join streams straight to Parquet in
constant memory. A multi-file partition runs the rule-5 hash aggregate (spills
to `DUCKDB_TMP`). Reads the shared corpus read-only; writes only `SERVING_DIR`
(a worktree points it at `./data-local/serving`). Tunables:
`DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, `DUCKDB_TMP` (defaults under the output
dir, never the shared store).

## Not yet

`scripts/build_rate_summary.py` + `make build-summary` still build the old
`summary/` that `serving/` reads — deleted in #100 when the API repoints onto
`cross_network_rollup`. This step does not touch `serving/` or the parser.
