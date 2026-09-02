# Postgres schema — Honest Healthcare

**Authoritative on-disk schema is [../docs/schema.md](../docs/schema.md).** This
file covers only what Postgres actually holds. The serving layer reads Parquet, not
Postgres.

## Connection

```
Host:     localhost:5432  (db:5432 inside Docker)
Database: honest_healthcare
User / password: postgres / postgres
```

Test isolation uses the same database with `search_path=test` (`TEST_DATABASE_URL`).

## Tables written and read

| Table | Written by | Purpose |
|---|---|---|
| `index_files` | `make discover`, `make parse` | The parse queue — one row per MRF URL. `location` (signed URL, natural key within a month), `status` (`pending`/`processing`/`completed`/`failed`), `file_size_bytes`, `market_types[]`, `hios_issuer_ids[]`, `plan_states[]`, per-file `reporting_entity_*`, `created_at`, `completed_at`, `failure_reason`. GIN indexes on the array columns. |
| `billing_codes` | `make parse` | Reference upsert — `billing_code` PK, `billing_code_type`, `name`, `description`. `ON CONFLICT DO NOTHING` (first occurrence wins). |
| `coverage_log` | `make parse` | One row per parsed file (a re-parse replaces it) — rate/provider row counts, new codes/NPIs/TINs, distinct networks/settings/billing-classes, `notes` (GA-filter drop counts). The ETL never reads it; `make cov-report` flags partial-looking `completed` files from it (#52). |

Status lifecycle and recovery: [../etl/queue.md](../etl/queue.md).
Discovery upsert strategy: [../etl/discover.md](../etl/discover.md).

## Legacy — present in `init.sql`, neither written nor read

`negotiated_rates`, `provider_mappings`, `place_of_service_codes`, and the
`vw_rates_detailed` view predate the Parquet migration. Treat as legacy until
re-adopted. `db/migrations/*.sql` holds idempotent migrations for a running DB
(`init.sql` only runs on a fresh volume); `make migrate` applies them.
