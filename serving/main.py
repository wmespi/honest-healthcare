"""Honest Healthcare API — FastAPI over DuckDB / the serving Parquet tables.

App wiring only. Table sources and the connection live in data_sources.py;
the routes in routers/. See serving/serving.md.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .data_sources import (
    NETWORKS_SRC, PLACES_SRC, PLANS_SRC, PRICES_SRC, PROVIDERS_SRC, SERVICES_SRC, SERVING_DIR,
    db, manifest, missing_build,
)
from .routers import find_care

app = FastAPI(title="Honest Healthcare API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(find_care.router)

# The tables GET / counts, so a glance says what the build holds.
_COUNTED = {"plans": PLANS_SRC, "services": SERVICES_SRC, "places": PLACES_SRC,
            "prices": PRICES_SRC, "providers": PROVIDERS_SRC}


@app.get("/")
def health():
    """Health: when the build ran, the networks it priced and how many rows
    each find-care table holds. A missing build is an error, not a degraded
    mode: 503 with what's absent, so nobody mistakes an unbuilt stack for an
    empty corpus."""
    missing = missing_build()
    if missing:
        return JSONResponse(status_code=503, content={
            "status": "no_build",
            "message": f"serving tables missing under {SERVING_DIR} — run `make build`",
            "missing": missing,
        })
    conn = db()
    networks = [{"network_id": r[0], "name": r[1]} for r in conn.execute(
        f"SELECT network_id, name FROM {NETWORKS_SRC} ORDER BY network_id").fetchall()]
    rows = {name: conn.execute(f"SELECT count(*) FROM {src}").fetchone()[0] for name, src in _COUNTED.items()}
    return {"status": "ok", "built_at": manifest().get("built_at"), "networks": networks, "rows": rows}
