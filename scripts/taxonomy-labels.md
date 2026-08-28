# `make taxonomy-labels` — NUCC specialty labels

*Read this when working on the provider-specialty label used by provider search
and the cost card.*

Builds `data/reference/nucc_taxonomy.parquet` from the **NUCC Health Care Provider
Taxonomy Code Set** (nucc.org, public domain). Python over DuckDB — a small CSV
reshape, no streaming parser ([language principle](../AGENTS.md#the-language-principle)).

`make taxonomy-labels` → `scripts/build_taxonomy_labels.py --data-dir /app/data`.
`NUCC_URL=` overrides the source. The script tries `nucc_taxonomy_261.csv` →
`251` → `250` (NUCC re-stamps the trailing version twice a year).

## Why

The NPPES GA subset carries a raw `taxonomy_code` (e.g. `207RC0000X`). NPPES's own
`taxonomy_group` is a useless coarse bucket. The backend LEFT JOINs this table so
provider search and the cost card show "· Cardiology" instead of "· Physician
(individual)".

## Output — `data/reference/nucc_taxonomy.parquet`

```
taxonomy_code | grouping | classification | specialization
display_name   (NUCC "Display Name", e.g. "Cardiovascular Disease Physician")
specialty      (clean short label: specialization, else classification)
is_individual  (NUCC Section == "Individual")
```
