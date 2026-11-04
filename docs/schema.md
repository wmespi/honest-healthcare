# Storage schema — Parquet + Postgres

*Read this when writing a query against the data or changing what the parser
writes. The single home for the on-disk layout — Parquet and Postgres both.*

The serving layer reads **Parquet**. Postgres holds only the discovery queue and one
log table. `du -sh data/*` and `make psql` give the current inventory.

**Why Parquet + DuckDB, not Postgres.** One sequential bulk writer (the ETL) and
read-only analytical queries (the API) — the opposite of OLTP. Columnar ZSTD is far
smaller for repetitive rate data, DuckDB scans only the columns a query needs, and
neither side needs a server process.

---
## Parquet — `data/anthem/` (a build input — `make build` reads this, the API doesn't)

| File | Columns |
|---|---|
| `prices/net=<slug>/{id}.parquet` | `file_id`, `group_set_id`, `network_name`, `billing_code_type`, `billing_code`, `negotiation_arrangement`, `negotiated_type`, `negotiated_rate`, `expiration_date`, `service_code`, `billing_class`, `modifier`, `setting` |

One row per **(network × negotiated price)** — NOT fanned out per provider group.
Hive-partitioned by `network_name` (slug = `slugifyNetwork` in `etl/extraction/partition.go` == `network_slug()` in `serving/data_sources.py`); a network-filtered query adds `net = ?` and DuckDB
prunes to the one directory. Join to `group_sets` on `(file_id, group_set_id)` to
expand a price to its provider groups.

- `service_code` — sorted `|`-joined place-of-service array.
- `modifier` — sorted `|`-joined `billing_code_modifier` array: `"26"` = professional
  / physician work, `"TC"` = technical / equipment + facility, `""` = global (most rows). `(billing_code, modifier, service_code, setting)` is what pins a rate
  for a patient.

| File | Columns |
|---|---|
| `group_sets/{id}.parquet` | `file_id`, `group_set_id`, `provider_group_id` |

Deduplicated provider-group rosters. `group_set_id` = FNV-64a of a block's sorted
`provider_reference` ids (`hashGroupSet` in `etl/extraction/stream.go`); written once per distinct
roster per file. `prices ⨝ group_sets` reproduces every original
`(code, rate, provider_group)` tuple exactly.

| File | Columns |
|---|---|
| `providers/{id}.parquet` | `file_id`, `provider_group_id`, `network_name`, `npi`, `tin_type`, `tin_value` |
| `codes/{id}.parquet` | `billing_code_type`, `billing_code`, `name`, `description` |
| `npi_lookup.parquet` | `npi`, `tin_value` |
| `index_file_plans.parquet` | `file_id`, `plan_id` |

`tin_value` is the billing entity's tax id — in Anthem's files it's an **NPI**
(`tin_type` is always `'npi'`), usually an organization's and sometimes a doctor's
own, so it doubles as a stable practice key: one real
practice recurs across the MRF as many file-local `provider_group_id`s but keeps
one `tin_value`, and it resolves to a name via NPPES (`npi = tin_value`).
`/imaging/providers` collapses on it ([#48](https://github.com/wmespi/honest-healthcare/issues/48)).

`index_file_plans.parquet` is the plan → file link in the index `make discover` last read whole: one row per `(file_id, plan_id)`, sorted, copied from the Postgres table of the same name below ([etl/discover.md](../etl/discover.md#the-monthly-rebuild)).
The build reads it to tell which parsed file prices a network, so it reads no database.

## Parquet — `data/serving/` (the build step's output — [build/build.md](../build/build.md), read straight by the API)

`make build` (`build/build.py`) is the **only** thing `serving/` reads — a missing
table here is a `503` from `GET /`, never a fallback to raw `anthem/` / `nppes/` /
`reference/` / `cms/`.

| Table | Columns |
|---|---|
| `rates/net=<slug>/part.parquet` | `network_name`, `net`, `file_id`, `group_set_id`, `billing_code_type`, `billing_code`, `modifier`, `setting`, `service_code`, `negotiated_type`, `negotiation_arrangement`, `expiration_date`, `negotiated_rate`, `scope`, `is_sentinel`, `medicare_allowed`, `vs_medicare` |
| `group_sets.parquet` | `file_id`, `group_set_id`, `provider_group_id` |
| `group_members.parquet` | `file_id`, `provider_group_id`, `npi`, `tin_value` |
| `group_networks.parquet` | `file_id`, `provider_group_id`, `net`, `network_name` |
| `provider_dim.parquet` | `npi`, `name`, `specialty`, `nucc_classification`, `nucc_grouping`, `cms_provider_type`, `org_name`, `group_name`, `org_pac_id`, `grad_year`, `lat`, `lon`, `mips_score`, `mips_source`, `mips_year`, `service_lines`, `is_hospital`, `is_clinic`, `entity_type`, `last_name`, `first_name`, `taxonomy_code`, `taxonomy_group`, `address_line1`, `address_line2`, `city`, `postal_code` |
| `provider_affiliations.parquet` | `npi`, `ccn`, `facility_name` |
| `zip_centroids.parquet` | `zip`, `lat`, `lon` |
| `code_dim.parquet` | `billing_code`, `billing_code_type`, `label`, `category`, `rbcs_subcategory`, `rbcs_family`, `rbcs_is_major`, `search_text`, `shoppable`, `medicare_allowed`, `opps_payment` |
| `evidence.parquet` | `npi`, `billing_code`, `tier` (`billed` or `typical`), `prevalence`, `year`, `tot_srvcs`, `tot_benes`, `tot_bene_days`, `avg_mdcr_allowed`, `is_drug` |
| `rate_hist.parquet` | `payer`, `net`, `network_name`, `billing_code_type`, `billing_code`, `setting`, `scope`, `modifier`, `is_sentinel`, `bucket`, `n`, `n_rates` |
| `cross_network_rollup.parquet` | `billing_code`, `billing_code_type`, `net`, `network_name`, `n_groups`, `min_rate`, `p10`, `median`, `p90`, `max_rate` |
| `manifest.json` | `built_at`, `networks`, `scope`, `partial`, `target_patterns`, `codes`, `inputs`, `rows` |
| `networks.parquet` | `network_id`, `name`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `plans.parquet` | `plan_id`, `name`, `carrier`, `metal_level`, `needs_referral`, `network_id`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `premiums.parquet` | `plan_id`, `rating_area`, `age`, `premium`, `tobacco_premium`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `zips.parquet` | `zip`, `lat`, `lon`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `services.parquet` | `service_id`, `billing_code`, `name`, `keywords`, `about`, `could_change`, `asks`, `preventive_note`, `medicare_rate`, `medicare_facility_rate`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |
| `places.parquet` | `place_id`, `name`, `kind`, `address`, `city`, `lat`, `lon`, `phone`, `stars`: a find-care table ([build.md](../build/build.md#the-find-care-tables)) |

`rates` is the parser's **price grain** — one row per negotiated price, no group
fan-out (`group_set_id` links to `group_sets` for the join a query needs at read
time, after pruning on `net` + `billing_code`). Fanning out to one row per
`(price × provider group)` would be orders of magnitude larger. `scope` (`outpatient_scope` in
`serving/data_sources.py`), `is_sentinel` (a ceiling over the built networks) and the MPFS benchmark are added
at build time. The build keeps every row and doesn't combine prices across files
(`provider_group_id` is file-local;
[etl/mrf-model.md](../etl/mrf-model.md#one-plan-one-contract)).

`rate_hist` is the browse primitive: a $25-bucketed, roster-weighted histogram —
`n` sums each price's roster size, `n_rates` is the raw price-row count.
`is_sentinel` is a dimension (not a filter), so the histogram can show the
placeholder rows; `cross_network_rollup` excludes them.

`provider_dim.org_name` (raw NPPES entity name) and `.group_name` (the CMS Doctors &
Clinicians billing-group identity) are deliberately separate columns: folding them
would let a shared group affiliation overwrite an individual practitioner's own name
in a search result. `evidence` is scoped to NPIs reachable through a rate;
`provider_dim` is the full GA NPPES universe.

The entity model: [architecture.md](architecture.md#serving-entity-model-grain--provider-group).

### Why price grain + `group_sets`

The MRF lists every participating provider group under nearly every billing code, so
a flat layout fans out to one row per `(code × price × group × network)`. `rates` +
`group_sets` stores each roster once; a query re-joins them after pruning.

### `network_name`

The structured network label for a price — one member of the
`provider_references[].network_name` array (e.g. `"GA Blue Value HIX Individual
Network"`), one value per row, equal to its `net` partition. **The reliable filter
for the target plan.** A provider group in two networks lands in both partitions.
`rates` carries no `plan_name`; `/plans` serves the curated plan → network bridge in
`serving/plan_networks.json`, and the friendly plan picker filters on its network.

---

## Parquet — `data/nppes/`, `data/reference/`, `data/cms/`

Paths are under `data/`, and each file is built by `make reference` with the `STEP` shown.

| File | What it holds |
|---|---|
| `nppes/ga_providers.parquet`<br>[`STEP=nppes`](../etl/nppes.md) | Georgia's NPPES providers. Join `taxonomy_code` to `nucc_taxonomy.parquet` for the real specialty label.<br>**Columns:** `npi`, `entity_type`, `org_name`, `last_name`, `first_name`, `taxonomy_code`, `taxonomy_group`, `is_hospital`, `is_clinic`, `address_line1`, `address_line2`, `city`, `state`, `postal_code` |
| `reference/code_labels.parquet`<br>[`STEP=code-labels`](../reference/code-labels.md) | A label and RBCS category for each billing code.<br>**Columns:** `billing_code_type`, `billing_code`, `short_name`, `rbcs_category`, `rbcs_subcategory`, `rbcs_family`, `rbcs_is_major`, `label`, `search_text` |
| `reference/nucc_taxonomy.parquet`<br>[`STEP=taxonomy-labels`](../reference/taxonomy-labels.md) | The NUCC label for each taxonomy code.<br>**Columns:** `taxonomy_code`, `grouping`, `classification`, `specialization`, `display_name`, `specialty`, `is_individual` |
| `cms/ga_provider_service.parquet`<br>[`STEP=cms-utilization`](../reference/cms-utilization.md) | One row per Georgia NPI, HCPCS code and place of service billed to Medicare Part B. The source of the build's `evidence` table.<br>**Columns:** `npi`, `hcpcs_cd`, `place_of_service` (`F` or `O`), `tot_benes`, `tot_srvcs`, `tot_bene_day_srvcs`, `avg_mdcr_alowd_amt`, `provider_type`, `hcpcs_drug_ind`, `year` |
| `reference/specialty_procedure_profiles.parquet`<br>[`STEP=specialty-profiles`](../reference/specialty-profiles.md) | Tier 2: codes billed by at least `prevalence` of a specialty, from CMS, NPPES and NUCC. The source of `evidence`'s typical tier.<br>**Columns:** `specialty` (NUCC classification), `hcpcs_cd`, `billers`, `specialty_providers`, `prevalence` |
| `reference/mips_ga.parquet`<br>[`STEP=mips`](../reference/mips.md) | CMS MIPS overall score per Georgia clinician, newest performance year. Left-joined into `provider_dim`, and null for clinicians CMS publishes no score for.<br>**Columns:** `npi`, `mips_score`, `mips_source`, `performance_year` |
| `reference/pcp_geocode.parquet`<br>[`STEP=geocode`](../reference/geocode.md) | Census coordinates for every Georgia PCP or imaging NPI and every billing ID with an NPPES address, inside Georgia's bounding box. Read by the build into `provider_dim.lat` and `lon`.<br>**Columns:** `npi`, `latitude`, `longitude` |
| `reference/geocode_addresses.parquet`<br>[`STEP=geocode`](../reference/geocode.md) | Every address sent to Census and what it answered, so a run sends only new ones. Includes each hospital's address from `hospitals_ga.parquet`. Empty but `geocoded_on` for an address Census couldn't match.<br>**Columns:** `address_line1`, `address_line2`, `city`, `state`, `postal_code`, `match_type`, `matched_address`, `latitude`, `longitude`, `benchmark`, `geocoded_on` |
| `reference/zip_centroids.parquet`<br>[`STEP=zip-centroids`](../reference/zip-centroids.md) | Census Gazetteer ZCTA internal points, copied to `serving/` for `?zip=` distance.<br>**Columns:** `zip`, `lat`, `lon` |
| `reference/mpfs_ga.parquet`<br>[`STEP=mpfs`](../reference/mpfs.md) | The CMS Physician Fee Schedule allowed amount, (work RVU × work GPCI + PE RVU × PE GPCI + MP RVU × MP GPCI) × conversion factor, per Georgia locality. `medicare_allowed` is null for carrier-priced rows (status `C`), and bundled or non-covered statuses are dropped. The source of `code_dim.medicare_allowed` and `rates.medicare_allowed`. Physician fee schedule only.<br>**Columns:** `billing_code`, `billing_code_type` (`CPT` for 5 digits, else `HCPCS`), `modifier` (empty, `26`, `TC` and others), `pos` (`nonfacility` or `facility`), `locality` (`01` Atlanta or `99` the rest of Georgia), `medicare_allowed`, `status` |
| `reference/opps.parquet`<br>[`STEP=opps`](../reference/opps.md) | CMS OPPS Addendum B: the national Medicare hospital-outpatient payment per code. Codes with no dollar rate are dropped. Read by the build into `code_dim.opps_payment`.<br>**Columns:** `billing_code`, `billing_code_type`, `status_indicator`, `apc`, `payment_rate`, `year` |
| `reference/dac_ga.parquet`<br>[`STEP=doctors-clinicians`](../reference/doctors-clinicians.md) | One row per NPI, from CMS Doctors & Clinicians (Care Compare): a real group-practice identity (`org_pac_id` and name) independent of Anthem's buckets, plus demographics. Read by the build into `provider_dim`. Georgia only (`npi_lookup`, else `State='GA'`).<br>**Columns:** `npi`, `last_name`, `first_name`, `credential`, `primary_specialty`, `org_pac_id`, `org_name`, `grad_year`, `med_school`, `gender` |
| `reference/dac_hospital_affiliations.parquet`<br>[`STEP=doctors-clinicians`](../reference/doctors-clinicians.md) | Many rows per NPI: the CCN to NPI bridge for the Hospital Care Compare quality layer (roadmap step 2). `ccn` is `facility_affiliations_certification_number`, and `facility_name` is the `facility_type` label unless the source carried `hosp_afl_lbn_*` names. Scoped to the NPIs in `dac_ga.parquet`.<br>**Columns:** `npi`, `ccn`, `facility_name` |
| `reference/hospitals_ga.parquet`<br>[`STEP=hospitals`](../reference/hospitals.md) | One row per Georgia hospital, from CMS Hospital General Information. `stars` is Medicare's overall rating, null where CMS has none.<br>**Columns:** `ccn`, `name`, `address`, `city`, `state`, `postal_code`, `county`, `phone`, `hospital_type`, `ownership`, `emergency_services`, `stars` |
| `reference/exchange_plans_ga.parquet`<br>[`STEP=exchange-plans`](../reference/exchange-plans.md) | One row per medical plan on Georgia's exchange, keyed on its HIOS plan ID. `etl parse` reads it to check the files `etl/targets.yaml` names. `network_id` is the issuer and Georgia's network ID (`45334-GAN001`).<br>**Columns:** `plan_id`, `plan_year`, `issuer_id`, `carrier`, `name`, `metal_level`, `plan_type`, `needs_referral`, `network_id` |
| `reference/exchange_networks_ga.parquet`<br>[`STEP=exchange-plans`](../reference/exchange-plans.md) | One row per provider network the plans above use.<br>**Columns:** `network_id`, `issuer_id`, `name` |
| `reference/exchange_premiums_ga.parquet`<br>[`STEP=exchange-plans`](../reference/exchange-plans.md) | The monthly premium for one person, for ages 0 to 64, where 64 means 64 and over.<br>**Columns:** `plan_id`, `rating_area`, `age`, `premium`, `tobacco_premium` |

---

## Postgres — `honest_healthcare` (discovery queue + log only)

`localhost:5432` (`db:5432` in Docker), database `honest_healthcare`, user/password
`postgres`/`postgres`. `db/init.sql` creates a fresh volume; `db/migrations/*.sql` are
idempotent and `make migrate` applies them.

| Table | Written by | Purpose |
|---|---|---|
| `index_files` | `make discover` / `make parse` | The parse queue — one row per MRF. `address` (the location without its signed query, unique: the key a file keeps across months — [etl/discover.md](../etl/discover.md#the-monthly-rebuild)), `location` (this month's signed URL), `status` (`pending`/`processing`/`completed`/`failed`/`skipped` — [etl/queue.md](../etl/queue.md#status-lifecycle)), `source_updated_at` / `parsed_updated_at` (Anthem's `Last-Modified`, as last seen and as last parsed — [etl/queue.md](../etl/queue.md#when-anthem-updates-a-file)), `file_size_bytes`, `market_types[]`, `hios_issuer_ids[]`, `plan_states[]`, `reporting_entity_*`, `created_at`, `completed_at`, `failure_reason`. GIN indexes on the array columns. |
| `index_file_plans` | `make discover` | The plan → file link in this month's index — one row per `(file, plan)`, **scoped to Georgia individual-market plans** ([etl/discover.md](../etl/discover.md)): `file_id` (FK, `ON DELETE CASCADE`), `plan_id`, `plan_id_type`, `plan_name`, `market_type`, unique on `(file_id, plan_id, plan_name, market_type)`. Answers *"which files serve plan X"*; `etl parse` checks each file `etl/targets.yaml` names against it ([etl/parse.md](../etl/parse.md#target-selection)), and a full `make discover` copies its `(file_id, plan_id)` pairs to `anthem/index_file_plans.parquet` for the build. Indexed on `plan_id` (`text_pattern_ops`, prefix lookup); `plan_name` lookups are `ILIKE '%…%'` and unindexed. |
| `billing_codes` | `make parse` | Reference upsert — `billing_code` PK, `billing_code_type`, `name`, `description`; first occurrence wins. |
| `coverage_log` | `make parse` | One row per *parsed* file (`file_id` `UNIQUE`; a re-parse upserts; a probe-`skipped` file gets none) — rate/provider row counts, new codes/NPIs/TINs, distinct networks/settings/billing-classes, `notes` (GA-filter drop counts). Nothing reads it back. |

Status lifecycle and stuck-row recovery: [etl/queue.md](../etl/queue.md). The monthly
rebuild: [etl/discover.md](../etl/discover.md#the-monthly-rebuild). Test isolation (`test.*`,
`data-test/`): [testing.md](testing.md).
