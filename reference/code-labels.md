# `make code-labels` — consumer procedure labels

*Read this when working on the "what is this procedure" layer that powers
`/billing_codes` search and browse-by-category.*

Builds `data/reference/code_labels.parquet` from **public data only**. Runs in
Python over DuckDB ([language principle](../AGENTS.md#the-language-principle)): it
joins the parsed `codes/*.parquet` against a small reference CSV and reshapes —
SQL-shaped work, no streaming parser.

`make code-labels` → `python3 -m reference.code_labels --data-dir /app/data` in the
serving container (`reference/code_labels.py`). `RBCS_URL=` overrides the source;
`--rbcs-file` / `--test` exist on the module. Cache download + atomic Parquet
write come from `reference/_common.py`, shared with `taxonomy_labels`.

## Sources

- **CMS Restructured BETOS Classification System (RBCS)** — 185 readable families
  (`rbcs_family`, e.g. "Arthroplasty - Knee", "MRI/MRA - Spine"). `rbcs_subcategory`
  covers ~84% of rate volume as a fallback. Public domain. URL is resolved from
  `data.cms.gov/data.json` with a hard-coded RY2026 fallback (CMS re-stamps the
  path yearly).
- **The MRF's own `codes.name`** → `short_name` (fallback label + search text).
- **A hand-curated `FAMILY_SYNONYMS` map** in the module so "colonoscopy",
  "mri back", "blood test" resolve to the right family.
- **No AMA CPT descriptors** (licensed). This is why the label layer exists at
  all — the Georgia MRF's own `codes.name` is near-useless ("Medical", "Surgery").

## Output — `data/reference/code_labels.parquet`

```
billing_code_type | billing_code | short_name
rbcs_category | rbcs_subcategory | rbcs_family | rbcs_is_major
label        (rbcs_family, else title-cased short_name)
search_text  (lowercased: label + short_name + category words) ← /billing_codes queries this
```
