# Architecture

*The standing architecture diagrams — one per **boundary**, not one per
component (a component diagram goes stale the first time a file moves). Read
this for how the pieces fit; read the per-topic docs in the
[AGENTS.md doc map](../AGENTS.md#where-to-look) for how each one works.*

Diagrams render on github.com; the GitHub mobile app sometimes shows the source
instead. Labels are plain text so every Markdown renderer has a chance.

## Layers

The pipeline is three layers, each in its own container, and each reads only what the layer before it delivers ([decided on #135](https://github.com/wmespi/honest-healthcare/issues/135#issuecomment-6669410856)).
A layer can change freely as long as what it delivers keeps its contract, so another payer's ingestion can differ from Anthem's while integration and the app read the same tables.

| Layer | Container | Code | Reads | Writes |
|---|---|---|---|---|
| Ingestion | `ingestion`: Go + Python/DuckDB | `ingestion/` | Anthem, NPPES, Georgia Access, CMS, NUCC, Census | `data/raw/`, its own files, and `data/ingestion/`, the contract integration reads |
| Integration | `integration`: Python/DuckDB | `integration/` | `data/ingestion/`, read-only | `data/serving/`, the find-care tables |
| Application | `serving`: FastAPI + DuckDB, `localhost:8000`; `frontend`: React + Vite, `localhost:5173` | `serving/`, `frontend/` | `data/serving/`, read-only | nothing |

The contract between ingestion and integration is the datasets in `integration/inputs/datasets.py`, each with its columns and key, which the build checks before it reads them; between integration and the app it's the find-care tables below.
The on-disk layout is in [docs/schema.md](schema.md).

Inside ingestion:

| Step | Tech | Dir | Purpose |
|---|---|---|---|
| Discovery | Go | `ingestion/etl/discovery/` | Monthly read of Anthem's index: the files it lists (`data/raw/anthem/index_files.parquet`) and the plan→file link (`index_file_plans.parquet`) |
| Extraction | Go (streaming JSON) | `ingestion/etl/extraction/` | Parses the gzipped MRFs `ingestion/etl/targets.yaml` names in one pass → raw Parquet, with a parse record per file; a provider probe aborts a download before `in_network` when no wanted provider is in it |
| Reference | Go (NPPES) + Python/DuckDB (NUCC, CMS utilization, MPFS, OPPS, DAC, geocode, MIPS, ZIP centroids, Georgia exchange plans, hospitals) | `ingestion/etl/nppes/`, `ingestion/reference/` | External public datasets → dimension Parquet |
| Publish | Python + DuckDB | `ingestion/anthem_rates.py` | The last step of `make parse`: each target network's rates from the parsed files, keyed on Georgia's network ID (`data/ingestion/payer_rates/`) |

The Go CLI (`ingestion/etl/`, one module) dispatches from `main.go` to the `discovery` /
`extraction` / `nppes` / `fixture` packages; shared structs, config, and the
progress reader live in `ingestion/etl/core/`.
Every container builds from a multi-stage Dockerfile in [deploy/](../deploy/README.md) (`prod` target = deployable artifact; compose runs the `dev` target).

## Data flow

```mermaid
flowchart LR
  subgraph ingestion ["ingestion: Go streams, Python and DuckDB reshape"]
    IDX[Anthem index] -->|"this month's files and the plans each prices"| LIST[(file list and plan link)]
    LIST --> PICK["the files targets.yaml names, new or updated since their parse record"]
    TGT[targets.yaml] --> PICK
    PICK --> PROBE{"probe: any wanted providers?"}
    PROBE -->|"no: abort early, record skipped"| REC[(parse record per file)]
    PROBE -->|"yes: stream"| RAW[(rates as Anthem files them: prices, group_sets, providers, codes)]
    RAW --> REC
    REC --> PICK
    RAW --> PUB["publish each target network's rates: plan link, labels, one plan one contract"]
    LIST --> PUB
    TGT --> PUB
    NPPES[NPPES national CSV] --> GA[(GA providers)]
    GA --> PROBE
    PUF[Georgia Access plan files] --> XNET[(exchange networks, plans, premiums and cost sharing)]
    XNET -->|plan IDs| PICK
    XNET -->|plan to network| PUB
    PUBLIC[CMS, NUCC and Census datasets] --> REF[(reference: NUCC, MPFS, OPPS, CMS billing, CMS groups, MIPS, geocodes, hospitals, ZIPs)]
    PUB --> PAYER[("payer rates by Georgia network: rates, rate groups, rosters")]
  end
  subgraph integration ["integration: Python and DuckDB, all product decisions"]
    PAYER --> FC["integration/tables/"]
    REF --> FC
    GA --> FC
    XNET --> FC
    SVC[integration/services.yaml] --> FC
    FC --> FCT[("find-care tables: networks, plans, premiums, zips, services, cost_sharing, places, prices, providers")]
  end
  subgraph app [application]
    FCT --> API[FastAPI, one persistent DuckDB connection]
    API --> WEB[React: find-care screen]
  end
```

Everything ingestion hands integration (payer rates, GA providers, the exchange and reference datasets) is in `data/ingestion/`; Anthem's index, parse records and rates as filed stay in `data/raw/`.

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
the build, not enforced by a database. Full column notes: [integration/build.md](../integration/build.md#the-find-care-tables).

## Runtime

```mermaid
flowchart TB
  subgraph host [one machine today, one VM later]
    ING[ingestion container: Go + Python - make discover, parse, reference]
    INT[integration container: Python + DuckDB - make build]
    API[serving container: FastAPI + DuckDB]
    WEB[frontend container]
    RAWD[(data/raw: ingestion's own files)]
    CON[(data/ingestion: the contract)]
    SRV[(data/serving: the find-care tables)]
    ING -->|writes| RAWD
    ING -->|writes| CON
    CON -->|read-only| INT
    INT -->|writes| SRV
    SRV -->|read-only| API
    WEB --> API
  end
  TS[Tailscale serve] --> WEB
  TS --> API
```

Each container mounts only its own code folder and the data folders above, so integration can't read Anthem's raw files and the app can't read anything but its tables; `data-test/` is split the same way.
`make refresh` runs discover, reference, parse and geocode in the ingestion container, then the build in the integration container.
