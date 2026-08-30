"""Honest Healthcare API — FastAPI over DuckDB / Parquet.

App wiring only. Data sources and the connection factory live in
data_sources.py; consumer-label helpers in labels.py; the routes in routers/.
See serving/serving.md.
"""
import datetime as _dt
import glob as _glob
import os as _os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .data_sources import (
    GROUP_SETS_SRC, NPI_LOOKUP_PATH, PRICES_GLOB, PRICES_SRC, PROVIDERS_SRC, db,
)
from .routers import providers, rates, reference

app = FastAPI(title="Honest Healthcare API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rates.router)
app.include_router(providers.router)
app.include_router(reference.router)


def _data_as_of():
    """Newest prices Parquet mtime → the 'rates as of' date for the trust bar."""
    try:
        files = _glob.glob(PRICES_GLOB, recursive=True)
        if files:
            return _dt.date.fromtimestamp(max(_os.path.getmtime(f) for f in files)).isoformat()
    except Exception:
        pass
    return None


@app.get("/")
def health():
    conn = db()
    try:
        prices    = conn.execute(f"SELECT COUNT(*) FROM {PRICES_SRC}").fetchone()[0]
        edges     = conn.execute(f"SELECT COUNT(*) FROM {GROUP_SETS_SRC}").fetchone()[0]
        providers = conn.execute(f"SELECT COUNT(*) FROM {PROVIDERS_SRC}").fetchone()[0]
        # context for the trust bar (issue #32) — priceable NPIs (npi_lookup, the
        # same set /providers/search flags has_rates against), the network list,
        # code coverage, and the data date.
        if _os.path.exists(NPI_LOOKUP_PATH):
            priceable_npis = conn.execute(
                f"SELECT COUNT(DISTINCT npi) FROM read_parquet('{NPI_LOOKUP_PATH}', union_by_name=true)"
            ).fetchone()[0]
        else:
            priceable_npis = conn.execute(f"SELECT COUNT(DISTINCT npi) FROM {PROVIDERS_SRC}").fetchone()[0]
        networks = [r[0] for r in conn.execute(
            f"SELECT DISTINCT network_name FROM {PRICES_SRC} ORDER BY 1"
        ).fetchall()]
        n_codes = conn.execute(f"SELECT COUNT(DISTINCT billing_code) FROM {PRICES_SRC}").fetchone()[0]
        return {"status": "ok", "total_prices": prices,
                "total_group_set_edges": edges, "total_providers": providers,
                "priceable_npis": priceable_npis, "networks": networks,
                "n_codes": n_codes, "as_of": _data_as_of()}
    except Exception as e:
        return {"status": "ok", "note": str(e)}
