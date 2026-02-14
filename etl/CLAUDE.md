# ETL Service

Data pipeline following Medallion Architecture (Bronze → Silver → Gold) for ingesting hospital pricing transparency data.

## Tech Stack

- **Python 3.10+** with Pandas and PyArrow
- **SQLAlchemy** + psycopg2 for database loading
- **requests** for HTTP downloads

## Pipeline Stages

Run in order:

```bash
# 1. Bronze: Download raw CSVs from hospital CMS-HPT endpoints
python etl/bronze/bronze_emory.py

# 2. Silver: Clean, standardize, filter for MS-DRG/APC codes
python etl/silver/silver_emory.py

# 3. Gold: Aggregate by hospital/code/payer, compute min/max/median
python etl/gold/gold_emory.py

# 4. Load: Sync gold CSV into PostgreSQL with indexes
python etl/scripts/db_loader.py
```

Or via Docker:
```bash
docker-compose exec etl python /app/etl/bronze/bronze_emory.py
# ... etc
```

## Directory Structure

```
etl/
├── bronze/           # Raw data download
│   └── bronze_emory.py
├── silver/           # Data cleaning and standardization
│   └── silver_emory.py
├── gold/             # Aggregation and analytics-ready output
│   └── gold_emory.py
├── scripts/          # Database loading utilities
│   └── db_loader.py
├── anthem/           # Planned: Anthem data source
├── archive/          # Archived pipeline versions
├── tests/            # Tests (empty, recommend pytest)
├── utils/            # Shared utilities (empty)
└── pyproject.toml    # Python project config
```

## Data Flow

- **Bronze**: Discovers hospitals from Emory's CMS-HPT index URL, downloads raw CSVs, maintains `hospital_catalog.json`
- **Silver**: Handles multi-encoding CSVs, dynamic header detection, filters for MS-DRG (inpatient) and APC (outpatient) codes, outputs `emory_all_cleaned.csv`
- **Gold**: Aggregates cleaned data, computes statistics, outputs `emory_gold.csv`
- **DB Loader**: Replaces `emory_negotiated_rates` table, creates B-tree + GIN trigram indexes

## Data Storage

All data lives in `/data` (or `/app/data` in Docker):
- `data/bronze/` — Raw downloaded CSVs
- `data/silver/` — Cleaned intermediate files
- `data/gold/` — Final aggregated files

The `/data` directory is gitignored.

## Conventions

- Each data source (Emory, Anthem, etc.) gets its own set of bronze/silver/gold scripts
- File naming: `{stage}_{source}.py` (e.g., `bronze_emory.py`)
- Pipeline stages are independent scripts, no orchestration framework (Airflow, etc.)
- Silver layer handles encoding quirks (cp1252, latin1, iso-8859-1)
- No linter/formatter configured yet (recommend Ruff)
- No tests yet
