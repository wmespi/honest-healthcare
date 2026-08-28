# `make discover` — Phase 1: index discovery

*Read this when working on the monthly metadata sync into the Postgres queue.*

Populates `index_files` (the parse queue) from the Anthem master index. Cheap,
incremental, safe to re-run. `make discover` → `etl-go discover`.

| Variable | Effect |
|---|---|
| `TEST=1` | test schema + `data-test/` |
| `SCHEMA=1` | stream the index, write `data/anthem/index_schema.json`, **no DB writes** |
| `NO_CACHE=1` | force re-download of the master index |
| `INDEX_URL=…` | override the monthly master-index URL |
| `LIMIT=n` | cap reporting structures (default 100 in test mode) |

## What it does

1. Downloads the master index to a local gzip cache
   (`data/anthem/index_cache.json.gz`, ~8.7 GB) via parallel HTTP Range requests.
   Re-runs on the same monthly URL skip the download unless `NO_CACHE=1`.
2. Streams the JSON, walks every `reporting_structure`, and for each
   `in_network_files` entry accumulates, per unique file URL:
   - `market_types` — `individual` / `group`
   - `hios_issuer_ids` — 5-digit HIOS issuer IDs (first 5 chars of each HIOS
     `plan_id`; maps to state)
   - `plan_states` — 2-letter state codes from HIOS `plan_id[5:7]` (positional,
     deterministic — the no-regex GA signal)
   - `reporting_entity_name` / `reporting_entity_type` — from the index root; the
     parser later overwrites these with the per-file value
   - `network_entity` — prefix before `" : "` in the file description (BlueCard
     files only; else NULL)
   - `description`, `location`

   Plan **names** are intentionally not stored — the plans × files cross-product
   blows the heap (400k+ plans, 10k+ files). `market_types` + `hios_issuer_ids` +
   `plan_states` cover filtering; `network_name` (from `provider_references`) is
   the per-rate network label written at parse time.
3. Writes `data/anthem/index_schema.json` (a compact, array-truncated example).
4. Bulk-loads via `COPY` into a `TEMP` staging table, then set-based
   `UPDATE … FROM _idx_stage` + `INSERT … LEFT JOIN … WHERE t.id IS NULL`. GIN
   indexes on the array columns are dropped before the write, rebuilt once after.

## Monthly refresh — signed-URL expiry

Every `location` is a CloudFront-signed URL (`?Expires=…&Signature=…`) that dies in
~30 days, and the index's file paths carry a `YYYY-MM_` prefix — so **each month's
files are new rows, not updates**.

```bash
make discover NO_CACHE=1 INDEX_URL="https://…/2026-09-01_anthem_index.json.gz"
# then prune the dead prior month:
make psql
#   DELETE FROM index_files
#   WHERE location LIKE '%/2026-08\_%' AND status IN ('pending','failed');
```

`location` is not a cross-month key. A query-stripped `url_path` column would fix
this — see [../docs/known-gaps.md](../docs/known-gaps.md). The 8.7 GB
`index_cache.json.gz` is only needed during a run — safe to delete between refreshes.
