# `make cms-utilization` — provider ↔ procedure evidence

*Read this when working on the "does this provider actually perform this
procedure" question — the caveat on the cost card (job 1) and the badges on the
provider menu (job 4).*

Builds `data/cms/ga_provider_service.parquet` from **public data only**: CMS
["Medicare Physician & Other Practitioners — by Provider and
Service"](https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-provider-and-service).
One row per `NPI × HCPCS × place-of-service` **actually billed to Medicare Part
B**, filtered to Georgia rendering providers.

`make cms-utilization` → `python3 -m reference.cms_utilization --data-dir /app/data`
in the serving container (`reference/cms_utilization.py`). `CMS_URL=` overrides
the source; `YEAR=` overrides the stamped service year; `--cms-file` / `--test`
exist on the module.

## Why this exists

The MRF is a **rate sheet**. Anthem's `provider_references` are coarse
network-administration buckets — one group can be ~7,000 NPIs across ~4,000 TINs
and many specialties. So the rate resolver will happily show a clinical social
worker a $14k surgical rate: the code is contracted to her *group*, not to her.

`plausibility()` in `serving/labels.py` is a hand-coded heuristic (behavioral
provider + procedural code → "unlikely"). It's a guess and wrong at the edges
(NPs/PAs have broad scopes). This dataset replaces the guess with evidence for
the Part B population: `did_bill(npi, code)` in `serving/evidence.py`.

## Why Python/DuckDB and not Go

The source CSV is ~3.25 GB (~9.8M rows, 28 columns, quoted free-text fields).
That *sounds* like Go territory, but the work is a **filter + projection**, not a
streaming parse of something that can't fit in memory (cf. NPPES at ~9 GB, which
stays in Go). DuckDB's parallel, vectorized C++ CSV reader scans all cores,
pushes the `state = 'GA'` filter down, handles the quoting correctly, and streams
to Parquet out-of-core — **~4 seconds** on a dev Mac, versus minutes for a
single-threaded Go `encoding/csv` pass and hundreds of lines more code. This is
the [language principle](../AGENTS.md#the-language-principle) working as intended.

## Source resolution

`resolve_cms_url()` reads `data.cms.gov/data.json`, finds the dataset by title,
and picks the newest `*_Prov_Svc.csv` distribution (year parsed from the
`_D<YY>_` in the filename). Hard-coded fallback URL is the 2024 service year
(published 2026-05). CMS re-stamps the path and bumps `D<YY>` annually.

The download streams to `data/cms/.cache/prov_svc_d<year>.csv` with resume via
HTTP Range (`reference/_common.py:fetch_to_cache_streaming`). Year is in the
cache name so next year's run doesn't reuse a stale CSV.

## Output — `data/cms/ga_provider_service.parquet`

```
npi | hcpcs_cd | place_of_service     ('F' facility / 'O' office/non-facility)
tot_benes | tot_srvcs | tot_bene_day_srvcs
avg_mdcr_alowd_amt        avg Medicare allowed $ — a cross-check vs the MRF rate
provider_type             Medicare's rendering-provider specialty
hcpcs_drug_ind            'Y' = Part B drug / J-code
year                      service year
```

~284k GA rows / ~34k NPIs / ~3k HCPCS codes (2024). F and O are kept as separate
rows — `did_bill()` aggregates over them.

## How the serving layer uses it (`serving/evidence.py`)

- `did_bill(conn, npi, code)` → `None` (file not built) · `{"billed": False}` ·
  `{"billed": True, year, tot_srvcs, tot_benes, avg_mdcr_allowed, …}`.
  `/rates/quote` returns this as `medicare_utilization`, and demotes
  `plausibility` from `"unlikely"` when the provider demonstrably bills the code.
- `billed_codes(conn, npi, codes)` → `{code: {tot_srvcs, tot_benes, year}}` for
  the billed subset — one query, badges the `/providers/{npi}/procedures` menu
  (`medicare` field per row).
- `medicare_specialty(conn, npi)` → CMS's rendering-provider specialty label,
  folded into `plausibility()` (`serving/labels.py`) — usually cleaner than a
  stale / vague self-reported NUCC taxonomy. Fallback only: it's null for the
  ~86% of GA providers with no Part B claims, so it can't replace NUCC/NPPES as
  the primary specialty source.

All three no-op to `None`/`{}` until `make cms-utilization` has run, so the API
works without it.

The frontend (`ProviderCostCard`, `ProviderMenu`) surfaces this: a
"billed N times to Medicare in <year>" line on the cost card (or "no Part B
claims either" when the group-rate caveat is showing), and a "Medicare" badge on
billed menu rows.

## Caveats (also in [docs/known-gaps.md](../docs/known-gaps.md))

- **Medicare Part B only** — misses pediatric, pure-commercial, and cash-only
  practice. `billed: True` is strong; `billed: False` is weak.
- Records derived from **≤ 10 beneficiaries are excluded entirely** from the
  source file — another reason absence proves little.
- **~2-year lag** — 2024 is the latest available in 2026.
- Type-2 (facility / organizational) NPIs are mostly absent — this is a
  **practitioner** signal.
- Drug codes (J-codes) are included; `hcpcs_drug_ind` lets the UI filter them.

## Tests

`serving/tests/test_cms_utilization.py` — hermetic, runs the builder against
`reference/testdata/cms_sample.csv` (15 rows: 12 GA + 1 FL + 1 TX + 1
corrupt-NPI) in test isolation. Picked up by `make test-api`.
