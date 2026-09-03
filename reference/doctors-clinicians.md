# `make doctors-clinicians` — real practice identity + the CCN↔NPI bridge

*Read this when working on **who a provider really practices with** (the group
name on the provider card / list) or the **hospital-affiliation** data that
unlocks the Hospital Care Compare quality layer (roadmap step 2).*

Builds two Parquet tables under `data/reference/` from the **public** CMS
**Doctors and Clinicians** dataset (Care Compare / Provider Data Catalog,
`data.cms.gov/provider-data`):

| Output | Grain | Columns |
|---|---|---|
| `dac_ga.parquet` | one row per NPI | `npi \| last_name \| first_name \| credential \| primary_specialty \| org_pac_id \| org_name \| grad_year \| med_school \| gender` |
| `dac_hospital_affiliations.parquet` | many rows per NPI | `npi \| ccn \| facility_name` |

`make doctors-clinicians` → `python3 -m reference.doctors_clinicians
--data-dir /app/data` in the serving container (`reference/doctors_clinicians.py`).
`DAC_URL=` / `AFFIL_URL=` override the sources; `--dac-file` / `--affiliations-file`
/ `--test` exist on the module.

## Why this exists (issue #62)

The MRF's `provider_references` are **network-administration buckets**, not
practices — the largest Blue Value bucket is 14.5k NPIs across ~2k TINs. We need
a practice identity that doesn't come from Anthem:

- **`org_pac_id` + `org_name`** — CMS's group-practice PAC ID and legal business
  name. A second, independent practice key alongside Anthem's `tin_value`
  (they do **not** line up 1:1 — a PAC ID is a PECOS group enrollment, a TIN is a
  billing entity).
- **`ccn`** — the Medicare certification number of each hospital a clinician is
  affiliated with. This is the **CCN↔NPI bridge**: Hospital Care Compare (star
  ratings, mortality, readmission, HCAHPS, infection rates) is keyed by CCN, and
  had no way to reach an individual clinician until this table.
- **demographics** — `grad_year` → years in practice, `med_school`, `credential`,
  `gender` for the provider card.

## Validated against the real data

| | |
|---|---|
| National Downloadable File | **839 MB** · 3.39M rows · 1.62M distinct NPIs |
| Facility Affiliation Data | **132 MB** |
| `dac_ga.parquet` | **32,411 clinicians** · 3,129 groups · 89% carry a group · grad years 1954–2026, no outliers |
| `dac_hospital_affiliations.parquet` | **54,384 rows** · 20,509 clinicians · 2,816 CCNs (all 6-digit, GA hospitals `11xxxx`) |

Output parquets are ~0.6 MB + ~0.2 MB. 32,411 of 75,058 `npi_lookup` NPIs
matched; the rest are org NPIs / non-Medicare-enrolled clinicians. ~1,700 matched
NPIs practice outside GA but hold a GA Anthem contract — the `npi_lookup` scope
keeps them where a bare `State='GA'` filter would not. Behavioral-health coverage
is thin: several GA psychiatrists / LPCs we hold rates for are absent from the
National file entirely (they don't bill Medicare) — see caveats.

## Why Python/DuckDB and not Go

The National Downloadable File is ~840 MB / ~3.4M rows. But the work is a
filter + projection + one window function (pick the primary group per NPI) —
DuckDB's parallel C++ CSV reader pushes the GA / `npi_lookup` filter down and
finishes in seconds. Not a hand-rolled streaming parse of something that can't
fit in memory (cf. NPPES at ~9 GB, which stays in Go). See
[../AGENTS.md#the-language-principle](../AGENTS.md).

## Sources

Resolved from the Provider Data Catalog metastore
(`data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items`) by dataset
title, newest CSV distribution:

- **National Downloadable File** (`DAC_NationalDownloadableFile.csv`) — one row
  per **(clinician × Medicare enrollment × group × practice address)**. A
  clinician with two group affiliations and three office addresses is six rows.
- **Facility Affiliation Data** (`Facility_Affiliation.csv`) — one row per
  **(clinician × facility)**; `facility_affiliations_certification_number` is the
  CCN, `facility_type` is `Hospital` / `Inpatient rehabilitation facility` /
  `Long-term care hospital` / … .

Hard-coded fallback URLs are in the module (CMS re-stamps the resource hash on
every monthly publish — pass `DAC_URL=` / `AFFIL_URL=` to override). The download
streams to `data/reference/.cache/` with HTTP-Range resume
(`reference/_common.py:fetch_to_cache_streaming`).

**Older single-file layout.** Some historical vintages of the National file
carried wide `hosp_afl_1..5` (CCN) + `hosp_afl_lbn_1..5` (hospital name) columns
instead of a separate affiliation file. The builder detects and unpivots those
when no separate affiliation source is available — and `hosp_afl_lbn_N` gives a
real hospital name, where `Facility_Affiliation.csv` only gives the type. The
test fixture uses this layout so `--dac-file` produces both outputs offline.

## Flattening choices

- **One group per NPI.** The National file lists a clinician once per group; we
  keep the group they appear under **most often**, breaking ties by the larger
  `num_org_mem` then by `org_pac_id`. Demographics are constant across a
  clinician's rows, so only the group pick matters. A solo clinician (no
  `org_pac_id`) is kept with `org_pac_id` / `org_name` NULL.
- **GA scope.** Prefer the exact set of NPIs in `data/anthem/npi_lookup.parquet`
  (the providers we actually hold Anthem rates for); fall back to `State = 'GA'`
  when `npi_lookup` hasn't been built yet.
- **Affiliations are scoped to `dac_ga.parquet`** — only NPIs we kept. Deduped on
  `(npi, ccn)`. `facility_name` carries the `facility_type` label from
  `Facility_Affiliation.csv` (CMS ships no proper name there); a human hospital
  name comes from a later `ccn` → CMS **Hospital General Information** join
  (roadmap step 2). From the wide-column layout, `facility_name` is the real
  `hosp_afl_lbn_N` value.

## How the serving layer uses it (`serving/labels.py`)

`dac_bits()` module-caches the two presence checks (like `nppes_cols`).
`provider_card(conn, npi)` — embedded in `/rates/quote` and
`/providers/{npi}/procedures` — gains, when the tables are built:

- `group_name` — `org_name` from `dac_ga.parquet`
- `years_in_practice` — `current_year − grad_year` (NULL when `grad_year` is
  missing or implausible)
- `hospital_affiliations` — `[{ccn, facility_name}, …]`

`/providers/search` annotates each row with `group_name` via a guarded LEFT JOIN.
Everything degrades to `None` / `[]` / omitted until `make doctors-clinicians`
runs, so the API works without it.

*Not yet wired:* `/rates/providers` still groups practice rows on Anthem's
`tin_value`. Annotating those rows (or regrouping on `org_pac_id`) is a
follow-up — it touches `serving/routers/rates.py`, which the MPFS branch also
edits.

## Caveats (also in [../docs/known-gaps.md](../docs/known-gaps.md))

- **Medicare-enrolled clinicians only** — misses pure-commercial / cash-only
  practice and most pediatric-only clinicians. An NPI absent from `dac_ga` is not
  "not a real provider".
- **CMS directory accuracy is imperfect** — stale group links, missing
  affiliations, clinicians who left a practice months ago. Treat `org_name` as a
  strong hint, not ground truth.
- **`org_pac_id` ≠ `tin_value`.** Two independent practice keys; a future PR may
  reconcile them.
- **12-month claims lookback** — a clinician with no recent Medicare claims drops
  out of the National file entirely.
- **Monthly publish** — re-run when CMS updates the dataset (the metastore
  resolution picks up the new URL automatically).

## Tests

`serving/tests/test_doctors_clinicians.py` — hermetic, runs the builder against
`reference/testdata/dac_sample.csv` (14 rows: 9 GA clinicians incl. one with two
groups + two hospitals, 2 out-of-state, 1 corrupt NPI) in test isolation. Covers
**both geo branches**: `State='GA'` (no `npi_lookup`) and `npi IN npi_lookup`
(`test_geo_scope_is_npi_lookup_not_state` — the production path, where the
National file's all-varchar NPI vs `npi_lookup`'s BIGINT caused a
real-data-only `BinderException`).
`serving/tests/test_api_contract.py` checks `provider_card` / the provider menu
carry the new fields (the `conftest.py` fixture builds tiny `dac_ga` +
`dac_hospital_affiliations` parquets). Picked up by `make check-local` and
`make test-api`.
