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

1. Downloads the master index to a local gzip cache (`data/anthem/index_cache.json.gz`,
   multi-GB) via parallel HTTP Range requests; re-runs on the same monthly URL skip
   the download unless `NO_CACHE=1`. The cache is only needed during a run.
2. Streams the JSON, walks every `reporting_structure`, and per unique file URL
   accumulates `market_types`, `hios_issuer_ids` (first 5 chars of each HIOS
   `plan_id`), `plan_states` (2-letter state from `plan_id[5:7]` — the positional,
   no-regex GA signal), `reporting_entity_*` (from the index root; the parser later
   overwrites them with the per-file value), `network_entity` (prefix before `" : "`
   in a BlueCard file description, else NULL), `description`, `location`.
3. Keeps the **plan → file link**: every `(reporting_plan, in_network_file)` pair,
   **scoped to Georgia individual-market plans** (`market_type == "individual"` or
   `plan_id[5:7] == "GA"`), becomes an `index_file_plans` row (`plan_id`,
   `plan_id_type`, `plan_name`, `market_type`, `file_id`). That answers *"which files
   serve plan X"* and is what [`parse`](parse.md) selects on. The scope is the
   project's product boundary (AGENTS.md, `docs/direction.md`), not `targets.yaml`:
   the full cross-product is hundreds of millions of rows/month, almost all employer
   plans nothing here selects on.
4. Writes `data/anthem/index_schema.json` (a compact, array-truncated example).
5. Bulk-loads via `COPY` into a `TEMP` staging table, then set-based
   `UPDATE … FROM _idx_stage` + `INSERT … LEFT JOIN … WHERE t.id IS NULL`. GIN
   indexes on the array columns are dropped before the write, rebuilt once after.
6. Backfills `file_size_bytes` with HEAD requests so the queue can be size-ordered.

## Why the pairs never sit in memory

`reporting_plans[] × in_network_files[]` is a cross-product of tens of millions of
pairs, so accumulating per file would blow the heap (Critical Rule 3 applies to
discovery too). Instead each unique file URL gets a run-local `file_key` int on first
sight, so a staged pair carries 8 bytes, not the signed URL; `planStager` buffers a
bounded batch (200k pairs), dedupes within it, `COPY`s it to a `TEMP` `_plan_stage`
and resets; after the `index_files` upsert assigns ids, one statement resolves
`file_key → id` and inserts `DISTINCT ON (file_id, plan_id, plan_name, market_type)
… ON CONFLICT DO NOTHING`. Cross-batch duplicates and re-runs are absorbed there.

## Monthly refresh — signed-URL expiry

Every `location` is a CloudFront-signed URL (`?Expires=…&Signature=…`) that dies after
about a month, and the index's file paths carry a `YYYY-MM_` prefix — so **each month's
files are new rows, not updates**.

```bash
make discover NO_CACHE=1 INDEX_URL="https://…/2026-09-01_anthem_index.json.gz"
# then prune the dead prior month:
make psql
#   DELETE FROM index_files
#   WHERE location LIKE '%/2026-08\_%' AND status IN ('pending','failed');
```

`location` is not a cross-month key ([../docs/known-gaps.md](../docs/known-gaps.md)).
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
