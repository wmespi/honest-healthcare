"""Shared helpers for the reference-data builders (RBCS, NUCC, CMS utilization).

Small on purpose — a cached download and an atomic Parquet write. The real work
(joins, reshaping) is DuckDB SQL in each builder. Per the AGENTS.md language
principle, these builders are Python-over-DuckDB because they reshape CSVs
against landed Parquet with DuckDB's parallel C++ readers — not a hand-rolled
streaming parse.
"""
import os
import sys
import urllib.request


def store_dir(data_dir: str, test: bool, sub: str, env: str = "") -> str:
    """A data sub-store directory. `data-test/<sub>` under --test; otherwise the
    `env` var if set (so a worktree can rebuild into ./data-local/<sub> while
    serving still reads the shared corpus — GH #59 Part C), else
    `<data_dir>/<sub>`. `serving/data_sources.py` mirrors the same env vars."""
    if test:
        return f"data-test/{sub}"
    if env and os.getenv(env):
        return os.environ[env]
    return f"{data_dir}/{sub}"


def ref_dir(data_dir: str, test: bool) -> str:
    """The reference-output directory — honors REFERENCE_DIR (see store_dir)."""
    return store_dir(data_dir, test, "reference", "REFERENCE_DIR")


def cms_dir(data_dir: str, test: bool) -> str:
    """The CMS-output directory — honors CMS_DIR."""
    return store_dir(data_dir, test, "cms", "CMS_DIR")


def nppes_dir(data_dir: str, test: bool) -> str:
    """The NPPES directory (read) — honors NPPES_DIR."""
    return store_dir(data_dir, test, "nppes", "NPPES_DIR")


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


def fetch_to_cache_streaming(cache_path, urls, local=None, *, timeout=120,
                             progress_every=64 * 1024 * 1024):
    """Like fetch_to_cache, but streams to disk in chunks and resumes a partial
    download via an HTTP Range request — for sources too large to hold in memory
    (the CMS utilization CSV is ~3 GB).

    `local` short-circuits. An existing full `cache_path` is reused (size checked
    against Content-Length when the server reports it). A partial `.part` file is
    resumed. On success the `.part` is renamed onto `cache_path`.
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
            req = urllib.request.Request(u)
            if have:
                req.add_header("Range", f"bytes={have}-")
                print(f"  resuming {u} at {have:,} bytes")
            else:
                print(f"  downloading: {u}")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # A 200 to a Range request means the server ignored it — start over.
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
