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

```
prices/net=<slug>/{id}.parquet
    file_id | group_set_id | network_name
    billing_code_type | billing_code | negotiation_arrangement
    negotiated_type | negotiated_rate | expiration_date
    service_code | billing_class | modifier | setting
```
One row per **(network × negotiated price)** — NOT fanned out per provider group.
Hive-partitioned by `network_name` (slug = `slugifyNetwork` in `etl/extraction/partition.go` == `network_slug()` in `serving/data_sources.py`); a network-filtered query adds `net = ?` and DuckDB
prunes to the one directory. Join to `group_sets` on `(file_id, group_set_id)` to
expand a price to its provider groups.

- `service_code` — sorted `|`-joined place-of-service array.
- `modifier` — sorted `|`-joined `billing_code_modifier` array: `"26"` = professional
  / physician work, `"TC"` = technical / equipment + facility, `""` = global (most rows). `(billing_code, modifier, service_code, setting)` is what pins a rate
  for a patient.

```
group_sets/{id}.parquet     file_id | group_set_id | provider_group_id
```
Deduplicated provider-group rosters. `group_set_id` = FNV-64a of a block's sorted
`provider_reference` ids (`hashGroupSet` in `etl/extraction/stream.go`); written once per distinct
roster per file. `prices ⨝ group_sets` reproduces every original
`(code, rate, provider_group)` tuple exactly.

```
providers/{id}.parquet   file_id | provider_group_id | network_name | npi | tin_type | tin_value
codes/{id}.parquet       billing_code_type | billing_code | name | description
npi_lookup.parquet       npi | tin_value
```

`tin_value` is the billing entity's tax id — in Anthem's files it's an **org NPI**
(`tin_type` is always `'npi'`), so it doubles as a stable practice key: one real
practice recurs across the MRF as many file-local `provider_group_id`s but keeps
one `tin_value`, and it resolves to a name via NPPES (`npi = tin_value`).
`/rates/providers` collapses on it ([#48](https://github.com/wmespi/honest-healthcare/issues/48)).

## Parquet — `data/serving/` (the build step's output — [build/build.md](../build/build.md), read straight by the API)

`make build` (`build/build.py`) is the **only** thing `serving/` reads — a missing
table here is a `503` from `GET /`, never a fallback to raw `anthem/` / `nppes/` /
`reference/` / `cms/`.

```
rates/net=<slug>/part.parquet
    network_name | net | file_id | group_set_id
    billing_code_type | billing_code | modifier | setting | service_code
    negotiated_type | negotiation_arrangement | expiration_date | negotiated_rate
    scope | is_sentinel | source_kind | medicare_allowed | vs_medicare
group_sets.parquet             file_id | group_set_id | provider_group_id
group_members.parquet          file_id | provider_group_id | npi | tin_value
group_networks.parquet         file_id | provider_group_id | net | network_name
provider_dim.parquet           npi | name | specialty | nucc_classification
    | nucc_grouping | cms_provider_type | org_name | group_name | org_pac_id
    | grad_year | lat | lon | service_lines | is_hospital | is_clinic
    | entity_type | last_name | first_name | taxonomy_code | taxonomy_group
    | address_line1 | address_line2 | city | postal_code
provider_affiliations.parquet  npi | ccn | facility_name
code_dim.parquet               billing_code | billing_code_type | label
    | category | rbcs_subcategory | rbcs_family | rbcs_is_major | search_text
    | shoppable | medicare_allowed
evidence.parquet               npi | billing_code | tier ('billed' | 'typical')
    | prevalence | year | tot_srvcs | tot_benes | tot_bene_days
    | avg_mdcr_allowed | is_drug
rate_hist.parquet              payer | net | network_name | billing_code_type
    | billing_code | setting | scope | modifier | is_sentinel | bucket | n | n_rates
cross_network_rollup.parquet   billing_code | billing_code_type | net
    | network_name | n_groups | min_rate | p10 | median | p90 | max_rate
manifest.json                  built_at | networks | partial | inputs | rows
```

`rates` is the parser's **price grain** — one row per negotiated price, no group
fan-out (`group_set_id` links to `group_sets` for the join a query needs at read
time, after pruning on `net` + `billing_code`). Fanning out to one row per
`(price × provider group)` would be orders of magnitude larger. `scope` (`outpatient_scope` in
`serving/data_sources.py`), `is_sentinel` (a store-wide ceiling), `source_kind`
(`plan_specific` | `shared`, from `index_file_plans`), and the MPFS benchmark are
added at build time. **Rule 5** (AGENTS.md): the build keeps every row and tags
`source_kind`; it does **not** collapse across files (`provider_group_id` is
file-local) — that's resolved at read time, per practice, preferring `plan_specific`
over `shared` ([etl/mrf-model.md](../etl/mrf-model.md#conflict-resolution-strategy)).

`rate_hist` is the browse primitive: a $25-bucketed, roster-weighted histogram —
`n` sums each price's roster size, `n_rates` is the raw price-row count.
`is_sentinel` is a dimension (not a filter), so the histogram can show the
placeholder rows; `cross_network_rollup` (read by `/rates/by_network`) excludes them.

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

```
data/nppes/ga_providers.parquet
    npi | entity_type | org_name | last_name | first_name
    taxonomy_code | taxonomy_group | is_hospital | is_clinic
    address_line1 | address_line2 | city | state | postal_code
    ← join taxonomy_code to nucc_taxonomy.parquet for the real specialty label

data/reference/code_labels.parquet     (make reference STEP=code-labels — reference/code-labels.md)
    billing_code_type | billing_code | short_name
    rbcs_category | rbcs_subcategory | rbcs_family | rbcs_is_major | label | search_text

data/reference/nucc_taxonomy.parquet   (make reference STEP=taxonomy-labels — reference/taxonomy-labels.md)
    taxonomy_code | grouping | classification | specialization
    display_name | specialty | is_individual

data/cms/ga_provider_service.parquet   (make reference STEP=cms-utilization — reference/cms-utilization.md)
    npi | hcpcs_cd | place_of_service ('F'/'O')
    tot_benes | tot_srvcs | tot_bene_day_srvcs
    avg_mdcr_alowd_amt | provider_type | hcpcs_drug_ind | year
    ← one row per (GA NPI × HCPCS × POS) billed to Medicare Part B; the
      did_bill() evidence layer (serving/evidence.py).

data/reference/specialty_procedure_profiles.parquet  (make reference STEP=specialty-profiles — reference/specialty-profiles.md)
    specialty (NUCC classification) | hcpcs_cd
    billers | specialty_providers | prevalence
    ← Tier 2: codes billed by >= prevalence of a specialty (from CMS ∩ NPPES ∩
      NUCC). Read by evidence.code_tiers().

data/reference/mpfs_ga.parquet         (make reference STEP=mpfs — reference/mpfs.md)
    billing_code | billing_code_type ('CPT' 5-digit, else 'HCPCS')
    modifier ('' | '26' | 'TC' | …) | pos ('nonfacility' | 'facility')
    locality ('01' Atlanta | '99' rest of GA) | medicare_allowed | status
    ← CMS Physician Fee Schedule allowed $ = (workRVU·GPCIw + peRVU·GPCIpe +
      mpRVU·GPCImp) × conversionFactor, per GA locality. medicare_allowed is
      NULL for carrier-priced (status 'C') rows; bundled / non-covered statuses
      are dropped. Read by serving/benchmark.medicare_allowed() → /rates/quote's
      `medicare_allowed` + `vs_medicare`. Physician fee schedule only.
data/reference/dac_ga.parquet          (make reference STEP=doctors-clinicians — reference/doctors-clinicians.md)
    npi | last_name | first_name | credential | primary_specialty
    org_pac_id | org_name | grad_year | med_school | gender
    ← one row per NPI, from CMS Doctors & Clinicians (Care Compare). A real
      group-practice identity (org_pac_id + name) independent of Anthem's
      buckets, plus demographics. Read by labels.provider_card() /
      /providers/search. GA-scoped (npi_lookup, else State='GA').

data/reference/dac_hospital_affiliations.parquet   (make reference STEP=doctors-clinicians — reference/doctors-clinicians.md)
    npi | ccn | facility_name
    ← many rows per NPI; the CCN↔NPI bridge for the Hospital Care Compare
      quality layer (roadmap step 2). ccn =
      facility_affiliations_certification_number; facility_name is the
      facility_type label unless the source carried hosp_afl_lbn_* names.
      Scoped to the NPIs in dac_ga.parquet.
```

---

## Postgres — `honest_healthcare` (discovery queue + log only)

`localhost:5432` (`db:5432` in Docker), database `honest_healthcare`, user/password
`postgres`/`postgres`. `db/init.sql` creates a fresh volume; `db/migrations/*.sql` are
idempotent and `make migrate` applies them.

| Table | Written by | Purpose |
|---|---|---|
| `index_files` | `make discover` / `make parse` | The parse queue — one row per MRF URL. `location` (signed URL, the natural key within a month), `status` (`pending`/`processing`/`completed`/`failed`/`skipped` — [etl/queue.md](../etl/queue.md#status-lifecycle)), `file_size_bytes`, `market_types[]`, `hios_issuer_ids[]`, `plan_states[]`, `reporting_entity_*`, `created_at`, `completed_at`, `failure_reason`. GIN indexes on the array columns. |
| `index_file_plans` | `make discover` | The plan → file link — one row per `(file, plan)`, **scoped to Georgia individual-market plans** ([etl/discover.md](../etl/discover.md)): `file_id` (FK, `ON DELETE CASCADE`), `plan_id`, `plan_id_type`, `plan_name`, `market_type`, unique on `(file_id, plan_id, plan_name, market_type)`. Answers *"which files serve plan X"*; `etl parse -targets` selects the queue on it ([etl/parse.md](../etl/parse.md#target-selection)). Indexed on `plan_id` (`text_pattern_ops`, prefix lookup); `plan_name` lookups are `ILIKE '%…%'` and unindexed. |
| `billing_codes` | `make parse` | Reference upsert — `billing_code` PK, `billing_code_type`, `name`, `description`; first occurrence wins. |
| `coverage_log` | `make parse` | One row per *parsed* file (`file_id` `UNIQUE`; a re-parse upserts; a probe-`skipped` file gets none) — rate/provider row counts, new codes/NPIs/TINs, distinct networks/settings/billing-classes, `notes` (GA-filter drop counts). Nothing reads it back. |

Status lifecycle and stuck-row recovery: [etl/queue.md](../etl/queue.md). Discovery
upsert strategy: [etl/discover.md](../etl/discover.md). Test isolation (`test.*`,
`data-test/`): [testing.md](testing.md).
