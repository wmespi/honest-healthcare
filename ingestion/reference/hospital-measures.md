# `make reference STEP=hospital-measures`: hospital quality measures

*Read this when working on a hospital's quality signal beyond its star rating.*

**Builds** two files from four CMS Provider Data Catalog datasets, module `ingestion/reference/hospital_measures.py`.
`data/ingestion/reference/hospital_measures_ga.parquet` is one row per Georgia hospital and measure.
**Columns:** `ccn`, `measure`, `score`, `compared`, `sample`, `start_date`, `end_date`.
`data/ingestion/reference/hospital_measures_national.parquet` is one row per measure with a national value.
**Columns:** `measure`, `score`, `start_date`, `end_date`.

## Measures kept

- `OP-32` (visits after outpatient colonoscopy) and `OP-36` (visits after outpatient surgery) from [Unplanned Hospital Visits](https://data.cms.gov/provider-data/dataset/632h-zaca).
- `OP-29` and `OP-31` from [Timely and Effective Care](https://data.cms.gov/provider-data/dataset/yv7e-xc69).
- `OP-10` and `OP-39` from [Outpatient Imaging Efficiency](https://data.cms.gov/provider-data/dataset/wkfw-kthe).
- `H_STAR_RATING`, the HCAHPS patient survey summary star, from [HCAHPS](https://data.cms.gov/provider-data/dataset/dgck-syfz).

CMS spells ids `OP_32` in some files and `OP-10` in others, so the builder writes one spelling with a hyphen.
The HCAHPS id keeps CMS's underscores.
Every other measure in those files is left out.

## What the columns mean

- `score` is CMS's number, null where CMS publishes "Not Available" or "Not Applicable".
  Its unit differs by measure, so read the measure's definition on CMS before showing a number.
- `compared` is CMS's own label (for example "Better Than the National Rate"), null where the file has none.
  Only the unplanned-visit measures carry one, and its wording differs between `OP-32` and `OP-36`.
- `sample` is the denominator (unplanned visits), the sample (timely care) or completed surveys (HCAHPS).
- `start_date` and `end_date` are the measurement window CMS states, which differs by measure.

## National values

The national files hold `OP-10`, `OP-39`, `OP-29` and `OP-31`, in the national table.
The unplanned-visit measures have no national file, because the row's `compared` label is that comparison.
The HCAHPS national file has no summary star, so `H_STAR_RATING` has no national row.
Query the national table, not a constant, for a comparison.

## Guards

The builder stops if CMS renames a column it reads, if a hospital repeats a measure, if a listed measure has no Georgia row or a listed national measure is missing.

## Re-run when

CMS refreshes these files (it publishes the hospital datasets a few times a year) and changes each file's path, so the builder reads the current path from the dataset metadata.
The catalog answers 429 to quick repeated calls, so the builder waits and retries.
Run `make reference STEP=hospital-measures`.
`ARGS="--file <dataset-id>=<csv>"` reads a local copy of one dataset.
