# `make geocode` — lat/long for GA PCP providers

*Read this when working on the "how close is this provider" step of Flow A
(issue #87, `docs/direction.md` build sequence step 1) — distance ranking
and, eventually, the provider map.*

Builds `data/reference/pcp_geocode.parquet` from **public, free, keyless**
data only: the
[US Census Bulk Geocoding API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services.html).
One row per GA PCP-eligible NPI with a `latitude` / `longitude`.

`make geocode` → `python3 -m reference.geocode --data-dir /app/data` in the
serving container (`reference/geocode.py`). `--test` / `--limit N` /
`--batch-size N` / `--benchmark NAME` / `--census-response-file FILE` exist on
the module (the last is test-only — see Tests below).

## Why this exists

`docs/direction.md`'s "Geography (and the Google Maps question)" section rules
out Google Maps as a *data source* (no free tier since March 2025, Places
content can't be cached beyond a `place_id`) — right for a live map view and
"get directions" hand-off, wrong for landing coordinates on our own file. The
Census batch geocoder is the free alternative: no key, no per-call billing,
coordinates we own and can cache indefinitely.

## Why PCP-scoped, not all of GA NPPES

The batch endpoint accepts at most 10,000 addresses per request. The full GA
NPPES set is hundreds of thousands of rows — geocoding all of it before a
second service line exists to use it isn't worth the run time or the risk
surface (more batches, more chances one fails partway). Scoping to
`PCP_TAXONOMY_CODES` (mirrors `serving/service_lines.py`'s constant of the
same name — kept in sync by hand, the same pattern
`SERVICE_LINE_BILLING_CODES` uses for the frontend's `SERVICE_LINE_CODES`)
matches how the rest of the PCP pilot has scoped its data work, and keeps this
to **~24,700 candidate NPIs**. Widening it is a real future step (any new
service line needs the same coordinates) but not a reason to hold this one.

## Address dedup

Many PCPs share a practice address. The builder geocodes **distinct
addresses**, not one row per NPI — verified 2026-09-04: 24,697 GA PCP-eligible
NPIs resolve to **~13,486 distinct addresses** (a ~45% cut), which also drops
the run from 3 Census batches to 2. The matched coordinate is rejoined onto
every NPI at that address afterward.

## What "geocoded" means here

Only rows Census's batch endpoint returns with `match_status = "Match"` land
in the output — a `No_Match` address (bad suite number, PO box, a typo in the
source NPPES row) is simply **absent**, never zeroed or defaulted to a ZIP
centroid. A live run's match rate prints at the end (`N/M addresses matched`);
the real 2026-09-04 run came back at **86.1%** (21,259/24,697 NPIs) — NPPES
addresses are self-reported and not address-validated at intake.

**A `Match` can still be the wrong state.** `GA_LAT_RANGE` / `GA_LON_RANGE`
drop anything Census geocodes outside Georgia's real bounding box before it
reaches the output — verified on that same run: 7 of 21,259 matches (0.03%)
landed in Maryland, Tennessee, or Alabama, Census having matched a Georgia
town's address to a same-named town elsewhere (LaFayette GA vs TN, Riverdale
GA vs MD). One case traced to a genuine NPPES data error, not a geocoder
mistake — a row labeled `DECATUR, GA` carrying ZIP `35601`, which is Decatur,
Alabama; Census correctly geocoded the ZIP it was given. Dropped, logged
(`dropping N address(es) geocoded outside Georgia's bounding box`), never
silently kept — a distance ranking must never show a provider states away as
"nearby" because of a bad match.

## Re-run when

- The NPPES pull refreshes (`make nppes`) and a PCP's address changes — this
  builder doesn't currently diff against the prior run, it always re-geocodes
  every current candidate address from scratch (idempotent, just not
  incremental — fine at this scale, revisit if the candidate set grows).
- `PCP_TAXONOMY_CODES` changes in `serving/service_lines.py` — update the
  mirrored copy here too, same discipline as `SERVICE_LINE_BILLING_CODES`.

## Tests

`serving/tests/test_geocode.py` — hermetic, no network. A tiny NPPES fixture
(`data-test/nppes/ga_providers.parquet`, built by the test itself) exercises
every path: two NPIs sharing one address (dedup + rejoin land both on the
*same* coordinate), one PCP-eligible address Census marks `No_Match` (must be
absent, not zeroed), and one non-PCP-taxonomy NPI (must never reach the
candidate list regardless of its address). `--census-response-file` points
the builder at `reference/testdata/geocode_census_response_sample.csv` — a
canned Census-shaped response — instead of the real network call; only valid
for a candidate list that fits in one batch, which the test fixture does by
construction. Picked up by `make test-api` and `make check-local`.
