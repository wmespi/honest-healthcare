# `make reference STEP=mips` — CMS MIPS quality score per clinician

*Read this when working on the quality signal in the PCP picker (`/find-care`)
or on how `provider_dim.mips_score` is sourced.*

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
measured, not documented here: count `provider_dim.mips_score IS NOT NULL` over
the plan's priced PCPs.

## Downstream

`build/build.py` left-joins this file into `provider_dim` (`mips_score`,
`mips_source`, `mips_year`). `/providers/search` returns the score on every row
and feeds it to the blended ranking — [serving/serving.md](../serving/serving.md)
"Ranking".

## Re-run when

CMS publishes a new performance year. `make reference` skips it while the file
exists; `FORCE=1` or `STEP=mips` rebuilds.
