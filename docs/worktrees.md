# Parallel development with git worktrees

*Read this to run more than one branch / session at once without the Docker
stack, ports, or the data store colliding. Design + rationale: [GH #59].*

[GH #59]: https://github.com/wmespi/honest-healthcare/issues/59

---

## The model

A normal checkout binds a **branch**, a **directory**, and the **Docker Compose
stack** keyed to that directory. `git worktree` unbinds the branch from the
directory — one `.git`, many directories, each on its own branch.

- The **canonical checkout** (the original clone) runs the one always-up stack on
  the default ports (5432 / 8000 / 5173), on `main`. To reach it from other
  devices, see [access.md](access.md).
- **Feature worktrees** (`../hh-<topic>`, branch `espinoza/<type>/<topic>`) develop
  in parallel. Their hermetic tests run on host toolchains with **no Docker**
  (`make test LOCAL=1`). Merge serially to `main` — trunk, no `develop` branch.
- A worktree that needs the app running uses the same `make start` — its gitignored
  `.env` (written by `dev-setup.sh`) gives it its own compose project name and ports.
- **Parsing / ETL work** is stateful (Postgres queue + writes `data/anthem/`) — run it
  under `TEST=1` isolation, or in a stack with its own `./data`.

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

The committed `.claude/settings.json` pre-approves the safe read-only commands so a
fresh checkout doesn't re-prompt; machine-specific entries stay in the gitignored
`.claude/settings.local.json`.

---

## Runbook

```bash
make worktree TOPIC=cms-benchmarks TYPE=feat
#   → ../hh-cms-benchmarks on espinoza/feat/cms-benchmarks (off origin/main)
#   → runs dev-setup.sh: .venv, node_modules, .env with a free port triplet
cd ../hh-cms-benchmarks

make test LOCAL=1       # gofmt · vet · build · go test · pytest · vitest — NO Docker
git commit ... && git push -u origin espinoza/feat/cms-benchmarks && gh pr create

make start              # only when you need the app running (own ports, from .env)

# after the PR merges:
cd ../honest-healthcare && make worktree-rm TOPIC=cms-benchmarks
```

A lingering worktree is ~300 MB (`.venv` 70 MB + `node_modules` 215 MB). The
shared `.git` and `~/.local/share/mise` are **not** per-worktree — don't touch them.

## Disk hygiene

`make footprint` — this worktree, every sibling worktree, the shared git store +
mise, Docker, any stray DuckDB spill, and host-volume free space. Run it when disk
looks low.

`make clean` — reclaim a worktree's regenerable artifacts (stray `.tmp/`,
`data-test/`, caches). `DOCKER=1` also prunes Docker build cache / dangling images /
unused volumes; `DATA_LOCAL=1` drops `data-local/`.

**Why this matters:** a DuckDB query that spills without `SET temp_directory`
writes to `<cwd>/.tmp/` — and in a container `<cwd>` is `/app`, the bind-mounted
checkout. One killed ad-hoc query left a **176 GB** `.tmp/` in the repo that sat
unnoticed for a month. `serving/data_sources.py:db()` sets the spill dir correctly;
**any ad-hoc `duckdb.connect()` must too** — or run through `db()`.

---

## Data

`data/` is gitignored — a fresh worktree has none. `dev-setup.sh` writes
`HH_DATA_ROOT=<canonical>/data` into `.env`, so the worktree's stack **reads** the
one shared Parquet store (not copied).

A worktree must **never write** the shared store: `make parse` / `make reference`
write `data/` — use `TEST=1` (`test.*` schema + `data-test/`), or a separate stack
with its own `./data`. To rebuild *serving tables* without touching the shared
store, set `SERVING_DIR=/app/data-local/serving` in the worktree's `.env`; the API
reads only `SERVING_DIR`.

---

## Gotchas

- **Same branch, two worktrees** — git refuses. One branch, one directory.
- **Manual `rm -rf` of a worktree** — run `git worktree prune` after.
- **`db/migrations/NNN_*.sql`** — two parallel branches both adding `003_…`
  conflict on rebase. Renumber the later one.
- **Gitignored ≠ hidden.** `.env`, `.venv/`, `node_modules/`, `data/` are normal
  files — git just won't track them, and repo-wide search skips them: open a
  gitignored file by path. Each worktree has its **own** `.env`.
- **RAM** — two full stacks + a heavy DuckDB job on an 8 GB box is tight. Keep
  `DUCKDB_MEMORY_LIMIT=2GB` on non-primary stacks; bring feature stacks down when idle.
