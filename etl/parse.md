# `make parse` — Phase 2: MRF → Parquet

*Read this when working on the heavy per-file stream transform — the parser, the
Parquet writers, network attribution, or the GA NPI filter.*

For each `index_files` row that is `pending` **and serves a target plan**, streams
the gzipped MRF once and writes Parquet. IO-bound, embarrassingly parallel (not
yet parallelized). `make parse` → `etl parse` (package `etl/extraction`; the
parser is `stream.go`, the Parquet writers `extraction.go` + `partition.go`).

| Variable | Effect |
|---|---|
| `ID=n` | parse specific `index_files.id`(s), bypassing target selection (`-file-ids`) |
| `TARGETS=path` | a different target-plan list (default [`etl/targets.yaml`](targets.yaml)) |
| `TEST=1` | test schema + `data-test/`, caps at 1 file |
| `LIMIT=n` | cap files processed |
| `FIXTURE=path` | with `ID=n`: read a local `*.json.gz` instead of downloading (offline) |

CLI-only flags (no `make` var yet): `-all-npis`, `-min-groups n`, `-dry-run`, and
`-targets ""` (no target filter — every pending file, and no probe network
signal). See `etl parse -h`.

The parser writes **every network** a file carries. Which networks a plan is
actually priced on is a build-step decision, not a parse-step one; `prices/` stays
Hive-partitioned by `net=` so that step prunes to one directory.

## Target selection

A file is parsed because the master index says it serves a plan we are pricing —
not because of what its URL looks like. [`targets.yaml`](targets.yaml) lists the
plans; `discover` wrote the link into `index_file_plans`; the queue query is an
`EXISTS` semi-join over it (`targets.go:PlanMatchSQL` builds the predicate, all
patterns bound as parameters):

```sql
SELECT f.id, f.location FROM index_files f
WHERE f.status = 'pending'
  AND EXISTS (SELECT 1 FROM index_file_plans p
              WHERE p.file_id = f.id AND (p.plan_name ILIKE $1 OR p.plan_id LIKE $2))
ORDER BY f.file_size_bytes ASC NULLS LAST, f.id;
```

Adding a plan to `targets.yaml` is the whole of "parse this plan's files"; no
code changes. `-file-ids` bypasses selection entirely for one-off runs (re-parse
a known file, drive a fixture, probe a shard), and `-targets ""` disables it.
Ordering within the selected set is unchanged — smallest file first
([queue.md](queue.md)).

## Per-file steps

1. Mark the row `processing`, GET the gzipped file (or read `FIXTURE`), stream once
   via `streamMRF` (the shared token scanner; `provider_references` must precede
   `in_network`).
1a. **The probe** — at the close of `provider_references`, decide whether the rest
   of the file is worth downloading. If not, cancel the request and mark the row
   `skipped`. See [The provider probe](#the-provider-probe) below.
2. Build `provider_group_id → network_name` from `provider_references[].network_name`
   (a structured array, e.g. `["GA Blue Value HIX Individual Network"]`) and stamp
   `network_name` onto every provider and price row — **structured attribution, no
   string matching**.
3. **GA NPI filter** (default on when `data/nppes/ga_providers.parquet` exists): a
   provider row is kept only if its NPI is a Georgia NPPES NPI; a provider group
   with no GA NPI is dropped, and every price row whose whole roster it was goes
   too. `-all-npis` disables it. GA-plan-specific files lose ~0.4% of prices;
   BlueCard-mirror / out-of-state files lose 85–100% (many parse to zero rows —
   which is what the probe now catches before the download). `coverage_log.notes`
   records the drop counts.
4. For each `negotiated_rate` block: bucket provider references by network,
   fingerprint each network-scoped roster (`hashGroupSet` = FNV-64a of sorted
   provider_reference ids), and — first time that roster is seen in the file —
   write its membership edges to `group_sets`. Emit one `prices` row per
   `(network × price)` pointing at the `group_set_id`.
5. Upsert each new billing code into Postgres `billing_codes`
   (`ON CONFLICT (billing_code) DO NOTHING`).
6. Write the file's `coverage_log` row (row counts, new codes/NPIs/TINs, distinct
   networks/settings/billing-classes) — one row per file, a re-parse replaces it.
   Observational: the ETL never reads it, but `make cov-report` gates on it.
7. After the whole run, write `npi_lookup.parquet` (dedup NPI → TIN across all
   files parsed this run).
8. Mark the row `completed` (+ `completed_at`, + per-file `reporting_entity_*`), or
   `failed` (+ `failure_reason`).

**Completeness gate (before step 8, issue #52).** The JSON decoder stops at the
document's closing brace, so on its own it can't tell a whole file from one whose
download was cut short in the trailing bytes. After the decoder returns, the rest
of the compressed body is drained so gzip verifies its CRC-32 + ISIZE and the
HTTP layer surfaces a short body, and the bytes read are reconciled against
`Content-Length`. A truncated stream, a malformed `provider_references` /
`in_network` entry, a document that never closes, or one with neither section
(an error page served 200, an empty shard) is now marked `failed`, not
`completed`. `short read` / `stream truncated` failures are retryable
(`make db-reset WHAT=failed`); `corrupt gzip` / `malformed MRF` are kept failed.

While a parse runs, everything for the file is written under
`anthem/.inflight/{id}/` and promoted with an atomic rename only on a clean
stream — the serving layer never reads a half-written file.

Output layout and column lists: [../docs/schema.md](../docs/schema.md).
What network attribution still doesn't give the serving layer:
[../docs/known-gaps.md](../docs/known-gaps.md).

## The provider probe

The index links a plan to far more files than actually price it: ~245 files carry
the Blue Value link, one carries its network. The rest are national
BlueCard-mirror shards, and streaming them to the end cost ~40 GB of download that
ended in a rollback.

So the parser judges a file at the close of `provider_references` — which Anthem
writes *before* `in_network`, and which is a few MB of a body that can run to
gigabytes. Two signals, both read in that window (`etl/extraction/probe.go`):

| Signal | Fails when | Off when |
|---|---|---|
| **provider overlap** | fewer than `-min-groups` (default 1) provider groups survive the GA NPI filter | `-min-groups 0` |
| **network label** | no `provider_references[].network_name` matches a target's `network_patterns` | no target declares `network_patterns`, or `-targets ""` |

Either failing abandons the file: `streamMRF` returns `errNoWantedProviders`,
`parseRates` cancels the HTTP request through the `cancel` from `openMRF`, logs
the bytes read at the abort, and marks the row `skipped` with
`failure_reason = 'probe: no wanted providers — …'` ([queue.md](queue.md)).
Nothing is written and no `coverage_log` row is produced — the file was never
parsed.

The network signal is the one that matters, and the reason it isn't only an NPI
overlap check: a national BlueCard shard **does** list Georgia NPIs, so overlap
alone waves it through. Only the label says the rates aren't the plan's.

The probe applies however the file was selected — `-file-ids` included, because
"does this file price anyone on a target plan?" doesn't depend on how the file
reached the queue. To stream a file regardless, run it with `-min-groups 0
-targets ""`.

`network_patterns` is **not** a row filter, which is what distinguishes it from
the `-networks "GA *"` allowlist it replaced (removed in #98). The allowlist
dropped rows *inside* a file it had already paid to download, and it dropped real
target rows whenever a file labelled its networks config-style (`"EXCHANGES
SPECIALIST GATEKEEPER ON INDIVIDUAL"`) rather than `GA …`. The probe decides only
whether to download; a file that passes is written whole, every network included,
and the build step selects.

## Known parser issues

Harmless for a single-operator sequential run; real at scale.

- **Re-parse writes aren't transactional with the Parquet.** Parquet is keyed by
  `index_files.id` (overwritten cleanly) and `coverage_log` is replaced per file,
  but the `billing_codes` upsert and the `coverage_log` DELETE+INSERT aren't in a
  transaction with the Parquet promote — a crash between them leaves them
  briefly inconsistent.
- **`pending → processing` is not atomic** — SELECT then UPDATE in two statements.
  Two concurrent parse containers could double-process. Fix: `SELECT … FOR UPDATE
  SKIP LOCKED`.
- **No automatic retry.** A mid-stream timeout / 5xx / short read / stall marks
  the file `failed`; recovery is a manual `make db-reset WHAT=failed` (which
  re-queues the retryable reasons and leaves `corrupt gzip` / `malformed MRF` /
  `HTTP 4xx` failed). The fetch client bounds connect / TLS / response-header
  waits, and `watchStall` aborts a body transfer that delivers zero bytes for
  `stallTimeout` (3 min) — so a hung socket fails the file instead of blocking
  the queue. There is deliberately no *total* timeout: a multi-GB body streams
  for hours.
- **HEAD size is a hint, not verified.** `make size` (HEAD) and the parse GET can
  report different `Content-Length`; the parse GET is authoritative — it fetches
  the bytes actually parsed and overwrites `file_size_bytes` — and its body is
  reconciled against its own length. A deliberate non-check: a HEAD/GET mismatch
  on a CDN is a signed-URL rotation, not a corrupt file.
- **Single `pgx.Conn`, not a pool** — fine sequential; serializes if parsing is
  parallelized.
- **First-file schema capture is fragile** — double JSON round-trip; a first file
  with an unusual *type* on a known field now hard-fails the parse (was: silently
  zero-valued the field). Unknown extra fields are still ignored.
