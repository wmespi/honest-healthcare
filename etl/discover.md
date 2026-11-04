# `make discover` — Phase 1: index discovery

*Read this when working on the monthly metadata sync into the Postgres queue.*

Rebuilds `index_files` (the parse queue) and `index_file_plans` (the plan → file link) from this month's Anthem master index.
Safe to re-run.
`make discover` → `etl discover` (package `etl/discovery`).

| Variable | Effect |
|---|---|
| `TEST=1` | test schema + `data-test/` |
| `SCHEMA=1` | stream the index, write `data/anthem/index_schema.json`, **no DB writes** |
| `NO_CACHE=1` | force re-download of the master index |
| `INDEX_URL=…` | override the monthly master-index URL |
| `LIMIT=n` | cap reporting structures (default 100 in test mode); a limited run only adds and updates |

## What it does

1. Downloads the master index to a local gzip cache (`data/anthem/index_cache.json.gz`,
   multi-GB) via parallel HTTP Range requests; re-runs on the same monthly URL skip
   the download unless `NO_CACHE=1`. The cache is only needed during a run.
2. Streams the JSON, walks every `reporting_structure`, and per file (its address,
   [below](#the-monthly-rebuild)) accumulates `market_types`, `hios_issuer_ids` (first 5 chars of each HIOS
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
5. Bulk-loads via `COPY` into `TEMP` staging tables, then rebuilds the queue from them
   set-based, in one transaction ([below](#the-monthly-rebuild)). GIN indexes on the
   array columns are dropped before the write, rebuilt once after.
6. Backfills `file_size_bytes` with HEAD requests so the queue can be size-ordered.

## Why the pairs never sit in memory

`reporting_plans[] × in_network_files[]` is a cross-product of tens of millions of
pairs, so accumulating per file would blow the heap (Critical Rule 3 applies to
discovery too). Instead each file gets a run-local `file_key` int on first
sight, so a staged pair carries 8 bytes, not the signed URL; `planStager` buffers a
bounded batch (200k pairs), dedupes within it, `COPY`s it to a `TEMP` `_plan_stage`
and resets; after the `index_files` upsert assigns ids, one statement resolves
`file_key → id` and inserts `DISTINCT ON (file_id, plan_id, plan_name, market_type)
… ON CONFLICT DO NOTHING`. Cross-batch duplicates collapse there, and so do a limited run's pairs that are already linked.

## The monthly rebuild

Each month's index is a new list.
An out-of-state location is a CloudFront-signed URL (`?Expires=…&Signature=…`) that dies after about a month, and its file name carries a `YYYY-MM_` prefix, so those are new files every month.
Discover matches files on `index_files.address`, the location without its query.
A file Anthem keeps listing, like a Georgia file, therefore keeps its id, status and parse record from month to month, and only its signed location is refreshed.
Parsed Parquet is keyed by that id (`prices/net=<slug>/<id>.parquet`), so a re-parse replaces the file's output instead of adding a second copy.

A run that reads the whole index makes the queue match it, in one transaction:

- every file the index lists is added or refreshed;
- a file it no longer lists is removed, unless it's `completed`: its Parquet uses its id, so its row stays, with no plan links;
- the plan links are replaced with this index's.

A limited run (`LIMIT=n`) reads only part of the index, so it only adds and updates.
Discover stops before downloading the index if a file is `processing`, because a parse is in it or one stopped part way ([queue.md](queue.md#recovery)).
It locks the queue while it writes, so a parse that starts meanwhile waits for it.
Whether a parsed file is downloaded again is parse's call ([queue.md](queue.md#when-anthem-updates-a-file)).

```bash
make discover   # this month's index
make discover NO_CACHE=1 INDEX_URL="https://…/2026-12-01_anthem_index.json.gz"
```

## Which files serve a plan?

```sql
SELECT f.id, f.status, f.location
FROM index_files f
JOIN index_file_plans p ON p.file_id = f.id
WHERE p.plan_name ILIKE '%blue value%';
```

`etl parse` runs the same shape as an `EXISTS` semi-join, with the patterns read
from [`targets.yaml`](targets.yaml) — see [parse.md](parse.md).
