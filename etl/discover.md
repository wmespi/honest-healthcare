# `make discover` — Phase 1: index discovery

*Read this when working on the monthly metadata sync into the Postgres queue.*

Populates `index_files` (the parse queue) and `index_file_plans` (the plan → file
link) from the Anthem master index. Cheap, incremental, safe to re-run.
`make discover` → `etl discover` (package `etl/discovery`).

| Variable | Effect |
|---|---|
| `TEST=1` | test schema + `data-test/` |
| `SCHEMA=1` | stream the index, write `data/anthem/index_schema.json`, **no DB writes** |
| `NO_CACHE=1` | force re-download of the master index |
| `INDEX_URL=…` | override the monthly master-index URL |
| `LIMIT=n` | cap reporting structures (default 100 in test mode) |

## What it does

1. Downloads the master index to a local gzip cache
   (`data/anthem/index_cache.json.gz`, ~10 GB and growing month over month) via
   parallel HTTP Range requests.
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
3. Keeps the **plan → file link** itself: every `(reporting_plan,
   in_network_file)` pair the structure publishes becomes one `index_file_plans`
   row — `plan_id`, `plan_id_type`, `plan_name`, `market_type`, `file_id`. That
   is what makes *"which files serve plan X"* answerable, and what
   [`parse`](parse.md) selects on instead of guessing from a filename.
4. Writes `data/anthem/index_schema.json` (a compact, array-truncated example).
5. Bulk-loads via `COPY` into a `TEMP` staging table, then set-based
   `UPDATE … FROM _idx_stage` + `INSERT … LEFT JOIN … WHERE t.id IS NULL`. GIN
   indexes on the array columns are dropped before the write, rebuilt once after.

## Why the pairs never sit in memory

`reporting_plans[] × in_network_files[]` is a cross-product — tens of millions of
pairs across the full index — so accumulating them per file (the `plan_names[]`
array `index_files` used to reserve a column for) blows the heap. Critical Rule 3
applies to discovery too. Instead:

- Each unique file URL gets a run-local `file_key` int as it is first seen. A
  staged pair carries the 8-byte key, not the ~500-byte signed URL.
- `planStager` buffers a bounded batch (200k pairs), deduplicates *within* the
  batch — duplicates cluster, because a run of structures usually repeats the
  same plans over the same shared network file — `COPY`s it to a `TEMP`
  `_plan_stage`, and resets. Heap stays flat regardless of index size.
- After the `index_files` upsert has assigned ids, one statement resolves
  `file_key → id` and inserts `DISTINCT ON (file_id, plan_id, plan_name,
  market_type) … ON CONFLICT DO NOTHING`. Cross-batch duplicates and re-runs are
  both absorbed there, set-based, by Postgres.

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
this — see [../docs/known-gaps.md](../docs/known-gaps.md). The multi-GB
`index_cache.json.gz` is only needed during a run — safe to delete between refreshes.

`index_file_plans.file_id` is `ON DELETE CASCADE`, so pruning a dead month's
`index_files` rows takes their plan links with them.

## Which files serve a plan?

```sql
SELECT f.id, f.status, f.location
FROM index_files f
JOIN index_file_plans p ON p.file_id = f.id
WHERE p.plan_name ILIKE '%blue value%';
```

`etl parse` runs the same shape as an `EXISTS` semi-join, with the patterns read
from [`targets.yaml`](targets.yaml) — see [parse.md](parse.md).
