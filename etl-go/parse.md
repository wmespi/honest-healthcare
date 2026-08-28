# `make parse` — Phase 2: MRF → Parquet

*Read this when working on the heavy per-file stream transform — the parser, the
Parquet writers, network attribution, or the GA NPI filter.*

For each `index_files` row with `status = 'pending'`, streams the gzipped MRF once
and writes Parquet. IO-bound, embarrassingly parallel (not yet parallelized).
`make parse` → `etl-go parse`.

| Variable | Effect |
|---|---|
| `ID=n` | parse specific `index_files.id`(s), bypass queue order (`-file-ids`) |
| `GA=1` | order the queue by `gaPriorityExpr` (GA / individual first) — see [queue.md](queue.md) |
| `TEST=1` | test schema + `data-test/`, caps at 1 file |
| `LIMIT=n` | cap files processed |
| `FIXTURE=path` | with `ID=n`: read a local `*.json.gz` instead of downloading (offline) |

CLI-only flags (no `make` var yet): `-all-npis`, `-networks "GA *"`, `-all-networks`,
`-dry-run`. See `etl-go parse -h`.

## Per-file steps

1. Mark the row `processing`, GET the gzipped file (or read `FIXTURE`), stream once
   via `streamMRF` (the shared token scanner; `provider_references` must precede
   `in_network`).
2. Build `provider_group_id → network_name` from `provider_references[].network_name`
   (a structured array, e.g. `["GA Blue Value HIX Individual Network"]`) and stamp
   `network_name` onto every provider and price row — **structured attribution, no
   string matching**.
3. **GA NPI filter** (default on when `data/nppes/ga_providers.parquet` exists): a
   provider row is kept only if its NPI is a Georgia NPPES NPI; a provider group
   with no GA NPI is dropped, and every price row whose whole roster it was goes
   too. `-all-npis` disables it. GA-plan-specific files lose ~0.4% of prices;
   BlueCard-mirror / out-of-state files lose 85–100% (many parse to zero rows).
   `coverage_log.notes` records the drop counts.
4. For each `negotiated_rate` block: bucket provider references by network,
   fingerprint each network-scoped roster (`hashGroupSet` = FNV-64a of sorted
   provider_reference ids), and — first time that roster is seen in the file —
   write its membership edges to `group_sets`. Emit one `prices` row per
   `(network × price)` pointing at the `group_set_id`.
5. Upsert each new billing code into Postgres `billing_codes`
   (`ON CONFLICT (billing_code) DO NOTHING`).
6. Write one `coverage_log` row (row counts, new codes/NPIs/TINs, distinct
   networks/settings/billing-classes) — observational, never read by the ETL.
7. After the whole run, write `npi_lookup.parquet` (dedup NPI → TIN across all
   files parsed this run).
8. Mark the row `completed` (+ `completed_at`, + per-file `reporting_entity_*`), or
   `failed` (+ `failure_reason`).

While a parse runs, everything for the file is written under
`anthem/.inflight/{id}/` and promoted with an atomic rename only on a clean
stream — the backend never reads a half-written file.

Output layout and column lists: [../docs/schema.md](../docs/schema.md).
The `-networks` allowlist and why it's skipped for `anthem/GA_*` files:
[../docs/known-gaps.md](../docs/known-gaps.md).

## Network allowlist

Default `GA *` (prefix match), applied to BlueCard-mirror / other-state files only —
**skipped for `anthem/GA_*` plan-specific files** (`isGAPlanSpecific` trusts the
filename; their `network_name` labels vary — `"GA Blue Value HIX Individual
Network"` in one file, `"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL"` in
another). A user-set `-networks` value applies everywhere. `-all-networks` disables it.

## Known parser issues

Harmless for a single-operator sequential run; real at scale.

- **No dedup on re-parse.** Parquet is keyed by `index_files.id` — re-parsing a
  file overwrites its files cleanly. But the Postgres `billing_codes` upsert and
  `coverage_log` append are not transactional with the Parquet write.
- **`pending → processing` is not atomic** — SELECT then UPDATE in two statements.
  Two concurrent parse containers could double-process. Fix: `SELECT … FOR UPDATE
  SKIP LOCKED`.
- **No retry on transient errors** — a mid-stream timeout / 5xx marks the file
  `failed`. Recovery is `make db-reset WHAT=failed`.
- **Single `pgx.Conn`, not a pool** — fine sequential; serializes if parsing is
  parallelized.
- **First-file schema capture is fragile** — double JSON round-trip; a first file
  with unusual fields silently zero-values struct fields.
