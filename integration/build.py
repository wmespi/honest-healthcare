"""`make build`: what ingestion wrote -> the find-care serving tables
(integration/tables/, integration/build.md), and a manifest of when and what was built.

Reads only <data-dir>/ingestion/, which ingestion delivers, and writes only
<data-dir>/serving/, which the app reads; data-test/ stands in for <data-dir>
under --test.
"""
import argparse
import datetime
import json
import os

from integration import tables
from integration.inputs.service_list import SERVICES_PATH


def build(data_dir, serving_dir, services_path=SERVICES_PATH):
    """Write the find-care tables and manifest.json under `serving_dir`; returns
    {table: rows}. A failed check writes nothing (integration/tables/__init__.py)."""
    rows = tables.build(data_dir, serving_dir, services_path)
    manifest = {
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "rows": rows,
    }
    # Through a temp file, so GET /api/health never reads half a manifest.
    path = f"{serving_dir}/manifest.json"
    with open(f"{path}.tmp", "w") as f:
        json.dump(manifest, f, indent=2)
    os.replace(f"{path}.tmp", path)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"))
    ap.add_argument("--test", action="store_true", help="read and write under data-test/")
    args = ap.parse_args()

    root = "data-test" if args.test else args.data_dir
    os.makedirs(f"{root}/serving", exist_ok=True)
    build(f"{root}/ingestion", f"{root}/serving")


if __name__ == "__main__":
    main()
