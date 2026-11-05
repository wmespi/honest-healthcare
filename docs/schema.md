# Storage schema - Parquet

*Read this when writing a query against the data or changing what the parser
writes. The single home for the on-disk layout.*

Everything is **Parquet** under `data/`, and there is no database.
`du -sh data/*` gives the current inventory.

**Why Parquet + DuckDB, not a database.** One sequential bulk writer (the ETL) and
read-only analytical queries (the API) — the opposite of OLTP. Columnar ZSTD is far
smaller for repetitive rate data, DuckDB scans only the columns a query needs, and
neither side needs a server process.

---
## Parquet — `data/anthem/` (a build input — `make build` reads this, the API doesn't)

| File | Columns |
|---|---|
| `prices/net=<slug>/{name}.parquet` | `file_id`, `group_set_id`, `network_name`, `billing_code_type`, `billing_code`, `negotiation_arrangement`, `negotiated_type`, `negotiated_rate`, `expiration_date`, `service_code`, `billing_class`, `modifier`, `setting` |

One row per **(network × negotiated price)** — NOT fanned out per provider group.
Hive-partitioned by `network_name` (slug = `slugifyNetwork` in `etl/extraction/partition.go`); a network-filtered query adds `net = ?` and DuckDB
prunes to the one directory. Join to `group_sets` on `(file_id, group_set_id)` to
expand a price to its provider groups.

- `service_code` — sorted `|`-joined place-of-service array.
- `modifier` — sorted `|`-joined `billing_code_modifier` array: `"26"` = professional
  / physician work, `"TC"` = technical / equipment + facility, `""` = global (most rows). `(billing_code, modifier, service_code, setting)` is what pins a rate
  for a patient.

| File | Columns |
|---|---|
| `group_sets/{name}.parquet` | `file_id`, `group_set_id`, `provider_group_id` |

Deduplicated provider-group rosters. `group_set_id` = FNV-64a of a block's sorted
`provider_reference` ids (`hashGroupSet` in `etl/extraction/stream.go`); written once per distinct
roster per file. `prices ⨝ group_sets` reproduces every original
`(code, rate, provider_group)` tuple exactly.

| File | Columns |
|---|---|
| `providers/{name}.parquet` | `file_id`, `provider_group_id`, `network_name`, `npi`, `tin_type`, `tin_value` |
| `codes/{name}.parquet` | `billing_code_type`, `billing_code`, `name`, `description` |
| `npi_lookup.parquet` | `npi`, `tin_value` |
| `index_files.parquet` | `file_id`, `name`, `address`, `location` |
| `index_file_plans.parquet` | `file_id`, `plan_id` |
| `parses/{name}.parquet` | `file_id`, `name`, `address`, `status`, `reason`, `anthem_updated_at`, `finished_at`, `compressed_bytes`, `price_rows`, `provider_rows`, `price_rows_dropped`, `provider_rows_dropped` |

`{name}` is the file's name in Anthem's index without `.json.gz` (`GA_JBNKMED0001`), and `file_id` is its ID, a hash of its address ([etl/discover.md](../etl/discover.md#a-files-id)).

`tin_value` is the billing entity's tax id — in Anthem's files it's an **NPI**
(`tin_type` is always `'npi'`), usually an organization's and sometimes a doctor's
own, so it doubles as a stable practice key: one real
practice recurs across the MRF as many file-local `provider_group_id`s but keeps
one `tin_value`, and it resolves to a name via NPPES (`npi = tin_value`).
The find-care build calls it the biller ([build/build.md](../build/build.md#the-find-care-tables)).

`index_files.parquet` is every file this month's index lists, and `index_file_plans.parquet` the Georgia individual-market plans it lists against each, one row per `(file_id, plan_id)`; `make discover` writes both ([etl/discover.md](../etl/discover.md)).
Parse picks the files `etl/targets.yaml` names from them, and the build reads the link to tell which parsed file prices a network.
`parses/{name}.parquet` is one row saying what the last parse of a file made of it, which decides whether parse downloads it again ([etl/parse.md](../etl/parse.md#when-parse-downloads-a-file)).

### Why price grain + `group_sets`

The MRF lists every participating provider group under nearly every billing code, so
a flat layout fans out to one row per `(code × price × group × network)`. `prices` +
`group_sets` stores each roster once, and the build re-joins them
(`provider_group_id` is file-local;
[etl/mrf-model.md](../etl/mrf-model.md#one-plan-one-contract)).

### `network_name`

The structured network label for a price — one member of the
`provider_references[].network_name` array (e.g. `"GA Blue Value HIX Individual
Network"`), one value per row, equal to its `net` partition. A provider group in two
networks lands in both partitions. A price carries no `plan_name`; a plan's network
comes from Georgia's exchange plan file
([reference/exchange-plans.md](../reference/exchange-plans.md)), and the build picks a
network's rates by this label ([build/build.md](../build/build.md#the-find-care-tables)).

## Parquet — `data/serving/` (the build step's output — [build/build.md](../build/build.md), read straight by the API)

`make build` (`build/build.py`) writes the **only** thing `serving/` reads — a missing
table here is a `503` from `GET /`, never a fallback to raw `anthem/` / `nppes/` /
`reference/` / `cms/`.

| Table | Columns |
|---|---|
| `networks.parquet` | `network_id`, `name`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `plans.parquet` | `plan_id`, `name`, `carrier`, `metal_level`, `needs_referral`, `network_id`, `deductible`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `premiums.parquet` | `plan_id`, `rating_area`, `age`, `premium`, `tobacco_premium`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `zips.parquet` | `zip`, `lat`, `lon`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `services.parquet` | `service_id`, `billing_code`, `service_category`, `name`, `keywords`, `about`, `could_change`, `asks`, `preventive_note`, `medicare_rate`, `medicare_facility_rate`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `cost_sharing.parquet` | `plan_id`, `service_category`, `copay`, `coinsurance`, `after_deductible`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `places.parquet` | `place_id`, `name`, `kind`, `address`, `city`, `lat`, `lon`, `phone`, `stars`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `prices.parquet` | `network_id`, `service_id`, `place_id`, `parts` (`part`, `max`), `several_rates`, `billers`, `providers`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `providers.parquet` | `npi`, `name`, `specialty`, `score`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `unlocated_places.parquet` | `place_id`, `name`, `kind`, `address`, `city`, `zip`: the places Census can't put on the map, which nothing serves ([build.md](../build/build.md#the-find-care-tables)) |
| `manifest.json` | `built_at`, `rows`: when the build ran and each table's row count, which `GET /` reads |

The entity model: [architecture.md](architecture.md#serving-entity-model).

---

## Parquet — `data/nppes/`, `data/reference/`, `data/cms/`

Paths are under `data/`, and each file is built by `make reference` with the `STEP` shown.

| File | What it holds |
|---|---|
| `nppes/ga_providers.parquet`<br>[`STEP=nppes`](../etl/nppes.md) | Georgia's NPPES providers. Join `taxonomy_code` to `nucc_taxonomy.parquet` for the real specialty label.<br>**Columns:** `npi`, `entity_type`, `org_name`, `last_name`, `first_name`, `taxonomy_code`, `taxonomy_group`, `is_hospital`, `is_clinic`, `address_line1`, `address_line2`, `city`, `state`, `postal_code` |
| `reference/nucc_taxonomy.parquet`<br>[`STEP=taxonomy-labels`](../reference/taxonomy-labels.md) | The NUCC label for each taxonomy code.<br>**Columns:** `taxonomy_code`, `grouping`, `classification`, `specialization`, `display_name`, `specialty`, `is_individual` |
| `cms/ga_provider_service.parquet`<br>[`STEP=cms-utilization`](../reference/cms-utilization.md) | One row per Georgia NPI, HCPCS code and place of service billed to Medicare Part B. The find-care build reads who billed each scan, and where, from it.<br>**Columns:** `npi`, `hcpcs_cd`, `place_of_service` (`F` or `O`), `tot_benes`, `tot_srvcs`, `tot_bene_day_srvcs`, `avg_mdcr_alowd_amt`, `provider_type`, `hcpcs_drug_ind`, `year` |
| `reference/mips_ga.parquet`<br>[`STEP=mips`](../reference/mips.md) | CMS MIPS overall score per Georgia clinician, newest performance year. The source of `providers.score`, which is null for clinicians CMS publishes no score for.<br>**Columns:** `npi`, `mips_score`, `mips_source`, `performance_year` |
| `reference/geocode_addresses.parquet`<br>[`STEP=geocode`](../reference/geocode.md) | Every address sent to Census and what it answered, so a run sends only new ones. Includes each hospital's address from `hospitals_ga.parquet`. Empty but `geocoded_on` for an address Census couldn't match.<br>**Columns:** `address_line1`, `address_line2`, `city`, `state`, `postal_code`, `match_type`, `matched_address`, `latitude`, `longitude`, `benchmark`, `geocoded_on` |
| `reference/zip_centroids.parquet`<br>[`STEP=zip-centroids`](../reference/zip-centroids.md) | Census Gazetteer ZCTA internal points, which the build writes to `zips` for `?zip=` distance.<br>**Columns:** `zip`, `lat`, `lon` |
| `reference/mpfs_ga.parquet`<br>[`STEP=mpfs`](../reference/mpfs.md) | The CMS Physician Fee Schedule allowed amount, (work RVU × work GPCI + PE RVU × PE GPCI + MP RVU × MP GPCI) × conversion factor, per Georgia locality. `medicare_allowed` is null for carrier-priced rows (status `C`), and bundled or non-covered statuses are dropped. The source of `services.medicare_rate`. Physician fee schedule only.<br>**Columns:** `billing_code`, `billing_code_type` (`CPT` for 5 digits, else `HCPCS`), `modifier` (empty, `26`, `TC` and others), `pos` (`nonfacility` or `facility`), `locality` (`01` Atlanta or `99` the rest of Georgia), `medicare_allowed`, `status` |
| `reference/opps.parquet`<br>[`STEP=opps`](../reference/opps.md) | CMS OPPS Addendum B: the national Medicare hospital-outpatient payment per code. Codes with no dollar rate are dropped. The source of `services.medicare_facility_rate`.<br>**Columns:** `billing_code`, `billing_code_type`, `status_indicator`, `apc`, `payment_rate`, `year` |
| `reference/dac_ga.parquet`<br>[`STEP=doctors-clinicians`](../reference/doctors-clinicians.md) | One row per NPI, from CMS Doctors & Clinicians (Care Compare): a real group-practice identity (`org_pac_id` and name) independent of Anthem's buckets, plus demographics. The build names a doctor's own billing NPI after its CMS group. Georgia only (`npi_lookup`, else `State='GA'`).<br>**Columns:** `npi`, `last_name`, `first_name`, `credential`, `primary_specialty`, `org_pac_id`, `org_name`, `grad_year`, `med_school`, `gender` |
| `reference/hospitals_ga.parquet`<br>[`STEP=hospitals`](../reference/hospitals.md) | One row per Georgia hospital, from CMS Hospital General Information. `stars` is Medicare's overall rating, null where CMS has none.<br>**Columns:** `ccn`, `name`, `address`, `city`, `state`, `postal_code`, `county`, `phone`, `hospital_type`, `ownership`, `emergency_services`, `stars` |
| `reference/exchange_plans_ga.parquet`<br>[`STEP=exchange-plans`](../reference/exchange-plans.md) | One row per medical plan on Georgia's exchange, keyed on its HIOS plan ID. `etl parse` reads it to check the files `etl/targets.yaml` names. `network_id` is the issuer and Georgia's network ID (`45334-GAN001`).<br>**Columns:** `plan_id`, `plan_year`, `issuer_id`, `carrier`, `name`, `metal_level`, `plan_type`, `needs_referral`, `network_id`, `deductible` |
| `reference/exchange_networks_ga.parquet`<br>[`STEP=exchange-plans`](../reference/exchange-plans.md) | One row per provider network the plans above use.<br>**Columns:** `network_id`, `issuer_id`, `name` |
| `reference/exchange_premiums_ga.parquet`<br>[`STEP=exchange-plans`](../reference/exchange-plans.md) | The monthly premium for one person, for ages 0 to 64, where 64 means 64 and over.<br>**Columns:** `plan_id`, `rating_area`, `age`, `premium`, `tobacco_premium` |
| `reference/exchange_cost_sharing_ga.parquet`<br>[`STEP=exchange-plans`](../reference/exchange-plans.md) | What each plan above charges the member in network, by benefit, in Georgia's wording, such as `$300.00 Copay after deductible`.<br>**Columns:** `plan_id`, `benefit`, `covered`, `copay`, `coinsurance` |

Test isolation (`data-test/`): [testing.md](testing.md).
