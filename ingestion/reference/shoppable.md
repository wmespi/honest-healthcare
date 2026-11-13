# `make reference STEP=shoppable` - CMS's shoppable items, with RBCS

*Read this when working on which codes count as candidates for a service the find-care screen offers.*

**Builds** `data/ingestion/reference/shoppable_services.parquet`.
It has one row per code in CMS's list of 500 shoppable items and services, sorted by `billing_code_type, billing_code`.
The columns are `billing_code`, `billing_code_type`, `rbcs_category`, `rbcs_subcategory` and `rbcs_family`, all text.
Module `ingestion/reference/shoppable.py`.

## Sources

**The codes** are in `ingestion/reference/shoppable_items.csv` (CPT, HCPCS and MS-DRG).
They come from CMS's list under the Transparency in Coverage rule, which CMS publishes only as a [PDF](https://www.cms.gov/files/document/transparency-coverage-500-items-services-508.pdf).
Its descriptions belong to the AMA, so only the codes are checked in.
The build stops if a code repeats in that file.

**The classification** is CMS's [Restructured BETOS Classification System](https://data.cms.gov/provider-summary-by-type-of-service/provider-service-classifications/restructured-betos-classification-system) (RBCS), downloaded when the step runs.
RBCS keeps every assignment a code ever had.
The build keeps the rows marked as the latest assignment, then the one with the latest end date for each code.
The three RBCS columns are empty where a code has no RBCS row, which is true of MS-DRG codes.
CMS's own `No RBCS Family` text is kept as it is.

## Finding the download

The step looks the dataset up by its title in CMS's catalog (`https://data.cms.gov/data.json`) and takes the newest CSV.
If that lookup fails it uses the URL written in the module.
`ARGS="--url <csv>"` overrides both, and `ARGS="--rbcs-file <csv>"` reads a local copy.
When CMS posts a new year, copy the CSV link from the landing page above.

## Stops

The build stops and says what to do when:

- the RBCS file lacks a column the build reads, which means CMS changed the layout
- fewer than 95% of the CPT and HCPCS codes get an RBCS row, which means the layout or code format probably changed

## Run it

`make reference STEP=shoppable`, then check the rows with `make sh S=ingestion` and DuckDB on the file.
`make reference` skips it when the file exists, and `FORCE=1` rebuilds it.
The test is `ingestion/reference/tests/test_shoppable.py`.
