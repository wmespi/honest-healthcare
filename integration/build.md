# build: what ingestion writes → the serving tables

*Read this when changing what the serving layer reads, or a product rule.*
`make build` (→ `integration/build.py`) is the one cheap, re-runnable step between
the streaming ETL and the API, and the **only** thing the API reads - a missing
table is a `503` from `GET /api/health`, not a fallback to raw Parquet.

## The find-care tables

[`integration/tables/`](tables/__init__.py) writes the tables the find-care screen reads, the ones in the [#135](https://github.com/wmespi/honest-healthcare/issues/135) ERD and in [docs/architecture.md](../docs/architecture.md#serving-entity-model).
Each table has its own file, named after it, with a class that names its key and columns and selects its rows.
[`integration/inputs/`](inputs/__init__.py) holds what they read: each dataset ingestion writes, with the command that makes it and the columns and key the build checks before reading any of them ([docs/schema.md](../docs/schema.md)), and build-only tables such as each biller's rates.
[`integration/table.py`](table.py) checks every table's columns and key, plus any rule of the table's own, inputs included.
`make build` makes and checks them all before writing anything, and writes them all before moving any into place, so a failed check or a failed write leaves the last build's files as they were, and the API sees two builds' tables side by side only in the moment the renames take.
Two builds from the same inputs write the same bytes, which `integration/tests/tables/` checks.
They cover the networks the payer rates in `data/ingestion/payer_rates/` list, which `make parse` publishes for the targets in [`ingestion/etl/targets.yaml`](../ingestion/etl/targets.yaml) ([ingestion/etl/parse.md](../ingestion/etl/parse.md#publishing-the-rates-by-network)); the build reads neither that file nor Anthem's own.
A network whose file failed keeps its earlier prices, marked `stale`, or has none, marked `missing`, and every other network builds as usual.
`manifest.json` beside them says when the build ran (`built_at`) and how many rows each table has (`rows`), and `GET /api/health` reads it.

| Table | Grain | Notes |
|---|---|---|
| `networks.parquet` | one row per network the payer rates list, with or without prices | `network_id`, such as `45334-GAN001`, Georgia's exchange `name` for it, and how fresh its prices are: `prices_status` is `current` when they're from the payer's latest copy of its file, `stale` when they're from an earlier one, and `missing` when it has none; `prices_as_of` is the day (UTC) the payer updated the copy they're from, and `prices_reason` says why they aren't current. Each has to be one of Georgia's networks, `missing` exactly when the payer rates have none for it, or the build stops. |
| `plans.parquet` | one row per plan on a network with prices | `plan_id`, the HIOS plan ID such as `45334GA0020002`, with Georgia's `name` as published, `carrier`, `metal_level`, `needs_referral`, `network_id` and `deductible`, the standard version's in-network deductible for one person. |
| `premiums.parquet` | one row per plan, rating area and age | `premium` and `tobacco_premium`, the monthly premium for one person, from Georgia's premium file. Each plan has premiums in every rating area it's sold in, for every age from 0 to 64, where 64 means 64 and over, or the build stops. |
| `zips.parquet` | one row per ZIP the Census lists | `zip`, `lat` and `lon`, the point a ZIP search measures distance from. Every ZIP, not only Georgia's, because the Census file has no state and someone near the state line may search from across it. Each must be five digits with a point on the map, or the build stops. |
| `services.parquet` | one row per service in [`integration/services.yaml`](services.yaml) | `service_id`, our own slug such as `mri-lower-back`, its one `billing_code`, its `service_category`, the benefit in Georgia's cost-sharing file it falls under, and what the screen says about it: `name`, `keywords`, `about`, `could_change`, `asks` and an optional `preventive_note`. `medicare_rate` is Medicare's office rate for the whole service, the median of Georgia's two payment areas, and `medicare_facility_rate` is Medicare's national hospital outpatient rate, empty where Medicare doesn't pay that way, as for mammograms. A slug, a five-character code that no other service bills, and text in every field are required, or the build stops. |
| `cost_sharing.parquet` | one row per plan and service category the services use | `plan_id`, `service_category`, and what the plan's standard version charges the member in network, from Georgia's benefits and cost-sharing file: `copay` in dollars and `coinsurance` as a percent of the rate, each empty when the plan doesn't charge one, and `after_deductible`, whether the member pays the whole rate until the deductible is met. "No Charge" is a $0 copay, and with both a copay and a coinsurance the member pays the copay and then the coinsurance, as the plans' summaries of benefits word it. The build stops if a plan doesn't cover a category, words its share in a way the build doesn't read, or applies the deductible to only one of the two. |
| `places.parquet` | one row per Georgia hospital, plus one per practice with a price, that a geocoder can put on the map | `place_id`, `name`, `kind` (`hospital`, `imaging_center` or `practice`), `address`, `city`, `lat`, `lon`, `phone`, and a hospital's `stars`. A hospital's `place_id` is its CCN. A practice's is the lowest billing NPI among those registered in its building, and where none is, as at a group's second site, the lowest NPI there with a short hash of the building. The point is the building's ([addresses](inputs/addresses.py)), and a building no geocoder located has none, because any other would be a guess. |
| `prices.parquet` | one row per network, service and place where the place's billers have a rate for it, plus one per hospital with its own fee for it | `network_id`, `service_id`, `place_id`, and `parts`, one entry per bill a patient there would get, each with the rate that has the strongest evidence behind it, the highest of those when several tie (`max`), and where the rate is from (`source`: `plan` for the plan's own file, `hospital` for a hospital's own, empty with no rate). A hospital's row is its own `facility` fee ([a hospital's own fee](#a-hospitals-own-fee)) and the `reading` by the groups in its building. Either puts the hospital on the list: the groups' rates ([decided on #135](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6646717812)), or its fee, which is evidence the hospital itself is in network. Only where the fee is empty does nothing on file confirm that. An empty reading at a hospital means another group reads the scan and bills it separately, and the plan's file doesn't place one there, so `billers` and `providers` are empty too unless a group there has a rate for another part. Its `cash_price` is what the hospital's own file says it charges when no plan pays, and it's empty at every other place. A place that does the scan shows its `all_in` rate, or the `scan` and the `reading` when it lists no all-in rate, and a reading group elsewhere shows an empty `scan` and its `reading`. `several_rates` says a part shown has more than one rate with that evidence, or a hospital's fee more than one at its source, and `billers` lists the names of the billing groups behind the row, which at a hospital are the groups reading there. `providers` lists the clinicians Medicare shows billing the service for those billers, never the whole roster, so it's exact for the row's network and service, and empty when Medicare shows no one. Every network, service and place is in its table, or the build stops. |
| `providers.parquet` | one row per clinician a `prices` row lists | `npi`, and the `name` and `specialty` from the clinician's own NPPES record, the specialty as NUCC's name for its primary code, and `score`, Medicare's quality (MIPS) score, empty when there's none. Every clinician a price lists is here with a name and a specialty, or the build stops. |
| `unlocated_places.parquet` | one row per hospital or practice no geocoder can put on the map | `place_id`, `name`, `kind`, `address`, `city` and `zip`. It's left off the screen, so nothing serves it, and a price there isn't shown. |

Who prices a service where follows the rules on #135 ([reading groups versus places](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6639007442)):

- [`biller_doctors`](inputs/biller_doctors.py) keeps, for each billing NPI with a rate for a service, the doctors Medicare shows billing that service: those on the rosters the rate covers, and the biller itself when it bills under a doctor's own NPI.
  `biller_rates` takes them as its evidence, and `prices` as the clinicians on each row.
- [`biller_rates`](inputs/biller_rates.py) keeps a network's outpatient professional rates for each service, by the billing NPI each covers.
  A biller appears if it has a doctor in `biller_doctors` for that service, or if its NPPES primary specialty code registers it as an imaging center for that kind of scan ([imaging_centers](inputs/imaging_centers.py)).
  It does the scan if it's such a center or one of them billed the service in an office, and otherwise it only reads it.
  A rate's modifier says which bill it prices (`PARTS`), a rate with a modifier in `OTHER_BILLS` is a different bill and is left out, and the build stops on a modifier in neither list.
  Each rate records the strongest evidence behind it, in the order `EVIDENCE` lists: a doctor on the rate's own roster whom Medicare shows billing the service, a doctor elsewhere under the biller, or the biller's registration as a center.
  `prices` shows the rate with the strongest evidence, because a biller often sits under several fee schedules and the file doesn't say which a patient gets.
- [`addresses`](inputs/addresses.py) puts every address the build reads in a building, and gives the building one point.
  Two addresses are one building when Census standardized them alike, or when their street and ZIP match in USPS's abbreviations once cut before any suite.
  Every address on a hospital's street is in the hospital's building.
  The point is Census's for an address in the building, the hospital's own first, or OpenStreetMap's when Census located none ([geocode.md](../ingestion/reference/geocode.md)).
- [`biller_places`](inputs/biller_places.py) puts each biller at every building NPPES lists it in, its main address and its secondary locations: the hospital in that building, or the practice there under its name.
  A practice takes the address and phone of its lowest NPI's record there.
  Billers in one building whose names match without punctuation or endings like LLC are one practice, keyed on the lowest NPI, and different practices in one building stay apart ([what one place is](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6643172584)).
  A doctor's own NPI takes its CMS group's name, and away from a hospital it has no place without one. A biller with no Georgia record has none either.
  Every billing ID in Anthem's files is an NPI, and the build stops if a file lists a tax ID instead, since NPPES can't place it.
  The build lists every billing ID with a price but no place, so an NPI missing from the monthly NPPES snapshot doesn't drop out silently.
- [`service_sites`](inputs/service_sites.py) says at which of a biller's places its rate for a service is shown.
  Those are the places where Doctors & Clinicians lists one of its doctors for the service practicing, each with those doctors.
  A biller none of whose doctors is listed at any of its places shows the service at its main place, with all of them.
  A registered imaging center shows it at every one of its places, because the registration, not a doctor, is its evidence.

## A hospital's own fee

[`hospital_fees`](inputs/hospital_fees.py) prices a hospital's `facility` fee for a service on a network from one source, never a blend of two, since one contract prices a plan ([AGENTS.md](../AGENTS.md#critical-rules), rule 5), in this order:

1. The plan's own file (`plan`): its institutional rates for the service's code, a fee-for-service dollar amount for an outpatient bill with no modifier (`facility_rates`).
   A rate prices a hospital when it covers a billing NPI that NPPES registers as a hospital, with an address, its own or another it lists, in the hospital's building ([addresses](inputs/addresses.py)).
2. Only where the plan's file prices none there, the hospital's own price file (`hospital`), one per hospital in `hospital_rates/`, which `make reference STEP=hospital-rates` writes.
   It reads the file's rows for the service's code on a bill that isn't only the doctor's, outpatient or with no setting, with no modifier, and with a negotiated dollar amount above $1 and below `CEILING`, since files write runs of nines where they have no price.
   Only a `fee schedule` row's dollar amount is the fee, since a percentage row's is the hospital's estimate from its own charge, a per diem is a day's rate, and a case rate covers more than the service.
   A file names each rate's payer and plan in its own words, so a rate counts only under a name [`integration/hospital_plans.yaml`](hospital_plans.yaml) gives that hospital and the network ([hospital_plans](inputs/hospital_plans.py)).
   The build stops on a name the list doesn't give the hospital, so a new one is read before it is priced or left out.

The fee is the highest rate at its source, and `several_rates` says there is more than one, since neither file says which applies.
A hospital on the map with a fee has a row for it whether or not a group with a rate is placed there.
[`hospital_cash`](inputs/hospital_cash.py) is the hospital's cash price for the service: the highest on the same rows of its file, whatever their payer.
It's shown on the hospital's rows, and a cash price alone puts no hospital on the list.

## Running it

```
make build                 # data/ingestion/ -> data/serving/, in the integration container
make build TEST=1          # data-test/ingestion/ -> data-test/serving/
python -m integration.build --data-dir <path>   # <path>/ingestion/ -> <path>/serving/
make coverage              # what each step keeps, from rates on file to rows shown; writes nothing
make coverage BY=service   # one line per service
```

**Coverage.** `make coverage` makes the same tables and counts, for each network, the service and biller pairs with a rate, those with evidence and of which kind, those with a place for the service and one of those places on the map, and the rows shown ([coverage.py](coverage.py)).
At hospitals it counts the rows by what they show (a fee and a reading, a fee only, a reading only, or neither), those with a fee from each source, and those with a cash price.
It is where to look when a service or a place is missing, and it takes `TEST=1` and `--data-dir` as the build does.

**Memory.** The build holds its inputs and tables in one DuckDB connection, which spills
to disk past its memory limit. It reads only `data/ingestion/`, which its container mounts read-only, and writes only
`data/serving/` (a worktree's `HH_SERVING_DIR` mounts its own folder there). Tunables:
`DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, `DUCKDB_TMP`.
