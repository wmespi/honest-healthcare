# Known gaps

*The running list of things that are wrong, missing, or deferred. Parser-internal
issues that only bite at scale are in [../etl/parse.md](../etl/parse.md). Where
the product is headed — and which of these gaps that closes — is in
[direction.md](direction.md).*

## Attribution

- **Plan → *file* is derived now; plan → *network* still isn't.** The index's
  `reporting_plans` ↔ `in_network_files` linkage is captured end-to-end as
  `index_file_plans` (#97), so "which files serve `BLUE VALUE IND NETWORK HMO -
  INDIV - ANTHEM`" is a query, and `etl parse` selects the queue on it. What that
  does *not* give is the plan → `network_name` map the serving layer wants: the
  plan name comes from the index, the network label from a rate file's
  `provider_references`, and the two share no key. **Interim (GH #33):** a
  hand-curated `serving/plan_networks.json` maps friendly plan names → network,
  served by `/plans` and shown as a "Your plan" section in the network picker.
  Today it holds one entry (Blue Value). Deriving it — from HIOS `plan_id` + a
  CMS public-use file, or by intersecting a target plan's files with the networks
  those files carry — is still open.
- **`network_name` is NOT uniform across files.** `GA_JBNKMED0001` (id 21057, the
  target plan's only clean source) uses `"GA Blue Value HIX Individual Network"`;
  other `anthem/GA_*` files use config-style labels
  (`"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL"`). The `-networks "GA *"`
  allowlist that silently dropped those rows is gone (#98) — the parser writes
  every network a file carries and the build step selects — but the labels still
  don't line up between files, so nothing yet tells the serving layer that a
  config-style label belongs to a given plan. Same missing plan → network map as
  the bullet above. Every other big `anthem/GA_*` file is a *different* GA
  individual plan, not Blue Value.
- **A target plan's `network_patterns` are hand-written.** The parse probe (#98)
  decides whether a file is worth downloading by matching
  `provider_references[].network_name` against the patterns on that target in
  [`etl/targets.yaml`](../etl/targets.yaml) — `"GA Blue Value HIX*"` for Blue
  Value. Those were read off the one file that carries the label, not derived:
  adding a plan means finding its network label by hand, and a plan whose label is
  guessed wrong has every one of its files `skipped`. It fails loudly —
  `failure_reason` names the labels the file actually carried, and the end-of-run
  guard (#110) exits non-zero when every target file network-skips and nothing has
  ever completed — but deriving the patterns is the same open problem as the plan
  → network map.
- **Coverage confirmed for the target network, not quantified here.** Parsing
  `GA_JBNKMED0001` took the target network from zero attributed rates to
  populated (the `coverage/` one-off snapshots this replaced are gone —
  #94). Don't trust a point-in-time count in a doc; regenerate one with `make
  cov-probe LABEL=<label>` / `make cov-report`.

## Data scope

- **Consumer rate views show outpatient professional fee-for-service only.**
  `serving/data_sources.outpatient_scope()` — `billing_class='professional' AND
  setting IN ('outpatient','both') AND negotiation_arrangement='ffs' AND
  negotiated_type IN ('fee schedule','negotiated')` — gates `/rates/providers`,
  `/rates/by_network`, `/rates/quote` and the no-code `/rates/distribution`
  overview (via `rate_hist.scope`). What's excluded and still in the store:
  - **`negotiated_type='percentage'`** (~9M CPT rows) — `negotiated_rate` is a
    percent of billed charges (`60.0` = 60%), not dollars. Was rendering as
    "$60.00".
  - **`per diem`** (per inpatient day) and **`derived`** (algorithmic fallback).
  - **`billing_class='institutional'`** — facility/UB-04 lines.
  - **`setting='inpatient'`** — inpatient-only rates. `setting='inpatient'` on
    the scoped routes is *ignored*, not honoured.
  - **`negotiation_arrangement='bundle'`** — price covers other services too.
  A dedicated inpatient / facility view is the follow-up.
- **HCPCS drug codes (J-codes) inflate pooled means.** `outpatient_scope()` does
  *not* exclude physician-administered drugs — some are gene therapies /
  biologics priced $3–4.5M per course (`J1411`, `J1413`, `J3391`…). The no-code
  network overview is served off `rate_hist` (buckets cap at $5k) so its
  min/median/max stay sane, but the volume-weighted `avg` still skews high and a
  code-level drill-down on a J-code shows the real millions. Not shoppable care —
  a `drug` scope flag (or dropping HCPCS J/Q from the consumer views) is the
  proper fix; deferred. **Partial mitigation (GH #61):** `make mpfs` builds a
  Medicare allowed amount per code (`data/reference/mpfs_ga.parquet`) and
  `/rates/quote` carries `medicare_allowed` + a `vs_medicare` ratio, so a
  drug-code rate can be shown against its benchmark ("$4.4M vs Medicare's $2k")
  rather than as a bare number. Using the benchmark to filter / down-weight rows
  far above Medicare in the pooled `avg` is the follow-up.
- **Sentinel / placeholder rates — no discrete tell, cut by a per-code ceiling.**
  Anthem fills the MRF-required positive `negotiated_rate` with $0.01–$1.50 (and
  proportionally-tiny values on big-ticket codes) for not-separately-priced
  codes. They share `fee schedule` / `ffs` / `professional` with the real rates —
  no field distinguishes them, and the exact values are a long tail, not a fixed
  set. Jobs 1–3 drop rows at or below `_sentinel_ceiling` = `GREATEST($1.00, 5% ×
  the code's rate_hist median)`. The histogram / overview still show them (min
  `$0`), and the ceiling is deliberately loose (5% × median) so a genuinely
  cheap contract survives — a tighter cut needs the discrete signal we don't
  have ([GH #51](https://github.com/wmespi/honest-healthcare/issues/51)).
- **`/networks`, `/billing_codes`, `/procedure_categories` are not scoped** —
  `rate_summary` / `code_rollup` sum every scope. They answer "what's priced in
  this network", not "what does an outpatient visit cost".
- **`00810` (anesthesia) has no CPT fee-schedule rate in any parsed file** —
  anesthesia is typically priced in base units, not a flat CPT amount. Known,
  not a bug: tracked as `KNOWN_GAP_CODES` in
  `serving/tests/test_coverage.py` and handled in `scripts/frontend_smoke.py`.

## Provider ↔ procedure

- **`plausibility()` is a heuristic; CMS utilization is the evidence layer.** A
  social worker in a rollup provider group "has" a $14k surgical rate because
  Anthem's `provider_references` are network-administration buckets, not
  practices. Two reference builds add real evidence:
  - `make cms-utilization` → `did_bill(npi, code)` (Tier 1) from CMS "by Provider
    and Service" — [reference/cms-utilization.md](../reference/cms-utilization.md).
  - `make specialty-profiles` → "typical for this specialty" (Tier 2) — codes
    billed by ≥3% of the provider's specialty —
    [reference/specialty-profiles.md](../reference/specialty-profiles.md).

  - `make doctors-clinicians` → a real group-practice identity (`org_pac_id` +
    `org_name`) and the hospital-affiliation `ccn`↔`npi` bridge, independent of
    Anthem's buckets — [reference/doctors-clinicians.md](../reference/doctors-clinicians.md).
    Surfaced on `provider_card` (`group_name`, `years_in_practice`,
    `hospital_affiliations`) and `/providers/search`. **Open:** `/rates/providers`
    still groups practice rows on Anthem's `tin_value`, not `org_pac_id` (a
    follow-up that touches `serving/routers/rates.py`); Medicare-enrolled
    clinicians only; CMS directory accuracy is imperfect; `org_pac_id` and
    `tin_value` are not reconciled.

  `/providers/{npi}/procedures` defaults to `tier=plausible` (billed + typical
  only, with a `group_count`); `/rates/quote` returns `tier` + `medicare_utilization`.
  Frontend: cost-card evidence line, menu badges + a "show all N group-contracted
  rates" expander. A strict Tier-1 filter keeps ~47% of priceable providers; Tier
  1+2 keeps ~94%.

  Remaining limits: **Part B only** (no pediatric / pure-commercial / cash), rows
  with ≤10 beneficiaries are **excluded entirely** (so `billed: False` is weak),
  ~2-year lag, practitioner (type-1) signal, **single year (2024)** — "stopped
  doing it" looks like "never". Georgia has no All-Payer Claims Database, so
  there's no public commercial-utilization source to widen this.
  [GH #14](https://github.com/wmespi/honest-healthcare/issues/14).

- **`has_rates` / `n_with_rates` are corpus-wide unless a `network_name` is
  passed.** `/providers/search` and `/specialties` default to the `npi_lookup`
  (any-Anthem-network) signal; pass `network_name` and they scope to that
  plan's `providers` roster instead (`_rated_npi()`). The plan-first frontend
  always passes it. The `providers`-roster proxy is "the NPI sits in a
  network-attributed provider group", not "a priced row was verified for this
  NPI in this network" — close but not identical; the exact check would join
  `prices ⨝ group_sets`. Deferred with the scale work
  ([#10](https://github.com/wmespi/honest-healthcare/issues/10)).

## Scale / performance

- **The serving-table build (`make build`) is a full rebuild, not incremental.**
  `/networks`, `/billing_codes`, `/procedure_categories`, `/rates/distribution`,
  and `/rates/by_network` all read the build's `rate_hist` /
  `cross_network_rollup` / `code_dim` — there is no live-scan fallback any more
  (#100: a missing build is a `503`, not a slower degraded path). The build
  recomputes the whole store each run (~193 s at 645M price rows / 54 networks
  on the box this was measured on); per-file partials → merge is a future
  refinement. It is also **not auto-triggered** — run it after each `make parse`
  batch, or the browse tables go stale.
- **`/rates/quote` and the provider menu can take ~2 s for a provider whose
  NPI sits in every distinct source file the corpus has.** Both resolve an NPI
  to its `(file_id, group_set_id)` sets via `group_members ⨝ group_sets`; a
  `file_id` predicate prunes that join for a typical provider, but the corpus
  has only ~30 distinct source files (one file's `provider_references` can list
  many networks), and a provider who bills through a large shared-network file
  can appear in most or all of them — pruning has nothing to exclude. Measured
  equally slow (actually slightly slower) against the pre-#100 raw
  per-file-glob architecture, so this is a pre-existing corpus characteristic,
  not a repoint regression. A precomputed `(npi) → group_set_id[]` index would
  remove the join entirely; not built.
- **`/rates/by_network` `n_groups` and `n_providers` measure different things —
  and `n_providers` is no longer computed at all.** `n_groups` (from
  `cross_network_rollup`, off the roster-weighted `rate_hist` CDF) counts a
  provider group once per distinct `(file_id, provider_group_id)` it appears
  in — one practice recurs as a group across every file that lists it — so at
  corpus scale it can far exceed a distinct-NPI count. The old live join that
  computed `n_providers` here is gone (the rollup can't derive it); the field
  is `null` until a distinct-NPI rollup exists — same root cause as the next
  bullet, [GH #48](https://github.com/wmespi/honest-healthcare/issues/48).
- **`rate_hist.n` (the roster-weighted "provider_groups" ranking hint behind
  `/billing_codes`, `/procedure_categories`, the network overview) is inflated,
  not a distinct count.** It sums each price's roster size, so a provider group
  in several of a code's rosters is counted per-roster (same as the retired
  `VOL_CTE` / `code_rollup`). Ordering is fine; **never render it as "N
  providers".** A real `(payer, code) → n_providers` distinct rollup:
  [GH #48](https://github.com/wmespi/honest-healthcare/issues/48).
- **`/rates/providers` + `/rates/quote` require a `network_name`** (`400
  {"code": "network_required"}`). The unpruned cross-network expansion spilled
  15–60 GB and a precomputed `(code, network, tin) → rate` rollup is infeasible
  here (one common code = 264k rollup rows; the build OOM'd on this box) — so the
  view is plan-scoped instead. With a network the `_prac` temp-table pass
  (`rates ⨝ group_sets ⨝ group_members ⨝ provider_dim`, one code + one network)
  is ~0.4 s. The plan-first front door ([direction.md](direction.md) Flow A)
  makes this the natural flow anyway. Per-row `n_groups` over-counts a provider
  group that spans several TINs (it's "groups this practice's rate reaches you
  through") — the aggregate `n_groups` in `summary` is the true distinct count
  over the query's rows (not collapsed by rule 5 — reading (a) of the #100
  checkpoint decision).
- **`/rates/distribution` for a code without a `network_name` serves off
  `rate_hist`**, not the live expansion (which was ~27 s). `provider_groups` /
  `n_providers` come back `null` there; the histogram bars are $25-bucket, not
  exact-rate.
- **`/rates/providers` `ga_hospitals_only` filters the rows but not `summary`** —
  the min/median/max still describe every practice. Niche param; revisit if used.
- **Backend now holds one process-wide DuckDB database** (`db()` returns a
  `cursor()` on it, not a fresh connection per request) — Parquet metadata /
  zonemaps stay warm across requests. This closes the last #10 scale item on
  the connection side; a query-result cache is a further-out follow-up.
- **`coverage_log.n_ga_hospital_npis`** is never populated (the NPPES join happens
  at query time). Backfill with a post-batch `providers ⨝ ga_providers` query if
  the number is wanted in the log.

## Operational

- **`make nppes` write is not atomic** — `ga_providers.parquet` is briefly 0 bytes
  during a re-extract and serving-layer queries touching it 500. Run when the API is
  idle.
- **Monthly index churn.** `location` is a signed URL with a `YYYY-MM_` path prefix
  — not a cross-month key. Re-discover monthly and prune the prior month
  ([../etl/discover.md](../etl/discover.md)). A query-stripped `url_path`
  column would fix it.
- **Large GA files.** `GA_HXRCMED0001` (~2.1 GB), `GA_AHPPMEDGAHF*` (3–7 GB) are the
  richest Blue-Value-adjacent sources. The `prices` + `group_sets` split makes them
  tractable; still parse individually and watch `du -sh data`.

## Deferred by design

- **Rule 5 selection runs at read time, not build time.** `build/build.py`
  drops the exact-duplicate rate lines and tags every surviving row
  `source_kind` (`plan_specific` | `shared`), but the "plan-specific beats
  shared / lower shared wins" *choice* isn't applied until the serving layer
  reads it — and the serving layer doesn't yet
  ([#100](https://github.com/wmespi/honest-healthcare/issues/100)). Also
  `source_kind` is uniformly `shared` until `make discover` repopulates Step 1's
  `index_file_plans` on the canonical box.
