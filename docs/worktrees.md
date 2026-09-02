# Parallel development with git worktrees

*Read this to run more than one branch / session at once without the Docker
stack, ports, or the data store colliding. Design + rationale: [GH #59].*

[GH #59]: https://github.com/wmespi/honest-healthcare/issues/59

---

## The model

A normal checkout binds three things: a **branch**, a **directory**, and the
**Docker Compose stack** keyed to that directory. `git worktree` unbinds the
branch from the directory — one `.git`, many directories, each on its own branch,
sharing history and refs.

| Role | Directory | Branch | Stack | Ports |
|---|---|---|---|---|
| **Canonical / stable** | the original clone | `main` | project `honest-healthcare`, always up | 5432 / 8000 / 5173 |
| **Staging** *(optional)* | `../hh-staging` | `main` HEAD | project `hh-staging` | 5433 / 8010 / 5183 |
| **Feature** | `../hh-<topic>` | `espinoza/<type>/<topic>` | none by default | assigned by `dev-setup.sh` |

- The **canonical checkout** runs the stable stack that `tailscale serve` points
  at. It is only advanced by `make promote` (never edited directly).
- **Feature worktrees** develop in parallel. Their hermetic tests run on host
  toolchains with **no Docker**. Spin a stack only for a live check.
- Merge model is **trunk + promotion**: `feature → PR → CI → main`. "Stable" is
  just the commit the canonical stack was last built at — `main` may run ahead of
  it. No long-lived `develop` branch; a *staging stack* on `main` HEAD gives the
  integration view instead.
- **Parsing / ETL work** is stateful (Postgres queue + writes `data/anthem/`) —
  it always gets its own stack, or runs under `TEST=1` isolation.

---

## One-time host setup

The container-only quickstart in the [README](../README.md) needs none of this.
For parallel work you need pinned host toolchains that don't touch your system
installs:

```bash
brew install mise                 # one static binary; pins python/node/go per-repo
# add the shell hook once:  https://mise.jdx.dev/getting-started.html
```

`.mise.toml` pins python 3.10 / node 20 / go 1.24.13 to match the container
images. `scripts/dev-setup.sh` then creates a `.venv`, runs `npm ci`, and (in a
feature worktree) writes a `.env`.

---

## Runbook

### Start a workstream

```bash
make worktree TOPIC=cms-benchmarks TYPE=feat
#   → ../hh-cms-benchmarks on espinoza/feat/cms-benchmarks (off origin/main)
#   → runs dev-setup.sh: .venv, node_modules, .env with a free port triplet
cd ../hh-cms-benchmarks
```

### Develop

```bash
make check-local        # gofmt · vet · build · go test · pytest contract · vitest — NO Docker
git commit ...
git push -u origin espinoza/feat/cms-benchmarks
gh pr create
```

### Live check (only when you need the app running)

```bash
make stack-up           # this worktree's stack: own project name + ports from .env
curl localhost:$API_PORT/          # API_PORT is in .env
make stack-down
```

…or merge to `main` and look at the staging stack.

### Finish

```bash
# after the PR merges:
cd ../honest-healthcare
git worktree remove ../hh-cms-benchmarks      # or: make worktree-rm TOPIC=cms-benchmarks
```

### Promote to the Tailscale-visible app

```bash
cd ../honest-healthcare      # the canonical checkout
make promote                 # git checkout main && pull && docker compose up -d --build
```

---

## Data

`data/` is gitignored — a fresh worktree has none. `dev-setup.sh` writes
`HH_DATA_ROOT=<canonical>/data` into `.env`, so the worktree's stack **reads** the
one shared Parquet store (9.5 GB, not copied).

A worktree must **never write** the shared store. If you're regenerating
reference / CMS / summary parquet:

```bash
scripts/dev-setup.sh --rebuild-reference
#   seeds ./data-local/{reference,cms,anthem/summary} from the shared store and
#   writes a gitignored docker-compose.override.yml that shadows just those dirs
```

Everything else (`prices/`, `group_sets/`, `providers/`, `nppes/`) still comes
from the shared store. For work that mutates `data/anthem/` wholesale (parsing),
use `TEST=1` (`test.*` schema + `data-test/`) or a fully separate stack with its
own `./data`.

---

## Gotchas

- **Same branch, two worktrees** — git refuses. One branch, one directory.
- **Manual `rm -rf` of a worktree** — run `git worktree prune` after.
- **`db/migrations/NNN_*.sql`** — two parallel branches both adding `003_…`
  conflict on rebase. Renumber the later one, or prefix new migrations with a
  UTC timestamp.
- **One `tailscaled` per machine** — staging must use distinct ports
  (`TS_WEB_PORT` / `TS_API_PORT`), and its frontend needs `VITE_API_URL` set.
- **Go caches** (`~/go/pkg/mod`, `~/.cache/go-build`) are shared across worktrees
  and concurrency-safe — not a collision.
- **Gitignored ≠ hidden.** `.env`, `.venv/`, `node_modules/`, `data/` are normal
  files — editors and tools read/write them freely; git just won't track them.
  Each worktree has its **own** `.env`. One caveat: `.gitignore` also scopes
  *search* (ripgrep, editor project-search) which skips ignored paths — open a
  gitignored file by path, don't expect a repo-wide grep to surface it.
- **RAM** — two full stacks + a heavy DuckDB job on an 8 GB box is tight. Keep
  `DUCKDB_MEMORY_LIMIT=2GB` on non-primary stacks; bring feature stacks down when
  idle.
