# `make parse` — Phase 2: MRF → Parquet

*Read this when working on the per-file stream transform — the parser, the Parquet
writers, network attribution, the GA NPI filter, or the provider probe.*

For each file [`targets.yaml`](targets.yaml) names whose `index_files` row is `pending`,
streams the gzipped MRF once and writes Parquet. `make parse` → `etl parse` (package
`etl/extraction`: parser `stream.go`, probe `probe.go`, writers `extraction.go` +
`partition.go`). Needs the NPPES and exchange-plans references first (`make reference`).

| Variable | Effect |
|---|---|
| `ID=n` | parse specific `index_files.id`(s), bypassing target selection (`-file-ids`) |
| `TARGETS=path` | a different target-plan list (default [`targets.yaml`](targets.yaml)) |
| `TEST=1` | test schema + `data-test/`, caps at 1 file |
| `LIMIT=n` | cap files processed |
| `FIXTURE=path` | with `ID=n`: read a local `*.json.gz` instead of downloading |

CLI-only: `-all-npis`, `-min-groups n`, `-dry-run`, `-targets ""` (with `-file-ids`
only: no probe network signal). See `etl parse -h`.

The parser writes **every network** a file carries; which networks a plan is priced
on is a build-step decision. `prices/` stays Hive-partitioned by `net=` so the build
prunes to one directory.

## Target selection

A file is parsed because [`targets.yaml`](targets.yaml) names it, and nothing else is downloaded.
The index links each target plan to many more files than the one that prices it, such as out-of-state BlueCard shards, and none of those is downloaded, even while it's `pending`.
A target names its files by the last part of their index location, without the query (`GA_JBNKMED0001.json.gz`).
`PlanIDs` in `targets.go` reads `data/reference/exchange_plans_ga.parquet` ([reference/exchange-plans.md](../reference/exchange-plans.md)) for every plan ID on the target's exchange networks, and stops if the file is missing or a network has no plans.
For each target, the queue query finds the named files and asks whether `index_file_plans`, which `discover` writes, links each one to one of those plans:

```sql
SELECT f.id, f.location, f.status,
       EXISTS (SELECT 1 FROM index_file_plans p
               WHERE p.file_id = f.id AND p.plan_id = ANY($2)) AS linked
FROM index_files f
WHERE regexp_replace(split_part(f.location, '?', 1), '^.*/', '') = ANY($1)
ORDER BY f.file_size_bytes ASC NULLS LAST, f.id;
```

Parse stops unless every named file matches exactly one queue row and is linked, so a mistyped name, or a file that prices only a plan no longer sold, can't slip through ([mrf-model.md](mrf-model.md#one-plan-one-contract)).
It downloads the named files that are still `pending` and leaves the rest alone.
`-file-ids` bypasses selection and doesn't read the plan file, and `-targets ""` works only with it.
Ordering: smallest first ([queue.md](queue.md)).

## Per-file steps

1. Mark the row `processing`, GET the file (or read `FIXTURE`), stream once via
   `streamMRF` (`provider_references` must precede `in_network`).
2. **Probe** at the close of `provider_references` — cancel the request and mark the
   row `skipped` if the file isn't worth the rest of the download
   ([below](#the-provider-probe)).
3. Build `provider_group_id → network_name` from
   `provider_references[].network_name` and stamp it on every provider and price
   row — structured attribution, no string matching.
4. **GA NPI filter** (on when `data/nppes/ga_providers.parquet` exists; `-all-npis`
   disables): keep a provider row only if its NPI is a Georgia NPPES NPI; a group
   with no GA NPI is dropped, and every price whose whole roster it was goes too.
   `coverage_log.notes` records the drop counts.
5. For each `negotiated_rate` block: bucket references by network, fingerprint each
   network-scoped roster (`hashGroupSet`, FNV-64a of sorted reference ids), write a
   roster's edges to `group_sets` the first time it is seen, and emit one `prices`
   row per `(network × price)` pointing at the `group_set_id`.
6. Upsert new codes into `billing_codes` (`ON CONFLICT DO NOTHING`); write the
   file's `coverage_log` row (one per file; a re-parse replaces it).
7. After the run, write `npi_lookup.parquet` (NPI → TIN across files parsed).
8. Mark the row `completed` (+ `completed_at`, per-file `reporting_entity_*`) or
   `failed` (+ `failure_reason`).

**Completeness gate (before step 8).** The JSON decoder stops at the closing brace,
so it can't tell a whole file from one cut short. The rest of the body is drained so
gzip verifies CRC-32 + ISIZE and the HTTP layer surfaces a short body, and bytes read
are reconciled against `Content-Length`. A truncated stream, a malformed entry, a
document that never closes, or one with neither section (an error page served 200)
is `failed`, not `completed`. `short read` / `stream truncated` are retryable
(`make db-reset WHAT=failed`); `corrupt gzip` / `malformed MRF` stay failed.

While a parse runs the file is written under `anthem/.inflight/{id}/` and promoted by
atomic rename only on a clean stream — serving never reads a half-written file.

Output layout: [../docs/schema.md](../docs/schema.md). Open attribution gaps:
[../docs/known-gaps.md](../docs/known-gaps.md).

## The provider probe

The index links a plan to far more files than price it; the rest are national
BlueCard-mirror shards, and streaming them to the end is a large download that ends
in a rollback. Anthem writes `provider_references` *before* `in_network`, a small
slice of a body that can run to gigabytes, so the parser judges the file there
(`etl/extraction/probe.go`):

| Signal | Fails when | Off when |
|---|---|---|
| **provider overlap** | fewer than `-min-groups` (default 1) groups survive the GA NPI filter | `-min-groups 0` |
| **network label** | no `provider_references[].network_name` matches a target's `network_patterns` | no target declares `network_patterns`, or `-targets ""` |

Failing either returns `errNoWantedProviders`; `parseRates` cancels the request and
marks the row `skipped` with `failure_reason = 'probe: no wanted providers — …'`
([queue.md](queue.md)). Nothing is written and no `coverage_log` row is produced.

The network signal is the one that matters: a national shard **does** list Georgia
NPIs, so overlap alone waves it through. `network_patterns` is not a row filter — the
probe decides only whether to download; a file that passes is written whole. The
probe applies however the file was selected (`-file-ids` included); to stream a
file regardless, use `-file-ids <id> -min-groups 0 -targets ""`.

### End-of-run guard

`network_patterns` is hand-written, so a stale one fails quietly: a renamed network
means the one file carrying the rates is `skipped` and the run exits 0 with nothing
ingested. After the file loop, a target-selected run with the network signal active
checks its tally; if ≥1 file was skipped on the network signal and none completed, it
prints a banner naming the patterns and the labels seen, then **exits non-zero** if no
target file has ever `completed`, or only **warns** if an earlier run landed data
(serving's rates are now stale). `-file-ids` re-parses and probes without
`network_patterns` can't trip it.

## Known parser issues

- **Re-parse writes aren't transactional with the Parquet** — a crash between the
  `billing_codes` upsert / `coverage_log` replace and the Parquet promote leaves them
  briefly inconsistent.
- **`pending → processing` is not atomic** (SELECT then UPDATE); two concurrent parse
  containers could double-process. Fix: `SELECT … FOR UPDATE SKIP LOCKED`.
- **No automatic retry.** A timeout / 5xx / short read / stall marks the file
  `failed`; recovery is manual `make db-reset WHAT=failed`. `watchStall` aborts a body
  delivering zero bytes for `stallTimeout`; there is deliberately no total timeout (a
  multi-GB body streams for hours).
- **HEAD size is a hint.** The parse GET is authoritative and overwrites
  `file_size_bytes`; a HEAD/GET mismatch is a signed-URL rotation, not corruption.
- **Single `pgx.Conn`, not a pool** — serializes if parsing is parallelized.
- **First-file schema capture is fragile** (double JSON round-trip); an unusual *type*
  on a known field hard-fails the parse. Unknown extra fields are ignored.
