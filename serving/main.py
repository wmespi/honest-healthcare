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
    CMS_UTILIZATION_PATH, GA_NPPES_PATH, GROUP_SETS_SRC, MPFS_GA_PATH,
    NPI_LOOKUP_PATH, NUCC_PATH, PRICES_GLOB, PRICES_SRC, PROVIDERS_SRC,
    RATE_HIST_PATH, RATE_SUMMARY_PATH, db, have_summary,
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
        # COUNT(*) over the parquet globs is footer-only (fast even at 1e9 rows).
        edges     = conn.execute(f"SELECT COUNT(*) FROM {GROUP_SETS_SRC}").fetchone()[0]
        providers = conn.execute(f"SELECT COUNT(*) FROM {PROVIDERS_SRC}").fetchone()[0]

        if _os.path.exists(NPI_LOOKUP_PATH):
            priceable_npis = conn.execute(
                f"SELECT COUNT(DISTINCT npi) FROM read_parquet('{NPI_LOOKUP_PATH}', union_by_name=true)"
            ).fetchone()[0]
        else:
            priceable_npis = 0

        # trust bar (issue #32): total rate rows, the network list, code coverage.
        # The DISTINCT aggregates would full-scan `prices` (645M+ rows → OOM), so
        # read them from the browse summary when it's built (#10).
        if have_summary():
            src = f"read_parquet('{RATE_SUMMARY_PATH}')"
            prices = conn.execute(f"SELECT COALESCE(SUM(n_rates), 0) FROM {src}").fetchone()[0]
            n_codes = conn.execute(f"SELECT COUNT(DISTINCT billing_code) FROM {src}").fetchone()[0]
            net_src = src
        else:
            prices = conn.execute(f"SELECT COUNT(*) FROM {PRICES_SRC}").fetchone()[0]
            n_codes = conn.execute(f"SELECT COUNT(DISTINCT billing_code) FROM {PRICES_SRC}").fetchone()[0]
            net_src = PRICES_SRC
        networks = [r[0] for r in conn.execute(
            f"SELECT DISTINCT network_name FROM {net_src} "
            f"WHERE network_name IS NOT NULL AND network_name != '' ORDER BY 1"
        ).fetchall()]

        # Which optional reference builds (README.md steps 6-8) are actually
        # on disk — file-exists flags, not a guess from response shape. A
        # consumer (test_golden.py's skip guard, a future admin panel) can
        # tell "no NPPES yet" from "NPPES loaded but this NPI has no rates"
        # without probing several endpoints and inferring from their shape.
        reference_loaded = {
            "nppes": _os.path.exists(GA_NPPES_PATH),
            "nucc": _os.path.exists(NUCC_PATH),
            "cms_utilization": _os.path.exists(CMS_UTILIZATION_PATH),
            "mpfs": _os.path.exists(MPFS_GA_PATH),
            "rate_hist": _os.path.exists(RATE_HIST_PATH),
        }

        return {"status": "ok", "total_prices": int(prices),
                "total_group_set_edges": edges, "total_providers": providers,
                "priceable_npis": priceable_npis, "networks": networks,
                "n_codes": n_codes, "as_of": _data_as_of(),
                "reference_loaded": reference_loaded}
    except Exception as e:
        return {"status": "ok", "note": str(e)}
