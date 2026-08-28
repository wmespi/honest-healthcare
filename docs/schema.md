# Storage schema — Parquet + Postgres

*Read this when writing a query against the data or changing what the parser
writes. This is the single source of truth for the on-disk layout; `db/SCHEMA.md`
and `db/DUCKDB_DESIGN.md` predate the Parquet migration.*

The backend reads **Parquet**. Postgres holds only the discovery queue and two
small reference/log tables.

---

## Parquet — `data/anthem/` (what the backend reads)

```
prices/net=<slug>/{id}.parquet
    file_id | group_set_id | network_name
    billing_code_type | billing_code | negotiation_arrangement
    negotiated_type | negotiated_rate | expiration_date
    service_code | billing_class | modifier | setting
```
One row per **(network × negotiated price)** — NOT fanned out per provider group.
Hive-partitioned by `network_name` (slug = `etl-go/partition.go:slugifyNetwork` ==
backend `network_slug()`); a network-filtered query adds `net = ?` and DuckDB
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
`provider_reference` ids (`etl-go/mrf.go:hashGroupSet`); written once per distinct
roster per file. `prices ⨝ group_sets` reproduces every original
`(code, rate, provider_group)` tuple exactly.

```
providers/{id}.parquet   file_id | provider_group_id | network_name | npi | tin_type | tin_value
codes/{id}.parquet       billing_code_type | billing_code | name | description
npi_lookup.parquet       npi | tin_value
```

`{id}` is `index_files.id`; `file_id` carries it on every row. `provider_group_id`
is the MRF's **file-local** `provider_reference.id` — all cross-file joins key on
`(file_id, provider_group_id)`.

### Why the split

The MRF lists every participating provider group under nearly every billing code,
so a flat layout fans out to one row per `(code × price × group × network)` — file
28947 alone was 723M rows. `prices` + `group_sets` stores each roster once: file
21057 went 682k → 76k price rows + 2.8k roster edges (~9×), and the ratio grows
with file size. `PRICE_GROUPS_SRC` in the backend re-joins them.

### `network_name`

The real, structured network label for a price — one member of the
`provider_references[].network_name` array (e.g. `"GA Blue Value HIX Individual
Network"`), one value per row, equal to its `net` partition. **The reliable filter
for the target plan.** A provider group in two networks lands in both partitions.
There is no `plan_name` — `/plans` returns `[]`.

---

## Parquet — `data/nppes/` and `data/reference/`

```
data/nppes/ga_providers.parquet
    npi | entity_type | org_name | last_name | first_name
    taxonomy_code | taxonomy_group | is_hospital | is_clinic
    address_line1 | address_line2 | city | state | postal_code
    ← join taxonomy_code to nucc_taxonomy.parquet for the real specialty label

data/reference/code_labels.parquet     (make code-labels — scripts/code-labels.md)
    billing_code_type | billing_code | short_name
    rbcs_category | rbcs_subcategory | rbcs_family | rbcs_is_major | label | search_text

data/reference/nucc_taxonomy.parquet   (make taxonomy-labels — scripts/taxonomy-labels.md)
    taxonomy_code | grouping | classification | specialization
    display_name | specialty | is_individual
```

---

## Postgres — `honest_healthcare` (discovery + reference only)

| Table | Written by | Purpose |
|---|---|---|
| `index_files` | `make discover` / `make parse` | The parse queue — URLs, `status`, `file_size_bytes`, `market_types`, `hios_issuer_ids`, `plan_states`, per-file `reporting_entity_*`, `completed_at` |
| `billing_codes` | `make parse` | Reference upsert — `billing_code` PK, `name`, `description` |
| `coverage_log` | `make parse` | One observational row per parsed file (row counts, new codes/NPIs/TINs, distinct networks/settings) — never read by the ETL |

`negotiated_rates`, `provider_mappings`, `place_of_service_codes`, and the
`vw_rates_detailed` view still exist in `db/init.sql` but are **neither written nor
read** — legacy until re-adopted.

Test isolation: `-test` / `make … TEST=1` writes `test.*` and `data-test/`; both
are safe to truncate. The `test` schema drifts when a `public` column is added —
`make migrate` recreates it. See [testing.md](testing.md).
