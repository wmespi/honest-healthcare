# build: what ingestion writes → the serving tables

*Read this when changing what the serving layer reads, or a product rule.*
`make build` (→ `integration/build.py`) is the one cheap, re-runnable step between the streaming ETL and the API, and the **only** thing the API reads - a missing table is a `503` from `GET /api/health`, not a fallback to raw Parquet.

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
| `plans.parquet` | one row per plan on a network with prices, as a person picks it: Georgia's plan IDs for one design in each service area are one row | `plan_id`, the HIOS plan ID such as `45334GA0020002`, with Georgia's `name` as published, `carrier`, `metal_level`, `needs_referral`, `network_id` and `deductible`, the standard version's in-network deductible for one person. `plan_id` is the lowest of the IDs with the same network, `name`, `metal_level`, `deductible` and `needs_referral`, and `plan_ids` lists them all, or the build stops if their cost sharing differs. |
| `premiums.parquet` | one row per plan, rating area and age | `premium` and `tobacco_premium`, the monthly premium for one person, from Georgia's premium file, for every plan ID a plans row stands for. Each has premiums in every rating area it's sold in, for every age from 0 to 64, where 64 means 64 and over, or the build stops. |
| `zips.parquet` | one row per ZIP the Census lists | `zip`, `lat` and `lon`, the point a ZIP search measures distance from. Every ZIP, not only Georgia's, because the Census file has no state and someone near the state line may search from across it. Each must be five digits with a point on the map, or the build stops. |
| `services.parquet` | one row per service in [`integration/services.yaml`](services.yaml) with a row in `prices` | `service_id`, our own slug such as `mri-lower-back`, its one `billing_code` (an MS-DRG for a hospital stay, whose `code_types` says so), its `family` ([the service model](#the-service-model)), and what the screen says about it: `name`, `keywords`, `about`, `could_change`, `asks` and an optional `preventive_note`, each the service's own or its family's. `bills` is each bill a patient could get for it, in its family's order: its `part`, the `label` the screen shows, its `billing_class` (`institutional` for a facility's own fee), the `benefits` the member's share for it falls under, and what the screen says where it has no rate. `extra_bills` is each extra bill it names (`anesthesia`, `pathology`, `doctor`: `part`, `label`, `note`, the `missing` note and `code`, from `families.yaml`). `medicare_rate` is Medicare's office rate for the whole service, the median of Georgia's two payment areas, and `medicare_facility_rate` is Medicare's national hospital outpatient rate, empty where Medicare doesn't pay that way, as for mammograms. |
| `families.parquet` | one row per family with a service in `services` | `family`, the `name` the screen calls it, and its `position` in the screen's list, and its `measures`, the ids of the quality measures that fit any of its services (empty when none does), and its `empty_note`, what the screen says when the plan has no place with a price for one of its services (empty for the screen's own wording). |
| `cost_sharing.parquet` | one row per plan and benefit a service's bills fall under | `plan_id`, `benefit`, our key for it, its `label`, and what the plan's standard version charges the member in network, from Georgia's benefits and cost-sharing file, under Georgia's name for the benefit: `copay` in dollars (`copay_per`, `day` or `stay`, when it is charged for each) and `coinsurance` as a percent of the rate, each empty when the plan doesn't charge one, and `after_deductible`, whether the member pays the whole rate until the deductible is met, and `copay_limited`, whether a catastrophic plan's copay covers only a limited number of visits a year before the deductible, after which the member pays the whole rate until the deductible. "No Charge" is a $0 copay, and with both a copay and a coinsurance the member pays the copay and then the coinsurance, as the plans' summaries of benefits word it. The build stops if a plan doesn't cover a benefit, words its share in a way the build doesn't read, or applies the deductible to only one of the two, except a catastrophic plan's copay before the deductible. |
| `places.parquet` | one row per Georgia hospital, plus one per practice with a price, that a geocoder can put on the map | `place_id`, `name`, `kind` (`hospital`, a kind of center such as `imaging_center`, or `practice`) and `kind_name`, what the screen calls it, `address`, `city`, `lat`, `lon`, `phone`, and a hospital's `stars`. A hospital's `place_id` is its CCN. A practice's is the lowest billing NPI among those registered in its building, and where none is, as at a group's second site, the lowest NPI there with a short hash of the building. The point is the building's ([addresses](inputs/addresses.py)), and a building no geocoder located has none, because any other would be a guess. |
| `prices.parquet` | one row per network, service and place where the place's billers have a rate for it, plus one per hospital or center with its own fee for it, each showing at least one rate | `network_id`, `service_id`, `place_id`, and `parts`, one entry per bill a patient there would get, in the order its family lists them, each with the rate that has the strongest evidence behind it, the highest of those when several tie (`max`), and where the rate is from (`source`: `plan` for the plan's own file, `hospital` for a hospital's own, empty with no rate). A hospital stay's fee that a hospital prices only per day or as a percent of its charges has `max` empty and `terms` saying it ("$2,472 a day", "39% of the hospital’s charges"), and the hospital still has a row. A hospital's row shows its family's `hospital` bills: its own fee ([a facility's own fee](#a-facilitys-own-fee)) and the others by the groups in its building, such as a scan's reading. Either puts the hospital on the list: the groups' rates ([decided on #135](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6646717812)), or its fee, which is evidence the hospital itself is in network. Only where the fee is empty does nothing on file confirm that. An empty reading at a hospital means another group reads the scan and bills it separately, and the plan's file doesn't place one there, so `billers` and `providers` are empty too unless a group there has a rate for another part. Its `cash_price` is what the hospital's own file says it charges when no plan pays, and it's empty at every other place. Any other place shows the first of its family's `elsewhere` alternatives it has a rate for every bill of, else the last, and an `on_site` bill has a rate only where the place does the service. So a place that does a scan shows its `all_in` rate, or the `scan` and the `reading` when it lists no all-in rate, and a reading group elsewhere shows an empty `scan` and its `reading`. A center that bills its own fee, such as a surgery center, shows that fee even with no doctor's rate, and the doctor's fee empty. `several_rates` says a part shown has more than one rate with that evidence, or a facility's fee more than one at its source, and `billers` lists the names of the billing groups behind the row, which at a hospital are the groups reading there. `providers` lists the clinicians Medicare shows billing the service for those billers, never the whole roster, so it's exact for the row's network and service, and empty when Medicare shows no one. Every network, service and place is in its table, or the build stops. |
| `providers.parquet` | one row per clinician a `prices` row lists | `npi`, and the `name` and `specialty` from the clinician's own NPPES record, the specialty as NUCC's name for its primary code, and `score`, Medicare's quality (MIPS) score, empty when there's none. Every clinician a price lists is here with a name and a specialty, or the build stops. |
| `quality.parquet` | one row per subject and quality measure CMS has a value or comparison for | `subject` (a `place_id`, or a clinician's NPI as text), `subject_kind` (`place` or `clinician`), `measure`, and its `name`, `about`, `source` (a plain label of the CMS dataset) and `position`, from [`integration/measures.yaml`](measures.yaml) ([measures](inputs/measures.py)); `score`, `shown`, how the value reads, such as `8.2 per 1,000`; `compared`, CMS's own comparison in plain words or, where CMS has none, the national value; `period`; and `services`, the service ids the measure fits. A hospital's rows are on its CCN, a surgery center's on the place its NPI bills from (never a hospital's), and a clinician's volume rows only for a clinician in `providers` whose volume CMS displays; a volume's `score` is its count, or the low end of CMS's band. |
| `extra_bills.parquet` | one row per network, service in `services` and extra bill (`anesthesia`, `pathology`, `doctor`) the service names a code for in [`services.yaml`](services.yaml) and the network lists dollar amounts for | `network_id`, `service_id`, `part`, the `code`, `low`, `typical` and `high` (the 10th percentile, median and 90th percentile of the network's no-modifier professional fee schedule lines for the code), and `lines`, how many. [Why a plan's usual range](#extra-bills) |
| `unlocated_places.parquet` | one row per hospital or practice no geocoder can put on the map | `place_id`, `name`, `kind`, `address`, `city` and `zip`. It's left off the screen, so nothing serves it, and a price there isn't shown. |

## The service model

What a service's bills are is data: [`integration/families.yaml`](families.yaml) describes each family of services, and [`integration/services.yaml`](services.yaml) names the services in each ([families](inputs/families.py), [service_list](inputs/service_list.py)).
A family with `asks_specialty` (office visits, whose one code is the same whatever the doctor's specialty) also has the kinds of doctor in [`integration/specialties.yaml`](specialties.yaml) ([specialties](inputs/specialties.py)): each has a `benefit`, `primary_care` or `specialist`, and the NUCC codes that count as it, none in two kinds.
`families.specialties` lists them for such a family, `prices.specialties` the kinds a row's clinicians are, by their NPPES specialty code, or, where they give none (a generic nurse practitioner, or none listed), the kinds of the NPPES specialty codes of the row's billers' own NPIs, such as a dermatology practice's, and `providers.specialty_id` each clinician's kind.
The build prints the specialty labels of those clinicians that count as no kind, with how many places each is at, and how many rows end with no kind at all.
A family's bills are its parts: a `professional` bill is priced from the plan's rates with a modifier it names, and the `institutional` one, at most one, is a facility's own fee.
A professional bill may name its own `place_of_service`, which it reads in place of the family's, so a procedure's doctor's fee done in an office and one done at a hospital or surgery center are two bills; an entry joining codes with "|" ranks them equal, and a rate naming a code of two bills counts for both.
Its `hospital` and `elsewhere` lists say which bills a place shows, and `elsewhere` may name the institutional bill for a family whose `centers` bill their own fee, and its `benefits` which of a plan's benefits each falls under, unless the bill or the service names its own.
Its `centers` are the NPPES specialty codes that register a biller as a center doing its services, with the kind of place each makes, `evidence_codes` the Medicare codes that show a doctor does them, and `place_of_service` the codes whose rates it reads, an office unless it says otherwise.
The file's `facilities` are NPPES specialty codes, such as a generic surgery center's, that bill their own fee for any family whose `elsewhere` names the institutional bill but are no evidence a biller does the service, since such a facility has a contract fee for every code.
For those families a biller registered as one of the family's centers or as a facility has no professional rate, because the fee schedule on its own NPI isn't a doctor's fee, and `medicare_rate` is empty, because Medicare's physician rate covers only the doctor's part.
A family's codes are CMS's shoppable services ([shoppable.md](../ingestion/reference/shoppable.md)) whose RBCS subcategory or family is in its `groups` and not in its `except`, plus any in its `codes`.
A family's text is what the screen says about a service that doesn't say its own, so it says nothing that isn't true of every service in it.
The build stops on a service whose code isn't one of its family's, a code named twice in one family, a malformed slug or code, and anything in either file it can't use: an undeclared family, benefit or kind, a repeated part or modifier, a list that names a part the family lacks, or a center code with two kinds or one NUCC doesn't list.
It lists, without stopping, each family's shoppable codes the plans price that no service names, as candidates for one.

Who prices a service where follows the rules on #135 ([reading groups versus places](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6639007442)):

- [`biller_doctors`](inputs/biller_doctors.py) keeps, for each billing NPI with a rate for a service, the doctors Medicare shows billing that service, by its own code or its family's `evidence_codes`: those on the rosters the rate covers, and the biller itself when it bills under a doctor's own NPI.
  `biller_rates` takes them as its evidence, and `prices` as the clinicians on each row.
- [`biller_rates`](inputs/biller_rates.py) keeps a network's outpatient professional rates for each service, each for the bill in its family that names its modifier, by the billing NPI each covers.
  A service keeps only its lines for the best place of service its family reads that any of them names on the network.
  A rate under the file's shared labels is used only where the network's own labels have none for the biller, service and bill.
  A biller appears if it has a doctor in `biller_doctors` for that service, or if its NPPES primary specialty code registers it as a center for the service's family.
  It does the service where it is (`on_site`) if it's such a center or one of those doctors billed the service in an office, and otherwise it bills only the parts done elsewhere, such as a scan's reading.
  A rate is for the bill that names each of its modifiers, not counting one in `BODY_SITES`, which only says where on the body, so a surgery a plan prices only on one side keeps its price.
  A rate with a modifier in `OTHER_BILLS` is a different bill and is left out, and the build stops on a modifier that neither a family's bills nor those lists name.
  Each rate records the strongest evidence behind it, in the order `EVIDENCE` lists: a doctor on the rate's own roster whom Medicare shows billing the service, a doctor elsewhere under the biller, or the biller's registration as a center.
  `prices` shows the rate with the strongest evidence, because a biller often sits under several fee schedules and the file doesn't say which a patient gets.
- [`addresses`](inputs/addresses.py) puts every address the build reads in a building, and gives the building one point.
  Two addresses are one building when Census standardized them alike, or when their street and ZIP match in USPS's abbreviations once cut before any suite.
  Every address on a hospital's street is in the hospital's building.
  The point is Census's for an address in the building, the hospital's own first, or OpenStreetMap's when Census located none ([geocode.md](../ingestion/reference/geocode.md)).
- [`biller_places`](inputs/biller_places.py) puts each biller at every building NPPES lists it in, its main address and its secondary locations: the hospital in that building, or the practice there under its name.
  A practice takes the address and phone of its key NPI's record there, the name of the biller with rates for the most services (the lowest NPI where two tie), and the kind of center its billers are registered as, the first in the order of `kinds` where they're two; a generic surgery center's kind (`facilities`) counts only for a practice with no doctor's rate.
  Billers in one building are one practice when their names match without punctuation or endings like LLC, or when two companies are at the same office (the same suite, or neither lists one), and billers linked through others are one too.
  A doctor billing under their own NPI joins only the practice its CMS group's name matches, because a doctor often works from more than one practice's office.
  A practice is keyed on the lowest NPI with a doctor's rate, an organization's before a doctor's own, so a clinic keeps its key and address however many of its clinicians bill under their own NPIs (its centers' the same way where it has none), and billers in different suites with different names stay apart ([what one place is](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6643172584)).
  A doctor's own NPI takes its CMS group's name, and away from a hospital it has no place without one.
  A biller with no Georgia record has none either.
  A roster row billed under an NPI bills as that NPI.
  A row billed under a tax ID (`ein`, as Ambetter's are), which NPPES can't place, bills as its own NPI, so each NPI in the group bills in its own right with itself as its only doctor (`roster_biller` in [datasets](inputs/datasets.py), the one definition every table uses).
  The build stops on any other `tin_type`, and on a tax ID that isn't `NN-NNNNNNN`.
  The build lists every billing ID with a price but no place, so an NPI missing from the monthly NPPES snapshot doesn't drop out silently.
- [`service_sites`](inputs/service_sites.py) says at which of a biller's places its rate for a service is shown.
  Those are the places where Doctors & Clinicians lists one of its doctors for the service practicing, each with those doctors.
  A biller none of whose doctors is listed at any of its places shows the service at its main place, with all of them.
  A registered center shows it at every one of its places, because the registration, not a doctor, is its evidence.

## Extra bills

A procedure also brings bills the patient doesn't choose: anesthesia, from the facility's group, and the lab exam of removed tissue, from the lab the doctor sends it to.
A hospital stay brings the doctor's whole fee (`doctor`, such as the delivery package or the joint replacement), which the doctor bills apart from the hospital, and anesthesia.
Both plans' files give these codes' rates to nearly every provider on their rosters as one generic fee schedule, so who bills them can't be known, only what the plan usually pays.
`extra_bills.parquet` is therefore the plan's range for the code, never a place's, and the screen shows it apart from each place's most you'd pay.
A rate is a line with no modifier, so a both-sides line (modifier 50, 1.5 times) and a percentage line are left out, as is a placeholder, at most $1 or 5% of the network's median for the code, which is often the code's unit count.
An anesthesia rate is a per-unit price times the code's base units plus one, so it covers about the first 15 minutes, and a lab exam keeps only outpatient lines.
Pathway lists anesthesia under the hospital (place of service 21), so anesthesia and doctor lines are kept whatever setting they name.
A network with no line for a service's code has no row, and the screen says so in the part's `missing` note.

## A facility's own fee

[`facility_fees`](inputs/facility_fees.py) prices a hospital's or a center's own fee for a service whose family has an institutional bill on a network from one source, never a blend of two, since one contract prices a plan ([AGENTS.md](../AGENTS.md#critical-rules), rule 5), in this order:

1. The plan's own file (`plan`): its institutional rates for the service's code, a fee-for-service dollar amount for an outpatient bill with no modifier (`facility_rates`).
   A rate prices a hospital when it covers a billing NPI that NPPES registers as a hospital, with an address, its own or another it lists, in the hospital's building ([addresses](inputs/addresses.py)).
   It prices a center, such as a surgery center, when the family offers the fee away from a hospital and it covers a billing NPI that NPPES registers as a center of the family (`centers`), at each place that biller has ([biller_places](inputs/biller_places.py)); a center has no price file of its own to fall back on.
   A facility's fee counts only at a place where [`service_sites`](inputs/service_sites.py) has a row for the network and service, that is, where a group with evidence for the service works; a center's counts wherever it is.
   A rate under the file's shared labels is used only where the network's own labels have none for the biller, service and bill.
2. Only where the plan's file prices none there, the hospital's own price file (`hospital`), one per hospital in `hospital_rates/`, which `make reference STEP=hospital-rates` writes.
   It reads the file's rows for the service's code on a bill that isn't only the doctor's, outpatient or with no setting, with no modifier, and with a negotiated dollar amount above $1 and below `CEILING`, since files write runs of nines where they have no price.
   Only a `fee schedule` row's dollar amount is the fee, since a percentage row's is the hospital's estimate from its own charge, a per diem is a day's rate, and a case rate covers more than the service.
   A file names each rate's payer and plan in its own words, so a rate counts only under a name [`integration/hospital_plans.yaml`](hospital_plans.yaml) gives that hospital and the network ([hospital_plans](inputs/hospital_plans.py)).
   A hospital's own file prices a network only at a hospital the network's own file lists (a roster on it bills as one of the hospital's NPIs, `network_hospitals`), since a file names a payer's plan, not which of its networks.
   The build stops on a name the list doesn't give the hospital, so a new one is read before it is priced or left out.

The fee is the highest rate at its source, and `several_rates` says there is more than one, since neither file says which applies.
A hospital or center on the map with a fee has a row for it whether or not a group with a rate is placed there.
[`hospital_cash`](inputs/hospital_cash.py) is the hospital's cash price for the service: the highest on the same rows of its file, whatever their payer.
It's shown on the hospital's rows, and a cash price alone puts no hospital on the list.

A hospital stay (an MS-DRG service in the `stays` family) is priced by the same table, one source, in the first term that source has:
a dollar `fee`, else `per_day`, else `percent` of the hospital's charges.
The plan's file gives a `negotiated` or `fee schedule` dollar amount as the fee and a `per diem` as a day's rate, with no modifier; its `derived` amounts (the payer's own estimate) and bundles are left out.
The setting the plan's file names isn't read, because an MS-DRG pays only for a stay and a file can call one outpatient.
The hospital's own file gives a `fee schedule` or `case rate` dollar amount as the fee, else a `per diem`, else a `percent of total billed charges`, under the names [`hospital_plans.yaml`](hospital_plans.yaml) gives the network.
A code matches only a rate or row of its service's `code_types`, so a DRG never meets another code type's digits.
A dollar amount (not a per diem) that one billing NPI in the plan's file, or one hospital under a network's name in its own file, lists for more than 25 distinct MS-DRGs is a default, not the stay's price, and is left out, so the next term or source applies; tiers of related DRGs stay under it.
`prices` shows a per day or percent term as `terms` with `max` empty, so the screen can say it and add no total.

## Running it

```
make build                 # data/ingestion/ -> data/serving/, in the integration container
make build TEST=1          # data-test/ingestion/ -> data-test/serving/
python -m integration.build --data-dir <path>   # <path>/ingestion/ -> <path>/serving/
make coverage              # what each step keeps, from rates on file to rows shown; writes nothing
make coverage BY=service   # one line per service
```

**Coverage.** `make coverage` makes the same tables and counts, for each network, the service and biller pairs with a rate, those with evidence and of which kind, those with a place for the service and one of those places on the map, and the rows shown ([coverage.py](coverage.py)).
At hospitals it counts the rows by what they show (the facility's own fee and a doctor's bill such as a reading, its fee only, or a doctor's bill only), those with a fee from each source, and those with a cash price.
It is where to look when a service or a place is missing, and it takes `TEST=1` and `--data-dir` as the build does.

**Memory.** The build holds its inputs and tables in one DuckDB connection, which spills to disk past its memory limit.
It reads only `data/ingestion/`, which its container mounts read-only, and writes only `data/serving/` (a worktree's `HH_SERVING_DIR` mounts its own folder there).
Tunables: `DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, `DUCKDB_TMP`.
