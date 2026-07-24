# Honest Healthcare

Price transparency tooling for Anthem MRF (Machine-Readable Files) data. Parses negotiated rate files, stores them in Parquet, and exposes a rate explorer UI.

---

## What do the negotiated rates mean?

The negotiated rate is the **contracted allowed amount** — the total price the insurer and provider have agreed is acceptable for a given service. It is neither the hospital's billed charge (which is inflated arbitrarily) nor a pure reimbursement figure. It is the price the system actually transacts at.

```
Hospital bills:       $10,000  (chargemaster rate — largely meaningless)
Negotiated rate:       $1,200  (contracted allowed amount)
  ↓
Provider writes off:   $8,800  (contractually foregone — never collected)
  ↓
Patient pays:            $240  (e.g. 20% coinsurance × $1,200)
Insurer pays:            $960  (remaining 80%)
```

**Lower negotiated rate = lower patient cost**, because:

- **On a deductible** — patient pays the full negotiated rate (not the billed charge)
- **With coinsurance** — patient's 20% is 20% of $1,200, not 20% of $10,000
- **Toward out-of-pocket max** — accumulates faster, hitting the cap sooner

This only holds in-network. Out-of-network, the insurer may pay based on a different benchmark and the patient can be balance-billed for the gap — which is exactly why this data matters.

---

## Architecture

| Layer | Tech | Purpose |
|---|---|---|
| ETL | Go (streaming JSON) | Parses multi-GB MRF gzip files in a single pass, writes Parquet |
| Storage | Parquet + ZSTD | `data/anthem/rates/`, `providers/`, `codes/`, `npi_lookup.parquet` |
| Backend | Python + DuckDB | Queries Parquet globs in-process; no separate query server |
| Frontend | React + Vite | Rate explorer: histogram, filters, NPI search |
| Discovery DB | Postgres | Tracks `index_files` queue (URLs, status, plan names) |

For MRF data model details, rate file conflict resolution, and ETL flags, see [etl-go/ETL.md](etl-go/ETL.md).

---

## Running locally

```bash
docker compose up
```

Parse a specific rate file by ID (from the `index_files` table):

```bash
docker compose exec etl_go go run . -parse -file-ids 10065
```

UI: http://localhost:5173
