# build — raw + reference Parquet → the serving tables

*Read this when changing what the serving layer reads, or a product rule.*
`make build` (→ `build/build.py`) is the one cheap, re-runnable step between
the streaming ETL and the API, and the **only** thing the API reads — a missing
table is a `503` from `GET /`, not a fallback to raw Parquet.

## What it writes — `data/serving/` (entity model: docs/architecture.md 2b)

| Table | Grain | Notes |
|---|---|---|
| `rates/net=<slug>/part.parquet` | one row per price (the parser's grain) | `scope`, `is_sentinel`, `medicare_allowed`, `vs_medicare` added. Hive-partitioned by `net`. No group fan-out — expansion to provider groups happens at query time after pruning on `net` + `billing_code`. Holds only the built networks (below). |
| `group_sets.parquet` | `(file_id, group_set_id, provider_group_id)` | a price's roster, from `anthem/group_sets`, kept only for the sets a price in `rates` points at. |
| `group_members.parquet` | `(file_id, provider_group_id, npi, tin_value)` | deduped from `anthem/providers`. |
| `group_networks.parquet` | `(file_id, provider_group_id, net, network_name)` | which networks a file-local group is attributed to — `group_members ⨝ group_networks` answers "does this NPI have a rate in network X" without touching `rates`. |
| `provider_dim.parquet` | one row per GA NPPES NPI | ⨝ NUCC specialty ⨝ CMS `provider_type` ⨝ DAC (`group_name`, `org_pac_id`, `grad_year`) ⨝ geocode (`lat`,`lon`) ⨝ MIPS (`mips_score`, `mips_source`, `mips_year`) + `service_lines`. **`org_name`** (raw NPPES entity name) and **`group_name`** (DAC's billing-group identity) are deliberately separate columns — a shared org affiliation must never overwrite an individual's own name. |
| `provider_affiliations.parquet` | `(npi, ccn, facility_name)` | the DAC hospital CCN↔NPI bridge, verbatim. |
| `code_dim.parquet` | one row per code used in `rates` | RBCS `label`/`category`/`rbcs_family`, MPFS `medicare_allowed` (global modifier), OPPS `opps_payment`, `shoppable`. |
| `evidence.parquet` | `(npi, billing_code, tier, ...)` | `billed` rows carry the Medicare utilization detail (`year`, `tot_srvcs`, `tot_benes`, `tot_bene_days`, `avg_mdcr_allowed`, `is_drug`); `typical` rows carry `prevalence` so the read layer can raise (never lower) the threshold. Scoped to NPIs in `group_members`. |
| `rate_hist.parquet` | `(net, code, setting, scope, modifier, is_sentinel, bucket)` | a $25 roster-weighted histogram — `n` sums each price's `group_set` size, `n_rates` is the raw price-row count. `is_sentinel` is a dimension (not a filter) so the histogram can still show the placeholder rows; the rollup below excludes them. |
| `cross_network_rollup.parquet` | `(code, network)` | `n_groups`, `min`/`p10`/`median`/`p90`/`max` off the `rate_hist` CDF (global modifier, outpatient-prof, non-sentinel). No route reads it today. |
| `zip_centroids.parquet` | one row per ZIP | A copy of `reference/zip_centroids.parquet` so the API reads only `serving/`. |
| `manifest.json` | one object | `built_at`, `networks` built, `scope` (`targets` | `all` | `custom`), `partial` (`scope != all`), `target_patterns` (the `etl/targets.yaml` patterns a `targets` build used), `codes` (the billing codes priced; `"all"` for every code), `inputs` (which optional datasets — nppes/nucc/cms_utilization/mpfs/opps/dac/geocode/mips/zip_centroids — were present), `rows` (per-table counts, including the find-care tables). `GET /`'s `built_at`, `partial_build` and `reference_loaded`, and `benchmark.py`'s OPPS check, read this instead of a raw file's existence. |

## The find-care tables

[`build/tables/`](tables/__init__.py) writes the tables the find-care screen will read, the ones in the [#135](https://github.com/wmespi/honest-healthcare/issues/135) ERD, beside the ones above until those are removed.
Each table has its own file, named after it, with a class that names its key and columns and selects its rows.
[`build/inputs/`](inputs/__init__.py) holds what they read: each dataset with the command that makes it, and build-only tables such as which parsed file prices each network.
[`build/table.py`](table.py) checks every table's columns and key, plus any rule of the table's own, inputs included.
`make build` makes and checks them before writing anything, old tables included, so a failed check leaves the last build's files as they were.
Two builds from the same inputs write the same bytes, which `serving/tests/tables/` checks.
They always cover the targets in [`etl/targets.yaml`](../etl/targets.yaml), whatever `NET` says, and their row counts join the manifest's `rows`.

| Table | Grain | Notes |
|---|---|---|
| `networks.parquet` | one row per network a target names | `network_id`, such as `45334-GAN001`, and Georgia's exchange `name` for it. |
| `plans.parquet` | one row per plan on those networks | `plan_id`, the HIOS plan ID such as `45334GA0020002`, with Georgia's `name` as published, `carrier`, `metal_level`, `needs_referral`, `network_id` and `deductible`, the standard version's in-network deductible for one person. The index must link each plan to its network's file, or the build stops. |
| `premiums.parquet` | one row per plan, rating area and age | `premium` and `tobacco_premium`, the monthly premium for one person, from Georgia's premium file. Each plan has premiums in every rating area it's sold in, for every age from 0 to 64, where 64 means 64 and over, or the build stops. |
| `zips.parquet` | one row per ZIP the Census lists | `zip`, `lat` and `lon`, the point a ZIP search measures distance from. Every ZIP, not only Georgia's, because the Census file has no state and someone near the state line may search from across it. Each must be five digits with a point on the map, or the build stops. |
| `services.parquet` | one row per service in [`build/services.yaml`](services.yaml) | `service_id`, our own slug such as `mri-lower-back`, its one `billing_code`, its `service_category`, the benefit in Georgia's cost-sharing file it falls under, and what the screen says about it: `name`, `keywords`, `about`, `could_change`, `asks` and an optional `preventive_note`. `medicare_rate` is Medicare's office rate for the whole service, the median of Georgia's two payment areas, and `medicare_facility_rate` is Medicare's national hospital outpatient rate, empty where Medicare doesn't pay that way, as for mammograms. A slug, a five-character code that no other service bills, and text in every field are required, or the build stops. |
| `cost_sharing.parquet` | one row per plan and service category the services use | `plan_id`, `service_category`, and what the plan's standard version charges the member in network, from Georgia's benefits and cost-sharing file: `copay` in dollars and `coinsurance` as a percent of the rate, each empty when the plan doesn't charge one, and `after_deductible`, whether the member pays the whole rate until the deductible is met. "No Charge" is a $0 copay, and with both a copay and a coinsurance the member pays the copay and then the coinsurance, as the plans' summaries of benefits word it. The build stops if a plan doesn't cover a category, words its share in a way the build doesn't read, or applies the deductible to only one of the two. |
| `places.parquet` | one row per Georgia hospital, plus one per practice with a price, that Census can put on the map | `place_id`, the CCN for a hospital and the practice's lowest billing NPI otherwise, `name`, `kind` (`hospital`, `imaging_center` or `practice`), `address`, `city`, `lat`, `lon`, and a hospital's `phone` and `stars`. The point is always Census's, because any other would be a guess. |
| `prices.parquet` | one row per network, service and place where the place's billers have a rate for it | `network_id`, `service_id`, `place_id`, and `parts`, one entry per bill a patient there would get, each with the highest rate on file (`max`). A hospital's row is its own `facility` fee, empty because Anthem's professional file doesn't price it, and the `reading` by the groups in its building, whose rates put the hospital on the list though nothing on file confirms the hospital itself is in network ([decided on #135](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6646717812)). A place that does the scan shows its `all_in` rate, or the `scan` and the `reading` when it lists no all-in rate, and a reading group elsewhere shows an empty `scan` and its `reading`. `several_rates` says a part shown has more than one rate on file, and `billers` lists the billing NPIs behind the row. `providers` lists the clinicians Medicare shows billing the service for those billers, never the whole roster, so it's exact for the row's network and service, and empty when Medicare shows no one. Every network, service and place is in its table, or the build stops. |
| `providers.parquet` | one row per clinician a `prices` row lists | `npi`, and the `name` and `specialty` from the clinician's own NPPES record, the specialty as NUCC's name for its primary code, and `score`, Medicare's quality (MIPS) score, empty when there's none. Every clinician a price lists is here with a name and a specialty, or the build stops. |
| `unlocated_places.parquet` | one row per hospital or practice Census can't put on the map | `place_id`, `name`, `kind`, `address`, `city` and `zip`, saved to try with another geocoder. It's left off the screen, so nothing serves it, and a price there isn't shown. |

A file prices a network when the index links it to one of the network's plans and its rates carry one of the target's `network_patterns` labels.
The plans on a network come from Georgia's exchange plan file, and the link comes from `data/anthem/index_file_plans.parquet`, which `make discover` writes from the index it last read whole, so the build reads no database.
Exactly one parsed file may price each network, or the build stops ([one plan, one contract](../etl/mrf-model.md#one-plan-one-contract)).
A parsed file the index doesn't link to the network's plans is left out, such as `GA_JBKEMED0001`, which carries Blue Value's label but prices a plan no longer sold.
A target network with no file that prices it, or one that Georgia's exchange files don't list or list no plans on, also stops the build.

Who prices a service where follows the rules on #135 ([reading groups versus places](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6639007442)):

- [`biller_doctors`](inputs/biller_doctors.py) keeps, for each billing NPI with a rate for a service, the doctors Medicare shows billing that service: those on the rosters the rate covers, and the biller itself when it bills under a doctor's own NPI.
  `biller_rates` takes them as its evidence, and `prices` as the clinicians on each row.
- [`biller_rates`](inputs/biller_rates.py) keeps a network's outpatient professional rates for each service, by the billing NPI each covers.
  A biller appears if it has a doctor in `biller_doctors` for that service, or if its NPPES primary specialty code registers it as an imaging center for that kind of scan ([imaging_centers](inputs/imaging_centers.py)).
  It does the scan if it's such a center or one of them billed the service in an office, and otherwise it only reads it.
- [`biller_places`](inputs/biller_places.py) puts each biller at its own NPPES address: the hospital in that building, or the practice there under its name.
  Two addresses are one building when Census standardized them alike, or when their street and ZIP match in USPS's abbreviations.
  Billers in one building whose names match without punctuation or endings like LLC are one practice, keyed on the lowest NPI, and different practices in one building stay apart ([what one place is](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6643172584)).
  A doctor's own NPI takes its CMS group's name, and away from a hospital it has no place without one. A biller with no Georgia record has none either.
  Every billing ID in Anthem's files is an NPI, and the build stops if a file lists a tax ID instead, since NPPES can't place it.
  The build lists every billing ID with a price but no place, so an NPI missing from the monthly NPPES snapshot doesn't drop out silently.

## The product rules

- **`scope`** — `outpatient_prof` mirrors `serving/data_sources.outpatient_scope`
  (professional · FFS · outpatient/both · fee-schedule/negotiated); everything
  else is `other`. `serving/tests/test_build.py` pins the two strings equal. The
  column also drops a line whose place-of-service list leaves out office (`11`)
  when the same code has an office-applicable line; a code priced only for
  facility sites keeps what it has
  ([etl/mrf-model.md](../etl/mrf-model.md#several-price-lines-for-one-provider-and-code)).
- **`is_sentinel`** — `rate ≤ max($1, 5% of the code's median rate)`, computed
  over the networks this build holds (pre-fan-out) with `approx_quantile`, so the
  same code can flag differently in a `targets` build and an `all` build.
- **`medicare_allowed` / `vs_medicare`** — GA median MPFS allowed $ for the
  code + modifier, non-facility; `vs_medicare` = `rate / allowed`.
- **One plan, one contract** ([rule 5](../AGENTS.md)) — the build keeps every row and
  doesn't combine or rank prices across files. `provider_group_id` is file-local.

## Scope

By default the build reads only the `net=` partitions whose network name matches a
`network_patterns` entry in [`etl/targets.yaml`](../etl/targets.yaml) — the same
patterns the parse probe uses — and removes any other `rates/net=*` directory from
the serving dir, so the store holds exactly the networks in the manifest. Raw
partitions for other networks stay on disk and a `NET=all` build serves them. A
build that matches nothing exits non-zero rather than writing an empty store.

Rates, rosters and evidence cover only the billing codes of the service lines in
`serving/service_lines.py` (`PRICED_SERVICE_LINES`) unless `ALL_CODES=1`; the
manifest's `codes` says which. A code outside them has no price in the store.

## Running it

```
make build                 # the networks in etl/targets.yaml, in the serving container
make build NET=all         # every network partition
make build NET=<slug,slug> # a subset of network partitions
make build ALL_CODES=1      # every billing code, not just the priced service lines
make build TEST=1          # data-test/
python -m build.build --data-dir <path> --serving-dir <path> [--networks <slugs>]
```

**Memory.** One partition at a time; the join pipelines straight into the COPY
(no aggregate, no full materialisation), so memory is bounded by the join
build sides (`group_sets`, `code_ceiling`, `mpfs`), not the
fan-out. Reads the shared corpus read-only; writes only `SERVING_DIR` (a
worktree points it at `./data-local/serving`). Tunables:
`DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, `DUCKDB_TMP`.
