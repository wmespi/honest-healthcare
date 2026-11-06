# infra/ - where the app runs: images, ports, tester access

*Read this before touching a Dockerfile, a port, or sharing the app with someone.*

This folder is about where the app runs: the local stack below, and the cloud deploy for invited testers as [#17](https://github.com/wmespi/honest-healthcare/issues/17) lands.

Each layer's Dockerfile sits in its layer's folder and builds from that folder alone, so no image can hold another layer's code ([architecture.md](../docs/architecture.md#layers)).
Each has a dev target (the toolchains and test deps) and a prod target (the deployable artifact).
`docker-compose.yml` runs the dev targets and layers on the dev conveniences (the layer's own code bind-mounted, `--reload`, loopback ports, build caches).
CI builds all four prod images and smokes each (`images` job in `.github/workflows/ci.yml`); `docker images` gives their sizes.

| Dockerfile | Dev target | Prod target |
|---|---|---|
| `ingestion/Dockerfile` | `dev`: `python:3.14-slim` + the Go toolchain (tag matches `go.mod`'s `toolchain`); `ingestion/` mounted, `go run .` and `python3 -m` on demand | `prod`: static `CGO_ENABLED=0` `etl` binary beside the Python steps |
| `integration/Dockerfile` | `dev`: `python:3.14-slim` + pinned wheels + pytest; `integration/` mounted | `prod`: the build, run once (`python3 -m integration.build`) |
| `application/Dockerfile` | `serving-dev`: `python:3.14-slim` + pinned wheels + test deps; `application/serving/` mounted, `uvicorn --reload`. `frontend-dev`: `node:20-slim` vite dev server | `serving-prod`: `uvicorn`, no reload. `frontend-prod`: `nginx:alpine` serving the built `dist/` |

Each container mounts only the data folders its layer reads and writes, the layer before it read-only ([architecture.md](../docs/architecture.md#layers)), as in the `docker run` lines below.

```bash
docker build --target prod          -t hh-ingestion   ingestion
docker build --target prod          -t hh-integration integration
docker build --target serving-prod  -t hh-serving     application
docker build --target frontend-prod -t hh-frontend    application
docker run --rm -v "$PWD/data/raw:/app/data/raw" -v "$PWD/data/ingestion:/app/data/ingestion" hh-ingestion etl discover
docker run --rm -v "$PWD/data/ingestion:/app/data/ingestion:ro" -v "$PWD/data/serving:/app/data/serving" hh-integration
docker run -p 8000:8000 -v "$PWD/data/serving:/app/data/serving:ro" hh-serving
```

The frontend container keeps `node_modules` in a volume filled when its image was built,
so a dependency added to `application/frontend/package.json` isn't there until the image and the volume
are rebuilt: `docker compose up -d --build --renew-anon-volumes frontend`. Until then Vite
says it can't resolve the new package.

## Ports and multiple stacks

`serving` (8000) and `frontend` (5173) bind to **`${BIND_HOST:-127.0.0.1}`** —
loopback by default. `tailscale serve` (`scripts/tailscale-up.sh`) proxies
`127.0.0.1:<port>` onto the tailnet, so a `0.0.0.0` bind would clash with
`tailscaled`; set `BIND_HOST=0.0.0.0` only for a non-tailnet stack that needs LAN
access.

`container_name:` is unset, so `COMPOSE_PROJECT_NAME` (default: directory name)
scopes every container, network and volume. `API_PORT` / `WEB_PORT`,
`HH_DATA_ROOT` and `HH_SERVING_DIR` are env-driven: copy `.env.example` → `.env` in a worktree to run a
second stack beside the canonical one (`make worktree` does this).

## Sharing the app with a tester

There is no public site: the app runs on this machine and is reached over
[Tailscale](https://tailscale.com), a private network only invited people can join.
`scripts/tailscale-up.sh` sets up serving and prints the link.

1. **Invite them** — [admin console](https://login.tailscale.com/admin/users) →
   **Users** → **Invite external user** → their email.
2. **They install Tailscale** ([iOS](https://apps.apple.com/app/tailscale/id1470499037) ·
   [Android](https://play.google.com/store/apps/details?id=com.tailscale.ipn) ·
   [desktop](https://tailscale.com/download)) and sign in with the invited email.
   It only needs to be installed and signed in, not open.
3. **Send the link** `http://<machine>.<tailnet>.ts.net:5173` (`tailscale status` →
   `Self` → `DNSName`). The browser's "Not Secure" is expected: plain HTTP inside the
   tailnet.

If the link doesn't load: confirm they're signed into Tailscale, that this machine
is on with the stack up (`docker compose ps`), and that **Machines** in the admin
console lists their device. To revoke, remove them under **Users** or **Machines**.
