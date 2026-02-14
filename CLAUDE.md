# Honest Healthcare

Healthcare price transparency platform that aggregates hospital pricing data (starting with Emory Healthcare) and presents it through a searchable comparison UI.

## Architecture

```
ETL (Bronze→Silver→Gold) → PostgreSQL → FastAPI Backend → React Frontend
```

- **ETL** (`/etl`): Medallion architecture pipeline that downloads, cleans, and aggregates hospital pricing CSVs
- **Backend** (`/backend`): FastAPI REST API serving filtered pricing data
- **Frontend** (`/frontend`): React + Vite SPA with cost comparison UI
- **Database**: PostgreSQL 15 with PostGIS + pg_trgm for fuzzy text search
- **Deploy** (`/deploy`): Dockerfiles for each service

## Running the Project

```bash
# Start all services via Docker Compose
docker-compose up --build

# Run ETL pipeline (in order)
docker-compose exec etl python /app/etl/bronze/bronze_emory.py
docker-compose exec etl python /app/etl/silver/silver_emory.py
docker-compose exec etl python /app/etl/gold/gold_emory.py
docker-compose exec etl python /app/etl/scripts/db_loader.py
```

### Service Ports

| Service  | Port  |
|----------|-------|
| Frontend | 3000  |
| Backend  | 8000  |
| Database | 5432  |

### Environment Variables

- `DATABASE_URL` (backend/etl): `postgresql://postgres:postgres@db:5432/honest_healthcare`
- `VITE_API_URL` (frontend): `http://localhost:8000`

## Development Rules

- **No global dependency installs.** All dependencies must be handled inside Docker containers. Never run `pip install`, `npm install -g`, or similar on the host machine.

## Git Conventions

- Branch naming: `username/feat/description` or `username/fix/description`
- Do not commit files in `/data` (gitignored)
- No `.env` files should be committed
- Jupyter notebooks (`.ipynb`) are gitignored — use `# %%` cell-format `.py` files for interactive work instead

## Project Status

- Currently supports Emory Healthcare hospitals
- `/etl/anthem/` exists for planned Anthem data expansion
- No CI/CD pipeline yet
- No test suite yet (directories exist but are empty)
- No authentication/authorization on the API
