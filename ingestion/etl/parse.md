# `make parse` — Phase 2: MRF → Parquet

*Read this when working on the per-file stream transform - which files are downloaded, the parser, the Parquet writers, network attribution, the GA NPI filter, or the provider probe.*

Streams each gzipped MRF that [`targets.yaml`](targets.yaml) names once, and writes Parquet named after the file (`GA_JBNKMED0001.parquet`), whose rows carry its file ID ([discover.md](discover.md#a-files-id)).
`make parse` → `etl parse` (package `ingestion/etl/extraction`: selection `extraction.go`, parse record `record.go`, parser `stream.go`, probe `probe.go`, writers `partition.go`).
Needs this month's file list from `make discover`, and the NPPES and exchange-plans references (`make reference`).

| Variable | Effect |
|---|---|
| `FILE=name[,name]` | parse these files from this month's index now, bypassing target selection and the parse record (`-files`) |
| `TARGETS=path` | a different target-plan list (default [`targets.yaml`](targets.yaml)) |
| `TEST=1` | `data-test/` instead of `data/`, caps at 1 file |
| `LIMIT=n` | cap files processed |
| `FIXTURE=path` | parse a local `*.json.gz` instead of a file from the index |

CLI-only: `-all-npis`, `-min-groups n`, `-dry-run`, `-targets ""` (with `-files` or `-fixture` only: no probe network signal).
See `etl parse -h`.

The parser writes **every network** a file carries, and the last step of `make parse` publishes each target network's rates ([below](#publishing-the-rates-by-network)).
`prices/` stays Hive-partitioned by `net=` so the build prunes to one directory.

## Target selection

A file is parsed because [`targets.yaml`](targets.yaml) names it, and nothing else is downloaded.
The index links each target plan to many more files than the one that prices it, such as out-of-state BlueCard shards, and none of those is downloaded.
A target names its files by the last part of their address (`GA_JBNKMED0001.json.gz`), the `name` column of `data/raw/anthem/index_files.parquet` ([discover.md](discover.md)).
`PlanIDs` in `targets.go` reads `data/ingestion/reference/exchange_plans_ga.parquet` ([ingestion/reference/exchange-plans.md](../reference/exchange-plans.md)) for every plan ID on the target's exchange networks, and stops if the file is missing or a network has no plans.

Parse stops unless every name matches exactly one file in this month's index, and `index_file_plans.parquet` links that file to one of those plans.
So a mistyped name, a name mirrored on two hosts, or a file that prices only a plan no longer sold can't slip through ([mrf-model.md](mrf-model.md#one-plan-one-contract)).
Files are parsed in the order the targets name them.
`-files` bypasses selection and doesn't read the plan file.

## When parse downloads a file

Each file parse tries gets a parse record, `data/raw/anthem/parses/<name>.parquet`: one row with its name and address, `status` (`parsed`, `skipped` or `failed`), `reason`, `anthem_updated_at` (the `Last-Modified` of the copy it read), `finished_at`, the download size, and the rows written and dropped.
For each named file, parse reads the record and:

- downloads the file when there's no record, or the record is for another address, because the same name at a new address is a new file;
- asks Anthem (a HEAD request) when it's `parsed` or `skipped`, and downloads it again only if `Last-Modified` differs from `anthem_updated_at`, or the record has none;
- leaves a `failed` file alone, and says why, until someone has looked at it (Critical Rule 2).

Parse stops if Anthem doesn't answer, because it can't tell whether a parsed file is current.
A re-parse writes under the same name, so its Parquet replaces the old.
`skipped` is deliberately not `failed`: nothing went wrong, the file just isn't ours ([below](#the-provider-probe)).

```bash
# what the last parse of each file made of it
docker compose exec ingestion python3 -c "import duckdb; duckdb.sql(\"FROM '/app/data/raw/anthem/parses/*.parquet'\").show()"
make parse FILE=GA_JBNKMED0001.json.gz              # retry a failed file, or re-parse one now
```

`short read` and `stream truncated` in a `failed` reason are worth retrying; `corrupt gzip` and `malformed MRF` mean a bad file.
To stream a file the probe skips, run `docker compose exec ingestion go run . parse -files <name> -min-groups 0 -targets ""`.

## Publishing the rates by network

After the stream, `make parse` runs `ingestion/anthem_rates.py`, which writes what integration reads: `data/ingestion/payer_rates/anthem/` `rates`, `rate_groups` and `rosters`, keyed on Georgia's exchange network ID ([schema.md](../../docs/schema.md)).
A target network's rates are the ones under its `network_patterns`, matched as the probe matches them, in the one parsed file the index links to the network's plans.
The step stops unless each network is one of Georgia's, with a plan and one target; one parsed file prices it ([mrf-model.md](mrf-model.md#one-plan-one-contract)); and the index links every plan on it to that file.
The three files move into place together, so a failed check leaves the last ones, and the step runs even when nothing is downloaded, so a change to `targets.yaml` or Georgia's plans reaches the next build.

## Per-file steps

1. GET the file (or read `FIXTURE`), noting its `Last-Modified` and `Content-Length`, and stream it once via `streamMRF` (`provider_references` must precede `in_network`).
2. **Probe** at the close of `provider_references` - cancel the request and record the file `skipped` if it isn't worth the rest of the download ([below](#the-provider-probe)).
3. Build `provider_group_id → network_name` from `provider_references[].network_name` and stamp it on every provider and price row - structured attribution, no string matching.
4. **GA NPI filter** (on when `data/ingestion/nppes/ga_providers.parquet` exists; `-all-npis` disables): keep a provider row only if its NPI is a Georgia NPPES NPI; a group with no GA NPI is dropped, and every price whose whole roster it was goes too. The parse record counts the dropped rows.
5. For each `negotiated_rate` block: bucket references by network, fingerprint each network-scoped roster (`hashGroupSet`, FNV-64a of sorted reference ids), write a roster's edges to `group_sets` the first time it is seen, and emit one `prices` row per `(network × price)` pointing at the `group_set_id`. Each code goes to `codes/`.
6. Move the Parquet into place and record the file `parsed`, or `failed` with the reason.
7. After the run, write `npi_lookup.parquet` (NPI → TIN across files parsed).

**Completeness gate (before step 6).** The JSON decoder stops at the closing brace, so it can't tell a whole file from one cut short.
The rest of the body is drained so gzip verifies CRC-32 + ISIZE and the HTTP layer surfaces a short body, and bytes read are reconciled against `Content-Length`.
A truncated stream, a malformed entry, a document that never closes, or one with neither section (an error page served 200) is `failed`, not `parsed`.

While a parse runs the file is written under `anthem/.inflight/<name>/` and promoted by atomic rename only on a clean stream, so the build never reads a half-written file.
A failed parse deletes the folder, so one left behind means a parse stopped part way: parse stops on it until someone looks at why and deletes it (Critical Rule 2).

Output layout: [../docs/schema.md](../../docs/schema.md). Open attribution gaps: [../docs/known-gaps.md](../../docs/known-gaps.md).

## The provider probe

The index links a plan to far more files than price it; the rest are national BlueCard-mirror shards, and streaming them to the end is a large download that ends in a rollback.
Anthem writes `provider_references` *before* `in_network`, a small slice of a body that can run to gigabytes, so the parser judges the file there (`ingestion/etl/extraction/probe.go`):

| Signal | Fails when | Off when |
|---|---|---|
| **provider overlap** | fewer than `-min-groups` (default 1) groups survive the GA NPI filter | `-min-groups 0` |
| **network label** | no `provider_references[].network_name` matches a target's `network_patterns` | no target declares `network_patterns`, or `-targets ""` |

Failing either returns `errNoWantedProviders`; `parseRates` cancels the request and records the file `skipped`, with a reason starting `probe: no wanted providers`.
Nothing else is written.

The network signal is the one that matters: a national shard **does** list Georgia NPIs, so overlap alone waves it through.
`network_patterns` is not a row filter - the probe decides only whether to download; a file that passes is written whole.
The probe applies however the file was selected, `-files` included.

### End-of-run guard

`network_patterns` is hand-written, so a stale one fails quietly: a renamed network means the one file carrying the rates is `skipped` and the run exits 0 with nothing ingested.
After the file loop, a target-selected run with the network signal active checks its tally.
If ≥1 file was skipped on the network signal and none was parsed, it prints a banner naming the patterns, then **exits non-zero** if no file the targets name has Parquet on disk, or only **warns** if an earlier run left some (the build's rates are now stale).
`-files` re-parses and probes without `network_patterns` can't trip it.

## Known parser issues

- **No automatic retry.** A timeout / 5xx / short read / stall records the file `failed`, and it waits for `make parse FILE=`. `watchStall` aborts a body delivering zero bytes for `stallTimeout`; there is deliberately no total timeout (a multi-GB body streams for hours).
- **One parse at a time.** Nothing stops two parse runs from reading the same file at once.
- **First-file schema capture is fragile** (double JSON round-trip); an unusual *type* on a known field hard-fails the parse. Unknown extra fields are ignored.
