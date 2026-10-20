# `make reference STEP=cms-utilization` — provider ↔ procedure evidence

*Read this when working on "does this provider actually perform this procedure" —
the caveat on the cost card and the badges on the provider menu.*

**Builds** `data/cms/ga_provider_service.parquet` from CMS
["Medicare Physician & Other Practitioners — by Provider and Service"](https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-provider-and-service):
one row per `NPI × HCPCS × place-of-service` billed to Medicare Part B, Georgia
rendering providers only. Module `reference/cms_utilization.py`; `CMS_URL=` overrides
the source, `YEAR=` the stamped service year (via `ARGS`, e.g. `ARGS="--year 2024"`).

**Why.** Anthem's `provider_references` are network-administration buckets (one group
can span thousands of NPIs and many specialties), so the rate resolver would show a
social worker a surgical rate. `plausibility()` (`serving/labels.py`) is a hand-coded
guess; this dataset replaces it with evidence: `did_bill(npi, code)`.

**Python/DuckDB, not Go** ([language principle](../AGENTS.md#the-language-principle)):
the work is a filter + projection over a quoted CSV that DuckDB's parallel reader
handles in seconds, not a stream too big for memory.

**Source resolution.** `resolve_cms_url()` reads `data.cms.gov/data.json`, picks the
newest `*_Prov_Svc.csv` (year from `_D<YY>_` in the filename), with a hard-coded
fallback URL. The download resumes via HTTP Range into
`data/cms/.cache/prov_svc_d<year>.csv` (year in the name, so a new year never reuses
a stale CSV).

## Output

```
npi | hcpcs_cd | place_of_service     ('F' facility / 'O' office)
tot_benes | tot_srvcs | tot_bene_day_srvcs
avg_mdcr_alowd_amt        avg Medicare allowed $ — a cross-check vs the MRF rate
provider_type             Medicare's rendering-provider specialty
hcpcs_drug_ind            'Y' = Part B drug / J-code
year                      service year
```

F and O stay separate rows; `did_bill()` aggregates over them.

## How serving uses it (`serving/evidence.py`)

- `did_bill(conn, npi, code)` → `None` (file not built) · `{"billed": False}` ·
  `{"billed": True, year, tot_srvcs, tot_benes, avg_mdcr_allowed, …}`. `/rates/quote`
  returns it as `medicare_utilization` and demotes `plausibility` from `"unlikely"`
  when the provider demonstrably bills the code.
- `billed_codes(conn, npi, codes)` → billed subset, badging the
  `/providers/{npi}/procedures` menu.
- `medicare_specialty(conn, npi)` → CMS's specialty label, folded into
  `plausibility()`; a fallback only (null for providers with no Part B claims).
- `typical_codes()` / `code_tiers()` → Tier 2: each `(npi, code)` is `billed` (Tier 1)
  / `typical` (billed by ≥ threshold of the NPI's specialty —
  [specialty-profiles.md](specialty-profiles.md)) / `group` (fan-out noise).
  `/providers/{npi}/procedures?tier=plausible` (default) keeps billed + typical.

All no-op to `None`/`{}` until this step has run, so the API works without it.

## Caveats (also in [../docs/known-gaps.md](../docs/known-gaps.md))

- **Part B only** — misses pediatric, pure-commercial, cash-only. `billed: True` is
  strong; `billed: False` is weak.
- Records from **≤ 10 beneficiaries are excluded** from the source.
- **~2-year lag**, single year.
- Facility / organizational NPIs are mostly absent — a **practitioner** signal.
- Drug codes are included; `hcpcs_drug_ind` lets the UI filter them.

Tested by `serving/tests/test_cms_utilization.py` against `reference/testdata/cms_sample.csv`.
