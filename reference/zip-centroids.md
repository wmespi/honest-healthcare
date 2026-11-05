# `make reference STEP=zip-centroids` — ZIP → lat/lon for distance

*Read this when working on `/find-care?zip=` or the distance column.*

Builds `data/reference/zip_centroids.parquet` — `zip | lat | lon` — from the
Census Gazetteer ZCTA file (internal point of each ZIP Code Tabulation Area).
Public, free, keyless. `make reference STEP=zip-centroids` →
`python3 -m reference.zip_centroids` (`reference/zip_centroids.py`); `--file`
takes a local Gazetteer `.zip` or `.txt`, `--test` isolates the output.

## What it is for

The user types a ZIP; the API needs a point to measure from. A ZIP's centroid is
a deliberately coarse origin — it says "near the middle of this area", so
`distance_mi` is a straight-line approximation, not a drive time. Provider
coordinates come from [geocode.md](geocode.md).

## Gaps

A ZCTA is a Census approximation of a USPS ZIP: ZIPs that are PO-box or
single-building have no ZCTA and no row. An unknown ZIP is a 404 from the API
(`unknown_zip`), never silently ignored.

## Downstream

`build/build.py` copies it to `serving/zip_centroids.parquet`; the API reads only
that copy. The find-care build writes `serving/zips.parquet` from it
([build.md](../build/build.md#the-find-care-tables)), which `/find-care` measures from to
each place's point with a haversine in `serving/ranking.py`.
