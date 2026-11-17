# `make reference STEP=clinician-volume`: procedure volume per clinician

*Read this when working on how many times a Georgia clinician has done a procedure.*

**Builds** `data/ingestion/reference/clinician_volume_ga.parquet`, module `ingestion/reference/clinician_volume.py`, from CMS's Doctors and Clinicians
[Utilization Data](https://data.cms.gov/provider-data/dataset/n0yb-util), the volumes shown on clinician profile pages.
**Columns:** `npi`, `category`, `count`, `percentile`, `displayed`, `published`.

## What is kept

- The file is national with no state column, so it is kept to the NPIs in `data/ingestion/nppes/ga_providers.parquet`.
- `category` is CMS's procedure category text, written as CMS does.
  Run `SELECT category, count(*) FROM read_parquet('data/ingestion/reference/clinician_volume_ga.parquet') GROUP BY 1` for the list.
- `count` is CMS's reported value as text, either a number ("57") or a band ("1-10").
  CMS bands small volumes, so it is never cast to a number here.
- `percentile` is CMS's, null where CMS publishes none.
- `displayed` is whether CMS shows the row on the clinician's profile (`Profile_Display_Indicator` = Y).
- The file carries no measurement period.
  `published` is the catalog's release date for the file, not the period the counts cover.

## Guards

The builder stops if CMS renames a column it reads or if no Georgia clinician remains.

## Re-run when

CMS publishes a new file, a few times a year.
The builder reads the current file path from the dataset metadata and waits and retries on the catalog's 429.
Run `make reference STEP=clinician-volume`.
`ARGS="--file <csv> --published <date>"` reads a local copy.
