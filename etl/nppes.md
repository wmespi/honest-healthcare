# `make nppes` — NPPES Georgia provider subset

*Read this when working on the provider-identity reference data.*

Streams the CMS NPPES national dissemination file (~9 GB CSV in a zip) in a single
pass and writes the **Georgia-only** subset to `data/nppes/ga_providers.parquet`.
`make nppes` → `etl nppes` (package `etl/nppes`).

**In Go, not Python, by design** ([language principle](../AGENTS.md#the-language-principle)):
it's a single-pass stream over a source too large to materialize — exactly what the
Go layer exists for. It writes a dimension table but the *work* is streaming
acquisition, not relational reshaping.

| Variable | Effect |
|---|---|
| `URL="…_V3.zip"` | override the monthly URL — CMS re-cuts with `_V<n>` suffixes (`NPPES_Data_Dissemination_<Month>_<Year>_V2.zip`) |
| `FILE="local.zip"` | use a local zip (or plain CSV) — skips the ~1 GB re-download every run |

## Output — `data/nppes/ga_providers.parquet`

```
npi | entity_type | org_name | last_name | first_name
taxonomy_code | taxonomy_group | is_hospital | is_clinic
address_line1 | address_line2 | city | state | postal_code
```

- `is_hospital` = taxonomy prefix `28x`; `is_clinic` = `261Q` (`classifyTaxonomy`).
- `taxonomy_group` is a coarse bucket — join `taxonomy_code` to
  `nucc_taxonomy.parquet` for the real specialty label
  ([../reference/taxonomy-labels.md](../reference/taxonomy-labels.md)).
- Consumed by the GA NPI filter in [parse.md](parse.md) and by the serving layer's
  provider cards.

## Dev loop

- **`make test-e2e`** runs `extractNPPESGeorgia` hermetically over the 14-row
  `testdata/nppes_sample.csv` fixture with teardown — column mapping and taxonomy
  classification should never touch the 9 GB file.
- The write to `ga_providers.parquet` is **not atomic** — during a re-extract the
  file is briefly 0 bytes and serving-layer queries that touch it 500. Run `make nppes`
  when the API is idle, or expect transient errors. See
  [../docs/known-gaps.md](../docs/known-gaps.md).
