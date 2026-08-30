# Known gaps

*The running list of things that are wrong, missing, or deferred. Parser-internal
issues that only bite at scale are in [../etl/parse.md](../etl/parse.md). Where
the product is headed — and which of these gaps that closes — is in
[direction.md](direction.md).*

## Attribution

- **Plan-name attribution — bridged, not derived.** `network_name` is captured
  from `provider_references` and is the reliable filter (e.g. `GA Blue Value HIX
  Individual Network`). The free-text plan *name* (`BLUE VALUE IND NETWORK HMO -
  INDIV - ANTHEM`) never enters the pipeline. **Interim (GH #33):** a
  hand-curated `serving/plan_networks.json` maps friendly plan names → network,
  served by `/plans` and shown as a "Your plan" section in the network picker.
  Today it holds one entry (Blue Value). The real fix — *deriving* the map from
  HIOS `plan_id` + a CMS public-use file, or the index's `reporting_plans` ↔
  `in_network_files` linkage — is still open; `index_files.plan_names` /
  `idx_index_files_plan` remain unused.
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
  practices. Two reference builds add real evidence:
  - `make cms-utilization` → `did_bill(npi, code)` (Tier 1) from CMS "by Provider
    and Service" — [reference/cms-utilization.md](../reference/cms-utilization.md).
  - `make specialty-profiles` → "typical for this specialty" (Tier 2) — codes
    billed by ≥3% of the provider's specialty —
    [reference/specialty-profiles.md](../reference/specialty-profiles.md).

  `/providers/{npi}/procedures` defaults to `tier=plausible` (billed + typical
  only, with a `group_count`); `/rates/quote` returns `tier` + `medicare_utilization`.
  Frontend: cost-card evidence line, menu badges + a "show all N group-contracted
  rates" expander. A strict Tier-1 filter keeps ~47% of priceable providers; Tier
  1+2 keeps ~94%.

  Remaining limits: **Part B only** (no pediatric / pure-commercial / cash), rows
  with ≤10 beneficiaries are **excluded entirely** (so `billed: False` is weak),
  ~2-year lag, practitioner (type-1) signal, **single year (2024)** — "stopped
  doing it" looks like "never". Georgia has no All-Payer Claims Database, so
  there's no public commercial-utilization source to widen this.
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
