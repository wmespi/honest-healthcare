# Honest Healthcare

[![CI](https://github.com/wmespi/honest-healthcare/actions/workflows/ci.yml/badge.svg)](https://github.com/wmespi/honest-healthcare/actions/workflows/ci.yml)

A rate explorer for Anthem's price-transparency data. It streams Anthem's
multi-GB Machine-Readable Files (MRFs), stores the negotiated rates as Parquet,
and serves a consumer UI that answers four questions: what a procedure costs at a
given provider, how that compares across plans, how it compares across providers,
and the full "menu" of what a provider has rates for.

**Primary use case:** the `BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM` plan — an
individual HMO on Anthem's Blue Value network in Georgia.

Building on or working in the repo? Start with **[AGENTS.md](AGENTS.md)**.

---

## What "negotiated rate" means

The negotiated rate is the **contracted allowed amount** — the price the insurer
and provider have agreed on. It's not the hospital's billed charge (inflated
arbitrarily) and not a pure reimbursement figure. It's what the system actually
transacts at.

```
Hospital bills:       $10,000  (chargemaster rate — largely meaningless)
Negotiated rate:       $1,200  (contracted allowed amount)
  ↓
Provider writes off:   $8,800  (contractually foregone — never collected)
  ↓
Patient pays:            $240  (e.g. 20% coinsurance × $1,200)
Insurer pays:            $960  (remaining 80%)
```

**Lower negotiated rate = lower patient cost**: on a deductible you pay the full
negotiated rate (not the billed charge); with coinsurance your 20% is 20% of
$1,200, not of $10,000; and it accumulates toward your out-of-pocket max faster.
This only holds in-network. These are negotiated rates, not a guaranteed
out-of-pocket cost.

---

## Requirements

Everything runs in containers — Go, Node, Python libraries, Postgres, and DuckDB
are **not** installed on your machine. You need four host tools:

| Tool | Why | Install |
|---|---|---|
| **Docker** + Compose v2 | runs every service | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (macOS/Windows) or [Docker Engine](https://docs.docker.com/engine/install/) + the compose plugin (Linux). Verify: `docker compose version` ≥ 2 |
| **make** | the task runner — every workflow is a `make` target | macOS: `xcode-select --install` · Debian/Ubuntu: `sudo apt install make` · Windows: use [WSL2](https://learn.microsoft.com/windows/wsl/install) |
| **git** | clone the repo | [git-scm.com](https://git-scm.com/downloads) (preinstalled on macOS/Linux) |
| **bash** + **python3** ≥ 3.8 | a few `make` targets run host-side helper scripts — Python **standard library only**, no `pip install` needed | preinstalled on macOS/Linux; on Windows use WSL2 |

## From zero to a running app

**1 — Install** the four tools in the table above.

**2 — Clone.**

```bash
git clone https://github.com/wmespi/honest-healthcare.git
cd honest-healthcare
```

**3 — Start the stack.** First run builds the service images (a few minutes).

```bash
make start     # macOS — launches Docker Desktop if needed, then all containers
# Linux: start Docker yourself, then:  make up
```

**4 — Verify it's up** — no external data needed:

```bash
curl localhost:8000/        # → {"status":"ok", ...}
open http://localhost:5173  # the UI loads (empty until step 5)
make test-all               # full suite against committed fixtures
```

**5 — Load real rate data.** These pull large files from CMS / Anthem and take a
while; run them in order, stop any time — the UI shows whatever has parsed.

```bash
make nppes          # ~1 GB — Georgia provider registry (names, specialties,
                    #   and the GA filter that keeps parse output small)
make discover       # ~8.7 GB one-time — Anthem's master index → the file queue
make parse GA=1     # streams the Georgia / individual-market rate files into
                    #   Parquet, smallest first
```

**6 — Consumer labels** (recommended — without these, procedures show as
"Medical"/"Surgery" and specialties are blank):

```bash
make code-labels
make taxonomy-labels
```

**7 — Provider↔procedure evidence** (optional — powers the "billed to Medicare"
line and the plausible-vs-group menu tiering; the app works without it):

```bash
make cms-utilization      # ~3 GB — CMS Medicare Part B: did this NPI bill this code
make specialty-profiles   # what each specialty typically bills (needs 6 + cms-utilization)
```

The API picks up new Parquet automatically — just refresh the UI. `make help`
lists every workflow. To reach the UI from another device, open
`http://<this-machine's-ip-or-hostname>:5173` — the frontend finds the API on
port 8000 of whatever host you loaded it from. `scripts/tailscale-up.sh` sets up
private off-network access.

---

## Stack

| Layer | Tech |
|---|---|
| ETL | Go — a hand-rolled streaming JSON parser (MRFs exceed 10 GB) |
| Storage | Parquet + ZSTD, read in-process by DuckDB |
| API | Python + FastAPI |
| UI | React + Vite |
| Queue | Postgres 15 + PostGIS (discovery queue only) |

Architecture, the Go-vs-Python split, and where each piece is documented:
[AGENTS.md](AGENTS.md).
