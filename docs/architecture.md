# Architecture

*The standing architecture diagrams — one per **boundary**, not one per
component (a component diagram goes stale the first time a file moves). Read
this for how the pieces fit; read the per-topic docs in the
[AGENTS.md doc map](../AGENTS.md#where-to-look) for how each one works.*

Diagrams render on github.com; the GitHub mobile app sometimes shows the source
instead. Labels are plain text so every Markdown renderer has a chance.

## Layers

| Layer | Tech | Dir | Purpose |
|---|---|---|---|
| Discovery | Go + Postgres | `etl/discovery/` | Monthly metadata sync of MRF URLs into the `index_files` queue; keeps the plan→file link (`index_file_plans`), and writes it to `data/anthem/index_file_plans.parquet` for the build |
| Extraction | Go (streaming JSON) | `etl/extraction/` | Parses gzipped MRFs in one pass → raw Parquet; a provider probe aborts a download before `in_network` when no wanted provider is in it |
| Reference | Go (NPPES) + Python/DuckDB (RBCS, NUCC, CMS utilization, MPFS, DAC, geocode, MIPS, ZIP centroids, Georgia exchange plans, hospitals) | `etl/nppes/`, `reference/` | External public datasets → dimension Parquet |
| Build | Python + DuckDB | `build/` | Raw + reference Parquet → the find-care tables; every product decision (who is on a plan, what a price is, where a place is) lives here — [build/build.md](../build/build.md) |
| Serving | Python + DuckDB | `serving/` | Reads the find-care tables in-process via FastAPI (`localhost:8000`) |
| Frontend | React + Vite | `frontend/` | The find-care screen — `localhost:5173` |
| Storage | Parquet + ZSTD | `data/` | `data/anthem/`, `data/nppes/`, `data/reference/`, `data/cms/`, `data/serving/` — [docs/schema.md](schema.md) |
| Queue DB | Postgres 15 | `db/` | `index_files` + `index_file_plans` + `billing_codes` + `coverage_log` |

The Go CLI (`etl/`, one module) dispatches from `main.go` to the `discovery` /
`extraction` / `nppes` / `fixture` packages; shared structs, config, and the
progress reader live in `etl/core/`. Docker services: `db`, `etl`, `serving`,
`frontend` — each from a multi-stage Dockerfile in
[deploy/](../deploy/README.md) (`prod` target = deployable artifact; compose
runs the `dev` target).

## Data flow

```mermaid
flowchart LR
  subgraph fetch [fetch - Go, streaming, dumb, stable]
    IDX[Anthem index] -->|"plans and files, keep the link"| Q[(queue: file, plan, market, status)]
    Q -->|"the month's link, after a full read"| LINK[(plan link parquet)]
    TGT[targets.yaml] --> Q
    Q -->|"files linked to a target plan"| PROBE{"probe: any wanted providers?"}
    PROBE -->|"no: abort early"| Q
    PROBE -->|"yes: stream"| RAW[(raw parquet per file: prices, group_sets, providers, codes)]
    NPPES[NPPES national CSV] --> GA[(GA providers)]
    GA --> PROBE
  end
  subgraph build [build - Python and DuckDB, all product decisions, minutes]
    RAW --> FC["build/tables/"]
    LINK --> FC
    REF[(reference: NUCC, MPFS, OPPS, CMS billing, CMS groups, MIPS, geocodes, hospitals, ZIPs)] --> FC
    GA --> FC
    SVC[build/services.yaml] --> FC
    XNET[(reference: exchange networks, plans, premiums and cost sharing)] --> FC
    FC --> FCT[("find-care tables: networks, plans, premiums, zips, services, cost_sharing, places, prices, providers")]
  end
  subgraph serve [serve]
    FCT --> API[FastAPI, one persistent DuckDB connection]
    API --> WEB[React: find-care screen]
  end
```

## Serving entity model

```mermaid
erDiagram
    plans {
        plan_id str PK
        name str
        carrier str
        metal_level str
        needs_referral bool
        network_id str FK
        deductible number
    }
    cost_sharing {
        plan_id str PK, FK
        service_category str PK
        copay number(optional)
        coinsurance number(optional)
        after_deductible bool
    }
    networks {
        network_id str PK
        name str
    }
    premiums {
        plan_id str PK, FK
        rating_area int PK
        age int PK
        premium number
        tobacco_premium number
    }
    services {
        service_id str PK
        billing_code str
        service_category str
        name str
        keywords str
        about str
        could_change str[]
        asks str[]
        preventive_note str(optional)
        medicare_rate number(optional)
        medicare_facility_rate number(optional)
    }
    prices {
        network_id str PK, FK
        service_id str PK, FK
        place_id str PK, FK
        parts part[]
        several_rates bool
        billers str[]
        providers bigint[] FK
    }
    places {
        place_id str PK
        name str
        kind str
        address str
        city str
        lat number
        lon number
        phone str(optional)
        stars number(optional)
    }
    providers {
        npi bigint PK
        name str
        specialty str
        score number(optional)
    }
    zips {
        zip str PK
        lat number
        lon number
    }

    services ||--o{ prices : "service_id"
    places ||--o{ prices : "place_id"
    networks ||--o{ plans : "network_id"
    plans ||--o{ premiums : "plan_id"
    plans ||--o{ cost_sharing : "plan_id"
    services }o--o{ cost_sharing : "service_category"
    networks ||--o{ prices : "network_id"
    providers }o--o{ prices : "providers"
```

The find-care tables, the same model as the ERD on
[#135](https://github.com/wmespi/honest-healthcare/issues/135), which holds the decisions behind
it. Each box reads column, type, then key, and `[]` means a list; a list of keys is checked by
the build, not enforced by a database. Full column notes: [build/build.md](../build/build.md#the-find-care-tables).

## Runtime

```mermaid
flowchart TB
  subgraph host [one machine today, one VM later]
    C1[serving container: FastAPI + DuckDB; also runs make reference + make build - Python]
    C2[frontend container]
    C3[etl container: Go - make discover + make parse]
    D[(data on disk: raw, reference, serving)]
    C1 --> D
    C3 --> D
  end
  TS[Tailscale serve] --> C2
  TS --> C1
```

The `discover → parse → reference → build` chain runs across the etl (Go) and serving
(Python) containers; `make refresh` runs all of it.
