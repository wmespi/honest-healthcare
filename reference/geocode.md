# `make reference STEP=geocode` — where each biller and hospital is

*Read this when working on where a place is on the map, or how far it is from a ZIP code.*

**Builds** `data/reference/geocode_addresses.parquet` from the free, keyless
[US Census Bulk Geocoding API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services.html):
Census's standardized address and point for the NPPES address of every billing ID
(`tin_value`) under `anthem/providers`, and for each hospital's address in
`hospitals_ga.parquet` ([hospitals.md](hospitals.md)). The find-care build places each biller
and hospital with it ([build.md](../build/build.md#the-find-care-tables)). Module
`reference/geocode.py`; module flags `--test`, `--fresh`, `--limit N`, `--batch-size N`,
`--benchmark NAME`, `--census-response-file FILE` (test-only; pass via `ARGS`).

**Why Census, not Google Maps.** Google has no free tier and its Places content can't
be cached beyond a `place_id` (`docs/direction.md`, "Geography"); the Census geocoder
is free and gives coordinates we own and can cache indefinitely.

**Address dedup.** The batch endpoint takes at most 10,000 addresses per request, and
many billers share a building, so the builder sends each distinct address once.

**Only new addresses.** `data/reference/geocode_addresses.parquet` keeps every address
sent to Census, exactly as NPPES has it, with what Census answered: `match_type`,
`matched_address` (its standardized form), `latitude`, `longitude`, `benchmark` and
`geocoded_on`, all empty but the date for a `No_Match`. A run sends only the addresses
that file doesn't hold, so a run with nothing new makes no calls, and `make reference`
runs this step every time. The file is saved after each batch, so a run that fails
part-way resumes from the next batch. `ARGS="--fresh"` sends every address again, for
when Census's own address data has moved on; deleting the file does the same.

## What "geocoded" means

- An address Census returns as `No_Match` (bad suite, PO box, typo) is kept with an empty
  answer, so it isn't sent again, and the build leaves its place off the map rather than
  guess. NPPES addresses are self-reported and unvalidated, so the match rate, which the
  run prints, is well below 100%.
- **A `Match` can still be the wrong state** (a same-named town elsewhere, or an NPPES
  row whose ZIP belongs to another state). The file keeps Census's answer as given, and
  the build counts a match outside Georgia's bounding box as no match.

## Re-run when

It runs with every `make reference`, and `make refresh` runs it again after the parse so
new billing IDs get coordinates the same month. A changed NPPES address is a new address,
so it's sent; an unchanged one isn't. If Census fails part-way, re-run it.

Tested by `reference/tests/test_geocode.py`, hermetic: a tiny NPPES fixture covers
shared-address dedup, a `No_Match`, a wrong-state match, and an NPI that isn't a billing ID,
whose address is never sent; `--census-response-file` points at
`reference/testdata/geocode_census_response_sample.csv` instead of the network. It also checks
that a re-run with nothing new makes no call, and that a new address is the only one sent.
