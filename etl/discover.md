# `make discover` — Phase 1: index discovery

*Read this when working on the monthly read of Anthem's master index: the file list and the plan → file link.*

Reads this month's Anthem master index and writes two Parquet files under `data/anthem/`: the files it lists (`index_files.parquet`) and the plans each one prices (`index_file_plans.parquet`).
Parse picks the files [`targets.yaml`](targets.yaml) names from them ([parse.md](parse.md#target-selection)), and the build reads the link ([build.md](../build/build.md#the-find-care-tables)).
Safe to re-run.
`make discover` → `etl discover` (package `etl/discovery`).

| Variable | Effect |
|---|---|
| `TEST=1` | `data-test/` instead of `data/` |
| `SCHEMA=1` | stream the index, write `data/anthem/index_schema.json` and nothing else |
| `NO_CACHE=1` | force re-download of the master index |
| `INDEX_URL=…` | override the monthly master-index URL |
| `LIMIT=n` | read only the first n reporting structures, with `TEST=1` or `SCHEMA=1` only (default 100 in test mode) |

## What it does

1. Downloads the master index to a local gzip cache (`data/anthem/index_cache.json.gz`, multi-GB) via parallel HTTP Range requests; re-runs on the same monthly URL skip the download unless `NO_CACHE=1`.
   The cache is only needed during a run.
2. Streams the JSON and walks every `reporting_structure`.
   Each file it lists becomes one `index_files` row: `file_id`, `name`, `address` and `location` ([below](#a-files-id)).
3. Keeps the **plan → file link**: every `(file_id, plan_id)` pair once, **scoped to Georgia individual-market plans** (`market_type == "individual"` or `plan_id[5:7] == "GA"`).
   That answers *"which files price plan X"*.
   The scope is the project's product boundary (`docs/direction.md`), not `targets.yaml`, so a new target never needs a new discover: the full cross-product is hundreds of millions of rows a month, almost all employer plans nothing here selects on.
4. Writes `data/anthem/index_schema.json` (a compact, array-truncated example).
5. Writes both Parquet files sorted by ID, so the same index always makes the same bytes, and moves them into place together, so nothing reads one month's files with another's links.

Pairs are kept once each in memory as the index streams.
The index publishes `reporting_plans[] × in_network_files[]`, tens of millions of pairs, but kept to Georgia's individual market they come to a few hundred thousand distinct ones, so memory doesn't grow with the cross-product (Critical Rule 3 applies to discovery too).
Anthem's plan names and market types aren't kept: the plan ID is what links a file to Georgia's plan file.

## A file's ID

A file's `address` is its location without the query a signed URL carries.
Its `file_id` is a hash of the address (`core.FileID`), so a file has the same ID every month, and only its signed `location` changes.
Parsed Parquet is keyed by that ID (`prices/net=<slug>/<id>.parquet`), so a re-parse replaces the file's output instead of adding a second copy.
Discover stops if two addresses ever hash to one ID.

A file's `name` alone isn't unique: one BlueCard file can be mirrored on many hosts.
An out-of-state location is CloudFront-signed (`?Expires=…&Signature=…`) and dies after about a month, and its name carries a `YYYY-MM_` prefix, so those are new files every month.

Each run writes this month's lists whole, so a file the index no longer lists is gone from them.
Its parsed Parquet stays on disk, and the build leaves it out, because the link no longer names it.
Whether a listed file is downloaded is parse's call ([parse.md](parse.md#when-parse-downloads-a-file)).

```bash
make discover   # this month's index
make discover NO_CACHE=1 INDEX_URL="https://…/2026-12-01_anthem_index.json.gz"
```

## Which files price a plan?

```bash
docker compose exec serving python3 -c "import duckdb; duckdb.sql(\"
  SELECT f.file_id, f.name, f.address
  FROM '/app/data/anthem/index_files.parquet' f
  JOIN '/app/data/anthem/index_file_plans.parquet' l USING (file_id)
  WHERE l.plan_id = '45334GA0020002'\").show()"
```
