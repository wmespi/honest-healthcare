"""Honest Healthcare API — FastAPI over DuckDB / the serving Parquet tables.

App wiring only. Table sources and the connection live in data_sources.py;
consumer-label helpers in labels.py; the routes in routers/. See serving/serving.md.
"""
import datetime as _dt
import glob as _glob
import os as _os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .data_sources import (
    CODE_DIM_SRC, GROUP_MEMBERS_SRC, GROUP_SETS_SRC, RATE_HIST_SRC, RATES_GLOB,
    SERVING_DIR, db, manifest, missing_build,
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
    """Newest rates Parquet mtime → the 'rates as of' date for the trust bar."""
    try:
        files = _glob.glob(RATES_GLOB, recursive=True)
        if files:
            return _dt.date.fromtimestamp(max(_os.path.getmtime(f) for f in files)).isoformat()
    except Exception:
        pass
    return None


@app.get("/")
def health():
    """Health + the trust-bar context. A missing build is an error, not a
    degraded mode: 503 with what's absent, so nobody mistakes an unbuilt
    stack for an empty corpus."""
    missing = missing_build()
    if missing:
        return JSONResponse(status_code=503, content={
            "status": "no_build",
            "message": f"serving tables missing under {SERVING_DIR} — run `make build`",
            "missing": missing,
        })
    conn = db()
    m = manifest()
    try:
        # Footer totals — scalar reads of the small tables, never a `rates` scan.
        prices = conn.execute(f"SELECT COALESCE(SUM(n_rates), 0) FROM {RATE_HIST_SRC}").fetchone()[0]
        edges = conn.execute(f"SELECT COUNT(*) FROM {GROUP_SETS_SRC}").fetchone()[0]
        members, priceable_npis = conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT npi) FROM {GROUP_MEMBERS_SRC}").fetchone()
        n_codes = conn.execute(f"SELECT COUNT(DISTINCT billing_code) FROM {CODE_DIM_SRC}").fetchone()[0]
        networks = [r[0] for r in conn.execute(
            f"SELECT DISTINCT network_name FROM {RATE_HIST_SRC} "
            f"WHERE network_name IS NOT NULL AND network_name != '' ORDER BY 1"
        ).fetchall()]

        # Which optional reference datasets went INTO the build (the API never
        # reads them directly any more). test_golden.py's skip guard and a
        # future admin panel read these rather than inferring from response
        # shape — "no NPPES" vs "NPPES loaded but this NPI has no rates".
        inputs = m.get("inputs", {})
        reference_loaded = {k: bool(inputs.get(k)) for k in
                            ("nppes", "nucc", "cms_utilization", "mpfs", "dac")}

        return {"status": "ok", "total_prices": int(prices),
                "total_group_set_edges": edges, "total_providers": members,
                "priceable_npis": priceable_npis, "networks": networks,
                "n_codes": n_codes, "as_of": _data_as_of(),
                "built_at": m.get("built_at"), "partial_build": bool(m.get("partial")),
                "reference_loaded": reference_loaded}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "note": str(e)})
