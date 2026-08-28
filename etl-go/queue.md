# The parse queue — priority, lifecycle, recovery

*Read this when working on queue ordering, GA prioritization, or recovering stuck
`index_files` rows.*

## Status lifecycle

```
[discover] → pending
[parse]    → pending → processing → completed
                                 ↘ failed
```

Default order: `file_size_bytes ASC NULLS LAST, id` (smallest first — fast
feedback). `Content-Length` from the parse GET is written to `file_size_bytes` on
every parse; `make size` backfills it ahead of time so the queue can be size-ordered.

## GA prioritization (`make parse GA=1`)

`gaPriorityExpr` (`priority.go`) scores each pending row 0–3 for the primary use
case (individual-market Georgia rates). All signals are deterministic/structural —
no regex, no plan-name matching:

- `market_types ∋ 'individual'`
- `plan_states ∋ 'GA'`
- `hios_issuer_ids ∩ {49046, 45334, 44113}`
- `location` is an `…amazonaws.com/anthem/GA_…` plan-specific file

Tiers: **3** = individual AND a GA signal · **2** = individual · **1** = a GA signal
· **0** = the rest. Order becomes `gaPriorityExpr DESC, file_size_bytes ASC NULLS
LAST, id`.

The `anthembcbsga.mrf.bcbs.com` host is deliberately **not** a signal — it's the
BlueCard mirror and serves every Blues plan's files.

## Recovery

Do **not** auto-reset on startup — investigate repeated failures on a specific file
first (a bad file, not a transient error — Critical Rule 2).

```bash
make db-reset WHAT=processing   # stale 'processing' rows after a crash → pending
make db-reset WHAT=failed       # transiently-failed rows → pending
                                #   (keeps bad-gzip / unexpected-EOF / HTTP 4xx failed)
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
to Blue Value.
