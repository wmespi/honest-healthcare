# `make reference STEP=asc-measures`: ambulatory surgery center quality

*Read this when working on a surgery center's quality signal.*

**Builds** two files, module `ingestion/reference/asc_measures.py`, from CMS's
[ASC Quality Measures - Facility](https://data.cms.gov/provider-data/dataset/4jcv-atw7) and
[- National](https://data.cms.gov/provider-data/dataset/wue8-3vwe) files.
`data/ingestion/reference/asc_measures_ga.parquet` is one row per Georgia ASC and measure.
**Columns:** `npi`, `ccn`, `measure`, `score`, `compared`, `sample`, `period`.
`data/ingestion/reference/asc_measures_national.parquet` is one row per measure.
**Columns:** `measure`, `national`, `median`, `period`.

## Measures kept

- `ASC-9` and `ASC-11` are percentages with no comparison label, so `compared` and `sample` are null.
- `ASC-12`, `ASC-17` and `ASC-18` are risk-standardized rates, and `ASC-19` is a ratio.
  `score` is that rate or ratio, `compared` is CMS's performance category (for example "No Different Than the National Rate"), `sample` is CMS's total cases.

Every other ASC measure in the file is left out.
The file's other columns (confidence interval, footnotes) are not kept.

## What the columns mean

- `ccn` is CMS's `Facility ID`, the ASC's certification number, and `npi` is the ASC's NPI.
- `score` is null where CMS publishes "N/A".
  A row with neither a score nor a label is not kept.
- `period` is CMS's `Year` as text.
- `national` is the national average for `ASC-9` and `ASC-11` (with their `median`), and the national rate or ratio for the others (no median).

## Guards

The builder stops if CMS renames a column it reads, if no Georgia rows remain, or if an ASC repeats a measure.

## Re-run when

CMS publishes a new year.
The builder reads the current file path from the dataset metadata and waits and retries on the catalog's 429.
Run `make reference STEP=asc-measures`.
`ARGS="--file <csv> --national-file <csv>"` reads local copies.
