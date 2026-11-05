# Known gaps

*What is wrong, missing, or deferred — current state only; a fixed gap is deleted,
not struck through. Parser-internal scale issues: [../etl/parse.md](../etl/parse.md).
Where the product is headed: [direction.md](direction.md) and epic
[#95](https://github.com/wmespi/honest-healthcare/issues/95).*

## Attribution

- **`network_name` is not uniform across files.** The target plan's clean source uses
  `"GA Blue Value HIX Individual Network"`; other `anthem/GA_*` files use
  config-style labels (`"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL"`) and are
  different plans. The parser writes every network a file carries, and nothing maps a
  config-style label to a plan, so a target names its labels by hand (below).
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

- **Only the services in `build/services.yaml` have prices.** The find-care tables
  price the scans it lists, and every other code (PCP visits, for one) has none.
  [#158](https://github.com/wmespi/honest-healthcare/issues/158) plans how the list grows.
- **Prices are outpatient professional fee-for-service only.** `biller_rates`
  ([build/build.md](../build/build.md#the-find-care-tables)) keeps fee-schedule or
  negotiated dollar rates for an outpatient professional visit. Excluded but still in the
  parsed files: `negotiated_type` of `percentage` (a percent of billed charges, not
  dollars), `per diem` and `derived`; `billing_class='institutional'`;
  `setting='inpatient'`; `negotiation_arrangement='bundle'`. An inpatient or facility view
  is not built.
- **Sentinel / placeholder rates have no discrete tell.** Anthem fills the required
  positive `negotiated_rate` with tiny values on not-separately-priced codes; they
  share every field with real rates. `biller_rates` drops a rate of at most $1 or 5% of
  the median for that service and part on the network, which is deliberately loose, so a
  genuinely cheap contract survives. Tighter cut:
  [#51](https://github.com/wmespi/honest-healthcare/issues/51).
- **A practice in `places` has no phone number.** The NPPES extract
  ([etl/nppes.md](../etl/nppes.md)) doesn't keep the practice location's phone, so only
  hospitals, from Hospital General Information, have one.

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

## Scale / performance

- **`make build` is a full rebuild, not incremental**, and is not auto-triggered:
  run it after each `make parse` batch or the find-care tables go stale. A missing build
  is a `503`, not a degraded path. Per-file partials → merge is a future refinement.
- **`coverage_log.n_ga_hospital_npis` is never populated** (the NPPES join happens at
  query time).

## Operational

- **`make reference STEP=nppes` is not atomic** — `ga_providers.parquet` is briefly
  empty during a re-extract and queries touching it 500. Run it when the API is idle.
- **Large GA files** (multi-GB) are tractable thanks to the price/roster split; still
  parse individually and watch `du -sh data`.

## Imaging

- **Places Census can't put on the map aren't shown.** Census can't place about one address in
  eight, many of them rural, and some are hospitals whose number its street ranges don't cover,
  such as 5353 Reynolds Street in Savannah. `places` leaves them out, prices there included,
  and `make build` saves them to `unlocated_places.parquet` to try with another geocoder.
- **A hospital's own billing ID can land as a practice** when its NPPES address is written
  differently from the hospital's beyond spelling, as "541 441 HISTORIC HWY N" is for "541
  HISTORIC HIGHWAY 441-NORTH", or when the hospital isn't in Hospital General Information.
  `make build` lists them.
- **Practices registered outside Georgia aren't listed.** The NPPES extract keeps an NPI only
  when its primary location is in Georgia, and `places` needs a location to show,
  so a billing ID registered elsewhere has no place. Most are teleradiology groups, which aren't
  places a patient goes, but some are: groups registered elsewhere with Georgia sites (NPPES
  secondary practice locations, which the extract doesn't read), and in-network imaging
  centers just across the state line. A new NPI missing from the monthly NPPES snapshot drops
  out the same way. A reading group's fee can still be part of what a Georgia facility's scan
  costs, and `prices` leaves it out.
- **A hospital on the find-care list may not be in network itself.** `prices` lists a hospital
  when the groups reading scans in its building have rates, but Anthem's professional file
  prices those doctors, not the hospital, and the build doesn't check the hospital's own NPIs.
  The screen says the hospital's own network status isn't confirmed
  ([decided on #135](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6646717812)).
- **The find-care provider list has only the clinicians Medicare shows billing.** A `prices`
  row lists a clinician only when Medicare's file shows them billing that service for the
  row's billers, so one with few or no Medicare patients for it isn't listed, and the CMS
  utilization limits under Provider ↔ procedure apply.
- **A doctor billing under their own NPI is listed only under a CMS group.** Anthem keys some
  group contracts on one doctor's NPI, so the doctor's own name would mislabel a whole group.
  The place takes the name of the group the doctor bills Medicare with (CMS Doctors &
  Clinicians), and away from a hospital a doctor with no group there has no place, even when
  they do run their own office.
- **A hospital's own fee isn't on file.** Anthem's professional file carries the
  doctor's reading (`26`) and, for freestanding centers, the equipment part (`TC`). A
  hospital bills its own fee in a file we do not carry, so a hospital's row has the
  reading and no most you'd pay, and says so. The screen gives Medicare's hospital
  outpatient rate as a rough guide ([reference/opps.md](../reference/opps.md)); hospital
  price-transparency files would be the real source, and mammography has no Medicare rate.
- **A place can have several rates for one scan.** When a billing ID sits under more
  than one fee schedule for the same scan, the file does not say which applies to a
  given patient ([price lines](../etl/mrf-model.md#several-price-lines-for-one-provider-and-code)).
  The row shows the highest and says to ask which applies.
- **The find-care list carries some non-imaging practices.** A group's price is
  attached to every clinician on its roster, so a practice with one Medicare-billing
  imaging clinician (an orthopedic or primary care group, say) can appear beside the
  radiology groups.
- **Find-care cost sharing is the standard plan's.** `plans.deductible` and `cost_sharing`
  hold each plan's standard version (`-01`), in network. Someone with a cost-sharing
  reduction, on a version from `-02` to `-06`, has a lower deductible and pays less
  ([#152](https://github.com/wmespi/honest-healthcare/issues/152)).
- **The screen says what the member pays in words, not dollars.** It doesn't know how much
  of their deductible they've met, so a place shows their share as a sentence, such as "a
  $300 copay and then 50% of the rest", after the deductible, and no dollar figure.
