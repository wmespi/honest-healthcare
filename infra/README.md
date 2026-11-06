# infra/ - where the app runs: images, ports, tester access

*Read this before touching a Dockerfile, a port, or sharing the app with someone.*

This folder is about where the app runs: the local stack below, and the cloud deploy for invited testers as [#17](https://github.com/wmespi/honest-healthcare/issues/17) lands.

Each layer's Dockerfile sits in its layer's folder and builds from that folder alone, so no image can hold another layer's code ([architecture.md](../docs/architecture.md#layers)).
Each has a dev target (the toolchains and test deps) and a prod target (the deployable artifact).
`docker-compose.yml` runs the dev targets and layers on the dev conveniences (the layer's own code bind-mounted, `--reload`, loopback ports, build caches).
CI builds all three prod images and smokes each (`images` job in `.github/workflows/ci.yml`); `docker images` gives their sizes.

| Dockerfile | Dev target | Prod target |
|---|---|---|
| `ingestion/Dockerfile` | `dev`: `python:3.14-slim` + the Go toolchain (tag matches `go.mod`'s `toolchain`); `ingestion/` mounted, `go run .` and `python3 -m` on demand | `prod`: static `CGO_ENABLED=0` `etl` binary beside the Python steps |
| `integration/Dockerfile` | `dev`: `python:3.14-slim` + pinned wheels + pytest; `integration/` mounted | `prod`: the build, run once (`python3 -m integration.build`) |
| `application/Dockerfile` | `dev`: `python:3.14-slim` + Node + pinned wheels + test deps; `application/` mounted, each start builds the screen, then runs `uvicorn --reload` | `prod`: the API, the built screen and the find-care tables, run by a user that can't change them |

The application is one process at one origin: the API under `/api`, and the built screen at every other path ([serving.md](../application/serving/serving.md)).
Its prod image holds the find-care tables it serves, so the build is given them, and it stops on a missing or incomplete set.
Each other container mounts only the data folders its layer reads and writes, the layer before it read-only ([architecture.md](../docs/architecture.md#layers)), as in the `docker run` lines below.

```bash
docker build --target prod -t hh-ingestion   ingestion
docker build --target prod -t hh-integration integration
docker build --target prod --build-context tables=data/serving -t hh-application application
docker run --rm -v "$PWD/data/raw:/app/data/raw" -v "$PWD/data/ingestion:/app/data/ingestion" hh-ingestion etl discover
docker run --rm -v "$PWD/data/ingestion:/app/data/ingestion:ro" -v "$PWD/data/serving:/app/data/serving" hh-integration
docker run -p 8000:8000 -e INVITES="<entries>" hh-application
python3 infra/smoke.py http://localhost:8000 --invite <token>
```

`infra/smoke.py` checks a running application image: it turns away a request without an invite, the screen loads, its assets are cached, and the API answers a search.

## The invite gate

The app answers only invited testers (`application/serving/gate.py`), because the deployed service is open to the internet.
Each tester's link is any screen address with `?invite=<token>`.
The first visit keeps the token in a cookie and sends the browser on without it, and every request after needs that cookie, the API's included.
A request without one gets a 401: a page that says the app is open to invited testers, or a `Problem` under `/api`.

- **Invite someone:** `make invite NAME=<label>` prints an `INVITES` entry and the link to send.
  Add the entry to the app's `INVITES`, comma-separated.
  The token is kept nowhere else, so a lost link means a new invite.
- **Shut someone out:** remove their entry from `INVITES`.
  The app holds only the tokens' hashes, so their cookie stops working at once.
- **Off for the dev stack:** compose sets `GATE=off`, because the stack is on loopback and its testers come over the tailnet.
  Anywhere else the gate is on, and with no `INVITES` it lets nobody in and says so in its log.

The token is in the first request's address, so it shows in the request log, which only the app's owner can read.

## Working on the screen

The dev container builds the screen when it starts, so a screen edit shows after `docker compose restart application`.
To see each edit as it's saved, run Vite on the host instead: `npm run dev` in `application/frontend/` (after `scripts/dev-setup.sh`) serves the screen at `localhost:5173` and sends `/api` to this checkout's running app, on its `.env`'s `APP_PORT`.

The container keeps `node_modules` in a volume filled when its image was built, so a dependency added to `application/frontend/package.json` isn't there until the image and the volume are rebuilt: `docker compose up -d --build --renew-anon-volumes application`.
Until then the screen's build says it can't resolve the new package.

## Ports and multiple stacks

The app binds to **`${BIND_HOST:-127.0.0.1}:${APP_PORT:-8000}`**, loopback by default.
`tailscale serve` (`scripts/tailscale-up.sh`) proxies `127.0.0.1:<port>` onto the tailnet, so a `0.0.0.0` bind would clash with `tailscaled`; set `BIND_HOST=0.0.0.0` only for a non-tailnet stack that needs LAN access.

`container_name:` is unset, so `COMPOSE_PROJECT_NAME` (default: directory name) scopes every container, network and volume.
`APP_PORT`, `HH_DATA_ROOT` and `HH_SERVING_DIR` are env-driven: copy `.env.example` → `.env` in a worktree to run a second stack beside the canonical one (`make worktree` does this).

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
3. **Send the link** `http://<machine>.<tailnet>.ts.net:8000` (`tailscale status` →
   `Self` → `DNSName`). The browser's "Not Secure" is expected: plain HTTP inside the
   tailnet.

If the link doesn't load: confirm they're signed into Tailscale, that this machine
is on with the stack up (`docker compose ps`), and that **Machines** in the admin
console lists their device. To revoke, remove them under **Users** or **Machines**.
