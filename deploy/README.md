# deploy/ — images, ports, tester access

*Read this before touching a Dockerfile, a port, or sharing the app with someone.*

One Dockerfile per service. Each has a **`prod`** target (the deployable artifact)
and, where the dev loop needs a toolchain, a **`dev`** target. `docker-compose.yml`
runs the dev target and layers on the dev conveniences (bind-mounted source,
`--reload`, loopback ports, build caches). CI builds all three prod images and
smokes each (`images` job in `.github/workflows/ci.yml`); `docker images` gives
their sizes.

| File | `dev` target | `prod` target |
|---|---|---|
| `Dockerfile.etl` | Go toolchain (tag matches `go.mod`'s `toolchain`); source mounted, `go run .` on demand | static `CGO_ENABLED=0` binary on `alpine` (`ENTRYPOINT ["etl"]`) |
| `Dockerfile.serving` | same image — compose adds `--reload` + mount | `python:3.10-slim` + pinned wheels, `uvicorn` no reload |
| `Dockerfile.frontend` | `node:20-slim` vite dev server | `nginx:alpine` serving the built `dist/` |

```bash
docker build -f deploy/Dockerfile.etl      --target prod -t hh-etl .
docker build -f deploy/Dockerfile.serving                -t hh-serving .
docker build -f deploy/Dockerfile.frontend --target prod -t hh-frontend .
docker run --rm hh-etl parse -all-npis          # batch job
docker run -p 8000:8000 -v "$PWD/data:/app/data" hh-serving
```

A deployable `compose.prod.yml` (no bind mounts, resource limits) is
[#17](https://github.com/wmespi/honest-healthcare/issues/17); `.:/app` mount scoping
is [#21](https://github.com/wmespi/honest-healthcare/issues/21).

## Ports and multiple stacks

`serving` (8000) and `frontend` (5173) bind to **`${BIND_HOST:-127.0.0.1}`** —
loopback by default. `tailscale serve` (`scripts/tailscale-up.sh`) proxies
`127.0.0.1:<port>` onto the tailnet, so a `0.0.0.0` bind would clash with
`tailscaled`; set `BIND_HOST=0.0.0.0` only for a non-tailnet stack that needs LAN
access.

`container_name:` is unset, so `COMPOSE_PROJECT_NAME` (default: directory name)
scopes every container, network and volume. `DB_PORT` / `API_PORT` / `WEB_PORT` and
`HH_DATA_ROOT` are env-driven: copy `.env.example` → `.env` in a worktree to run a
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
