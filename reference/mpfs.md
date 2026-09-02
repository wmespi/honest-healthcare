# `make mpfs` — Medicare Physician Fee Schedule benchmark

*Read this when working on the per-code "is this rate plausible" check on the
cost card (job 1) or the `medicare_allowed` / `vs_medicare` fields on
`/rates/quote`.*

Builds `data/reference/mpfs_ga.parquet` from **public CMS data only**: the
[Physician Fee Schedule Relative Value Files](https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files).
One row per `(HCPCS/CPT code × modifier × facility|non-facility × Georgia
locality)` with the Medicare **allowed amount** — the fee schedule's price
before the 80/20 split.

`make mpfs` → `python3 -m reference.mpfs --data-dir /app/data` in the serving
container (`reference/mpfs.py`). `CMS_URL=` overrides the source zip, `YEAR=`
the stamped calendar year, `CF=` the conversion factor. `--rvu-file` /
`--gpci-file` / `--test` exist on the module.

## Why this exists (issue #61)

Anthem's MRF prices ~70 network-administration **provider groups**, not
providers. For most provider drill-downs there is no honest per-provider rate —
the code is contracted to the group. A Medicare allowed amount for every code
gives two things the MRF can't:

- **a sanity check** — "$180 vs Medicare's $60 = plausible commercial markup"
  vs. the $4.4M drug-code garbage ([#51](https://github.com/wmespi/honest-healthcare/issues/51)).
- **a fallback price** — when the MRF rate is group-rate noise: "Medicare allows
  $X here; GA commercial typically runs ~1.3–1.8× that".

`/rates/quote` returns `medicare_allowed` (the GA non-facility allowed amount)
and `vs_medicare` (headline rate ÷ that). Both are `null` until this build runs
— the endpoint never breaks on a missing file.

## The formula

For each `(code, modifier)` in the RVU file and each Georgia locality:

```
allowed = (workRVU · GPCI_work
         + peRVU   · GPCI_pe          ← non-facility PE RVU or facility PE RVU
         + mpRVU   · GPCI_mp) · conversionFactor
```

Two rows are written per `(code, modifier, locality)` — `pos = 'nonfacility'`
(the RVU file's non-facility PE RVU — office / freestanding) and
`pos = 'facility'` (the facility PE RVU — the physician's rate when the service
is done in a hospital/ASC; the *facility's* own fee is separate, see caveats).
The shoppable comparison uses `nonfacility`.

### Inputs

| Input | Source | Notes |
|---|---|---|
| work / PE (fac + non-fac) / MP RVUs + status | `PPRRVU<YY>_<MON>.csv` | in the quarterly RVU zip |
| GPCI_work / GPCI_pe / GPCI_mp per locality | `GPCI<YYYY>.csv` | **same zip** |
| conversion factor ($/RVU) | the RVU file's own `CONVERSION FACTOR` column when present, else `--cf`, else `CF_BY_YEAR[year]` in `mpfs.py` | printed on every run as `conversion factor: N (from …)` |

The builder downloads one zip (`https://www.cms.gov/files/zip/rvu<YY><q>.zip`,
`q` = a/b/c/d quarter) and pulls both CSVs out of it. Column names drift year to
year, so the SELECT resolves them fuzzily (normalize → substring match) rather
than by exact header.

## Status indicator handling

The PPRRVU `STATUS CODE` column:

| Status | Meaning | In `mpfs_ga.parquet` |
|---|---|---|
| `A` `R` `T` | active / restricted / paid-if-sole-service | allowed amount computed |
| `C` | carrier-priced — no national RVUs | row kept, `medicare_allowed` **NULL** (a "Medicare covers this, price set locally" flag) |
| `B` `N` `I` `P` `X` `E` | bundled / non-covered / not valid / statutory exclusion | **dropped** |
| anything else (`D` `F` `G` `H` `J` `M` …) | no fee-schedule amount | dropped (RVUs would be zero anyway) |

A `NON-FAC NA INDICATOR` / `FACILITY NA INDICATOR` of `NA` means that setting's
PE RVU is not payable — that POS row is dropped (e.g. 93000 is facility-`NA`, so
only its non-facility row exists).

`billing_code_type` is `'CPT'` for a 5-digit numeric code and `'HCPCS'`
otherwise — matching how the MRF / serving layer name them, so the benchmark
joins to a quote's `(billing_code, billing_code_type)`.

## Georgia locality handling

Georgia has two MPFS localities: **`01` Atlanta metro** and **`99` rest of
state** (GPCI carrier 10212). The builder filters the GPCI file to Georgia (by
its `State` column when present, else a locality-name match on
`GEORGIA` / `ATLANTA`) and carries `locality` in the output rather than
pre-collapsing. `serving/benchmark.medicare_allowed()` takes the **median across
the localities present** — one GA number. If a caller wants Atlanta specifically,
query the parquet directly on `locality = '01'`.

## Re-run when CMS publishes year N

The RVU file is quarterly (corrections) and the whole schedule is re-based every
January. Re-run `make mpfs YEAR=<N>` — and **check the conversion factor**: 2025
had a mid-year statutory change, and CMS occasionally omits the `CONVERSION
FACTOR` column from the CSV. `CF_BY_YEAR` in `mpfs.py` is a hand-maintained
fallback; verify it against that year's CMS final rule and pass `CF=` if needed.
The cache key includes the year, so a new year's run won't reuse the old zip.

## Caveats

- **Physician fee schedule only.** This is the *professional* component. The
  **facility fee** for the same encounter — hospital outpatient (OPPS), ASC, or
  inpatient (IPPS/DRG) — is a *separate* CMS schedule and is **not** in this
  file. A `pos = 'facility'` row here is still the physician's payment, just at
  the reduced (facility) PE RVU.
- **Medicare, not commercial.** GA commercial contracts typically run above
  Medicare; `vs_medicare` is a ratio to reason about, not a target price.
- **National RVUs.** Only the GPCI and conversion factor are geographic. No
  locality-specific RVU adjustments, no site-of-service payment differential
  beyond fac/non-fac PE.
- **No modifier -50/-51/-62 etc. pricing rules.** The file carries the raw
  per-(code, modifier) RVUs; multi-procedure / bilateral / co-surgeon payment
  reductions are not applied.

## Tests

`serving/tests/test_mpfs.py` — hermetic, runs the builder against
`reference/testdata/mpfs_sample.csv` (PPRRVU-shaped: a plain code, a 26/TC
split, a facility-`NA` code, a bundled `B` code, a carrier-priced `C` code) ×
`reference/testdata/mpfs_gpci_sample.csv` (GA localities 01/99 + a Florida row
that must be filtered) in test isolation (`data-test/reference/`). Checks the
RVU formula, the fac/non-fac PE split, status handling, and the GA filter.
`serving/tests/test_api_contract.py::test_quote_carries_medicare_benchmark`
covers the `/rates/quote` wiring. Picked up by `make test-api` and
`make check-local`.
