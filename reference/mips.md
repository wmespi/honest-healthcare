# `make reference STEP=mips` — CMS MIPS quality score per clinician

*Read this when working on how `providers.score` is sourced.*

Builds `data/reference/mips_ga.parquet` — `npi | mips_score | mips_source |
performance_year` — from the Provider Data Catalog's newest
"PY <year> Clinician Public Reporting: Overall MIPS Performance" file. Public, free,
keyless. `make reference STEP=mips` → `python3 -m reference.mips` (`reference/mips.py`);
`--year`, `--url`, `--file` (a local CSV) and `--test` exist on the module.

## What the score is

CMS's Merit-based Incentive Payment System final score, 0–100, for one
clinician and one performance year. It is a **quality** signal and nothing
else: it is not a price, a patient rating, or a measure of experience.

## Which row a clinician keeps

A clinician can appear under several reporting `source`s. The builder keeps one
row per NPI, preferring an individual score and then the most specific group-level
one (the score CMS attributes to that clinician). A published score of 0 is kept
as 0. The output is scoped to the NPIs in `data/nppes/ga_providers.parquet`.

## Coverage is partial by design

Most PCPs have no MIPS score — clinicians below CMS's volume thresholds are
exempt. A missing score stays `NULL`; it is never defaulted and never replaced
by another field. `years_in_practice` (from DAC `grad_year`) is a different
signal, shown beside it, never in its place. Coverage for a plan's PCPs is
measured, not documented here: count `score IS NOT NULL` in `providers`.

## Downstream

`build/tables/providers.py` reads it for the find-care `providers.score`, which
`/find-care` returns for each provider at a place.

## Re-run when

CMS publishes a new performance year. `make reference` skips it while the file
exists; `FORCE=1` or `STEP=mips` rebuilds.
