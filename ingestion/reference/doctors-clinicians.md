# `make reference STEP=doctors-clinicians` - real practice identity

*Read this when working on **who a provider really practices with**, such as the group
name a doctor's own billing NPI takes on the find-care screen.*

**Builds** two tables under `data/ingestion/reference/` from the public CMS **Doctors and
Clinicians** dataset (Care Compare, `data.cms.gov/provider-data`):

| Output | Grain | Columns |
|---|---|---|
| `dac_ga.parquet` | one row per NPI | `npi \| last_name \| first_name \| credential \| primary_specialty \| org_pac_id \| org_name \| grad_year \| med_school \| gender` |
| `dac_locations_ga.parquet` | one row per NPI and Georgia address it practices at | `npi \| address_line1 \| address_line2 \| city \| state \| postal_code \| phone` |

Module `ingestion/reference/doctors_clinicians.py`; `--dac-url` overrides the source (CMS
re-stamps the resource hash monthly); module flags `--dac-file`, `--test`.

**Why.** Anthem's `provider_references` are network-administration buckets, not
practices. This supplies an independent identity: `org_pac_id` + `org_name` (CMS's
group-practice PECOS enrollment — **not** 1:1 with Anthem's billing `tin_value`),
and demographics (`grad_year` → years in practice, `credential`, `gender`,
`med_school`). Python/DuckDB, not Go: a filter, a projection and one window function
([language principle](../../AGENTS.md#the-language-principle)).

## Sources

Resolved by title from the Provider Data Catalog metastore
(`data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items`), newest CSV, with
hard-coded fallback URLs in the module; the download resumes via HTTP Range into
`data/raw/downloads/`.

- **National Downloadable File** (`DAC_NationalDownloadableFile.csv`) — one row per
  clinician × Medicare enrollment × group × practice address.

## Flattening choices

- **One group per NPI**: the group the clinician appears under most often, ties
  broken by larger `num_org_mem`, then `org_pac_id`. A solo clinician keeps
  `org_pac_id` / `org_name` NULL.
- **Every Georgia address**: `dac_locations_ga.parquet` keeps each Georgia address a clinician in scope is listed at, once, whichever of its groups lists it there.
- **GA scope**: the NPIs on the rosters of every file parse has written,
  `data/raw/anthem/providers/*.parquet` (providers we hold Anthem rates for, including
  GA contractors practising out of state), falling back to `State = 'GA'` before any
  file is parsed.

## How it reaches serving

The find-care build names a doctor's own billing NPI after its CMS group, and lists a clinician at a biller's place only when CMS lists them practicing in that building
([build.md](../../integration/build.md#the-find-care-tables)).

## Caveats (also in [../docs/known-gaps.md](../../docs/known-gaps.md))

- **Medicare-enrolled clinicians only** — an NPI absent from `dac_ga` is not "not a
  real provider" (behavioral-health coverage is thin).
- **Directory accuracy is imperfect** — treat `org_name` as a strong hint.
- **`org_pac_id` ≠ `tin_value`**; they are not reconciled.
- A clinician with no recent Medicare claims drops out of the National file.
- CMS publishes monthly; re-run to pick up the new URL.

Tested by `ingestion/reference/tests/test_doctors_clinicians.py` against
`ingestion/reference/testdata/dac_sample.csv`, covering both geo branches (`State='GA'` and
the parsed rosters, with two files as two runs of parse leave them — the production
path, where the file's all-varchar NPI vs the rosters' BIGINT once raised a
real-data-only `BinderException`), and by
`integration/tests/tables/test_places.py` for a doctor's own NPI named after its CMS group
and `integration/tests/inputs/test_service_sites.py` for where a clinician is listed.
