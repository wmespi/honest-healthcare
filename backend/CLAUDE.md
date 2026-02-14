# Backend Service

FastAPI REST API serving hospital pricing data from PostgreSQL.

## Tech Stack

- **Python 3.10** with FastAPI
- **SQLAlchemy** ORM with psycopg2-binary driver
- **Pydantic** for request/response validation
- **Uvicorn** ASGI server

## Running Locally

```bash
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Or via Docker:
```bash
docker-compose up backend
```

## API Endpoints

| Method | Path         | Description                          |
|--------|--------------|--------------------------------------|
| GET    | `/`          | Health check                         |
| GET    | `/rates`     | Query negotiated rates with filters  |
| GET    | `/hospitals` | List all hospitals                   |
| GET    | `/payers`    | List all insurance payers            |
| GET    | `/plans`     | List plans (filterable by payer)     |
| GET    | `/procedures`| Search procedures with filters       |

Auto-generated API docs available at `http://localhost:8000/docs`.

## Database

- **Table**: `emory_negotiated_rates`
- **Key columns**: hospital_name, billing_code, billing_code_type, procedure_type, setting, payer, plan, min/max/median rates, record_count
- **Indexes**: B-tree on billing_code, GIN trigram on procedure_description for ILIKE search
- **Connection**: Configured via `DATABASE_URL` env var

## Code Structure

- `main.py` — FastAPI app, route definitions, CORS config
- `models.py` — SQLAlchemy ORM model for emory_negotiated_rates
- `database.py` — Database session/engine management

## Conventions

- All query endpoints support optional filter parameters
- CORS is enabled for cross-origin frontend requests
- No authentication currently implemented
- No linter/formatter configured yet (recommend Ruff)
- No tests yet (recommend pytest)
