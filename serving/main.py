"""Honest Healthcare API — FastAPI over DuckDB / Parquet.

App wiring only. Data sources and the connection factory live in
data_sources.py; consumer-label helpers in labels.py; the routes in routers/.
See serving/serving.md.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .data_sources import GROUP_SETS_SRC, PRICES_SRC, PROVIDERS_SRC, db
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


@app.get("/")
def health():
    conn = db()
    try:
        prices    = conn.execute(f"SELECT COUNT(*) FROM {PRICES_SRC}").fetchone()[0]
        edges     = conn.execute(f"SELECT COUNT(*) FROM {GROUP_SETS_SRC}").fetchone()[0]
        providers = conn.execute(f"SELECT COUNT(*) FROM {PROVIDERS_SRC}").fetchone()[0]
        return {"status": "ok", "total_prices": prices,
                "total_group_set_edges": edges, "total_providers": providers}
    except Exception as e:
        return {"status": "ok", "note": str(e)}
