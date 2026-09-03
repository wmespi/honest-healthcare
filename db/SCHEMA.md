# Postgres schema — Honest Healthcare

**Authoritative schema (Parquet + Postgres) is [../docs/schema.md](../docs/schema.md).**
This file is just a pointer — Postgres holds only the discovery queue plus two
small reference/log tables (`index_files`, `billing_codes`, `coverage_log`); the
serving layer reads Parquet, not Postgres.

- **Table columns, write paths, legacy tables, test isolation** —
  [../docs/schema.md § Postgres](../docs/schema.md#postgres--honest_healthcare-discovery--reference-only)
- **Parse-status lifecycle and stuck-row recovery** — [../etl/queue.md](../etl/queue.md)
- **Discovery upsert strategy / monthly churn** — [../etl/discover.md](../etl/discover.md)
- **Migrations** — `db/migrations/*.sql`, applied by `make migrate` (`init.sql`
  only runs on a fresh volume)
