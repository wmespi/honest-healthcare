# `make reference STEP=doctors-clinicians` — real practice identity

*Read this when working on **who a provider really practices with**, such as the group
name a doctor's own billing NPI takes on the find-care screen.*

**Builds** one table under `data/reference/` from the public CMS **Doctors and
Clinicians** dataset (Care Compare, `data.cms.gov/provider-data`):

| Output | Grain | Columns |
|---|---|---|
| `dac_ga.parquet` | one row per NPI | `npi \| last_name \| first_name \| credential \| primary_specialty \| org_pac_id \| org_name \| grad_year \| med_school \| gender` |

Module `reference/doctors_clinicians.py`; `--dac-url` overrides the source (CMS
re-stamps the resource hash monthly); module flags `--dac-file`, `--test`.

**Why.** Anthem's `provider_references` are network-administration buckets, not
practices. This supplies an independent identity: `org_pac_id` + `org_name` (CMS's
group-practice PECOS enrollment — **not** 1:1 with Anthem's billing `tin_value`),
and demographics (`grad_year` → years in practice, `credential`, `gender`,
`med_school`). Python/DuckDB, not Go: a filter, a projection and one window function
([language principle](../AGENTS.md#the-language-principle)).

## Sources

Resolved by title from the Provider Data Catalog metastore
(`data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items`), newest CSV, with
hard-coded fallback URLs in the module; the download resumes via HTTP Range into
`data/reference/.cache/`.

- **National Downloadable File** (`DAC_NationalDownloadableFile.csv`) — one row per
  clinician × Medicare enrollment × group × practice address.

## Flattening choices

- **One group per NPI**: the group the clinician appears under most often, ties
  broken by larger `num_org_mem`, then `org_pac_id`. A solo clinician keeps
  `org_pac_id` / `org_name` NULL.
- **GA scope**: the NPIs in `data/anthem/npi_lookup.parquet` (providers we hold
  Anthem rates for, including GA contractors practising out of state), falling back to
  `State = 'GA'` when it isn't built.

## How it reaches serving

The find-care build names a doctor's own billing NPI after its CMS group
([build.md](../build/build.md#the-find-care-tables)).

## Caveats (also in [../docs/known-gaps.md](../docs/known-gaps.md))

- **Medicare-enrolled clinicians only** — an NPI absent from `dac_ga` is not "not a
  real provider" (behavioral-health coverage is thin).
- **Directory accuracy is imperfect** — treat `org_name` as a strong hint.
- **`org_pac_id` ≠ `tin_value`**; they are not reconciled.
- A clinician with no recent Medicare claims drops out of the National file.
- CMS publishes monthly; re-run to pick up the new URL.

Tested by `reference/tests/test_doctors_clinicians.py` against
`reference/testdata/dac_sample.csv`, covering both geo branches (`State='GA'` and
`npi IN npi_lookup` — the production path, where the file's all-varchar NPI vs
`npi_lookup`'s BIGINT once raised a real-data-only `BinderException`), and by
`build/tests/tables/test_places.py` for a doctor's own NPI named after its CMS group.
