# Honest Healthcare

[![CI](https://github.com/wmespi/honest-healthcare/actions/workflows/ci.yml/badge.svg)](https://github.com/wmespi/honest-healthcare/actions/workflows/ci.yml)

Find the cheapest care near you on your plan, from Anthem's price-transparency data.
It streams Anthem's multi-GB Machine-Readable Files (MRFs), stores the negotiated rates
as Parquet, and serves a screen that takes a scan, a ZIP code and a plan and shows
every place on the plan nearby: the most you'd pay there, your share of it on the
plan, and who does the scan.

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

Everything runs in containers - Go, Node, Python libraries and DuckDB
are **not** installed on your machine. You need four host tools:

| Tool | Why | Install |
|---|---|---|
| **Docker** + Compose v2 | runs every service | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (macOS/Windows) or [Docker Engine](https://docs.docker.com/engine/install/) + the compose plugin (Linux). Verify: `docker compose version` ≥ 2 |
| **make** | the task runner — every workflow is a `make` target | macOS: `xcode-select --install` · Debian/Ubuntu: `sudo apt install make` · Windows: use [WSL2](https://learn.microsoft.com/windows/wsl/install) |
| **git** | clone the repo | [git-scm.com](https://git-scm.com/downloads) (preinstalled on macOS/Linux) |
| **bash** + **python3** ≥ 3.8 | a few `make` targets run host-side helper scripts — Python **standard library only**, no `pip install` needed | preinstalled on macOS/Linux; on Windows use WSL2 |

## From zero to a running app

**1 - Install** the four tools in the table above.

**2 - Clone.**

```bash
git clone https://github.com/wmespi/honest-healthcare.git
cd honest-healthcare
```

**3 - Start the stack.** First run builds the service images (a few minutes).

```bash
make start     # macOS — launches Docker Desktop if needed, then all containers
# Linux: start Docker yourself, then:  make up
```

**4 - Verify it's up** - no external data needed:

```bash
curl localhost:8000/        # → 503 {"missing": [...]}: the API is up, with no find-care tables yet
open http://localhost:5173  # the UI loads (empty until step 6)
make test                   # full suite against committed fixtures
```

**5 - Load the data.** These pull large files from Anthem, CMS and Census and take a
while the first time; run them in order.

```bash
make discover       # large download - Anthem's master index → this month's file list
make reference      # the public datasets: Georgia's providers (NPPES) and plan files, Medicare's
                    #   rates and billing, CMS groups, hospitals, MIPS scores, ZIPs and geocodes;
                    #   builds whatever is missing, STEP=<name> runs one, FORCE=1 rebuilds all
make parse          # streams the rate files ingestion/etl/targets.yaml names into Parquet,
                    #   then publishes their rates by Georgia network
make reference STEP=geocode
                    # places the billing IDs parse found
```

**6 - Build the find-care tables:**

```bash
make build          # what ingestion delivered (data/ingestion/) → data/serving/
```

After the first load, `make refresh` is the whole monthly job.

After `make build`, the API picks up the new tables automatically — just refresh the UI. `make help`
lists every workflow. To reach the UI from another device, open
`http://<this-machine's-ip-or-hostname>:5173` — the frontend finds the API on
port 8000 of whatever host you loaded it from. `scripts/tailscale-up.sh` sets up
private off-network access; to send that link to someone who isn't a
developer, see [infra/README.md](infra/README.md#sharing-the-app-with-a-tester).

---

## Developing on a branch

The quickstart above needs no host runtimes.
To run the tests without Docker, add one tool:

```bash
brew install mise          # pins python/node/go per-repo; your system installs are untouched
# add the shell hook once:  https://mise.jdx.dev/getting-started.html
scripts/dev-setup.sh       # .venv + npm ci; run once

git switch -c espinoza/feat/my-thing
make test LOCAL=1          # gofmt · vet · build · go test · pytest · vitest, no Docker
```

Work on one branch at a time in this checkout.
To run **more than one branch at once**, `make worktree TOPIC=my-thing` creates `../hh-my-thing` with its own ports and data settings.
Rules and footprint: [AGENTS.md](AGENTS.md#development-commands).

---

## Stack

| Layer | Tech |
|---|---|
| ETL | Go — a hand-rolled streaming JSON parser (MRFs exceed 10 GB) |
| Storage | Parquet + ZSTD, read in-process by DuckDB |
| API | Python + FastAPI |
| UI | React + Vite |

Architecture, the Go-vs-Python split, and where each piece is documented:
[AGENTS.md](AGENTS.md).
