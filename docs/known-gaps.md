# Known gaps

*What is wrong, missing, or deferred - current state only; a fixed gap is deleted, not struck through.
Parser-internal scale issues: [../etl/parse.md](../ingestion/etl/parse.md).
Where the product is headed: [direction.md](direction.md) and epic [#191](https://github.com/wmespi/honest-healthcare/issues/191).*

## Attribution

- **`network_name` is not uniform across files.** The target plan's clean source uses
  `"GA Blue Value HIX Individual Network"`; other `anthem/GA_*` files use
  config-style labels (`"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL"`) and are
  different plans. The parser writes every network a file carries, and nothing maps a
  config-style label to a plan, so a target names its labels by hand (below).
- **A target's `network_patterns` are hand-written** in
  [`ingestion/etl/targets.yaml`](../ingestion/etl/targets.yaml). The provider probe skips a file whose
  `provider_references[].network_name` matches none of them, so a wrong guess skips
  every file for that plan. It fails loudly (the parse record's `reason` lists the
  labels the file carried; the end-of-run guard exits non-zero when every target file
  is skipped and none has Parquet on disk). Deriving the patterns is the same open
  problem.
- **Coverage is verified for the target network, not quantified in a doc.** Read
  the parse records ([ingestion/etl/parse.md](../ingestion/etl/parse.md#when-parse-downloads-a-file)) and run `make test-live`.

## Data scope

- **Only the services in `integration/services.yaml` have prices.**
  A family in `integration/families.yaml` takes its codes from CMS's shoppable services, and a code shows once the file names it.
  `make build` lists each family's codes a plan prices that have no name yet.
  Hospital stays have no family, and a procedure's anesthesia and pathology are no part of it yet ([#191](https://github.com/wmespi/honest-healthcare/issues/191)).
- **Prices are outpatient professional fee-for-service only.** `biller_rates`
  ([integration/build.md](../integration/build.md#the-find-care-tables)) keeps fee-schedule or
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

## Provider ↔ procedure

- **CMS utilization is the evidence, with limits.** Anthem's
  `provider_references` are network-administration buckets, not practices, so a
  rollup group "has" every code. Evidence: who billed a code
  ([cms-utilization](../ingestion/reference/cms-utilization.md)), and real practice
  identity ([doctors-clinicians](../ingestion/reference/doctors-clinicians.md)). Limits:
  Part B only (no pediatric / pure-commercial / cash); rows with ≤10 beneficiaries
  are excluded, so `billed: False` is weak; ~2-year lag; single year, so "stopped
  doing it" looks like "never"; Georgia has no all-payer claims database to widen
  it ([#14](https://github.com/wmespi/honest-healthcare/issues/14)).

## Procedures

- **Blue Value's file lists no facility's fee**, so its procedure rows show the doctor's fee alone.
  A procedure no doctor near the search is shown billing Medicare for, such as a child's tonsils, pregnancy care or a rare variant, has no row on it; `make test-live` counts them.
- **A doctor's fee at a hospital or surgery center needs a group whose doctors CMS lists in its building**, and most surgeons' groups are listed at their own offices, so most hospital rows show the facility's fee with the doctor's missing ([build.md](../integration/build.md)).
- **A generic ambulatory surgery center's fee shows only where such a group works in it**, since its contract prices every code whether it does the procedure or not.
- **A surgery done only at a facility is often listed under the office's place of service alone**, and that rate is read as the doctor's fee where the file lists none for a facility.

## Quality

- **No public measure fits visits, checkups, urgent care, counseling, therapy, lab tests, X-rays, ultrasound or MRI**, so the screen says places aren't compared on quality there ([integration/measures.yaml](../integration/measures.yaml)).
  CMS's lab certification file is keyed by address, not NPI, so labs' accreditation isn't shown.
- **Measures are shown, not ranked**, since one rank across them would need a home-made score ([#191](https://github.com/wmespi/honest-healthcare/issues/191)).
- **A clinician's procedure count is for Medicare patients only**, and CMS's file gives its release date, not the period it covers.
- **A surgery center's measures show only where its NPI bills at one of our places**, and the hospital patient-experience star lands but shows nowhere until hospital stays have a family.

## Scale / performance

- **`make build` is a full rebuild, not incremental**, and is not auto-triggered:
  run it after each `make parse` batch or the find-care tables go stale. A missing build
  is a `503`, not a degraded path. Per-file partials → merge is a future refinement.

## Operational

- **A failed file stales only its own networks, but discover is one step for every
  insurer.** Anthem's index unreadable stops `make refresh` for every network; scoping
  discover per insurer waits for a second one
  ([#187](https://github.com/wmespi/honest-healthcare/issues/187)).
- **Only `GET /api/health` says how fresh a network's prices are.** The screen doesn't
  show `prices_as_of` or a `stale` network yet ([build.md](../integration/build.md#the-find-care-tables)).
- **Large GA files** (multi-GB) are tractable thanks to the price/roster split; still
  parse individually and watch `du -sh data`.

## Visits, lab tests, therapy and counseling

- **None of them shows at a hospital.**
  Their families list no hospital bills, so a clinic, lab or urgent care in a hospital's building is left out, and so is the hospital's own fee for one.
- **An office visit is listed under a kind of doctor only where someone there names one.**
  A place whose clinicians are registered only as general nurse practitioners or physician assistants, and whose billing group names no kind either, such as a medical specialty clinic, is under no kind; `make build` lists the registrations that count as none ([integration/specialties.yaml](../integration/specialties.yaml)).
  A large system's practice can show under kinds its own doctors don't practice there, because its roster spans the system.
- **A yearly checkup's evidence is Medicare's wellness visit.**
  Medicare doesn't pay the checkup codes, so a practice shows when Medicare has a doctor of its billing a wellness visit, and children's checkups aren't offered because Medicare has no evidence for them.
- **Urgent care lists only the centers NPPES registers as urgent care.**
  It shows the rate for the urgent care place of service where a network's file has one, and the office rate where it doesn't.
- **Therapy is priced per code, and most codes are 15 minutes.**
  A session bills several, and the screen doesn't add them up.
- **Counseling shows the rate with no modifier.**
  A rate a plan lists for a psychologist (`AH`) or a clinical social worker (`AJ`) alone is left out, so where a plan pays them less, a session with one costs less than shown.
- **A lab test's price is the test alone.**
  The blood draw is a service of its own, and the screen doesn't add the two.

## Imaging

- **Places neither geocoder can put on the map aren't shown.**
  Census can't place many addresses, a lot of them rural, and OpenStreetMap, asked next, knows only some of those buildings ([geocode.md](../ingestion/reference/geocode.md)).
  `places` leaves the rest out, prices there included, and `make build` saves them to `unlocated_places.parquet` and prints how many buildings each geocoder located.
- **A clinician is listed only where CMS lists them practicing.**
  When Doctors & Clinicians places any of a biller's doctors for a service at one of its places, the service shows at those places with those doctors, and nowhere else.
  The directory is self-reported, so a doctor missing from an address they do work at is missing from that place, and a biller's place with no listed doctor doesn't show the service.
- **A hospital's own billing ID can land as a practice** when its NPPES address is written
  differently from the hospital's beyond spelling, as "541 441 HISTORIC HWY N" is for "541
  HISTORIC HIGHWAY 441-NORTH", or when the hospital isn't in Hospital General Information.
  `make build` lists them.
- **Practices registered outside Georgia aren't listed.** The NPPES extract keeps an NPI only
  when its primary location is in Georgia, and `places` needs a location to show,
  so a billing ID registered elsewhere has no place. Most are teleradiology groups, which aren't
  places a patient goes, but some are: groups registered elsewhere with Georgia sites, whose
  secondary locations the extract keeps but whose name and specialty it doesn't, and in-network imaging
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
- **Blue Value has no open source for a hospital's fee.**
  Its file's institutional lines are all revenue codes, and no hospital's own file names a plan on its exchange network ([a hospital's own fee](../integration/build.md#a-facilitys-own-fee)).
  A hospital on that network shows the reading and Medicare's hospital outpatient rate as a rough guide ([opps.md](../ingestion/reference/opps.md)), and mammography has no Medicare rate.
- **A hospital paid a percentage of its charges or a per diem shows no fee**, from either source, and a case rate in a hospital's own file is not read as a fee ([build.md](../integration/build.md#a-facilitys-own-fee)).
- **Some hosts refuse a non-browser client, so their hospitals have no price file:** Northside's asset host, `sghs.org`, `adventhealth.com`, and Phoebe, whose links go through a link-rewriting service ([hospital-rates.md](../ingestion/etl/hospital-rates.md)).
- **`ingestion/etl/hospitals.yaml` lists only some of Georgia's hospitals**, and some locations in listed systems' indexes match no CCN in the hospital reference: Emory Hospital Perry, Piedmont Macon, Piedmont McDuffie, Wellstar Windy Hill and NGMC Braselton ([hospital-rates.md](../ingestion/etl/hospital-rates.md)).
- **Some hospital billing NPIs with institutional rates in Pathway's file price no hospital's fee**, because they match no hospital's building, and the build prints them.
  Shepherd Center and Children's Healthcare of Atlanta at Hughes Spalding are not in the hospital reference.
  Wayne Memorial, Memorial Health University Medical Center, Polk Medical Center and Mountain Lakes Medical Center have a billing address that differs from the hospital reference's ([build.md](../integration/build.md#a-facilitys-own-fee)).
- **Which labels price a plan beyond its own is hand-written, and the order between them is a rule we chose.**
  A target's `shared_patterns` name the labels in its file that price the plan's members without its name, and the build shows a rate under one only where the plan's own label has none for the biller, service and bill ([parse.md](../ingestion/etl/parse.md#publishing-the-rates-by-network)).
  Nothing in the file states that order, and no hospital's own file lists a professional rate to test it against.
- **A place can have several rates for one scan.** When a billing ID sits under more
  than one fee schedule for the same scan, the file does not say which applies to a
  given patient ([price lines](../ingestion/etl/mrf-model.md#several-price-lines-for-one-provider-and-code)).
  The row shows the rate whose own roster has a doctor Medicare shows billing the scan.
  Where several rates still tie, it shows the highest and says to ask which applies; `make coverage` counts those rows.
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
