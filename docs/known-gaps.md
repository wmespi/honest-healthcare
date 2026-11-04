# Known gaps

*What is wrong, missing, or deferred — current state only; a fixed gap is deleted,
not struck through. Parser-internal scale issues: [../etl/parse.md](../etl/parse.md).
Where the product is headed: [direction.md](direction.md) and epic
[#95](https://github.com/wmespi/honest-healthcare/issues/95).*

## Attribution

- **The screen doesn't use real plan names yet.** `index_file_plans` links every
  published plan to its files, and `etl parse` selects the files that price the plans on
  the target exchange networks, read from Georgia's exchange plan file
  ([reference/exchange-plans.md](../reference/exchange-plans.md)). Nothing on the serving
  side reads that file yet: `serving/plan_networks.json` is still a hand-curated bridge
  served by `/plans`.
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

- **Only the priced service lines have prices.** The build keeps the billing codes of
  `PRICED_SERVICE_LINES` (`serving/service_lines.py`); every other code (PCP visits,
  for one) has no rate in the store. Add a line to the tuple, or `make build ALL_CODES=1`, to price more.
- **`/imaging/providers` shows outpatient professional fee-for-service only.**
  `outpatient_scope()` (`serving/data_sources.py`) defines the slice. Excluded but
  still in the store: `negotiated_type` of
  `percentage` (a percent of billed charges, not dollars), `per diem` and `derived`;
  `billing_class='institutional'`; `setting='inpatient'`;
  `negotiation_arrangement='bundle'`. A dedicated inpatient / facility view is not built.
- **Sentinel / placeholder rates have no discrete tell.** Anthem fills the required
  positive `negotiated_rate` with tiny values on not-separately-priced codes; they
  share every field with real rates. `/imaging/providers` drops rows at or below
  `GREATEST($1.00, 5% × the code's rate_hist median)` — deliberately loose, so a
  genuinely cheap contract survives. `rate_hist` still counts them. Tighter cut:
  [#51](https://github.com/wmespi/honest-healthcare/issues/51).
- **`/networks` is not scoped** — it answers "what's priced in this network", not
  "what does an outpatient visit cost".
- **`00810` (anesthesia) has no CPT fee-schedule rate** — anesthesia is priced in
  base units. Expected, not a bug.

## Provider ↔ procedure

- **CMS utilization is the evidence, with limits.** Anthem's
  `provider_references` are network-administration buckets, not practices, so a
  rollup group "has" every code. Evidence: who billed a code (Tier 1,
  [cms-utilization](../reference/cms-utilization.md)), specialty profiles (Tier 2,
  [specialty-profiles](../reference/specialty-profiles.md)), and real practice
  identity ([doctors-clinicians](../reference/doctors-clinicians.md)). Limits:
  Part B only (no pediatric / pure-commercial / cash); rows with ≤10 beneficiaries
  are excluded, so `billed: False` is weak; ~2-year lag; single year, so "stopped
  doing it" looks like "never"; Georgia has no all-payer claims database to widen
  it ([#14](https://github.com/wmespi/honest-healthcare/issues/14)).
- **`/imaging/providers` groups practices on Anthem's `tin_value`, not `org_pac_id`**,
  and the two are not reconciled; the CMS directory covers Medicare-enrolled
  clinicians only.

## Scale / performance

- **`make build` is a full rebuild, not incremental**, and is not auto-triggered:
  run it after each `make parse` batch or the browse tables go stale. A missing build
  is a `503`, not a degraded path. Per-file partials → merge is a future refinement.
- **`rate_hist.n` is a roster-weighted ranking hint, not a distinct count** — a
  group in several rosters counts per roster. Never render it as "N providers".
  A precomputed `(network, code)` NPI count would fan out to hundreds of
  millions of rows ([#48](https://github.com/wmespi/honest-healthcare/issues/48)).
- **`/imaging/providers` requires a `network_name`**
  (`400 {"code": "network_required"}`): the unpruned cross-network expansion spills
  tens of GB and a precomputed `(code, network, tin) → rate` rollup is infeasible.
- **`coverage_log.n_ga_hospital_npis` is never populated** (the NPPES join happens at
  query time).

## Operational

- **`make reference STEP=nppes` is not atomic** — `ga_providers.parquet` is briefly
  empty during a re-extract and queries touching it 500. Run it when the API is idle.
- **Large GA files** (multi-GB) are tractable thanks to the price/roster split; still
  parse individually and watch `du -sh data`.

## Imaging

- **Some distances are approximate.** Census can't place about one address in eight, many of
  them rural. `/imaging/providers` measures those from the center of the practice's ZIP code.
- **Practices registered outside Georgia aren't listed.** The NPPES extract keeps an NPI only
  when its primary location is in Georgia, and `/imaging/providers` needs a location to show,
  so a billing ID registered elsewhere has no row. Most are teleradiology groups, which aren't
  places a patient goes, but some are: groups registered elsewhere with Georgia sites (NPPES
  secondary practice locations, which the extract doesn't read), and in-network imaging
  centers just across the state line. A new NPI missing from the monthly NPPES snapshot drops
  out the same way. A reading group's fee can still be part of what a Georgia facility's scan
  costs, which the `places` and `prices` tables on
  [#135](https://github.com/wmespi/honest-healthcare/issues/135) have to settle.
- **A doctor billing under their own NPI is listed only under a CMS group.** Anthem keys some
  group contracts on one doctor's NPI, so the doctor's own name would mislabel a whole group.
  The row takes the name of the group the doctor bills Medicare with (CMS Doctors &
  Clinicians), and a doctor with no group there has no row, even when they do run their own
  office.
- **Imaging prices exclude the facility fee.** Anthem's professional file carries the
  doctor's reading (`26`) and, for freestanding centers, the equipment part (`TC`). A
  hospital bills its own fee in a file we do not carry, so a hospital-based practice
  shows a reading-only price and says so. The screen adds Medicare's hospital-outpatient
  rate as a labelled estimate ([reference/opps.md](../reference/opps.md)); hospital
  price-transparency files would be the real source, and mammography has no estimate.
- **A practice can show a price range for one scan.** When a billing ID sits under more
  than one fee schedule for the same scan, the file does not say which applies to a
  given patient ([price lines](../etl/mrf-model.md#several-price-lines-for-one-provider-and-code)).
  The row shows the typical price and the range; ask the practice which contract applies.
- **Imaging practice lists carry some non-imaging practices.** A group's price is
  attached to every clinician on its roster, so a practice with one Medicare-billing
  imaging clinician (an orthopedic or anesthesia group, say) can appear beside the
  radiology groups.
- **Plan cost sharing covers a few plan versions.** `serving/plan_cost_sharing.json` is
  copied by hand from Anthem summaries of benefits. Georgia's exchange publishes every
  plan's cost sharing in its benefits file, which nothing reads yet
  ([reference/exchange-plans.md](../reference/exchange-plans.md)). Cost-sharing-reduction variants, Bronze, and
  Pathway Silver are not carried; a member on one of those picks "Not sure" and sees the
  price without a share. Refresh the file each plan year.
- **Some imaging cost shares are not estimated.** A structure such as "$300 a day, then
  50% coinsurance" is shown as the plan's own words, not turned into a dollar figure. A
  screening mammogram is shown as preventive. Every estimate assumes the deductible
  progress the member typed in and no other care this year.
