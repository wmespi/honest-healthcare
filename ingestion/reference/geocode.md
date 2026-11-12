# `make reference STEP=geocode` - where each address is

*Read this when working on where a place is on the map, or how far it is from a ZIP code.*

**Builds** `data/ingestion/reference/geocode_addresses.parquet`: every address the find-care build puts something at, and what each of two free, keyless geocoders answered for it.
The build decides from it which addresses are one building and where that building is ([build.md](../../integration/build.md#the-find-care-tables)).
Module `ingestion/reference/geocode.py`; module flags `--test`, `--fresh`, `--limit N`, `--batch-size N`, `--benchmark NAME`, and the test-only `--census-response-file FILE` and `--osm-response-file FILE` (pass via `ARGS`).

## The two geocoders

- **Census** ([Bulk Geocoding API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services.html)) gets every address.
  That is the NPPES address, main and secondary, of every billing ID (`tin_value`) under `anthem/providers` ([nppes.md](../etl/nppes.md)), each hospital's address in `hospitals_ga.parquet` ([hospitals.md](hospitals.md)), and each address Doctors & Clinicians lists a clinician at ([doctors-clinicians.md](doctors-clinicians.md)).
  Its answer carries Census's standardized address, which is how the build knows two spellings are one building.
- **OpenStreetMap** ([Nominatim](https://operations.osmfoundation.org/policies/nominatim/)) gets only a biller's or a hospital's address that Census can't put in Georgia, because it knows buildings Census's street ranges don't cover.
  Only an answer that is the address itself is kept as a point: the same house number, in Georgia, in the same ZIP code or city.
  A street or a town would put a place on the map where it isn't.
  Its usage policy allows one request a second from one thread, with a User-Agent that names the project, so the run paces itself and it gets only what Census misses.

**Why these two, not Google Maps.** Google has no free tier and its Places content can't be cached beyond a `place_id` (`docs/direction.md`, "Geography").
Both of these are free and give coordinates we own and can cache indefinitely.

**Address dedup.** Census's batch endpoint takes at most 10,000 addresses per request, and many billers share a building, so the builder sends each distinct address once.

## The file

One row per address and geocoder, keyed on the address exactly as its source has it plus `source` (`census` or `openstreetmap`).
Each row holds what that geocoder answered: `match_type`, `latitude`, `longitude`, `geocoded_on`, and from Census alone `matched_address` (its standardized form) and `benchmark`.
An address a geocoder couldn't match is kept with an empty answer, so it isn't sent again.
`located` says the answer is a point inside Georgia's bounding box, and the build reads no other answer.

**Only new addresses.** A run sends a geocoder only the addresses the file holds no answer from it for, so a run with nothing new makes no calls, and `make reference` runs this step every time.
The file is saved as answers arrive, so a run that fails part-way resumes where it stopped.
If OpenStreetMap stops answering, the run says how many addresses are left and ends without them, and the next run asks.
`ARGS="--fresh"` sends every address again, for when a geocoder's own address data has moved on; deleting the file does the same.

## What "located" means

- NPPES addresses are self-reported and unvalidated (bad suite, PO box, typo), so the match rates, which the run prints, are well below 100%.
  The build leaves a building neither geocoder located off the map rather than guess.
- **A match can still be the wrong state** (a same-named town elsewhere, or an NPPES row whose ZIP belongs to another state).
  The file keeps the answer as given with `located` false, so the address goes on to OpenStreetMap and the build doesn't use the point.

## Re-run when

It runs with every `make reference`, and `make refresh` runs it again after the parse so new billing IDs get coordinates the same month.
A changed address is a new address, so it's sent; an unchanged one isn't.
If a geocoder fails part-way, re-run it.

Tested by `ingestion/reference/tests/test_geocode.py`, hermetic: a tiny NPPES fixture covers shared-address dedup, a `No_Match`, a wrong-state match, and an NPI that isn't a billing ID, whose address is never sent.
`--census-response-file` points at `ingestion/reference/testdata/geocode_census_response_sample.csv` and `--osm-response-file` at canned Nominatim answers instead of the network.
It also checks that a re-run with nothing new makes no call, that a new address is the only one sent, and that OpenStreetMap is asked only for what Census couldn't put in Georgia.
