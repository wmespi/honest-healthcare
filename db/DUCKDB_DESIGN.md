# DuckDB Migration Design

## Why DuckDB

The current Postgres stack was designed for OLTP (frequent small transactions). The MRF workload is the opposite:
- **ETL**: one writer, sequential, bulk inserts of hundreds of millions of rows
- **Backend**: read-only analytical queries — "what does procedure X cost at hospitals near me?"

Postgres's WAL, live B-tree index maintenance, and row-oriented storage create unnecessary overhead for this pattern. A single Blue Value HMO Georgia rate file took ~2 hours to write to Postgres but ~7 minutes to stream in dry-run mode — the 17x gap is entirely DB write overhead.

DuckDB + Parquet eliminates that overhead:
- No WAL, no checkpoint pressure during writes
- Columnar compression: ~10-20x smaller than Postgres for repetitive rate data
- Analytical queries read only the columns they need — fast scans across billions of rows
- No server process — embedded library in both the ETL and backend

Since the backend only reads and the backend needs a full rewrite anyway, the migration cost is effectively zero.

---

## Delivery Phases

### Phase 1 — Rate Distribution (no NPPES required)
> "I have **Anthem Bronze Blue Value HMO 5000**. For **knee replacement (CPT 27447)**, what is the distribution of negotiated rates across all providers in my network?"

Show min / max / median / percentile spread. No provider names or locations needed — just the rates themselves. Unblocks real user value immediately and validates the full ETL pipeline before adding geographic complexity.

**Data required:** rate files (already have) + plan-name mapping layer

### Phase 2 — Geographic Provider Lookup (end goal)
> "For the same procedure and plan, which specific hospitals are within 20 miles of my zip code, and what does each one get paid?"

Adds provider identity and location from NPPES. The Phase 1 schema is a direct subset — NPPES enrichment adds columns to existing rows rather than restructuring anything.

**Additional data required:** NPPES NPI registry (~9 GB CSV from CMS, updated monthly)

---

## Target Query Pattern (Phase 2)

> "I have **Anthem Bronze Blue Value HMO 5000**. For **knee replacement (CPT 27447)**, what are the negotiated rates at hospitals within 20 miles of my zip code?"

This drives the final schema decisions below.

---

## Data Sources Required

### 1. Anthem MRF Rate Files (current)
What we have: `provider_group_id → billing_code → negotiated_rate`

What we're missing: any human-readable provider information. A provider group ID is opaque — we can't tell if it's a hospital in Atlanta or a clinic in Savannah.

### 2. NPPES (National Plan and Provider Enumeration System) — **MISSING, CRITICAL**
CMS publishes the full NPI registry as a monthly CSV (~9 GB compressed):
- NPI → provider name, address, city, state, zip, phone, specialty
- Download: https://download.cms.gov/nppes/NPI_Files.html

Without NPPES, we can tell a user "your plan pays $1,200 for CPT 27447 to provider group 98765" but not which hospital that is or where it's located.

### 3. Plan-to-Network Mapping — **MISSING, IMPORTANT**
Consumer-facing plan names ("Anthem Bronze Blue Value HMO 5000 $30 $0 Virtual PCP") don't appear in the MRF rate files. The rate files use internal network names ("BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM").

A mapping layer is needed so users can select their plan name and get routed to the correct rate file(s). Sources:
- Healthcare.gov plan data (for ACA marketplace plans)
- CMS Plan Finder
- Possibly scraped from Anthem's plan pages

---

## Source File Structure

Every in-network rate file has these root-level keys (confirmed from `mrf_example.json`):
```
in_network            → rates per billing code, referencing provider_group_ids
last_updated_on       → "2026-07-01"
provider_references   → maps provider_group_id → NPI list
reporting_entity_name → "Anthem Blue Cross and Blue Shield Georgia"
reporting_entity_type → "Health Insurance Network"
version               → "2.0.0"
```

**Key join:** `in_network[].negotiated_rates[].provider_references[]` contains raw `provider_group_id`
integers — NOT NPIs. The root `provider_references[]` section maps those IDs to actual NPIs:

```
rates.provider_group_id
  ──► providers.provider_group_id
        ──► providers.npi
              ──► (Phase 2) NPPES lookup: name, address, city, state, zip, lat/lng
```

**File ordering note:** `in_network` appears before `provider_references` in the file. A single
streaming pass cannot produce a fully joined row. Strategy: write both tables separately during
the single pass, join at query time. DuckDB handles this efficiently.

---

## Proposed Schema

Four Parquet files for rate data + one DuckDB file for ETL state.

### Table 1: `rates` (large Parquet — one file per source rate file)

One row per `(provider_group_id, billing_code, billing_class, setting)` combination.
Sourced entirely from `in_network[]` during streaming.

```sql
CREATE TABLE rates (
    source_file_id             INTEGER,  -- links to index_files

    provider_group_id          BIGINT,   -- joins to providers and network_providers

    -- Procedure
    billing_code               VARCHAR,
    billing_code_type          VARCHAR,  -- CPT, HCPCS, DRG, RC, etc.
    billing_code_type_version  VARCHAR,  -- e.g. "2025"
    negotiation_arrangement    VARCHAR,  -- "ffs" | "bundle" | "capitation"

    -- Rate
    negotiated_rate            DOUBLE,
    negotiated_type            VARCHAR,  -- "fee schedule" | "derived" | "per diem" | "case rate" | "percentage"
    billing_class              VARCHAR,  -- "professional" | "institutional"
    setting                    VARCHAR,  -- "outpatient" | "inpatient"
    expiration_date            DATE,
    service_codes              VARCHAR   -- pipe-separated place-of-service codes e.g. "11|22"
);
```

### Table 2: `providers` (large Parquet — one file per source rate file)

Maps `provider_group_id` → individual NPIs. Sourced from root `provider_references[]`.
TIN deferred — not needed for NPI-based NPPES lookup.

```sql
CREATE TABLE providers (
    source_file_id    INTEGER,
    provider_group_id BIGINT,   -- joins back to rates.provider_group_id
    npi               BIGINT    -- used to look up provider identity in NPPES (Phase 2)
    -- tin_type / tin_value: deferred, not needed for current use case
);
```

### Table 3: `network_providers` (medium Parquet — bridge table)

One row per `(network_name, provider_group_id)`. A single provider group can belong to
multiple networks (e.g. `["Blue Value Individual Commercially Priced", "GA Blue Value HIX
Individual Network"]`). Sourced from `provider_references[].network_name[]`.

```sql
CREATE TABLE network_providers (
    source_file_id        INTEGER,
    network_name          VARCHAR,  -- "GA Blue Value HIX Individual Network"
    provider_group_id     BIGINT,   -- joins to rates and providers
    reporting_entity_name VARCHAR   -- "Anthem Blue Cross and Blue Shield Georgia"
);
```

### Table 4: `billing_codes` (small DuckDB table — reference/dim)

Low-cardinality fields (`billing_class`, `setting`, `negotiated_type`) stay inline in `rates` —
DuckDB's dictionary encoding compresses repeated strings to near-nothing. `billing_codes` is
worth separating only because `name` and `description` are long text that would bloat `rates`.

```sql
CREATE TABLE billing_codes (
    billing_code               VARCHAR PRIMARY KEY,
    billing_code_type          VARCHAR,
    billing_code_type_version  VARCHAR,
    name                       VARCHAR,
    description                VARCHAR
);
```

### Table 5: `index_files` (DuckDB native — ETL queue + source provenance)

Replaces Postgres `index_files`. Extended with metadata captured from each file's root keys.

```sql
CREATE TABLE index_files (
    id                     INTEGER PRIMARY KEY,
    plan_names             VARCHAR[],
    description            VARCHAR,
    location               VARCHAR UNIQUE,
    file_size_bytes        BIGINT,
    status                 VARCHAR DEFAULT 'pending',
    created_at             TIMESTAMP DEFAULT now(),
    completed_at           TIMESTAMP,
    -- Populated after parse (from file root metadata):
    reporting_entity_name  VARCHAR,  -- "Anthem Blue Cross and Blue Shield Georgia"
    reporting_entity_type  VARCHAR,  -- "Health Insurance Network"
    last_updated_on        DATE,     -- "2026-07-01"
    schema_version         VARCHAR   -- "2.0.0"
);
```

---

## File Layout

```
data/
  anthem/
    index_schema.json          -- captured during discovery
    mrf_example.json           -- captured during dry-run parse
    rates/
      GA_JBKEMED0001.parquet   -- one file per source rate file
      GA_JBNKMED0000.parquet
      ...
    providers.parquet          -- NPPES-enriched provider table (all states)
    billing_codes.parquet      -- CMS billing code reference
  duckdb/
    etl_state.duckdb           -- index_files queue + plans mapping (written by ETL)
    rates.duckdb               -- optional: DuckDB native view over Parquet files
```

Partitioning Parquet by source file (one `.parquet` per rate file) means:
- ETL can write each file independently and atomically swap on completion
- Backend can query a single state's files without scanning national data
- Failed/re-runs only replace the affected file

---

## Example Queries

**"What does my plan pay for CPT 99213 at offices near zip 30301?"**
```sql
SELECT
    provider_name,
    address,
    city,
    negotiated_rate,
    negotiated_type,
    expiration_date
FROM read_parquet('data/anthem/rates/GA_*.parquet')
WHERE billing_code = '99213'
  AND billing_code_type = 'CPT'
  AND network_name = 'BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM'
  AND state = 'GA'
  AND zip_distance(zip, '30301') <= 20  -- or haversine on lat/lng
ORDER BY negotiated_rate ASC;
```

**"What's the rate range for knee replacement (CPT 27447) across all Georgia hospitals?"**
```sql
SELECT
    provider_name,
    city,
    MIN(negotiated_rate) as min_rate,
    MAX(negotiated_rate) as max_rate,
    COUNT(*) as rate_variants
FROM read_parquet('data/anthem/rates/GA_*.parquet')
WHERE billing_code = '27447'
  AND provider_type ILIKE '%hospital%'
GROUP BY provider_name, city
ORDER BY min_rate ASC;
```

---

## Migration Considerations

### 1. NPPES Integration is the Critical Path
Without provider names and locations, the rate data is nearly unusable for the end user. NPPES should be loaded before any rate file parsing. The ETL join during parse:
- Download NPPES CSV once (~9 GB)
- Load into a DuckDB providers table
- During rate file parse, join provider_group_id → NPI → NPPES record to produce denormalized rows

### 2. go-duckdb Requires CGO
The `go-duckdb` library requires the DuckDB C library and CGO enabled. This affects Docker builds:
- Base image needs build tools (`gcc`, `g++`)
- Build time increases
- Alternative: write Parquet with `parquet-go` (pure Go, no CGO) and use DuckDB CLI for queries during development

### 3. Atomic File Updates for Zero-Downtime
While ETL writes a new Parquet file, the backend must keep serving the old one:
1. ETL writes to `GA_JBKEMED0001.parquet.tmp`
2. On success: `rename("...tmp", "...parquet")` — atomic on POSIX
3. Backend's next query picks up the new file automatically

### 4. Plan Name Mapping Layer
The biggest data gap. Users know "Anthem Bronze Blue Value HMO 5000" — not "BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM". Options:
- Manual curation for the plans we care about (fast to start)
- Scrape Healthcare.gov plan data for ACA marketplace plans
- CMS has a machine-readable plan crosswalk for marketplace plans

### 5. Geographic Queries
DuckDB has a spatial extension (`LOAD spatial`) that supports:
- `ST_Distance` for point-to-point distance
- H3 geospatial indexing for efficient radius queries

Alternatively: a zip-code-to-lat/lng lookup table (~40k rows) enables simple haversine distance filtering without a spatial extension.

### 6. Storage Budget per State
From the dry run on `GA_JBKEMED0001.json.gz` (one of ~46 Georgia files):
- Estimated provider rows: ~100-500M
- As Parquet with columnar compression: **~5-15 GB per file**
- All Georgia files: **~50-150 GB**
- National coverage (all states): **~500 GB - 2 TB**

A full national load will require external storage (S3, NAS) rather than local disk. DuckDB can query Parquet directly from S3 via the `httpfs` extension.

### 7. Postgres Removal
Once migration is complete, the following can be removed:
- `db/init.sql`
- `db/SCHEMA.md`
- `docker-compose.yml` `db` service and `postgres_data` volume
- `pgx/v5` dependency from `etl-go/go.mod`
- All `conn.Exec`, `conn.CopyFrom`, `conn.SendBatch` calls in the ETL

---

## Open Questions

1. **Where does NPPES data come from and how often is it refreshed?** CMS publishes monthly. Do we reload it monthly or just on first setup?
2. **How do we handle the plan name mapping?** Manual for now, or automate against Healthcare.gov?
3. **Do we deploy DuckDB files to the backend server or query them remotely via S3?** Local is simpler; S3 enables serverless deployment.
4. **What does the backend API surface look like?** The schema design above assumes: `GET /rates?billing_code=99213&zip=30301&plan_id=...&radius=20`
5. **Do we need historical rates?** MRF files change monthly. Do we store snapshots or only the current file?
