# build: what ingestion writes → the serving tables

*Read this when changing what the serving layer reads, or a product rule.*
`make build` (→ `integration/build.py`) is the one cheap, re-runnable step between
the streaming ETL and the API, and the **only** thing the API reads — a missing
table is a `503` from `GET /api/health`, not a fallback to raw Parquet.

## The find-care tables

[`integration/tables/`](tables/__init__.py) writes the tables the find-care screen reads, the ones in the [#135](https://github.com/wmespi/honest-healthcare/issues/135) ERD and in [docs/architecture.md](../docs/architecture.md#serving-entity-model).
Each table has its own file, named after it, with a class that names its key and columns and selects its rows.
[`integration/inputs/`](inputs/__init__.py) holds what they read: each dataset ingestion writes, with the command that makes it and the columns and key the build checks before reading any of them ([docs/schema.md](../docs/schema.md)), and build-only tables such as each biller's rates.
[`integration/table.py`](table.py) checks every table's columns and key, plus any rule of the table's own, inputs included.
`make build` makes and checks them all before writing anything, so a failed check leaves the last build's files as they were.
Two builds from the same inputs write the same bytes, which `integration/tests/tables/` checks.
They cover the networks the payer rates in `data/ingestion/payer_rates/` price, which `make parse` publishes for the targets in [`ingestion/etl/targets.yaml`](../ingestion/etl/targets.yaml) ([ingestion/etl/parse.md](../ingestion/etl/parse.md#publishing-the-rates-by-network)); the build reads neither that file nor Anthem's own.
`manifest.json` beside them says when the build ran (`built_at`) and how many rows each table has (`rows`), and `GET /api/health` reads it.

| Table | Grain | Notes |
|---|---|---|
| `networks.parquet` | one row per network the payer rates price | `network_id`, such as `45334-GAN001`, and Georgia's exchange `name` for it. Each has to be one of Georgia's networks, or the build stops. |
| `plans.parquet` | one row per plan on those networks | `plan_id`, the HIOS plan ID such as `45334GA0020002`, with Georgia's `name` as published, `carrier`, `metal_level`, `needs_referral`, `network_id` and `deductible`, the standard version's in-network deductible for one person. |
| `premiums.parquet` | one row per plan, rating area and age | `premium` and `tobacco_premium`, the monthly premium for one person, from Georgia's premium file. Each plan has premiums in every rating area it's sold in, for every age from 0 to 64, where 64 means 64 and over, or the build stops. |
| `zips.parquet` | one row per ZIP the Census lists | `zip`, `lat` and `lon`, the point a ZIP search measures distance from. Every ZIP, not only Georgia's, because the Census file has no state and someone near the state line may search from across it. Each must be five digits with a point on the map, or the build stops. |
| `services.parquet` | one row per service in [`integration/services.yaml`](services.yaml) | `service_id`, our own slug such as `mri-lower-back`, its one `billing_code`, its `service_category`, the benefit in Georgia's cost-sharing file it falls under, and what the screen says about it: `name`, `keywords`, `about`, `could_change`, `asks` and an optional `preventive_note`. `medicare_rate` is Medicare's office rate for the whole service, the median of Georgia's two payment areas, and `medicare_facility_rate` is Medicare's national hospital outpatient rate, empty where Medicare doesn't pay that way, as for mammograms. A slug, a five-character code that no other service bills, and text in every field are required, or the build stops. |
| `cost_sharing.parquet` | one row per plan and service category the services use | `plan_id`, `service_category`, and what the plan's standard version charges the member in network, from Georgia's benefits and cost-sharing file: `copay` in dollars and `coinsurance` as a percent of the rate, each empty when the plan doesn't charge one, and `after_deductible`, whether the member pays the whole rate until the deductible is met. "No Charge" is a $0 copay, and with both a copay and a coinsurance the member pays the copay and then the coinsurance, as the plans' summaries of benefits word it. The build stops if a plan doesn't cover a category, words its share in a way the build doesn't read, or applies the deductible to only one of the two. |
| `places.parquet` | one row per Georgia hospital, plus one per practice with a price, that Census can put on the map | `place_id`, the CCN for a hospital and the practice's lowest billing NPI otherwise, `name`, `kind` (`hospital`, `imaging_center` or `practice`), `address`, `city`, `lat`, `lon`, and a hospital's `phone` and `stars`. The point is always Census's, because any other would be a guess. |
| `prices.parquet` | one row per network, service and place where the place's billers have a rate for it | `network_id`, `service_id`, `place_id`, and `parts`, one entry per bill a patient there would get, each with the highest rate on file (`max`). A hospital's row is its own `facility` fee, empty because Anthem's professional file doesn't price it, and the `reading` by the groups in its building, whose rates put the hospital on the list though nothing on file confirms the hospital itself is in network ([decided on #135](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6646717812)). A place that does the scan shows its `all_in` rate, or the `scan` and the `reading` when it lists no all-in rate, and a reading group elsewhere shows an empty `scan` and its `reading`. `several_rates` says a part shown has more than one rate on file, and `billers` lists the names of the billing groups behind the row, which at a hospital are the groups reading there. `providers` lists the clinicians Medicare shows billing the service for those billers, never the whole roster, so it's exact for the row's network and service, and empty when Medicare shows no one. Every network, service and place is in its table, or the build stops. |
| `providers.parquet` | one row per clinician a `prices` row lists | `npi`, and the `name` and `specialty` from the clinician's own NPPES record, the specialty as NUCC's name for its primary code, and `score`, Medicare's quality (MIPS) score, empty when there's none. Every clinician a price lists is here with a name and a specialty, or the build stops. |
| `unlocated_places.parquet` | one row per hospital or practice Census can't put on the map | `place_id`, `name`, `kind`, `address`, `city` and `zip`, saved to try with another geocoder. It's left off the screen, so nothing serves it, and a price there isn't shown. |

A file prices a network when the index links it to one of the network's plans and its rates carry one of the target's `network_patterns` labels.
The plans on a network come from Georgia's exchange plan file, and the link comes from `data/raw/anthem/index_file_plans.parquet`, which `make discover` writes from the index it last read whole, so the build reads no database.
Exactly one parsed file may price each network, or the build stops ([one plan, one contract](../ingestion/etl/mrf-model.md#one-plan-one-contract)).
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

## Running it

```
make build                 # data/ingestion/ -> data/serving/, in the integration container
make build TEST=1          # data-test/ingestion/ -> data-test/serving/
python -m integration.build --data-dir <path>   # <path>/ingestion/ -> <path>/serving/
```

**Memory.** The build holds its inputs and tables in one DuckDB connection, which spills
to disk past its memory limit. It reads only `data/ingestion/`, which its container mounts read-only, and writes only
`data/serving/` (a worktree's `HH_SERVING_DIR` mounts its own folder there). Tunables:
`DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, `DUCKDB_TMP`.
