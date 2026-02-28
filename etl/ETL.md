# MRF ETL Pipeline - Implementation Plan

## Context

We need to build an end-to-end ETL pipeline that ingests insurer Machine Readable Files (MRFs), extracts **all** negotiated rates, and loads them into PostgreSQL. No filtering by state, hospital, or NPI — we ingest everything the insurer publishes and let the gold layer / frontend handle scoping.

**Anthem (Elevance)** is the best starting point because:

- We already have proven archived code for streaming their index and rate files
- Single S3-hosted index file with clear structure (`reporting_structure` → `in_network_files`)
- Known index URL pattern: `https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/YYYY-MM-DD_anthem_index.json.gz`

Runners-up for future expansion: **Aetna** (99.7% file accessibility, no barriers) and **Kaiser** (clean state separation, smaller files).

---

## Payor Comparison

| Payor | Index Format | Hosting | Barriers | Priority |
|-------|-------------|---------|----------|----------|
| **Anthem (Elevance)** | Single gzipped JSON | S3 | None | **1 - Selected** |
| **Aetna** | JSON index | Public URLs | None (99.7% accessible) | 2 |
| **Kaiser** | State-separated files | Public URLs | Smaller files | 3 |
| **UHC** | Table of contents JSON | Public URLs | Very large files | 4 |
| **Cigna** | JSON index | Public URLs | Complex structure | 5 |
| **Humana** | JSON index | Public URLs | Rate limiting | 6 |
| **BCBS** | Varies by affiliate | Varies | Fragmented across affiliates | 7 |

### Selection Rationale

1. **Proven code**: Archived scripts in `etl/archive/anthem/` already handle Anthem's index parsing and rate extraction
2. **Simple architecture**: Single index file → rate file URLs → streaming extraction
3. **Reliable hosting**: S3-hosted files with consistent URL patterns and no authentication

---

## Layer Architecture

### How bronze/silver/gold are segmented

| Layer | Scope | Code location | Data location | Purpose |
|-------|-------|---------------|---------------|---------|
| **Bronze** | Per-payor | `etl/<payor>/bronze.py` | `data/<payor>/index_urls.json` | Payor-specific discovery. Each payor has unique index formats, URL patterns, and hosting. No generalization possible. |
| **Silver** | Per-payor code, **universal DB schema** | `etl/<payor>/silver.py` | `data/<payor>/shards/*.parquet` → `mrf_rates` table | Payor-specific extraction that normalizes all rates into a standard schema. Writes Parquet shards locally as cache, then bulk loads into PostgreSQL. All payors produce identical columns. |
| **Gold** | Universal | Materialized views / `etl/gold.py` | PostgreSQL materialized views | Payor-agnostic aggregation layer that powers the frontend. Queries the `mrf_rates` table and creates summary views (e.g., min/max/median per procedure per provider). |

Adding a new payor means:
1. Write `etl/<payor>/inspect_keys.py` to discover the JSON structure
2. Create `etl/<payor>/bronze.py` and `silver.py`
3. Silver loads into the same `mrf_rates` table — gold views automatically include the new data

### Schema discovery (dev-time, not part of the pipeline)

Each payor's MRF files follow the CMS-mandated schema in theory, but vary in practice. Before we can *write* `silver.py` for a new payor, we need to understand how their rate files are actually structured. This is a one-time development task per payor, not a runtime pipeline step.

**Process for onboarding a new payor:**

1. **Run bronze** to collect rate file URLs from the index
2. **Run `inspect_keys.py`** against the first non-index rate file to discover:
   - Top-level keys and their types
   - `provider_references[0]` structure (how NPIs and provider groups are organized)
   - `in_network[0]` structure (how billing codes, negotiated rates, and prices are nested)
   - Any payor-specific quirks (e.g., some payors embed provider info directly in `negotiated_rates` instead of using `provider_references`)
3. **Save the discovered schema** to `data/<payor>/schema_sample.json` for reference
4. **Write silver.py** based on the confirmed structure

The silver schema we load into the DB is always the same — but the extraction logic to get there will differ per payor based on what `inspect_keys.py` reveals. There will be unknowns in the silver implementation until we complete this discovery step for each payor.

### Standard silver schema (all payors normalize to this)

This is the target schema for the `mrf_rates` PostgreSQL table. Every payor's silver layer must produce rows conforming to these columns:

```
npi                 STRING    # 10-digit NPI
billing_code        STRING    # CPT/HCPCS/DRG code
billing_code_type   STRING    # CPT, HCPCS, MS-DRG, etc.
procedure_name      STRING    # Human-readable name from MRF
negotiated_rate     FLOAT64   # Dollar amount
negotiated_type     STRING    # negotiated, fee schedule, derived, etc.
billing_class       STRING    # professional or institutional
service_codes       STRING    # Place of service codes (comma-separated)
expiration_date     STRING    # Rate expiration (YYYY-MM-DD)
network_name        STRING    # Network name
plan_name           STRING    # Plan name (from reporting_plans)
source_file         STRING    # URL of the rate file this came from
payor               STRING    # e.g. "anthem", "aetna"
```

> **Note:** This schema is our best guess based on the CMS standard and the archived Anthem extractor. The exact columns may be adjusted after running `inspect_keys.py` against actual Anthem rate files. Fields like `network_name`, `plan_name`, and `service_codes` depend on how the payor structures their `provider_references` and `negotiated_prices`.

### Relationship to existing data

The existing `emory_negotiated_rates` table stores **chargemaster** data (hospital price transparency files) with a different schema (`setting`, `payer`, `plan`, aggregated `min/max/median`). MRF data comes from **insurers** and has different semantics. These are separate datasets:

- **Chargemaster data** → existing `emory_negotiated_rates` table (unchanged)
- **MRF insurer data** → new `mrf_rates` table with its own model and endpoints

They can be unified later once both datasets are stable and we understand how to map them.

### Pipeline diagram

```
Per-payor                                         Universal
┌──────────────────────────────────────┐    ┌──────────────────────────┐
│  Bronze            Silver            │    │  Gold                    │
│ ┌───────────┐  ┌───────────────────┐│    │ ┌──────────────────────┐ │
│ │ Stream idx │  │ Stream rate files ││    │ │ Materialized views   │ │
│ │ Collect    │─>│ Write Parquet     ││    │ │ over mrf_rates table │ │
│ │ all URLs   │  │ Bulk load to DB   ││───>│ │ Aggregation for      │ │
│ └───────────┘  └───────────────────┘│    │ │ frontend queries     │ │
│  etl/anthem/    etl/anthem/         │    │ └──────────────────────┘ │
└──────────────────────────────────────┘    │  etl/gold.py             │
                                            └──────────────────────────┘
```

### Data directory structure

```
data/
  anthem/
    index_urls.json             # Bronze output: all discovered rate file URLs
    schema_sample.json          # inspect_keys output: discovered JSON structure
    shards/                     # Silver output: Parquet cache (also loaded to DB)
    checkpoint.json             # Silver resume state (items processed per URL)
  aetna/                        # Future — same structure
    index_urls.json
    schema_sample.json
    shards/
    checkpoint.json
```

---

## Checkpointing Strategy

Since we're processing hundreds of rate files, each potentially multi-GB, we need robust resume capability.

### Item-count checkpointing

Byte-offset seeking doesn't work with gzip streams (decompression state depends on all previous bytes). Instead, we track the number of `in_network` items successfully processed per URL:

```json
{
  "https://...file1.json.gz": {"status": "completed", "items_processed": 142000, "rows_loaded": 89000},
  "https://...file2.json.gz": {"status": "in_progress", "items_processed": 50000, "rows_loaded": 31000},
  "https://...file3.json.gz": {"status": "error", "items_processed": 12000, "error": "timeout"}
}
```

**On resume:** Re-open the gzip stream and skip the first N `in_network` items (via ijson iteration), then continue processing from item N+1. This re-downloads and re-decompresses the skipped portion, but avoids re-inserting duplicate rows.

**Parquet shards as local cache:** Silver writes Parquet shards to disk before bulk loading to the DB. If the DB load fails, we can retry from the local Parquet without re-downloading. If the stream fails mid-file, we have the partial Parquet and checkpoint to resume.

**Batch commits:** Silver commits to the DB in batches (e.g., every 10K rows). The checkpoint `items_processed` is updated after each successful batch commit, so we never lose more than one batch of work.

---

## Implementation Steps

### Step 1: Create shared utilities

**File:** `etl/utils/streaming.py`
- `stream_gzip_json(url, ijson_path)` — opens a gzip HTTP stream and yields ijson items
- Handles connection errors, retries, and timeout

**File:** `etl/utils/checkpoint.py`
- `CheckpointManager` class with `is_completed(url)`, `get_items_processed(url)`, `mark_progress(url, items, rows)`, `mark_completed(url)`, `mark_error(url, msg)`, `save()`
- Stores state in a JSON file per payor (`data/<payor>/checkpoint.json`)

Reuse existing: `etl/utils/logger.py` (already exists)

---

### Step 2: Bronze — Index scan and URL discovery

**File:** `etl/anthem/bronze.py`

Adapted from archived `etl/archive/anthem/anthem_index_parser.py`:

1. Stream Anthem index with ijson (`reporting_structure.item`)
2. Collect all `in_network_files` URLs, deduplicated by URL
3. Exclude dental/vision/pharmacy/behavioral by description keywords
4. Save to `data/anthem/index_urls.json`

**Output:** `data/anthem/index_urls.json` — array of `{description, location}`

No plan ID or NPI filtering. We collect every rate file URL the index references.

---

### Step 3: Silver — Stream, Filter, and Bulk Load to DB

**File:** `etl/anthem/extract_mrf_data.py` (formerly `silver.py`)

For each URL in `index_urls.json` (with checkpoint resume):

1. **Stream** the gzipped rate file with `ijson`.
2. **Filter** at the token level for target billing code types (Outpatient CPT/HCPCS).
3. **Buffer** valid rows in memory (e.g., 20,000 rows).
4. **Bulk Load** directly into the `mrf_rates` table via PostgreSQL `COPY FROM STDIN`.
5. **Stop Condition**: Processing automatically halts once the database size reaches **5GB** total, allowing for a phased evaluation of the data.
6. **Checkpoint** after each successful bulk load to track the URL and item count.

#### Anthem Silver Layer Strategy

Based on the [mrf_structure.json](../data/anthem/mrf_structure.json), we will use the following logic:

**1. Field Mapping**
- **npi**: `provider_references.item.provider_groups.item.npi` (resolved via `provider_reference` ID).
- **billing_code**: `in_network.item.billing_code`.
- **billing_code_type**: `in_network.item.billing_code_type`.
- **negotiated_rate**: `negotiated_prices.item.negotiated_rate`.
- **network_name**: `provider_references.item.network_name` (resolved via ID).

**2. Filtering & Performance**
- **Target Codes**: We will focus exclusively on the **Outpatient** setting for this initial phase.
    - **CPT (Current Procedural Terminology)**: This is our primary target. It covers the vast majority of outpatient medical procedures, office visits, and surgeries.
    - **HCPCS (Healthcare Common Procedure Coding System)**: We will include clinical HCPCS codes (like J-codes for injections or P-codes for lab tests) but explicitly **exclude non-procedural categories** including:
        - **A-Codes**: Transportation, Med/Surg Supplies, Administrative.
        - **B-Codes**: Enteral and Parenteral Therapy (Feeding/Nutrition).
        - **E-Codes / K-Codes**: Durable Medical Equipment (DME).
        - **L-Codes**: Orthotic and Prosthetic Procedures.
        - **V-Codes**: Vision and Hearing Services.
    - **Rationale**: This narrowing allows us to capture the "encounter-based" clinical costs that are most useful for price comparison, while shielding our database from the noise of thousands of rates for bandages, wheelchairs, and ambulance miles.
- **Computational Benefit**: We filter for these types at the **token level** using `ijson`. This allows the parser to skip the heavy work of object materialization for millions of irrelevant rates.

**3. Reference Resolution**
- **Phase A**: Fast scan of `provider_references` array to build an in-memory map of `{ id: { network_name, npis } }`.
- **Phase B**: Main scan of `in_network` rates to resolve metadata from the map and emit flattened rows.

**4. Error Handling**
- **Dead Letter Log**: Failures are categorized and logged to `data/anthem/failed_normalizations.json`:
    - `STRUCTURAL_MISMATCH`: The file layout differs from our map (requires review for new schema mapping).
    - `ENVIRONMENTAL_FAILURE`: Memory overload, network timeout, or process exit (retry-safe).
- **Structural Guarding**: Critical missing fields in individual items trigger a skip and a warning rather than a process crash.

#### Reliability & Persistence

To ensure we never lose data or duplicate work during container restarts:

1.  **Database Durability**: The PostgreSQL container uses a named Docker volume (`postgres_data`). This means all ingested rates are saved to your physical disk, not the container's ephemeral memory. Stopping or removing the container will NOT lose your data.
2.  **Progress Tracking**: The `CheckpointManager` saves a `checkpoint.json` file directly to the project's `data/anthem/` directory. Since this directory is mounted from your host machine into the container, the progress is never lost.
3.  **Resumption Logic**:
    *   **Skip Successful**: If `checkpoint.json` marks a URL as `completed`, it is skipped entirely on restart.
    *   **Partial Resume**: If the container shuts down mid-file (marks `in_progress`), it re-opens the stream and skips the previously processed items to avoid duplicating rows in the DB.
4.  **Sorting Logic**: Files are processed in **Smallest-to-Largest** order (based on `file_size_bytes` from `index_urls.json`) to maximize early feedback and schema verification on smaller, faster-loading files.
5.  **Auditability**: Every row in the `mrf_rates` table includes a `source_file` column. This allows us to verify exactly which records came from which Anthem URL.

---

### Step 4: Gold — Frontend aggregation layer

**File:** `etl/gold.py`

Creates materialized views in PostgreSQL for frontend queries:

```sql
-- Example: aggregated rates per procedure per provider
CREATE MATERIALIZED VIEW mrf_rates_summary AS
SELECT
  payor, npi, billing_code, billing_code_type, procedure_name, billing_class,
  MIN(negotiated_rate) as min_rate,
  MAX(negotiated_rate) as max_rate,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY negotiated_rate) as median_rate,
  COUNT(*) as record_count
FROM mrf_rates
GROUP BY payor, npi, billing_code, billing_code_type, procedure_name, billing_class;
```

Gold can also create additional views as the frontend needs evolve (e.g., by-hospital, by-payor comparisons). Running `gold.py` refreshes all materialized views.

---

### Step 5: Database model

**File:** `backend/models.py` — add `MRFRate` model for individual silver rows:
```
id, payor, npi, billing_code, billing_code_type,
procedure_name, negotiated_rate, negotiated_type, billing_class,
service_codes, expiration_date, network_name, plan_name, source_file
```

This is separate from the existing `NegotiatedRate` model (chargemaster data). Silver loads directly into this table.

---

### Step 6: Backend API endpoints

**File:** `backend/main.py` — add MRF-specific endpoints:
- `GET /mrf/rates` — query `mrf_rates_summary` view with filters: `payor`, `npi`, `billing_code`, `billing_class`
- `GET /mrf/providers` — distinct NPIs/providers with optional `payor` filter
- `GET /mrf/procedures` — distinct procedures with optional filters
- `GET /mrf/payors` — distinct payors

Follows the same patterns as existing `/rates`, `/hospitals`, `/procedures` endpoints.

---

### Step 7: Docker memory limit

**File:** `docker-compose.yml` — add memory limit to ETL service:
```yaml
deploy:
  resources:
    limits:
      memory: 4g
```

---

## Execution Order

```
1. etl/utils/streaming.py             (no deps)
2. etl/utils/checkpoint.py            (no deps)
3. etl/anthem/__init__.py             (empty)
4. etl/anthem/bronze.py               (uses streaming utils)
   → RUN: collect all rate file URLs
5. etl/anthem/extract_mrf_data.py          (uses streaming + checkpoint)
   → RUN: extract clinical rates directly to DB
6. etl/gold.py                        (creates materialized views)
   → RUN: create/refresh summary views
7. backend/models.py                  (add MRFRate model)
8. backend/main.py                    (add /mrf/* endpoints)
9. docker-compose.yml                 (memory limit)
```

Steps 1-3 are parallel. Steps 4-6 are sequential (each depends on prior output). Steps 7-9 can be done after 6.

**Before writing silver.py:** Run `etl/anthem/inspect_keys.py` (dev tool) to discover the JSON structure of Anthem's rate files. This is a one-time dev task, not a pipeline step.

---

## Verification

1. **Bronze:** Run `bronze.py` → confirm `data/anthem/index_urls.json` has URLs
2. **Silver:** Run `extract_mrf_data.py` with `limit=1` → confirm rows loaded to `mrf_rates` table
3. **Gold:** Run `gold.py` → confirm materialized views exist: `SELECT COUNT(*) FROM mrf_rates_summary`
4. **API:** `curl http://localhost:8000/mrf/rates?payor=anthem` returns data
