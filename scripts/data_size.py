"""Summarize on-disk data consumption — row counts + bytes per Parquet table.

Runs inside the serving container (needs duckdb). The Postgres side is added by
scripts/data_size.sh. Usage:

    docker compose exec -T -w /app serving python3 scripts/data_size.py [--data-dir /app/data] [--json]
"""
import argparse
import glob
import json
import os
import sys

import duckdb

# (label, path-relative-to-data-dir glob, hive_partitioning)
TABLES = [
    ("anthem/prices",        "anthem/prices/**/*.parquet",                     True),
    ("anthem/group_sets",    "anthem/group_sets/*.parquet",                    False),
    ("anthem/providers",     "anthem/providers/*.parquet",                     False),
    ("anthem/codes",         "anthem/codes/*.parquet",                         False),
    ("anthem/npi_lookup",    "anthem/npi_lookup.parquet",                      False),
    ("nppes/ga_providers",   "nppes/ga_providers.parquet",                     False),
    ("reference/code_labels","reference/code_labels.parquet",                  False),
    ("reference/nucc_taxonomy", "reference/nucc_taxonomy.parquet",             False),
    ("reference/specialty_procedure_profiles",
        "reference/specialty_procedure_profiles.parquet",                      False),
    ("cms/ga_provider_service", "cms/ga_provider_service.parquet",             False),
]


def human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{x:,.1f} {unit}" if unit != "B" else f"{int(x)} B"
        x /= 1024


def measure(con, data_dir: str, rel_glob: str, hive: bool):
    files = glob.glob(os.path.join(data_dir, rel_glob), recursive=True)
    if not files:
        return None
    size = sum(os.path.getsize(f) for f in files)
    src = f"read_parquet({[*files]!r}, union_by_name=true" + (", hive_partitioning=1" if hive else "") + ")"
    try:
        rows = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
    except Exception as e:  # a half-written / schema-mismatched file
        rows = -1
        print(f"  ! {rel_glob}: {e}", file=sys.stderr)
    return {"files": len(files), "bytes": size, "rows": rows}


def price_partitions(con, data_dir: str):
    files = glob.glob(os.path.join(data_dir, "anthem/prices/**/*.parquet"), recursive=True)
    if not files:
        return []
    out = []
    for d in sorted({os.path.dirname(f) for f in files}):
        net = os.path.basename(d).replace("net=", "")
        pf = glob.glob(os.path.join(d, "*.parquet"))
        size = sum(os.path.getsize(f) for f in pf)
        rows = con.execute(f"SELECT count(*) FROM read_parquet({[*pf]!r}, union_by_name=true)").fetchone()[0]
        out.append({"net": net, "files": len(pf), "bytes": size, "rows": rows})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect()
    rows_out = []
    tot_bytes = tot_rows = tot_files = 0
    for label, rel_glob, hive in TABLES:
        m = measure(con, args.data_dir, rel_glob, hive)
        if m is None:
            rows_out.append({"table": label, "files": 0, "bytes": 0, "rows": 0, "present": False})
            continue
        rows_out.append({"table": label, **m, "present": True})
        tot_files += m["files"]
        tot_bytes += m["bytes"]
        tot_rows += max(m["rows"], 0)

    parts = price_partitions(con, args.data_dir)

    if args.json:
        print(json.dumps({"tables": rows_out, "price_partitions": parts,
                          "total": {"files": tot_files, "bytes": tot_bytes, "rows": tot_rows}}, indent=2))
        return

    print(f"\n  {'Parquet table':<40} {'files':>7} {'rows':>15} {'size':>12}")
    print(f"  {'-'*40} {'-'*7} {'-'*15} {'-'*12}")
    for r in rows_out:
        if not r["present"]:
            print(f"  {r['table']:<40} {'—':>7} {'—':>15} {'—':>12}")
            continue
        rc = "err" if r["rows"] < 0 else f"{r['rows']:,}"
        print(f"  {r['table']:<40} {r['files']:>7} {rc:>15} {human(r['bytes']):>12}")
    print(f"  {'-'*40} {'-'*7} {'-'*15} {'-'*12}")
    print(f"  {'TOTAL (parquet)':<40} {tot_files:>7} {tot_rows:>15,} {human(tot_bytes):>12}")

    if parts:
        print(f"\n  anthem/prices by network partition")
        print(f"  {'-'*40} {'-'*7} {'-'*15} {'-'*12}")
        for p in parts:
            print(f"  {p['net'][:40]:<40} {p['files']:>7} {p['rows']:>15,} {human(p['bytes']):>12}")


if __name__ == "__main__":
    main()
