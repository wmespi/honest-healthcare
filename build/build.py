"""`make build`: raw + reference Parquet -> the find-care serving tables
(build/tables/, build/build.md), and a manifest of when and what was built.

Reads the shared corpus read-only and writes only the serving dir: SERVING_DIR,
else <data-dir>/serving, or data-test/serving under --test. The API reads
nothing else.
"""
import argparse
import datetime
import json
import os

from build import tables
from build.inputs.service_list import SERVICES_PATH
from build.inputs.targets import TARGETS_PATH
from reference._common import serving_dir as _serving_dir


def build(data_dir, serving_dir, targets_path=TARGETS_PATH, services_path=SERVICES_PATH):
    """Write the find-care tables and manifest.json under `serving_dir`; returns
    {table: rows}. A failed check writes nothing (build/tables/__init__.py)."""
    rows = tables.build(data_dir, serving_dir, targets_path, services_path)
    manifest = {
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "rows": rows,
    }
    with open(f"{serving_dir}/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"))
    ap.add_argument("--serving-dir", default=None,
                    help="output dir (default: SERVING_DIR env, else <data-dir>/serving)")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    data_dir = "data-test" if args.test else args.data_dir
    serving_dir = args.serving_dir or _serving_dir(data_dir, args.test)
    os.makedirs(serving_dir, exist_ok=True)
    build(data_dir, serving_dir)


if __name__ == "__main__":
    main()
