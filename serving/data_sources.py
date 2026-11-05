"""Where the serving tables live and how to query them.

The API reads ONLY `data/serving/` — the build step's output (`make build`,
build/build.md; schema in ../docs/schema.md). Raw `anthem/`, `nppes/`,
`reference/`, `cms/` are build inputs, never read here, and there is no
fallback when the build is missing: `GET /` says so, and every other route
errors. This module holds the table sources, the one process-wide DuckDB
database.
"""
import json
import os
import threading
from typing import Optional

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
# The build's output dir. SERVING_DIR lets a worktree serve its own
# ./data-local/serving while the build reads the shared corpus (GH #59); with
# nothing set this is DATA_DIR/serving. `or`, not the getenv default: Compose
# passes an unset var through as "".
SERVING_DIR = os.getenv("SERVING_DIR") or f"{DATA_DIR}/serving"


def _table(name: str) -> str:
    return f"read_parquet('{SERVING_DIR}/{name}.parquet')"


# The find-care tables (build/tables/, the ERD on #135), which the routes read.
NETWORKS_SRC = _table("networks")
PLANS_SRC = _table("plans")
SERVICES_SRC = _table("services")
COST_SHARING_SRC = _table("cost_sharing")
PLACES_SRC = _table("places")
PRICES_SRC = _table("prices")
PROVIDERS_SRC = _table("providers")
ZIPS_SRC = _table("zips")
MANIFEST_PATH = f"{SERVING_DIR}/manifest.json"

_TABLES = ("networks", "plans", "services", "cost_sharing", "places", "prices", "providers", "zips")


def missing_build() -> list:
    """Which serving tables are absent — [] when the build is complete."""
    out = [t for t in _TABLES if not os.path.exists(f"{SERVING_DIR}/{t}.parquet")]
    if not os.path.exists(MANIFEST_PATH):
        out.append("manifest.json")
    return out


_MANIFEST: Optional[tuple] = None   # (mtime, dict)


def manifest() -> dict:
    """The build's manifest.json (what it was built from, when, row counts).
    Cached on the file's mtime so a rebuild is picked up without a restart.
    {} when there is no build."""
    global _MANIFEST
    try:
        mt = os.path.getmtime(MANIFEST_PATH)
    except OSError:
        return {}
    if _MANIFEST is None or _MANIFEST[0] != mt:
        try:
            with open(MANIFEST_PATH) as f:
                _MANIFEST = (mt, json.load(f))
        except (OSError, ValueError):
            return {}
    return _MANIFEST[1]


_DUCK_TMP = os.getenv("DUCKDB_TMP", "/tmp/duckdb_spill")
_DUCK_MEM = os.getenv("DUCKDB_MEMORY_LIMIT", "4GB")
_DUCK_THREADS = os.getenv("DUCKDB_THREADS")
_CONN = None
_CONN_LOCK = threading.Lock()


def db():
    """A cursor on the one process-wide DuckDB database.

    The database is created once (memory limit, spill dir, thread count set
    there, once) and kept for the life of the process, so Parquet metadata and
    zonemaps stay cached across requests. Each call returns `cursor()` — a
    lightweight connection to that same database with its own transaction and
    temp-table namespace, which is what makes it safe to use from FastAPI's
    request threads concurrently. Never `duckdb.connect()` per request.
    """
    global _CONN
    if _CONN is None:
        with _CONN_LOCK:
            if _CONN is None:
                import duckdb
                conn = duckdb.connect()
                try:
                    os.makedirs(_DUCK_TMP, exist_ok=True)
                    conn.execute(f"SET memory_limit = '{_DUCK_MEM}'")
                    conn.execute(f"SET temp_directory = '{_DUCK_TMP}'")
                    if _DUCK_THREADS:
                        conn.execute(f"SET threads = {int(_DUCK_THREADS)}")
                    conn.execute("SET preserve_insertion_order = false")
                except Exception:
                    pass
                _CONN = conn
    return _CONN.cursor()
