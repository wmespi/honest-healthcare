# `make reference STEP=nppes` - NPPES Georgia provider subset

*Read this when working on the provider-identity reference data.*

Streams the CMS NPPES national dissemination zip in a single pass and writes its **Georgia-only** rows under `data/ingestion/nppes/`: the providers, from the multi-GB `npidata` CSV, and their secondary practice locations, from the `pl_pfile` CSV beside it.
`make reference STEP=nppes` → `etl nppes` (package `ingestion/etl/nppes`).

**In Go, not Python, by design** ([language principle](../../AGENTS.md#the-language-principle)):
it's a single-pass stream over a source too large to materialize - exactly what the
Go layer exists for. It writes a dimension table but the *work* is streaming
acquisition, not relational reshaping.

With no `URL`, it downloads this month's file, or last month's until this month's is out.

| Variable | Effect |
|---|---|
| `URL="…_V3.zip"` | override the monthly URL - CMS re-cuts with `_V<n>` suffixes (`NPPES_Data_Dissemination_<Month>_<Year>_V2.zip`) |
| `FILE="local.zip"` | use a local zip (or plain CSV) - skips the re-download every run |

## Output - `data/ingestion/nppes/ga_providers.parquet`

One row per NPI whose practice location is in Georgia.

```
npi | entity_type | org_name | last_name | first_name
taxonomy_code | taxonomy_group | is_hospital | is_clinic
address_line1 | address_line2 | city | state | postal_code | phone
```

- `is_hospital` = taxonomy prefix `28x`; `is_clinic` = `261Q` (`classifyTaxonomy`).
- `taxonomy_group` is a coarse bucket - join `taxonomy_code` to
  `nucc_taxonomy.parquet` for the real specialty label
  ([../reference/taxonomy-labels.md](../reference/taxonomy-labels.md)).
- `phone` is the practice location's number as NPPES has it.
- Consumed by the GA NPI filter in [parse.md](parse.md) and by the find-care build ([build.md](../../integration/build.md#the-find-care-tables)).

## Output - `data/ingestion/nppes/ga_locations.parquet`

One row per secondary practice location in Georgia: the other addresses a provider practices at, beside the one on its own record.

```
npi | address_line1 | address_line2 | city | state | postal_code | phone
```

- It keeps a Georgia location whatever state the NPI's own record is in.
- The find-care build makes each one of a biller's places.

## Dev loop

- **`make test`** runs the extract hermetically over `ingestion/etl/nppes/testdata/nppes_sample.csv` and `pl_sample.csv` with teardown, so column mapping, taxonomy classification and the Georgia filter never touch the full file.
- Each file is written beside its final name and moved into place when complete, so a reader never sees a half-written one.
