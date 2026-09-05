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
| **Canonical / tailnet** | the original clone | `tailnet` (local) | project `honest-healthcare`, always up, `tailscale serve` | 5432 / 8000 / 5173 |
| **Preview** *(ephemeral)* | `../hh-preview` | detached at any ref | project `hh-preview`, up only while reviewing | 5433 / 8010 / 5183 |
| **Feature** | `../hh-<topic>` | `espinoza/<type>/<topic>` | none by default | assigned by `dev-setup.sh` |

- The **canonical checkout** runs the one Tailscale-served stack. It sits on a
  local `tailnet` branch that moves **only** through `make promote` — never
  edited directly, never `git pull`ed. So a merge to `main` doesn't reach the
  people on your tailnet until you promote it.
- Merge model is **trunk + promotion**: `feature → PR → CI → main → (review) →
  promote`. The `tailnet` branch lags `main` by however many merged-but-unpromoted
  commits; `make tiers` shows the gap. No long-lived `develop` branch.
- To see a change before promoting it, `make preview REF=<branch>` brings up
  `../hh-preview` on the staging ports — ephemeral, so it costs RAM only while
  you're looking. `make preview-down` frees it.
- **Feature worktrees** develop in parallel. Their hermetic tests run on host
  toolchains with **no Docker**. Spin a stack only for a live check.
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

```bash
git config --global fetch.prune true   # drop local refs for branches deleted on origin
```

Keeps merged branches from piling up (the repo has `delete_branch_on_merge`, so
without this the *remote-tracking* refs linger). The committed `.claude/settings.json`
pre-approves the safe read-only commands (`make check`, `docker compose`, read-only
`git`/`gh`, `WebSearch`) so a fresh checkout doesn't re-prompt; machine-specific
entries stay in the gitignored `.claude/settings.local.json`.

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
make footprint          # paste the output into the PR body — REQUIRED
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

A lingering worktree is ~300 MB (`.venv` 70 MB + `node_modules` 215 MB). The
shared `.git` and `~/.local/share/mise` are **not** per-worktree — don't touch them.

## Disk hygiene

`make footprint` — the full picture: this worktree, every sibling worktree, the
shared git store + mise, Docker (images / build cache / volumes / the `Docker.raw`
VM image), any stray DuckDB spill, and host-volume free space. **Its output goes
in every PR body** (AGENTS.md) so a runaway is caught the moment it lands.

`make clean` — reclaim a worktree's regenerable artifacts: a stray `.tmp/`
DuckDB spill, `data-test/`, `.pytest_cache`, `__pycache__`. Flags:
`DOCKER=1` also runs `docker builder prune` + dangling-image + unused-volume
prune (safe — the running stack's `postgres_data` is in use, so it's skipped).
`DATA_LOCAL=1` also drops the `--rebuild-reference` cache.

**Why this matters:** a DuckDB query that spills without `SET temp_directory`
writes to `<cwd>/.tmp/` — and in a container `<cwd>` is `/app`, the
bind-mounted checkout. One killed ad-hoc query left a **176 GB** `.tmp/` in the
repo that sat unnoticed for a month. `serving/data_sources.py:db()` sets the
spill dir correctly; **any ad-hoc `duckdb.connect()` must too** — or run through
`db()`.

### The two tiers: `main` and the tailnet

There is **one** always-up stack (the canonical checkout, `tailscale serve`d).
The buffer between "merged to `main`" and "my family can see it" is *which commit
that stack runs* — pinned to the local `tailnet` branch, advanced only by
`make promote`.

```bash
cd ../honest-healthcare        # the canonical checkout — promote runs nowhere else

make tiers                     # tailnet sha + subject · origin/main · the unpromoted commits

make preview REF=espinoza/feat/x   # ../hh-preview stack at that ref, localhost:5183
make preview REF=main LAN=1         #   ...reachable at <this-host>.local:5183 from a laptop
make preview-down                  # stop it — frees the RAM, keeps the worktree for next time

make promote                   # prompt → checkout -B tailnet origin/main → rebuild → log + tag
make promote REF=tailnet-20260901-1000   # roll back to a previous promote
```

Every promote appends to `deploy/promote-log.md` (gitignored, per-machine) and
writes a `tailnet-<date>` tag; `git push origin <tag>` for a shared record.
There is **no** second always-on stack — the preview stack is ephemeral by
design, so two DuckDB processes never sit on RAM at once.

---

## Data

`data/` is gitignored — a fresh worktree has none. `dev-setup.sh` writes
`HH_DATA_ROOT=<canonical>/data` into `.env`, so the worktree's stack **reads** the
one shared Parquet store (9.5 GB, not copied).

A worktree must **never write** the shared store. If you're regenerating
reference / CMS / summary parquet:

```bash
scripts/dev-setup.sh --rebuild-reference
#   seeds ./data-local/{reference,cms,anthem/summary,serving} from the shared
#   store and appends the sub-store split to .env
```

The split (GH #59 Part C) is six env vars: `ANTHEM_DIR` / `NPPES_DIR` keep
reading the shared corpus; `REFERENCE_DIR` / `CMS_DIR` / `SUMMARY_DIR` /
`SERVING_DIR` point at `./data-local/…` (mounted at `/app/data-local` via the
repo bind). Both `serving/data_sources.py` and `reference/_common.py` fall back
to `DATA_DIR/<sub>` when a var is unset, so with no `.env` nothing changes. `make
code-labels` / `cms-utilization` / `build` in that worktree then write locally
while serving still reads `prices/`, `group_sets/`, `providers/` from the shared
store.

For work that mutates `data/anthem/` wholesale (parsing), use `TEST=1` (`test.*`
schema + `data-test/`) or a fully separate stack with its own `./data`.

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
