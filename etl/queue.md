# The parse queue — selection, lifecycle, recovery

*Read this when working on queue selection, ordering, or recovering stuck
`index_files` rows. The queue query + the target predicate live in
`etl/extraction`; the HEAD-backfill (`make size`) in `etl/discovery`.*

## Status lifecycle

```
[discover] → pending
[parse]    → pending → processing → completed
                                 ↘ failed
                                 ↘ skipped
```

| Status | Means |
|---|---|
| `pending` | in the queue, not yet attempted |
| `processing` | a parse is streaming it now (or crashed mid-stream — see Recovery) |
| `completed` | streamed clean, Parquet promoted, `coverage_log` row written |
| `failed` | something went wrong mid-stream — truncation, corrupt gzip, malformed MRF, HTTP error |
| `skipped` | the probe rejected it *before* `in_network`: the file prices nobody on a target plan. `failure_reason` starts `probe: no wanted providers` and carries the reading. Nothing past `provider_references` was downloaded, nothing written, no `coverage_log` row |

`skipped` is deliberately not `failed`. Nothing went wrong — the file simply
isn't ours — so `make db-reset WHAT=failed` leaves it alone and the queue never
pays to download it again. The probe and its two signals:
[parse.md](parse.md#the-provider-probe).

## Selection — target plans, not filenames

The queue is the pending rows the master index links to a plan in
[`targets.yaml`](targets.yaml), via `index_file_plans`. See
[parse.md](parse.md#target-selection) for the query and the escape hatches
(`-file-ids`, `-targets ""`).

This replaced `gaPriorityExpr`, which *ordered* every pending file by a 0–3 score
built from `market_types ∋ 'individual'`, `plan_states ∋ 'GA'`, a set of Anthem
GA `hios_issuer_ids`, and an `…amazonaws.com/anthem/GA_…` filename check. It was
a proxy for "serves the plan we care about" built out of signals that correlate
with it. Now the index answers the question directly, so the proxy is gone and
selection is exact rather than a ranking — a file either serves a target plan or
it is not in the queue.

## Order

`file_size_bytes ASC NULLS LAST, id` (smallest first — fast feedback).
`Content-Length` from the parse GET is written to `file_size_bytes` on every
parse; `make size` backfills it ahead of time so the queue can be size-ordered.

## Recovery

Do **not** auto-reset on startup — investigate repeated failures on a specific file
first (a bad file, not a transient error — Critical Rule 2).

```bash
make db-reset WHAT=processing   # stale 'processing' rows after a crash → pending
make db-reset WHAT=failed       # transiently-failed rows → pending
                                #   (keeps bad-gzip / unexpected-EOF / HTTP 4xx failed,
                                #    and never touches 'skipped')
```

To re-queue a `skipped` file — the target list changed, or you want to see what
it holds — set it back to `pending` by hand and parse it with the probe relaxed:

```bash
make psql   # UPDATE index_files SET status='pending', failure_reason=NULL WHERE id=…;
docker compose exec etl go run . parse -file-ids <id> -min-groups 0 -targets ""
```

## Large GA files

The `anthem/GA_*` plan-specific files above ~1 MB (`GA_HXRCMED0001` ~2.1 GB,
`GA_AHPPMEDGAHF*` 3–7 GB) are the richest source for the target plan. The
`prices` + `group_sets` split makes them tractable (file 28947: 723M flat rows →
far fewer), but parse them individually and watch `du -sh data`.

`GA_JBNKMED0001` (id 21057, 1.1 MB, 682k rows) is the target plan's **only** clean
source and the one file that uses the tidy `"GA Blue Value HIX Individual Network"`
label. Other big `anthem/GA_*` files are *different* GA individual plans
(Pathway/Gatekeeper HMO, etc.) — parsing them broadens GA coverage but adds nothing
to Blue Value, which is precisely why the queue no longer selects on the `GA_`
filename: those files are only worth parsing once their plan is in
[`targets.yaml`](targets.yaml).

Two different counts, both true, easy to conflate: the *index* links Blue Value to
many files via `index_file_plans` — most of them BlueCard-mirror shards the index
says serve the plan but that carry no Blue-Value-labelled network rows once parsed.
`GA_JBNKMED0001` is the only one of those linked files whose
`provider_references[].network_name` actually matches. Those shards now end as
`skipped` at the front of the stream rather than as a full download and a
rollback — the probe judges them on the network label, because their GA-NPI
overlap is real ([parse.md](parse.md#the-provider-probe)).

If a re-discover ever selects the shards but *not* `GA_JBNKMED0001` (its rates
landed under a network Anthem renamed, so the pattern no longer matches), the
run's end-of-run guard catches it — a non-zero exit on a first run, a loud
warning once data has landed ([parse.md](parse.md#the-end-of-run-guard-110)).
