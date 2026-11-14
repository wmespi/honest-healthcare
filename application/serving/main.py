"""Honest Healthcare: the find-care API over DuckDB and the serving Parquet
tables, and the screen, from one process at one origin.

App wiring only. The API is under /api: table sources and the connection live
in data_sources.py, the routes in routers/. Every other path is the built
screen (screen.py). See application/serving/serving.md.
"""
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .contract import Problem
from .data_sources import (
    FAMILIES_SRC, NETWORKS_SRC, PLACES_SRC, PLANS_SRC, PRICES_SRC, PROVIDERS_SRC, SERVICES_SRC, SERVING_DIR,
    db, manifest, missing_build,
)
from .routers import find_care
from .screen import SCREEN_DIR, Screen

# Every route is under /api, so the screen has every other path; the API uses no OAuth.
app = FastAPI(title="Honest Healthcare API", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
              swagger_ui_oauth2_redirect_url=None)

# The screen's bundle and the find-care answers are text that shrinks several times over.
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(find_care.router, prefix="/api")

# The tables /api/health counts, so a glance says what the build holds.
_COUNTED = {"plans": PLANS_SRC, "services": SERVICES_SRC, "families": FAMILIES_SRC, "places": PLACES_SRC,
            "prices": PRICES_SRC, "providers": PROVIDERS_SRC}


@app.get("/api/health")
def health():
    """Health: when the build ran, each network ingestion delivered and how
    fresh its prices are (`current`, `stale` or `missing`, as of when, and
    why), and how many rows each find-care table holds. A missing build is an
    error, not a degraded mode: 503 with what's absent, so nobody mistakes an
    unbuilt stack for an empty corpus."""
    missing = missing_build()
    if missing:
        return JSONResponse(status_code=503, content={
            "status": "no_build",
            "message": f"serving tables missing under {SERVING_DIR} - run `make build`",
            "missing": missing,
        })
    conn = db()
    cursor = conn.execute(f"SELECT network_id, name, prices_status, prices_as_of, prices_reason "
                          f"FROM {NETWORKS_SRC} ORDER BY network_id")
    columns = [c[0] for c in cursor.description]
    networks = [dict(zip(columns, r)) for r in cursor.fetchall()]
    rows = {name: conn.execute(f"SELECT count(*) FROM {src}").fetchone()[0] for name, src in _COUNTED.items()}
    return {"status": "ok", "built_at": manifest().get("built_at"), "networks": networks, "rows": rows}


@app.exception_handler(Exception)
async def internal_error(request, exc):
    """A failure says only that it happened, never how. Starlette raises the
    exception again once this has answered, so the traceback reaches the
    server's log."""
    return JSONResponse(status_code=500, content={"detail": Problem(
        code="internal_error", message="Something went wrong on our side.").model_dump()})


# Last, so every /api route is matched before it.
app.mount("/", Screen(directory=SCREEN_DIR, check_dir=False), name="screen")
