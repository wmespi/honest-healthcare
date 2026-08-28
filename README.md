# Honest Healthcare

Price transparency tooling for Anthem MRF (Machine-Readable Files) data. Streams
negotiated-rate files in one pass, stores them as Parquet, and exposes a consumer
rate explorer.

Contributors / agents: start with [AGENTS.md](AGENTS.md).

---

## What do the negotiated rates mean?

The negotiated rate is the **contracted allowed amount** — the total price the
insurer and provider have agreed is acceptable for a service. It is neither the
hospital's billed charge (inflated arbitrarily) nor a pure reimbursement figure. It
is the price the system actually transacts at.

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

- **On a deductible** — the patient pays the full negotiated rate, not the billed charge
- **With coinsurance** — the patient's 20% is 20% of $1,200, not of $10,000
- **Toward out-of-pocket max** — accumulates faster, hitting the cap sooner

This only holds in-network. Out-of-network the insurer may pay on a different
benchmark and the patient can be balance-billed for the gap — which is exactly why
this data matters. (These are negotiated rates, not a guaranteed out-of-pocket cost.)

---

## Architecture

| Layer | Tech | Purpose |
|---|---|---|
| Discovery | Go + Postgres | Monthly sync of MRF URLs into the `index_files` queue |
| Extraction | Go (streaming JSON) | Parses multi-GB MRF gzips in a single pass → Parquet |
| Storage | Parquet + ZSTD | `data/anthem/{prices,group_sets,providers,codes}/`; `data/nppes/`, `data/reference/` |
| Serving | Python + DuckDB | Queries the Parquet globs in-process; no separate query server |
| Frontend | React + Vite | Rate explorer: quote / menu / compare-providers / compare-networks |

Detail: [AGENTS.md](AGENTS.md) → the doc map. Data model and rate-file conflict
resolution: [etl-go/mrf-model.md](etl-go/mrf-model.md). On-disk schema:
[docs/schema.md](docs/schema.md).

---

## Target plan

The primary use case is analyzing rates for:

> **BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM**

an individual HMO on Anthem's Blue Value network in Georgia. The reliable filter is
`network_name = "GA Blue Value HIX Individual Network"`.

---

## Running locally

```bash
make start        # Docker Desktop (if needed) + all containers
make parse ID=21057   # parse one rate file by index_files.id
```

UI: http://localhost:5173 · API: http://localhost:8000 · `make help` for everything.
