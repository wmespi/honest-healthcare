# Serving layer — FastAPI + DuckDB

*Read this when adding or changing an API route.* The API reads **only**
`data/serving/` - the build step's output (`make build`, [integration/build.md](../../integration/build.md)).
There is no fallback to raw Parquet: a missing build is a `503` from `GET /api/health`,
not a slower degraded mode.
Every route is under `/api`, because the same process serves the built screen at every other path (`screen.py`), so the two share one origin.
Tests: `test_api_contract.py` (every route,
hermetic — the `api` fixture in `conftest.py` builds the find-care tables from the
default test corpus, then binds a `TestClient`) and `test_contract.py` (the
contract's own rules) and `test_screen.py` (how the screen is served, that every route is under `/api`, and that a failure says nothing about how); `make test-live` runs the golden answers and journeys against
the live API ([docs/journeys.md](../../docs/journeys.md)). Add a route → add a contract test.

Every route runs raw DuckDB SQL against `read_parquet(...)` on the serving
tables — no ORM, `?` placeholders, one process-wide connection (`db()`).

## Layout

| File | Holds |
|---|---|
| `main.py` | app + gzip + `include_router` under `/api` + `GET /api/health`: when the build ran (`built_at`, from the build's `manifest.json`), the networks it priced and each find-care table's row count, or `503` with what's missing; a failure is a `500` whose `detail` is a `Problem` (`internal_error`), with the traceback only in the log; the screen mounted last |
| `screen.py` | the built screen (`application/frontend/dist/`) at every path outside `/api`: a built file as it is, any other path `index.html`, so the screen's router takes it; a missing asset is a `404`; `assets/` cached for good (Vite names them by content hash) and `private`, so only the browser keeps them, everything else checked on every load |
| `contract.py` | The typed contract for the find-care screen ([#135](https://github.com/wmespi/honest-healthcare/issues/135)): the Pydantic models for `GET /api/plans`, `GET /api/services` and `GET /api/find-care`, which FastAPI publishes at `/api/docs`. A place's most you'd pay and a search's cheapest, closest, median and order are computed in the models, so a response can't contradict itself. |
| `data_sources.py` | the find-care table sources (`*_SRC`), `db()` (one process-wide DuckDB database, `cursor()` per call), `missing_build()` and `manifest()` |
| `routers/find_care.py` | The find-care screen's routes, in `contract.py`'s shape, over the find-care tables in [integration/build.md](../../integration/build.md#the-find-care-tables): `/plans` (by tier, the richest first, then deductible, each with the `detail` that tells it apart), `/services`, and `/find-care?service_id&plan_id&zip`, every place on the plan's network with a price for the service within `radius_mi` of the ZIP's center (`haversine_sql()`), with the plan's cost sharing for it and each place's providers. With no `radius_mi`, the tightest radius with at least 5 places. An unknown service, plan or ZIP is a `404` whose `detail` is a `Problem` (`unknown_service`, `unknown_plan`, `unknown_zip`). |

SQL lives inline in the route handlers.
`main.py` is wiring only - don't add routes there; a genuinely new area gets
`routers/<x>.py` (`router = APIRouter()`, `@router.get`) plus `app.include_router(..., prefix="/api")`, above the screen's mount.

## What a price is

Every rule about who is on a plan and what a price is lives in the build
([integration/build.md](../../integration/build.md#the-find-care-tables)), so a route reads the tables as
they are and never re-derives a price. A route that needs a new fact gets a column in
the build, not SQL that works it out per request.

## Query rules

- **Always go through `db()`** (sets `memory_limit` and the spill dir); never a bare
  `duckdb.connect()`.
- Name the columns in `GROUP BY`, never positions. When an alias shares a column's name,
  group by the full expression, because the column wins.
- Parameterise with `?`; never f-string user input into SQL (module-constant table and
  column names are fine). A value used twice in one query goes in once, through a CTE.

## Finishing a route

Add the contract test (`test_api_contract.py`: 200 + shape, and the intended error
codes); update `application/frontend/src/api.js` and add a vitest case if the frontend calls it;
`docker compose restart application`, `make test`, then hit the route against live data.

## Known limits

[../docs/known-gaps.md](../../docs/known-gaps.md) holds what the prices leave out.
