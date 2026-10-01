# `make reference STEP=doctors-clinicians` — real practice identity + the CCN↔NPI bridge

*Read this when working on **who a provider really practices with** (the group name on
the provider card / list) or the **hospital-affiliation** data behind the Hospital Care
Compare quality layer.*

**Builds** two tables under `data/reference/` from the public CMS **Doctors and
Clinicians** dataset (Care Compare, `data.cms.gov/provider-data`):

| Output | Grain | Columns |
|---|---|---|
| `dac_ga.parquet` | one row per NPI | `npi \| last_name \| first_name \| credential \| primary_specialty \| org_pac_id \| org_name \| grad_year \| med_school \| gender` |
| `dac_hospital_affiliations.parquet` | many rows per NPI | `npi \| ccn \| facility_name` |

Module `reference/doctors_clinicians.py`; `DAC_URL=` / `AFFIL_URL=` override the
sources (CMS re-stamps the resource hash monthly); module flags `--dac-file`,
`--affiliations-file`, `--test`.

**Why.** Anthem's `provider_references` are network-administration buckets, not
practices. This supplies an independent identity: `org_pac_id` + `org_name` (CMS's
group-practice PECOS enrollment — **not** 1:1 with Anthem's billing `tin_value`);
`ccn`, the Medicare certification number of each affiliated hospital — the
**CCN↔NPI bridge** that lets CCN-keyed Hospital Care Compare data reach a clinician;
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
- **Facility Affiliation Data** (`Facility_Affiliation.csv`) — one row per
  clinician × facility; `facility_affiliations_certification_number` is the CCN,
  `facility_type` the facility kind.

Some older vintages carried wide `hosp_afl_1..5` (CCN) + `hosp_afl_lbn_1..5` (name)
columns instead; the builder unpivots those when no separate affiliation source is
given (the test fixture uses this layout).

## Flattening choices

- **One group per NPI**: the group the clinician appears under most often, ties
  broken by larger `num_org_mem`, then `org_pac_id`. A solo clinician keeps
  `org_pac_id` / `org_name` NULL.
- **GA scope**: the NPIs in `data/anthem/npi_lookup.parquet` (providers we hold
  Anthem rates for, including GA contractors practising out of state), falling back to
  `State = 'GA'` when it isn't built.
- **Affiliations** are scoped to `dac_ga.parquet` and deduped on `(npi, ccn)`.
  `facility_name` is the `facility_type` label (CMS ships no name there) except in the
  wide layout, where it is the real hospital name; a proper name needs a later
  `ccn` → Hospital General Information join.

## How serving uses it (`serving/labels.py`)

When the tables exist, `provider_card()` (embedded in `/rates/quote` and
`/providers/{npi}/procedures`) gains `group_name`, `years_in_practice` (NULL when
`grad_year` is missing or implausible) and `hospital_affiliations`
(`[{ccn, facility_name}, …]`), and `/providers/search` annotates rows with
`group_name`. All degrade to `None` / `[]` until this step runs. `/rates/providers`
still groups on `tin_value`, not `org_pac_id`.

## Caveats (also in [../docs/known-gaps.md](../docs/known-gaps.md))

- **Medicare-enrolled clinicians only** — an NPI absent from `dac_ga` is not "not a
  real provider" (behavioral-health coverage is thin).
- **Directory accuracy is imperfect** — treat `org_name` as a strong hint.
- **`org_pac_id` ≠ `tin_value`**; they are not reconciled.
- A clinician with no recent Medicare claims drops out of the National file.
- CMS publishes monthly; re-run to pick up the new URL.

Tested by `serving/tests/test_doctors_clinicians.py` against
`reference/testdata/dac_sample.csv`, covering both geo branches (`State='GA'` and
`npi IN npi_lookup` — the production path, where the file's all-varchar NPI vs
`npi_lookup`'s BIGINT once raised a real-data-only `BinderException`), and by
`serving/tests/test_api_contract.py` for the card fields.
