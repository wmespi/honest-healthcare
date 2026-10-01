# The parse queue — selection, lifecycle, recovery

*Read this when working on queue selection, ordering, or recovering stuck
`index_files` rows. The queue query lives in `etl/extraction`; the size backfill
(`make discover`) in `etl/discovery`.*

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
| `skipped` | the probe rejected it *before* `in_network`: the file prices nobody on a target plan. `failure_reason` starts `probe: no wanted providers` and carries the reading. Nothing past `provider_references` was downloaded or written |

`skipped` is deliberately not `failed`: nothing went wrong, the file just isn't ours,
so `make db-reset WHAT=failed` leaves it alone and the queue never re-downloads it.
The probe: [parse.md](parse.md#the-provider-probe).

## Selection and order

The queue is the pending rows the master index links to a plan in
[`targets.yaml`](targets.yaml), via `index_file_plans` — exact, not a ranking
([parse.md](parse.md#target-selection) has the query and the escape hatches).
Order is `file_size_bytes ASC NULLS LAST, id` (smallest first, fast feedback); every
parse writes the GET's `Content-Length` to `file_size_bytes`, and `make discover`
backfills it ahead of time.

## Recovery

Do **not** auto-reset on startup — investigate repeated failures on a file first (a
bad file, not a transient error — Critical Rule 2).

```bash
make db-reset WHAT=processing   # stale 'processing' rows after a crash → pending
make db-reset WHAT=failed       # transiently-failed rows → pending
                                #   (keeps bad-gzip / unexpected-EOF / HTTP 4xx failed,
                                #    and never touches 'skipped')
```

To re-queue a `skipped` file (the target list changed, or you want to see what it
holds), set it back to `pending` and parse it with the probe relaxed:

```bash
make psql   # UPDATE index_files SET status='pending', failure_reason=NULL WHERE id=…;
docker compose exec etl go run . parse -file-ids <id> -min-groups 0 -targets ""
```

## Target-plan files

The index links the target plan to many files, but only one carries a
`provider_references[].network_name` that matches its `network_patterns`
(`GA_JBNKMED0001`, labelled `"GA Blue Value HIX Individual Network"`); the others
are BlueCard-mirror shards whose GA-NPI overlap is real but whose rates aren't the
plan's, so they end `skipped` at the front of the stream. Other big `anthem/GA_*`
files are *different* GA individual plans — worth parsing only once their plan is in
`targets.yaml`. If the pattern goes stale (Anthem renames the network), the
end-of-run guard catches it ([parse.md](parse.md#end-of-run-guard)). Parse very
large files individually and watch `du -sh data`.
