# `make reference STEP=opps` — Medicare hospital-outpatient rate (OPPS)

*Read this when working on the Medicare hospital rate the find-care screen gives as a
rough guide at a hospital (`services.medicare_facility_rate`).*

**Builds** `data/reference/opps.parquet` from CMS's quarterly
[Addendum B](https://www.cms.gov/medicare/payment/prospective-payment-systems/hospital-outpatient-pps/quarterly-addenda-updates):
one row per `(billing_code, billing_code_type)` with `status_indicator`, `apc`,
`payment_rate` (the national Medicare payment, before the 80/20 split) and the
calendar `year`. Module `reference/opps.py`; `ARGS="--cms-url <zip>"` overrides the source zip.

**Why.** Anthem's file has no hospital facility price for imaging: a hospital-based
practice shows a reading-only price and the hospital's own fee is missing. The OPPS rate
is the one public, per-code number for that missing part, so the app shows it as a
labelled estimate next to the scan. It is not a price Anthem filed.

## What is kept

Only codes with a dollar payment rate. Codes paid under the physician fee schedule
(status `A`, which includes mammography) and codes with no payable rate are dropped, so
their scans show no estimate. The rate is the national unadjusted amount: the local
wage index is not applied, and a scan billed together with other hospital services can
be packaged into one payment. The estimate says so rather than implying a quote.

## How it flows

The find-care build puts it on `services.medicare_facility_rate`, empty when the code
has no rate. The words shown to
the user live in `frontend/src/findCare/ResultsTable.jsx`.

## Re-run when CMS publishes a new quarter

`make reference STEP=opps ARGS="--cms-url <new zip>"`, then `make build`. The zip's file name
changes each quarter.
