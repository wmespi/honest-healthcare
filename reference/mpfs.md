# `make reference STEP=mpfs` — Medicare Physician Fee Schedule benchmark

*Read this when working on the per-code "is this rate plausible" check on the cost
card or the `medicare_allowed` / `vs_medicare` fields on `/rates/quote`.*

**Builds** `data/reference/mpfs_ga.parquet` from the CMS
[Physician Fee Schedule Relative Value Files](https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files):
one row per `(HCPCS/CPT code × modifier × facility|non-facility × Georgia locality)`
with the Medicare **allowed amount** (the price before the 80/20 split). Module
`reference/mpfs.py`; `CMS_URL=` overrides the source zip; `ARGS="--year N"` /
`--cf` set the year / conversion factor.

**Why.** Anthem's MRF prices network-administration provider groups, so there is no
honest per-provider rate. A Medicare amount per code gives a sanity check ("$180 vs
Medicare's $60" vs a multi-million-dollar drug code,
[#51](https://github.com/wmespi/honest-healthcare/issues/51)) and a fallback price.
`/rates/quote` returns `medicare_allowed` (GA non-facility) and `vs_medicare` (headline
rate ÷ that); both are `null` until this step has run.

## Formula

```
allowed = (workRVU · GPCI_work
         + peRVU   · GPCI_pe          ← non-facility or facility PE RVU
         + mpRVU   · GPCI_mp) · conversionFactor
```

Two rows per `(code, modifier, locality)`: `pos = 'nonfacility'` (office; what the
shoppable comparison uses) and `pos = 'facility'` (the *physician's* rate when done in
a hospital/ASC; the facility's own fee is a separate schedule).

| Input | Source |
|---|---|
| work / PE (fac + non-fac) / MP RVUs + status | `PPRRVU<YY>_<MON>.csv` in the quarterly RVU zip (`https://www.cms.gov/files/zip/rvu<YY><q>.zip`) |
| GPCI_work / _pe / _mp per locality | `GPCI<YYYY>.csv`, same zip |
| conversion factor | the RVU file's `CONVERSION FACTOR` column if present, else `--cf`, else `CF_BY_YEAR` in `mpfs.py`; printed every run |

Column names drift year to year, so the SELECT resolves them fuzzily.
`billing_code_type` is `'CPT'` for a 5-digit numeric code, else `'HCPCS'`.

## Status handling (PPRRVU `STATUS CODE`)

| Status | In `mpfs_ga.parquet` |
|---|---|
| `A` `R` `T` | allowed amount computed |
| `C` (carrier-priced) | kept, `medicare_allowed` **NULL** |
| `B` `N` `I` `P` `X` `E`, and all others | **dropped** (bundled / non-covered / no amount) |

A `NA` indicator on the NON-FAC / FACILITY PE RVU drops that POS row.

## Georgia localities

`01` Atlanta metro and `99` rest of state. The builder filters GPCI to Georgia and
keeps `locality`; `serving/benchmark.medicare_allowed()` takes the **median across
localities present**. For Atlanta specifically, query `locality = '01'`.

## Re-run when CMS publishes a new year

`make reference STEP=mpfs ARGS="--year <N>"`, and **check the conversion factor**:
CMS sometimes changes it mid-year or omits the column, and `CF_BY_YEAR` is a
hand-maintained fallback — verify against that year's final rule, pass `CF=` if
needed. The cache key includes the year.

## Caveats

- **Physician fee schedule only** — hospital outpatient (OPPS), ASC and inpatient
  (IPPS) facility fees are separate schedules, not modelled.
- **Medicare, not commercial** — `vs_medicare` is a ratio to reason about, not a
  target price.
- **National RVUs**; only GPCI and the conversion factor are geographic.
- No modifier -50/-51/-62 pricing rules (bilateral / multiple-procedure / co-surgeon
  reductions are not applied).

Tested by `serving/tests/test_mpfs.py` (formula, fac/non-fac split, status handling,
GA filter) against `reference/testdata/mpfs_*sample.csv`, and by
`serving/tests/test_api_contract.py::test_quote_carries_medicare_benchmark`.
