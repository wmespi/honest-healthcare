"""CMS Provider Data Catalog helpers shared by the quality-measure builders.

The catalog answers 429 when asked in quick succession, so the metadata call
retries with a growing pause. A builder names the columns it reads with
`require_columns`, which stops with a clear message when CMS renames one.
"""
import json
import time
import urllib.error
import urllib.request

from ._common import fetch_to_cache, raw_dir

METASTORE = "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/"


def _open(url: str, tries: int = 6):
    for i in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1:
                raise
            time.sleep(10 * (i + 1))


def metadata(dataset_id: str) -> dict:
    with _open(METASTORE + dataset_id) as r:
        return json.load(r)


def csv_url(dataset_id: str) -> str:
    """The dataset's current CSV. CMS re-stamps the file's path on every publish,
    so it is read from the dataset's metadata rather than kept here."""
    meta = metadata(dataset_id)
    for dist in meta.get("distribution", []):
        url = dist.get("downloadURL") or ""
        if url.lower().endswith(".csv"):
            print(f"  {meta.get('title')}, modified {meta.get('modified')}")
            return url
    raise SystemExit(f"{METASTORE}{dataset_id}: no CSV distribution")


def fetch_dataset(dataset_id: str, data_dir: str, test: bool, local: str | None = None) -> str:
    """A local path to the dataset's current CSV, cached under raw/downloads by its path."""
    if local:
        return local
    url = csv_url(dataset_id)
    cache = f"{raw_dir(data_dir, test, 'downloads')}/{url.rsplit('/', 2)[-2]}_{url.rsplit('/', 1)[-1]}"
    for i in range(6):
        try:
            return fetch_to_cache(cache, [url])
        except SystemExit:
            if i == 5:
                raise
            time.sleep(10 * (i + 1))


def require_columns(con, path: str, needed: list[str]) -> None:
    """Stop when the CSV at `path` lacks a column the builder reads."""
    have = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_csv('{path}', all_varchar=true, header=true)").fetchall()]
    missing = [c for c in needed if c not in have]
    if missing:
        raise SystemExit(f"{path}: CMS changed the file, no column {missing} (has {have})")
