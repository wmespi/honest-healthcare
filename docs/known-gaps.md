# Known gaps

*The running list of things that are wrong, missing, or deferred. Parser-internal
issues that only bite at scale are in [../etl/parse.md](../etl/parse.md).*

## Attribution

- **Plan-name attribution — still partial.** `network_name` is captured from
  `provider_references` and is the reliable filter (e.g. `GA Blue Value HIX
  Individual Network`). The free-text plan *name* (`BLUE VALUE IND NETWORK HMO -
  INDIV - ANTHEM`) never enters the pipeline; `index_files.plan_names` /
  `idx_index_files_plan` are unused. Mapping a member's plan name →
  network_name(s) is the remaining piece — HIOS `plan_id` + the CMS registry, or
  the index's `reporting_plans`. The old `plan_name` column (source-file
  `market_types`, never a real plan name) is gone; `/plans` returns `[]`; the
  frontend filters by `network_name` and needs a Plan → Network dropdown.
- **`network_name` is NOT uniform across files.** `GA_JBNKMED0001` (id 21057, the
  target plan's only clean source) uses `"GA Blue Value HIX Individual Network"`;
  other `anthem/GA_*` files use config-style labels
  (`"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL"`). That's why the
  `-networks "GA *"` default is skipped for `anthem/GA_*` files (`isGAPlanSpecific`
  trusts the filename). Every other big `anthem/GA_*` file is a *different* GA
  individual plan, not Blue Value.

## Provider ↔ procedure

- **`plausibility()` is a heuristic; CMS utilization is the evidence layer.** A
  social worker in a rollup provider group "has" a $14k surgical rate because
  Anthem's `provider_references` are network-administration buckets, not
  practices. The serving layer flags the mismatch and reframes the number as the
  group's rate. `make cms-utilization` (→ `data/cms/ga_provider_service.parquet`,
  [reference/cms-utilization.md](../reference/cms-utilization.md)) adds real
  evidence: `did_bill(npi, code)` from CMS "Medicare Physician & Other
  Practitioners — by Provider and Service". When a provider demonstrably bills a
  code, the heuristic's "unlikely" is demoted, and CMS's own `provider_type`
  feeds the heuristic when a self-reported NUCC taxonomy is vague. The cost card
  shows "billed N times to Medicare in <year>" / "no Part B claims either", and
  the provider menu badges billed rows. Remaining limits: **Part B only** (no
  pediatric / pure-commercial / cash), rows with ≤10 beneficiaries are
  **excluded entirely** (so `billed: False` is weak), ~2-year lag, practitioner
  (type-1) signal, single year (2024) — "stopped doing it" looks like "never".
  [GH #14](https://github.com/wmespi/honest-healthcare/issues/14).

## Scale / performance

- **Browse-layer aggregates full-scan.** `/networks`, `/billing_codes`,
  `/procedure_categories` aggregate all of `prices ⨝ group_sets` (`VOL_CTE`). Fine
  at 76k price rows; a precomputed summary table is the next step —
  [GH #10](https://github.com/wmespi/honest-healthcare/issues/10).
- **Backend opens a fresh `duckdb.connect()` per request** — no reuse, no
  `memory_limit`, no spill dir. A heavy query can OOM-kill the process. Part of #10.
- **`coverage_log.n_ga_hospital_npis`** is never populated (the NPPES join happens
  at query time). Backfill with a post-batch `providers ⨝ ga_providers` query if
  the number is wanted in the log.

## Operational

- **`make nppes` write is not atomic** — `ga_providers.parquet` is briefly 0 bytes
  during a re-extract and serving-layer queries touching it 500. Run when the API is
  idle.
- **Monthly index churn.** `location` is a signed URL with a `YYYY-MM_` path prefix
  — not a cross-month key. Re-discover monthly and prune the prior month
  ([../etl/discover.md](../etl/discover.md)). A query-stripped `url_path`
  column would fix it.
- **Large GA files.** `GA_HXRCMED0001` (~2.1 GB), `GA_AHPPMEDGAHF*` (3–7 GB) are the
  richest Blue-Value-adjacent sources. The `prices` + `group_sets` split makes them
  tractable; still parse individually and watch `du -sh data`.

## Deferred by design

- **No `raw/` ↔ `serving/` Parquet boundary.** The parser owns serving decisions
  (Hive partitioning, the GA NPI filter). A thin transform pass between raw and
  serving Parquet would let partitioning / labeling / conflict-resolution evolve
  without re-parsing multi-GB files. dbt is overkill now —
  [issue #13 item 5](https://github.com/wmespi/honest-healthcare/issues/13),
  flagged for discussion.
- **Conflict resolution** (Critical Rule 5) is documented in
  [../etl/mrf-model.md](../etl/mrf-model.md), not implemented — needs
  `source_file_id` / `plan_count` on price rows.

## Legacy / stale

- **`db/SCHEMA.md`** narrates the Postgres tables; [schema.md](schema.md) is the
  authoritative on-disk layout (Parquet + Postgres). Keep them reconciled.
