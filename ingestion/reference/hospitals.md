# `make reference STEP=hospitals`: Georgia's hospitals

*Read this when working on hospital places: their name, address, phone or star rating.*

**Builds** `data/ingestion/reference/hospitals_ga.parquet` from CMS's
[Hospital General Information](https://data.cms.gov/provider-data/dataset/xubh-q36u)
(Care Compare), module `ingestion/reference/hospitals.py`: one row per Georgia hospital, keyed by its
CCN, with its name, address, county, phone, hospital type, ownership, whether it has
emergency services, and `stars`, Medicare's overall rating from 1 to 5.

**Why.** A hospital is a place on the find-care screen, keyed by its CCN
([#135](https://github.com/wmespi/honest-healthcare/issues/135)). The insurer files only
name billing NPIs, and NPPES has no star rating, so this is the register of hospitals.

## What is kept

- **Georgia only** (`State = 'GA'`), every hospital type: acute care, critical access,
  psychiatric, children's, VA and Department of Defense hospitals.
- **`stars` is null** where CMS publishes "Not Available", which is common for small and
  specialty hospitals. The builder stops if a CCN repeats or a rating falls outside 1 to 5.

## How it flows

`make reference STEP=geocode` sends each hospital's address to Census too, so
`geocode_addresses.parquet` holds its coordinates and Census's standardized address
([geocode.md](geocode.md)). Comparing standardized addresses, or the street and ZIP in USPS's
abbreviations where Census can't place one, is how a biller registered at a hospital's
address can join that hospital. The find-care build reads this file for
`places`, where every hospital Census can place is a place ([build.md](../../integration/build.md#the-find-care-tables)).

## Not covered

- Census can't place some hospital addresses, for example a highway address in a small
  town. Those hospitals have no standardized address or point, so `places` leaves them out
  ([known gaps](../../docs/known-gaps.md#imaging)).
- A hospital's departments and campuses share its one CCN and address.

## Re-run when CMS publishes

CMS updates the dataset a few times a year and changes the file's path each time, so the
builder reads the current path from the dataset's metadata. Downloads are cached under
`data/raw/downloads/` by that path. Run `make reference STEP=hospitals`, then
`make reference STEP=geocode` for any new addresses. `ARGS="--file <csv>"` reads a local copy.
