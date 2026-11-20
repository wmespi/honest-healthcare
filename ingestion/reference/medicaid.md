# `make reference STEP=medicaid` - Medicaid evidence that a doctor does a service

*Read this when working on "does this doctor do this service" for care Medicare can't see, such as children's visits and pregnancy care.*

**Builds** `data/ingestion/reference/medicaid_ga.parquet` from HHS's [Medicaid Provider Spending by HCPCS](https://opendata.hhs.gov/datasets/medicaid-provider-spending/).
Module `ingestion/reference/medicaid_spending.py`.
Medicare's counterpart is [cms-utilization.md](cms-utilization.md); the build counts a doctor either shows billing a service as evidence ([build.md](../../integration/build.md)).

**Source.**
One Parquet file, a one-time release rather than a monthly one.
DuckDB reads it straight from its URL with `httpfs`, so no copy of it lands on disk.
`ARGS="--url <url>"` reads a different release, and `ARGS="--file <path>"` reads a local Parquet, which the tests use.
The columns read are the billing and servicing NPIs, the HCPCS code, the claim month (`YYYY-MM`) and the claim lines.

**Output.**
One row per `npi` and `hcpcs_cd`, with `claim_lines` summed over the window and `through`, the window's last month.
The `npi` is the servicing NPI, else the billing NPI when the servicing one is blank.
A value that isn't a number drops the row.

**Scope.**
Only NPIs in the NPPES Georgia subset ([nppes.md](../etl/nppes.md)): `ga_providers.parquet` and the NPIs in `ga_locations.parquet` beside it.
The file has no state column, so NPPES is the only Georgia filter.
The step stops and names `make reference STEP=nppes` when the providers file is missing.

**Window.**
The latest 12 months in the file, counted back from its newest `CLAIM_FROM_MONTH`.
The data ends where HHS's release ends, so `through` says how current it is.

**Caveats.**
- HHS suppresses rows under 12 claims, so a low-volume service, such as a delivery, is mostly hidden.
- There is no place of service, so the build counts Medicaid billing as in an office only for a service billed one way, such as a visit ([build.md](../../integration/build.md#the-service-model)).
- The file includes Medicaid managed care and CHIP.
- A code in the file is not a service the find-care catalog offers; the build uses only the codes a service or its family names.

**Tests.**
`ingestion/reference/tests/test_medicaid_spending.py` builds a small Parquet and a small NPPES subset in a temp directory and checks the scope, the servicing-else-billing NPI, the window and the sum.
