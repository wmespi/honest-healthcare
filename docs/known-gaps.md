# Known gaps

*What is wrong, missing, or deferred — current state only; a fixed gap is deleted,
not struck through. Parser-internal scale issues: [../etl/parse.md](../etl/parse.md).
Where the product is headed: [direction.md](direction.md) and epic
[#95](https://github.com/wmespi/honest-healthcare/issues/95).*

## Attribution

- **Plan → file is derived; plan → network is not.** `index_file_plans` links every
  published plan to its files, so "which files serve plan X" is a query and
  `etl parse` selects on it. The plan name comes from the index, the network label
  from a rate file's `provider_references`, and the two share no key. Until derived
  (HIOS `plan_id` + a CMS public-use file, or intersecting a target's files with the
  networks they carry), `serving/plan_networks.json` is a hand-curated bridge served
  by `/plans`.
- **`network_name` is not uniform across files.** The target plan's clean source uses
  `"GA Blue Value HIX Individual Network"`; other `anthem/GA_*` files use
  config-style labels (`"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL"`) and are
  different plans. The parser writes every network a file carries; nothing yet maps
  a config-style label to a plan — same missing bridge as above.
- **A target's `network_patterns` are hand-written** in
  [`etl/targets.yaml`](../etl/targets.yaml). The provider probe skips a file whose
  `provider_references[].network_name` matches none of them, so a wrong guess skips
  every file for that plan. It fails loudly (`failure_reason` lists the labels the
  file carried; the end-of-run guard exits non-zero when every target file is
  skipped and nothing ever completed). Deriving the patterns is the same open
  problem.
- **Coverage is verified for the target network, not quantified in a doc.** Query
  `coverage_log` (`make psql`) and run `make test-live`.

## Data scope

- **Consumer rate views show outpatient professional fee-for-service only.**
  `outpatient_scope()` (`serving/data_sources.py`) gates `/rates/providers`,
  `/rates/by_network`, `/rates/quote` and the no-code `/rates/distribution` overview
  (via `rate_hist.scope`). Excluded but still in the store: `negotiated_type` of
  `percentage` (a percent of billed charges, not dollars), `per diem` and `derived`;
  `billing_class='institutional'`; `setting='inpatient'` (ignored on scoped
  routes); `negotiation_arrangement='bundle'`. A dedicated inpatient / facility view
  is not built.
- **HCPCS drug codes (J-codes) inflate pooled means.** `outpatient_scope()` keeps
  physician-administered drugs, some priced in the millions per course. The overview
  leads with p25 / median / p75 (tails can't move them) but the volume-weighted
  `avg` the API still returns skews high. `/rates/quote` carries `medicare_allowed`
  + `vs_medicare` so a drug rate can be shown against its benchmark; using it to
  down-weight pooled rows, or a `drug` scope flag, is deferred.
- **Sentinel / placeholder rates have no discrete tell.** Anthem fills the required
  positive `negotiated_rate` with tiny values on not-separately-priced codes; they
  share every field with real rates. Jobs 1–3 drop rows at or below
  `GREATEST($1.00, 5% × the code's rate_hist median)` — deliberately loose, so a
  genuinely cheap contract survives. The histogram still shows them. Tighter cut:
  [#51](https://github.com/wmespi/honest-healthcare/issues/51).
- **`/networks`, `/billing_codes`, `/procedure_categories` are not scoped** — they
  answer "what's priced in this network", not "what does an outpatient visit cost".
- **`00810` (anesthesia) has no CPT fee-schedule rate** — anesthesia is priced in
  base units. Expected, not a bug.

## Provider ↔ procedure

- **`plausibility()` is a heuristic; CMS utilization is the evidence.** Anthem's
  `provider_references` are network-administration buckets, not practices, so a
  rollup group "has" every code. Evidence: `did_bill(npi, code)` (Tier 1,
  [cms-utilization](../reference/cms-utilization.md)), specialty profiles (Tier 2,
  [specialty-profiles](../reference/specialty-profiles.md)), and real practice
  identity ([doctors-clinicians](../reference/doctors-clinicians.md)). Limits:
  Part B only (no pediatric / pure-commercial / cash); rows with ≤10 beneficiaries
  are excluded, so `billed: False` is weak; ~2-year lag; single year, so "stopped
  doing it" looks like "never"; Georgia has no all-payer claims database to widen
  it ([#14](https://github.com/wmespi/honest-healthcare/issues/14)).
- **`/rates/providers` groups practices on Anthem's `tin_value`, not `org_pac_id`**,
  and the two are not reconciled; the CMS directory covers Medicare-enrolled
  clinicians only.
- **`has_rates` / `n_with_rates` are corpus-wide unless a `network_name` is
  passed.** With one they scope to that network's provider roster — "the NPI sits in
  a network-attributed group", not "a priced row was verified for this NPI". The
  exact check would join `rates ⨝ group_sets`
  ([#10](https://github.com/wmespi/honest-healthcare/issues/10)).

## Scale / performance

- **`make build` is a full rebuild, not incremental**, and is not auto-triggered:
  run it after each `make parse` batch or the browse tables go stale. A missing build
  is a `503`, not a degraded path. Per-file partials → merge is a future refinement.
- **`/rates/quote` and the provider menu are slow for a provider present in most
  source files.** Both resolve an NPI to `(file_id, group_set_id)` via
  `group_members ⨝ group_sets`; the `file_id` predicate has nothing to prune when the
  provider appears in nearly every file. A precomputed `(npi) → group_set_id[]`
  index would remove the join.
- **`rate_hist.n` is a roster-weighted ranking hint, not a distinct count** — a
  group in several rosters counts per roster. Never render it as "N providers". A
  `(payer, code) → n_providers` distinct rollup is
  [#48](https://github.com/wmespi/honest-healthcare/issues/48); until then
  `/rates/by_network` returns `n_providers: null`, and its `n_groups` counts a
  group once per `(file_id, provider_group_id)`.
- **`/rates/providers` + `/rates/quote` require a `network_name`**
  (`400 {"code": "network_required"}`): the unpruned cross-network expansion spills
  tens of GB and a precomputed `(code, network, tin) → rate` rollup is infeasible.
  Per-row `n_groups` over-counts a group spanning several TINs.
- **`/rates/distribution` without a `network_name` serves `rate_hist`** — buckets are
  $25 wide and `provider_groups` / `n_providers` are `null`.
- **`/rates/providers` `ga_hospitals_only` filters rows but not `summary`.**
- **`coverage_log.n_ga_hospital_npis` is never populated** (the NPPES join happens at
  query time).

## Operational

- **`make reference STEP=nppes` is not atomic** — `ga_providers.parquet` is briefly
  empty during a re-extract and queries touching it 500. Run it when the API is idle.
- **Monthly index churn.** `location` is a signed URL with a `YYYY-MM_` prefix, not a
  cross-month key; re-discover monthly and prune the prior month
  ([../etl/discover.md](../etl/discover.md)).
- **Large GA files** (multi-GB) are tractable thanks to the price/roster split; still
  parse individually and watch `du -sh data`.

## Deferred by design

- **Rule 5 runs at read time, not build time.** `build/build.py` drops exact
  duplicate lines and tags every row `source_kind`, but the "plan-specific beats
  shared" choice is not applied by serving yet. `source_kind` stays `shared` until
  `make discover` repopulates `index_file_plans`.
