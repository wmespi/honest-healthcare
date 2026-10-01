# `make reference STEP=geocode` — lat/long for GA PCP providers

*Read this when working on "how close is this provider" — distance ranking and,
eventually, the provider map (`docs/direction.md` build sequence).*

**Builds** `data/reference/pcp_geocode.parquet` from the free, keyless
[US Census Bulk Geocoding API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services.html):
one row per GA PCP-eligible NPI with `latitude` / `longitude`. Module
`reference/geocode.py`; module flags `--test`, `--limit N`, `--batch-size N`,
`--benchmark NAME`, `--census-response-file FILE` (test-only; pass via `ARGS`).

**Why Census, not Google Maps.** Google has no free tier and its Places content can't
be cached beyond a `place_id` (`docs/direction.md`, "Geography"); the Census geocoder
is free and gives coordinates we own and can cache indefinitely.

**Why PCP-scoped.** The batch endpoint takes at most 10,000 addresses per request;
geocoding all of GA NPPES before a second service line needs it isn't worth the run
time or risk. The candidate set is `PCP_TAXONOMY_CODES`, which mirrors the constant of
the same name in `serving/service_lines.py` — **kept in sync by hand**.

**Address dedup.** Many PCPs share an address, so the builder geocodes *distinct
addresses* and rejoins the coordinate onto every NPI there.

## What "geocoded" means

- Only rows Census returns as `match_status = "Match"` land in the output. A
  `No_Match` (bad suite, PO box, typo) is **absent**, never zeroed or defaulted to a
  ZIP centroid. The run prints `N/M addresses matched`; NPPES addresses are
  self-reported and unvalidated, so the rate is well below 100%.
- **A `Match` can still be the wrong state** (a same-named town elsewhere, or an NPPES
  row whose ZIP belongs to another state). `GA_LAT_RANGE` / `GA_LON_RANGE` drop
  anything outside Georgia's bounding box, logged as `dropping N address(es)
  geocoded outside Georgia's bounding box` — a distance ranking must never show a
  provider states away as "nearby".

## Re-run when

- The NPPES pull refreshes and addresses change. The builder always re-geocodes every
  candidate address (idempotent, not incremental).
- `PCP_TAXONOMY_CODES` changes — update the mirrored copy here too.

Tested by `serving/tests/test_geocode.py`, hermetic: a tiny NPPES fixture covers
shared-address dedup, a `No_Match`, and a non-PCP NPI that must never reach the
candidate list; `--census-response-file` points at
`reference/testdata/geocode_census_response_sample.csv` instead of the network.
