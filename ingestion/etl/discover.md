# `make discover`: index discovery

*Read this when working on the monthly read of a payer's master index: the file list and the plan → file link.*

Reads this month's master index of each payer (`anthem`, `ambetter`, `alliant`, `oscar`: `core.Payers`) and writes two Parquet files under `data/raw/<payer>/`: the files it lists (`index_files.parquet`) and the plans each one prices (`index_file_plans.parquet`).
Parse picks the files [`targets.yaml`](targets.yaml) names from them ([parse.md](parse.md#target-selection)), and the publish step reads the link to tell which parsed file prices a network ([parse.md](parse.md#publishing-the-rates-by-network)).
Safe to re-run.
`make discover` → `etl discover` (package `ingestion/etl/discovery`).

| Variable | Effect |
|---|---|
| `PAYER=name` | read only this payer's index (default every payer) |
| `TEST=1` | `data-test/` instead of `data/` |
| `SCHEMA=1` | stream the index, write `data/raw/<payer>/index_schema.json` and nothing else |
| `KEEP_INDEX=1` | keep Anthem's multi-GB index download after the run, so the next run of the month reads it from disk |
| `NO_CACHE=1` | download Anthem's index again even if a copy of this month's is kept |
| `INDEX_URL=…` | override one payer's index URL (Anthem's unless `PAYER`) |
| `LIMIT=n` | read only the first n reporting structures, with `TEST=1` or `SCHEMA=1` only (default 100 in test mode) |

## Where each payer's index is

`sources` in `discovery.go` says where each payer's index is:

- **Anthem**: a monthly URL, `YYYY-MM-01_anthem_index.json.gz`, several GB.
  It is downloaded by parallel HTTP Range requests to `data/raw/anthem/index_cache.json.gz` and deleted once the run is done, unless `KEEP_INDEX=1`.
  A copy of this month's index, kept or left by a run that stopped part way, is read from disk instead of downloaded again, unless `NO_CACHE=1`.
- **Ambetter** (Centene): a small plain JSON file whose dated link (`…_ambetter_index.json`) is on Centene's price transparency page, so discover finds the newest link there.
  It is streamed straight off the network and never kept.

- **Alliant**: a small plain JSON index whose dated link (`…_alliant-health-plans_index_nlc.json`) is on a meta-refresh page, so discover finds the newest link there as it does for Ambetter.
  It lists plans by 16-character plan ID, the 14-character plan ID plus a two-digit cost-sharing variant, which discover keeps as the plan ID.
  It is streamed straight off the network and never kept.

- **Oscar**: a small plain JSON index at a dated S3 URL (`…/oscar/<YYYYMMDD>_oscar_index.json`, the first of the month), built from the current month with no page to read.
  It lists every state, and discover scopes it to Georgia.
  Its Georgia networks share one product ID, so each target names its own file and label.
  It is streamed straight off the network and never kept.

Each is gzip or plain JSON by its first two bytes (`0x1f 0x8b`), whatever its name says.
A body that ends early, or an index that never closes, fails the payer.

A payer whose index can't be read keeps the lists of its last run, because the lists are written only after a whole index is read, and is named in a banner at the end of the run.
The other payers are still read, and discover exits non-zero only when no payer's index was read.

## What it does

1. Streams the JSON and walks every `reporting_structure`.
   Each file it lists becomes one `index_files` row: `file_id`, `name`, `address` and `location` ([below](#a-files-id)).
2. Keeps the **plan → file link**: every `(file_id, plan_id)` pair once, **scoped to Georgia individual-market plans** (`plan_market_type` is `individual`, or `plan_id[5:7] == "GA"` for a HIOS plan ID, in either case).
   That answers *"which files price plan X"*.
   A plan ID is a 14-character plan ID (Anthem) or a 10-character product ID (Ambetter), which stands for every plan under it ([parse.md](parse.md#target-selection)).
   The scope is the project's product boundary (`docs/direction.md`), not `targets.yaml`, so a new target never needs a new discover: the full cross-product is hundreds of millions of rows a month, almost all employer plans nothing here selects.
3. Writes `data/raw/<payer>/index_schema.json` (a compact, array-truncated example).
4. Writes both Parquet files sorted by ID, so the same index always makes the same bytes, and moves them into place together, so nothing reads one month's files with another's links.

Pairs are kept once each in memory as the index streams.
Anthem's index publishes `reporting_plans[] × in_network_files[]`, tens of millions of pairs, but kept to Georgia's individual market they come to a few hundred thousand distinct ones, so memory doesn't grow with the cross-product (Critical Rule 3 applies to discovery too).
A payer's plan names aren't kept: the plan ID is what links a file to Georgia's plan file.

## A file's ID

A file's `address` is its location without the query a signed URL carries.
Its `file_id` is a hash of the address (`core.FileID`), so a file has the same ID every month, and only its signed `location` changes.
Every parsed row carries that ID, and the plan link uses it, so the build can tell which plans a parsed file prices.
Parse saves a file's Parquet under its name instead (`prices/net=<slug>/GA_JBNKMED0001.parquet`), which is readable and unique on disk, because parse only reads a name that matches one file in the index ([parse.md](parse.md#target-selection)).
Discover stops if two addresses ever hash to one ID.

A file's `name` alone isn't unique: one BlueCard file can be mirrored on many hosts.
An out-of-state location is CloudFront-signed (`?Expires=…&Signature=…`) and dies after about a month, and its name carries a `YYYY-MM_` prefix, so those are new files every month.
Ambetter's Georgia file has a date in its name too, so it is a new file, at a new address, every month ([parse.md](parse.md#target-selection)).

Each run writes this month's lists whole, so a file the index no longer lists is gone from them.
Its parsed Parquet stays on disk, and the build leaves it out, because the link no longer names it.
Whether a listed file is downloaded is parse's call ([parse.md](parse.md#when-parse-downloads-a-file)).

```bash
make discover                    # every payer
make discover PAYER=ambetter     # one payer
make discover PAYER=anthem NO_CACHE=1 INDEX_URL="https://…/2026-12-01_anthem_index.json.gz"
```

## Which files price a plan?

```bash
docker compose exec ingestion python3 -c "import duckdb; duckdb.sql(\"
  SELECT f.file_id, f.name, f.address
  FROM '/app/data/raw/anthem/index_files.parquet' f
  JOIN '/app/data/raw/anthem/index_file_plans.parquet' l USING (file_id)
  WHERE l.plan_id = '45334GA0020002'\").show()"
```
