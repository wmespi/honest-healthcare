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
[parse]    completed / skipped → processing again, once Anthem updates the file
```

| Status | Means |
|---|---|
| `pending` | in the queue, not yet attempted |
| `processing` | a parse is streaming it now (or crashed mid-stream — see Recovery) |
| `completed` | streamed clean, Parquet promoted, `coverage_log` row written |
| `failed` | something went wrong mid-stream — truncation, corrupt gzip, malformed MRF, HTTP error |
| `skipped` | the probe rejected it *before* `in_network`: the file prices nobody on a target plan. `failure_reason` starts `probe: no wanted providers` and carries the reading. Nothing past `provider_references` was downloaded or written |

`skipped` is deliberately not `failed`: nothing went wrong, the file just isn't ours,
so `make db-reset WHAT=failed` leaves it alone, and parse downloads it again only once Anthem updates it.
The probe: [parse.md](parse.md#the-provider-probe).

## Selection and order

The queue is the files [`targets.yaml`](targets.yaml) names that are `pending`, or parsed and updated by Anthem since ([below](#when-anthem-updates-a-file)).
The master index has to link each one to a target plan via `index_file_plans`, and nothing else is downloaded
([parse.md](parse.md#target-selection) has the query and the escape hatches).
Order is `file_size_bytes ASC NULLS LAST, id` (smallest first, fast feedback); every
parse writes the GET's `Content-Length` to `file_size_bytes`, and `make discover`
backfills it ahead of time.

## When Anthem updates a file

A file keeps its id across months ([discover.md](discover.md#the-monthly-rebuild)), and parse keeps two of Anthem's `Last-Modified` dates for it:

- `source_updated_at`: when Anthem last updated the file, as parse last saw it;
- `parsed_updated_at`: the update the last finished parse read, `completed` or `skipped`.

`completed_at` is when that parse finished.
Each run, parse asks Anthem (a HEAD request) about every named file that is `completed` or `skipped`, and records the answer.
It downloads the file again when the answer differs from `parsed_updated_at`, or when that is empty because the parse predates it.
The re-parse keeps the file's id, so its Parquet replaces the old.
A `failed` or `processing` file is never retried this way (Critical Rule 2).
Parse stops if Anthem doesn't answer, because it can't tell whether a parsed file is current.

```sql
SELECT id, status, source_updated_at, parsed_updated_at, completed_at
FROM index_files WHERE address LIKE '%/GA_JBNKMED0001.json.gz';
```

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
plan's, and `targets.yaml` doesn't name them, so they're never downloaded. Other big `anthem/GA_*`
files are *different* GA individual plans — worth parsing only once their plan is in
`targets.yaml`. If the pattern goes stale (Anthem renames the network), the
end-of-run guard catches it ([parse.md](parse.md#end-of-run-guard)). Parse very
large files individually and watch `du -sh data`.
