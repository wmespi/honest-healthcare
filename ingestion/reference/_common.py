"""Shared helpers for the reference-data builders.

Small on purpose: where a builder reads and writes, a cached download and an
atomic Parquet write. The real work (joins, reshaping) is DuckDB SQL in each
builder. Per the AGENTS.md language principle, these builders are
Python-over-DuckDB because they reshape CSVs against landed Parquet with
DuckDB's parallel C++ readers, not a hand-rolled streaming parse.
"""
import os
import sys
import urllib.request


def store_dir(data_dir: str, test: bool, sub: str) -> str:
    """A directory of what ingestion delivers, which integration reads
    (docs/schema.md): `<data_dir>/ingestion/<sub>`, or `data-test/ingestion/<sub>`
    under --test."""
    return f"{'data-test' if test else data_dir}/ingestion/{sub}"


def raw_dir(data_dir: str, test: bool, sub: str) -> str:
    """A directory of ingestion's own files, which nothing after it reads:
    `<data_dir>/raw/<sub>`, or `data-test/raw/<sub>` under --test. Downloads are
    kept in `raw/downloads/`."""
    return f"{'data-test' if test else data_dir}/raw/{sub}"


def fetch_to_cache(cache_path, urls, local=None, *, header_check=None, headers=None, timeout=120,
                   progress_every=64 * 1024 * 1024):
    """Return a local path to the source file.

    `local` short-circuits to that path, and an existing `cache_path` is reused.
    Otherwise each URL in `urls` is tried until one succeeds: the download streams
    to `<cache_path>.part`, so a file of any size fits, resumes a partial `.part`
    with an HTTP Range request, and is moved onto `cache_path` once complete.
    `header_check` (bytes), when given, must appear in the file's first 200
    bytes, or the download is dropped and the next URL tried. `headers` go on
    each request, for a server that refuses Python's default User-Agent.
    """
    if local:
        return local
    if os.path.exists(cache_path):
        print(f"  cache hit: {cache_path}")
        return cache_path
    part = f"{cache_path}.part"
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    last_err = None
    for u in urls:
        try:
            have = os.path.getsize(part) if os.path.exists(part) else 0
            req = urllib.request.Request(u, headers=headers or {})
            if have:
                req.add_header("Range", f"bytes={have}-")
                print(f"  resuming {u} at {have:,} bytes")
            else:
                print(f"  downloading: {u}")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # A 200 to a Range request means the server ignored it, so start over.
                if have and r.status == 200:
                    have = 0
                total = have + int(r.headers.get("Content-Length", 0) or 0)
                mode = "ab" if have else "wb"
                got = have
                next_mark = got + progress_every
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if got >= next_mark:
                            pct = f" ({100 * got / total:.0f}%)" if total else ""
                            print(f"  ...{got:,} bytes{pct}")
                            next_mark = got + progress_every
            if header_check:
                with open(part, "rb") as f:
                    if header_check not in f.read(200):
                        os.remove(part)
                        raise ValueError("unexpected header")
            os.replace(part, cache_path)
            print(f"  wrote {cache_path} ({os.path.getsize(cache_path):,} bytes)")
            return cache_path
        except Exception as e:  # noqa: BLE001
            print(f"  ({u} failed: {e})", file=sys.stderr)
            last_err = e
    raise SystemExit(f"could not fetch source: {last_err}")


def write_parquet_atomic(con, select_sql: str, out_path: str) -> None:
    """COPY a SELECT to a temp Parquet, then os.replace() onto out_path — so a
    reader (the serving layer) never sees a half-written file."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = f"{out_path}.tmp.{os.getpid()}.parquet"
    con.execute(f"COPY ({select_sql}) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)")
    os.replace(tmp, out_path)
