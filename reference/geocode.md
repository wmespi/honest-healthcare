# `make reference STEP=geocode` — lat/long for GA providers and practices

*Read this when working on "how close is this provider" — distance ranking and,
eventually, the provider map (`docs/direction.md` build sequence).*

**Builds** `data/reference/pcp_geocode.parquet` from the free, keyless
[US Census Bulk Geocoding API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services.html):
one row per GA PCP- or imaging-eligible NPI, or billing ID, with `latitude` / `longitude`. Module
`reference/geocode.py`; module flags `--test`, `--fresh`, `--limit N`, `--batch-size N`,
`--benchmark NAME`, `--census-response-file FILE` (test-only; pass via `ARGS`).

**Why Census, not Google Maps.** Google has no free tier and its Places content can't
be cached beyond a `place_id` (`docs/direction.md`, "Geography"); the Census geocoder
is free and gives coordinates we own and can cache indefinitely.

**Why service-line-scoped.** The batch endpoint takes at most 10,000 addresses per request;
geocoding all of GA NPPES isn't worth the run time or risk. The candidate set is
`PCP_TAXONOMY_CODES` plus `IMAGING_TAXONOMY_CODES`, mirrors of the constants of the same
name in `serving/service_lines.py` (`serving/tests/test_geocode.py` fails if they drift),
plus every billing ID (`tin_value`) under `anthem/providers` with an NPPES record, because
`/imaging/providers` measures distance from the practice's own address. Each hospital's
address in `hospitals_ga.parquet` ([hospitals.md](hospitals.md)) is sent too, but lands only
in the address file below, since a hospital is keyed by CCN, not NPI.
The output keeps the name `pcp_geocode.parquet`.

**Address dedup.** Many PCPs share an address, so the builder geocodes *distinct
addresses* and rejoins the coordinate onto every NPI there.

**Only new addresses.** `data/reference/geocode_addresses.parquet` keeps every address
sent to Census, exactly as NPPES has it, with what Census answered: `match_type`,
`matched_address` (its standardized form), `latitude`, `longitude`, `benchmark` and
`geocoded_on`, all empty but the date for a `No_Match`. A run sends only the addresses
that file doesn't hold, so a run with nothing new makes no calls, and `make reference`
runs this step every time. The file is saved after each batch, so a run that fails
part-way resumes from the next batch. `ARGS="--fresh"` sends every address again, for
when Census's own address data has moved on; deleting the file does the same.

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

It runs with every `make reference`, and `make refresh` runs it again after the parse so
new billing IDs get coordinates the same month. A changed NPPES address is a new address,
so it's sent; an unchanged one isn't. If Census fails part-way, re-run it.
When either taxonomy list changes, update the mirrored copy here too.

Tested by `serving/tests/test_geocode.py`, hermetic: a tiny NPPES fixture covers
shared-address dedup, a `No_Match`, a billing ID outside the taxonomy lists that must be
geocoded, and a non-PCP NPI that must never reach the candidate list; `--census-response-file` points at
`reference/testdata/geocode_census_response_sample.csv` instead of the network. It also checks
that a re-run with nothing new makes no call, and that a new address is the only one sent.
