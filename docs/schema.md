# Storage schema — Parquet + Postgres

*Read this when writing a query against the data or changing what the parser
writes. This is the single source of truth for the layout — Parquet and Postgres
both (`db/SCHEMA.md` is a thin pointer here).*

The serving layer reads **Parquet**. Postgres holds only the discovery queue and two
small reference/log tables.

`make data-size` prints the current inventory — rows + on-disk bytes per Parquet
table (with an `anthem/prices` breakdown by network partition) and per Postgres
table. Use it to track the GA corpus toward the ~20 GB target and to spot
`index_files` bloat (parse status churn — `VACUUM (FULL)` reclaims it).

**Why Parquet + DuckDB and not Postgres.** The workload is one sequential bulk
writer (the ETL) and read-only analytical queries (the API) — the opposite of
OLTP. Writing one Blue Value Georgia file to Postgres took ~2 hours; streaming it
to Parquet takes ~7 minutes (the gap is WAL, index maintenance, and row-store
overhead). Parquet's columnar ZSTD is ~10–20× smaller for repetitive rate data,
DuckDB scans only the columns a query needs, and neither the ETL nor the API needs
a server process.

---

## Parquet — `data/anthem/` (what the serving layer reads)

```
prices/net=<slug>/{id}.parquet
    file_id | group_set_id | network_name
    billing_code_type | billing_code | negotiation_arrangement
    negotiated_type | negotiated_rate | expiration_date
    service_code | billing_class | modifier | setting
```
One row per **(network × negotiated price)** — NOT fanned out per provider group.
Hive-partitioned by `network_name` (slug = `etl/extraction/partition.go:slugifyNetwork` ==
serving `network_slug()`); a network-filtered query adds `net = ?` and DuckDB
prunes to the one directory. Join to `group_sets` on `(file_id, group_set_id)` to
expand a price to its provider groups.

- `service_code` — sorted `|`-joined place-of-service array.
- `modifier` — sorted `|`-joined `billing_code_modifier` array: `"26"` = professional
  / physician work, `"TC"` = technical / equipment + facility, `""` = global (~89%
  of rows). `(billing_code, modifier, service_code, setting)` is what pins a rate
  for a patient.

```
group_sets/{id}.parquet     file_id | group_set_id | provider_group_id
```
Deduplicated provider-group rosters. `group_set_id` = FNV-64a of a block's sorted
`provider_reference` ids (`etl/extraction/stream.go:hashGroupSet`); written once per distinct
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

### `summary/` — precomputed browse layer (`make build-summary`)

```
summary/rate_hist.parquet
    payer | net | network_name | billing_code_type | billing_code | setting
      | scope | bucket | n
summary/rate_summary.parquet
    payer | network_name | net | billing_code_type | billing_code | setting
      | n_rates | min_rate | max_rate | avg_rate
summary/code_rollup.parquet
    payer | billing_code_type | billing_code | n_provider_groups | n_rates
```

`scripts/build_rate_summary.py` rebuilds all three from `prices` (+ a
pre-aggregated per-roster size table for `code_rollup`) after a parse batch:

- **`rate_hist`** — a pre-bucketed rate histogram: `$25` buckets to `$5000`,
  then one overflow bucket at `5000`. The one heavy scan of `prices`, all-scalar
  `COUNT`. The network overview's bars *are* this table, and the serving layer
  derives p10/median/p90 from its CDF at read time (a code spans ~20-200 buckets
  — trivial). Exact per-group percentiles at build time OOM: millions of groups
  × any non-scalar accumulator, t-digest included.
  `scope` is `'outpatient_prof'` for outpatient professional fee-for-service
  dollar rates (`serving/data_sources.outpatient_scope` — the slice the consumer
  rate views compare), `'other'` otherwise. The no-code `/rates/distribution`
  overview filters to `'outpatient_prof'`; the two rollups below sum across both.
- **`rate_summary`** — scalar rollup of `rate_hist`, one row per priced
  `(network, code, setting)`; `min/max/avg` are bucket-approximate (± `$25`).
  `/networks` sums `n_rates` **across every scope** (it answers "what's priced
  here", not "what does an outpatient visit cost"). **No all-settings (`'*'`)
  rollup row** — every consumer that sums `n_rates` would double-count it; roll
  settings up at read time.
- **`code_rollup`** — `n_provider_groups` is SUM of the code's rosters' sizes, a
  ranking hint that over-counts a group in several of a code's rosters (same as
  the old `VOL_CTE`), computed against the roster-size table so it stays bounded
  at 1e9+ edges. `n_rates` is exact.

The `/networks`, `/billing_codes`, `/procedure_categories` and the no-code
`/rates/distribution` (network overview) endpoints read these instead of
scanning `prices` / `prices ⨝ group_sets` (645M rows / >1e9 edges at GA scale —
[issue #10](https://github.com/wmespi/honest-healthcare/issues/10)); they fall
back to the live scan when the files are absent. `payer` is `'anthem'` today;
the column is there for multi-payer. Build cost: ~3 min at 645M price rows /
1.1B roster edges (`rate_hist` ~80 s, the rest scalar).

`{id}` is `index_files.id`; `file_id` carries it on every row. `provider_group_id`
is the MRF's **file-local** `provider_reference.id` — all cross-file joins key on
`(file_id, provider_group_id)`.

### Why the split

The MRF lists every participating provider group under nearly every billing code,
so a flat layout fans out to one row per `(code × price × group × network)` — file
28947 alone was 723M rows. `prices` + `group_sets` stores each roster once: file
21057 went 682k → 76k price rows + 2.8k roster edges (~9×), and the ratio grows
with file size. `PRICE_GROUPS_SRC` in the serving layer re-joins them.

### `network_name`

The real, structured network label for a price — one member of the
`provider_references[].network_name` array (e.g. `"GA Blue Value HIX Individual
Network"`), one value per row, equal to its `net` partition. **The reliable filter
for the target plan.** A provider group in two networks lands in both partitions.
There is no `plan_name` — `/plans` returns `[]`.

---

## Parquet — `data/nppes/`, `data/reference/`, `data/cms/`

```
data/nppes/ga_providers.parquet
    npi | entity_type | org_name | last_name | first_name
    taxonomy_code | taxonomy_group | is_hospital | is_clinic
    address_line1 | address_line2 | city | state | postal_code
    ← join taxonomy_code to nucc_taxonomy.parquet for the real specialty label

data/reference/code_labels.parquet     (make code-labels — reference/code-labels.md)
    billing_code_type | billing_code | short_name
    rbcs_category | rbcs_subcategory | rbcs_family | rbcs_is_major | label | search_text

data/reference/nucc_taxonomy.parquet   (make taxonomy-labels — reference/taxonomy-labels.md)
    taxonomy_code | grouping | classification | specialization
    display_name | specialty | is_individual

data/cms/ga_provider_service.parquet   (make cms-utilization — reference/cms-utilization.md)
    npi | hcpcs_cd | place_of_service ('F'/'O')
    tot_benes | tot_srvcs | tot_bene_day_srvcs
    avg_mdcr_alowd_amt | provider_type | hcpcs_drug_ind | year
    ← one row per (GA NPI × HCPCS × POS) billed to Medicare Part B; the
      did_bill() evidence layer (serving/evidence.py). ~284k rows / ~34k NPIs.

data/reference/specialty_procedure_profiles.parquet  (make specialty-profiles — reference/specialty-profiles.md)
    specialty (NUCC classification) | hcpcs_cd
    billers | specialty_providers | prevalence
    ← Tier 2: codes billed by >= prevalence of a specialty (from CMS ∩ NPPES ∩
      NUCC). ~5.8k rules / ~51 specialties. Read by evidence.code_tiers().

data/reference/mpfs_ga.parquet         (make mpfs — reference/mpfs.md)
    billing_code | billing_code_type ('CPT' 5-digit, else 'HCPCS')
    modifier ('' | '26' | 'TC' | …) | pos ('nonfacility' | 'facility')
    locality ('01' Atlanta | '99' rest of GA) | medicare_allowed | status
    ← CMS Physician Fee Schedule allowed $ = (workRVU·GPCIw + peRVU·GPCIpe +
      mpRVU·GPCImp) × conversionFactor, per GA locality. medicare_allowed is
      NULL for carrier-priced (status 'C') rows; bundled / non-covered statuses
      are dropped. Read by serving/benchmark.medicare_allowed() → /rates/quote's
      `medicare_allowed` + `vs_medicare`. Physician fee schedule only — facility
      fees (OPPS/ASC/IPPS) are separate schedules, not modelled.
data/reference/dac_ga.parquet          (make doctors-clinicians — reference/doctors-clinicians.md)
    npi | last_name | first_name | credential | primary_specialty
    org_pac_id | org_name | grad_year | med_school | gender
    ← one row per NPI, from CMS Doctors & Clinicians (Care Compare). A real
      group-practice identity (org_pac_id + name) independent of Anthem's
      buckets, plus demographics. Read by labels.provider_card() /
      /providers/search. GA-scoped (npi_lookup, else State='GA').

data/reference/dac_hospital_affiliations.parquet   (make doctors-clinicians — reference/doctors-clinicians.md)
    npi | ccn | facility_name
    ← many rows per NPI; the CCN↔NPI bridge for the Hospital Care Compare
      quality layer (roadmap step 2). ccn =
      facility_affiliations_certification_number; facility_name is the
      facility_type label unless the source carried hosp_afl_lbn_* names.
      Scoped to the NPIs in dac_ga.parquet.
```

---

## Postgres — `honest_healthcare` (discovery + reference only)

```
Host:  localhost:5432  (db:5432 inside Docker)   Database: honest_healthcare
User / password: postgres / postgres
```

| Table | Written by | Purpose |
|---|---|---|
| `index_files` | `make discover` / `make parse` | The parse queue — one row per MRF URL. `location` (signed URL, the natural key within a month), `status` (`pending`/`processing`/`completed`/`failed`), `file_size_bytes`, `market_types[]`, `hios_issuer_ids[]`, `plan_states[]`, per-file `reporting_entity_*`, `created_at`, `completed_at`, `failure_reason`. GIN indexes on the array columns. |
| `index_file_plans` | `make discover` | The plan → file link — one row per `(file, plan)` the master index publishes: `file_id` (FK, `ON DELETE CASCADE`), `plan_id`, `plan_id_type`, `plan_name`, `market_type`, unique on `(file_id, plan_id, plan_name, market_type)`. This is what answers *"which files serve plan X"* and what `etl parse -targets` selects the queue on ([../etl/parse.md](../etl/parse.md#target-selection)). Indexed on `plan_id` and `lower(plan_name)` with `text_pattern_ops` for prefix / case-insensitive lookup. |
| `billing_codes` | `make parse` | Reference upsert — `billing_code` PK, `billing_code_type`, `name`, `description`. `ON CONFLICT DO NOTHING` (first occurrence wins). |
| `coverage_log` | `make parse` | One row per parsed file (a re-parse replaces it) — rate/provider row counts, new codes/NPIs/TINs, distinct networks/settings/billing-classes, `notes` (GA-filter drop counts). The ETL never reads it; `make cov-report` flags partial-looking `completed` files from it (#52). |

Status lifecycle and stuck-row recovery: [../etl/queue.md](../etl/queue.md).
Discovery upsert strategy: [../etl/discover.md](../etl/discover.md).

`db/migrations/*.sql` holds idempotent migrations for a running DB (`init.sql`
only runs on a fresh volume); `make migrate` applies them. The pre-Parquet
legacy tables (`negotiated_rates`, `provider_mappings`,
`place_of_service_codes`, `vw_rates_detailed`) were dropped by
`002_drop_legacy_tables.sql` and no longer exist in `init.sql` either.
`003_index_file_plans.sql` adds `index_file_plans` and drops
`index_files.plan_names TEXT[]` (+ its `idx_index_files_plan` GIN index) — the
per-file array reserved for plan attribution and never populated, superseded by
the relational form, which carries `plan_id` / `market_type` too and does not
have to hold the plans × files cross-product in memory.

Test isolation: `-test` / `make … TEST=1` writes `test.*` (same database,
`search_path=test` via `TEST_DATABASE_URL`) and `data-test/`; both are safe to
truncate. The `test` schema drifts when a `public` column is added — `make
migrate` recreates it. See [testing.md](testing.md).
