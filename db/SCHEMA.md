# Database Schema — Honest Healthcare

## Connection

```
Host:     localhost:5432  (or `db:5432` inside Docker)
Database: honest_healthcare
User:     postgres
Password: postgres
```

Test database uses the same credentials but `honest_healthcare_test` as the database name.

---

## Tables

### `index_files` — Processing Queue

The single source of truth for what has been discovered and what has been ingested. Every rate file URL found during discovery lives here. The ETL parser reads exclusively from this table to decide what to process next.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `reporting_entity_name` | TEXT | Insurer name from the index (e.g. "Anthem") |
| `reporting_entity_type` | TEXT | Usually "health insurance issuer" |
| `plan_name` | TEXT | Insurance plan name |
| `description` | TEXT | File description from the index |
| `location` | TEXT NOT NULL | S3 URL — natural unique key |
| `status` | VARCHAR(20) | `pending` / `processing` / `completed` / `failed` |
| `created_at` | TIMESTAMP | When discovered |
| `completed_at` | TIMESTAMP | When parse finished (added on migration) |

**Unique constraint:** `UNIQUE(location)` — discovery uses `ON CONFLICT (location) DO NOTHING`.

**Status transitions:**
```
[discover] → pending
[parse]    → pending → processing → completed
                               ↘ failed
```

To reset stale processing files after a crash:
```sql
UPDATE index_files SET status = 'pending' WHERE status = 'processing';
```

To force a full re-ingest:
```sql
UPDATE index_files SET status = 'pending' WHERE status = 'completed';
TRUNCATE negotiated_rates, provider_mappings, billing_codes;
```

---

### `provider_mappings` — NPI ↔ Provider Group

Maps individual National Provider Identifiers (NPIs) to the provider group they belong to, along with that group's Tax Identification Number (TIN). Populated by the parse phase from the `provider_references` section of each rate file.

| Column | Type | Notes |
|--------|------|-------|
| `provider_group_id` | BIGINT | Links to `negotiated_rates.provider_group_id` |
| `npi` | BIGINT | National Provider Identifier |
| `tin_type` | VARCHAR(20) | `ein` or `npi` |
| `tin_value` | VARCHAR(50) | Tax ID value |

**Indexes:** `idx_provider_mappings_npi`, `idx_provider_mappings_group`

---

### `negotiated_rates` — Core Rate Data

One row per (provider group, plan, billing code, negotiated price) combination. This is the largest table — expect hundreds of millions of rows for a full Anthem dataset.

| Column | Type | Notes |
|--------|------|-------|
| `provider_group_id` | BIGINT | Joins to `provider_mappings` |
| `plan_name` | VARCHAR(255) | Insurance plan (from `index_files.plan_name`) |
| `billing_code_type` | VARCHAR(50) | `CPT`, `HCPCS`, etc. |
| `billing_code` | VARCHAR(100) | FK to `billing_codes.billing_code` |
| `negotiation_arrangement` | VARCHAR(50) | e.g. `ffs` (fee-for-service) |
| `negotiated_type` | VARCHAR(50) | e.g. `negotiated`, `derived` |
| `negotiated_rate` | NUMERIC(12,2) | Dollar amount |
| `expiration_date` | DATE | When the rate expires |
| `service_code` | TEXT | Pipe-delimited place-of-service codes (e.g. `11|22`) |

**Indexes:** `idx_rates_group`, `idx_rates_code`

---

### `billing_codes` — Code Reference

Normalized lookup of billing code descriptions. Populated during parse — only the first occurrence of each code is recorded (deduplication happens in-process via a `seenBillingCodes` map).

| Column | Type | Notes |
|--------|------|-------|
| `billing_code_type` | VARCHAR(50) | |
| `billing_code` | VARCHAR(100) PK | |
| `name` | TEXT | Short procedure name |
| `description` | TEXT | Long-form description |

---

### `place_of_service_codes` — CMS Standard Codes (Seeded)

Static CMS reference table. Pre-populated from `init.sql` on first DB startup. Never written to by the ETL.

| Column | Type | Notes |
|--------|------|-------|
| `service_code` | VARCHAR(10) PK | |
| `name` | VARCHAR(255) | |
| `description` | TEXT | |

---

## Views

### `vw_rates_detailed`

Joins `negotiated_rates` with `billing_codes` and `place_of_service_codes` for human-readable query output. Does not include provider name/NPI — join to `provider_mappings` manually for that.

---

## Refresh Strategy

We do not keep historical data. When a new monthly index is published (typically the 1st of each month):

```sql
-- 1. Clear all ingested data (order matters if FKs are added later)
TRUNCATE negotiated_rates, provider_mappings, billing_codes, index_files;

-- 2. Re-run discovery with the new index URL
-- go run . -discover -index-url "https://...YYYY-MM-01_anthem_index.json.gz"

-- 3. Re-run parse
-- go run . -parse
```

The `completed_at` timestamps on `index_files` provide a lightweight record of when data was last ingested.

---

## Storage Expectations

Based on full Anthem dataset:

| Table | Expected Rows | Approximate Size |
|-------|--------------|-----------------|
| `negotiated_rates` | 100M–500M | 20–100 GB |
| `provider_mappings` | 10M–50M | 1–5 GB |
| `billing_codes` | ~10K | < 10 MB |
| `place_of_service_codes` | ~40 | < 1 MB |
| `index_files` | ~5,000 | < 1 MB |

Monitor live:
```sql
SELECT
  relname AS table,
  pg_size_pretty(pg_total_relation_size(oid)) AS total_size,
  pg_stat_get_live_tuples(oid) AS row_count
FROM pg_class
WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace
ORDER BY pg_total_relation_size(oid) DESC;
```
