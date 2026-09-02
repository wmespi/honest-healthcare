# deploy/ — container images

One Dockerfile per service. Each has a **`prod`** target (a minimal deployable
artifact) and, where the dev loop needs a toolchain, a **`dev`** target.
`docker-compose.yml` selects the dev target and layers on the dev conveniences
(bind-mounted source, `--reload`, loopback ports, persistent build caches).

| File | `dev` target | `prod` target | prod size |
|---|---|---|---|
| `Dockerfile.etl` | `golang:1.24.13-alpine` toolchain; source mounted, `go run .` on demand | static `CGO_ENABLED=0` binary on `alpine` (`ENTRYPOINT ["etl"]`) | ~40 MB |
| `Dockerfile.serving` | *(same image)* — compose adds `--reload` + mount | `python:3.10-slim` + pinned wheels, `uvicorn` no reload | ~340 MB |
| `Dockerfile.frontend` | `node:20-slim` vite dev server | `nginx:alpine` serving the built `dist/` | ~95 MB |

The base image tag for `etl` matches `go.mod`'s `toolchain` line so no toolchain
is auto-downloaded at build.

## Build / run the prod images

```bash
docker build -f deploy/Dockerfile.etl      --target prod -t hh-etl .
docker build -f deploy/Dockerfile.serving                -t hh-serving .
docker build -f deploy/Dockerfile.frontend --target prod -t hh-frontend .

docker run --rm hh-etl parse -all-npis          # batch job
docker run -p 8000:8000 -v "$PWD/data:/app/data" hh-serving
```

CI builds all three and smoke-tests them (`.github/workflows/ci.yml`, `images` job).

## Not here yet

A deployable `compose.prod.yml` (no bind mounts, prod commands, resource limits)
and the base/override compose split land with
[#17](https://github.com/wmespi/honest-healthcare/issues/17). `.:/app` mount
scoping is a follow-up on [#21](https://github.com/wmespi/honest-healthcare/issues/21).

## Ports

`docker-compose.yml` binds `serving` (8000) and `frontend` (5173) to
**`127.0.0.1` only**. Remote access is via `tailscale serve`
(`scripts/tailscale-up.sh`), which proxies `127.0.0.1:<port>` onto the tailnet —
so loopback binding is the intended path, and it avoids a `0.0.0.0` clash with
`tailscaled`. For plain-LAN access, change the mappings back to `"8000:8000"`.

## Multiple stacks (GH #59)

`container_name:` is unset, so the **project name**
(`COMPOSE_PROJECT_NAME`, default = directory name) scopes every container,
network and named volume. The host ports and the data root are env-driven —
`DB_PORT` / `API_PORT` / `WEB_PORT` (defaults `5432` / `8000` / `5173`) and
`HH_DATA_ROOT` (default `./data`). Copy `.env.example` → `.env` in a worktree to
run a second stack alongside the canonical one; with no `.env` the behaviour is
unchanged. Runbook: [../docs/worktrees.md](../docs/worktrees.md).
