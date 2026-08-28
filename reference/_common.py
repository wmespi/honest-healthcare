"""Shared helpers for the reference-data builders (RBCS, NUCC).

Small on purpose — a cached download and an atomic Parquet write. The real work
(joins, reshaping) is DuckDB SQL in each builder. Per the AGENTS.md language
principle, these builders are Python-over-DuckDB because they reshape small CSVs
against landed Parquet — not a streaming parse.
"""
import os
import sys
import urllib.request


def ref_dir(data_dir: str, test: bool) -> str:
    """The reference-output directory — data/reference or data-test/reference."""
    return f"{'data-test' if test else data_dir}/reference"


def _atomic_write_bytes(path: str, data: bytes) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def fetch_to_cache(cache_path, urls, local=None, *, header_check=None, timeout=120):
    """Return a local path to the source file.

    `local` short-circuits to that path; an existing `cache_path` is reused;
    otherwise each URL in `urls` is tried until one succeeds and is cached.
    `header_check` (bytes), when given, must appear in the first 200 bytes.
    """
    if local:
        return local
    if os.path.exists(cache_path):
        print(f"  cache hit: {cache_path}")
        return cache_path
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    last_err = None
    for u in urls:
        try:
            print(f"  downloading: {u}")
            with urllib.request.urlopen(u, timeout=timeout) as r:
                data = r.read()
            if header_check and header_check not in data[:200]:
                raise ValueError("unexpected header")
            _atomic_write_bytes(cache_path, data)
            print(f"  wrote {cache_path} ({len(data):,} bytes)")
            return cache_path
        except Exception as e:  # noqa: BLE001
            print(f"  ({u} failed: {e})", file=sys.stderr)
            last_err = e
    raise SystemExit(f"could not fetch source: {last_err}")


def write_parquet_atomic(con, select_sql: str, out_path: str) -> None:
    """COPY a SELECT to a temp Parquet, then os.replace() onto out_path — so a
    reader (the backend) never sees a half-written file."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = f"{out_path}.tmp.{os.getpid()}.parquet"
    con.execute(f"COPY ({select_sql}) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)")
    os.replace(tmp, out_path)
