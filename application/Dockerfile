# The application (application/): the find-care API (serving/, FastAPI + DuckDB
# over data/serving/) and the screen (frontend/, React + Vite), from one
# process at one origin: the API under /api and the built screen at every
# other path (serving/screen.py). Built from this folder alone, so no image can
# hold another layer's code. Two targets:
#   dev  - Python and Node, the API's test deps and the screen's node_modules;
#          docker-compose bind-mounts application/, and each start builds the
#          screen, then runs the API with --reload.
#   prod - the API, the built screen and the find-care tables, which the build
#          passes in: --build-context tables=<a data/serving folder>.
# The API's dependencies are pure-Python wheels (fastapi, uvicorn, duckdb,
# pydantic), so no build toolchain is needed.

FROM python:3.14-slim AS base
# The container is the isolation, so pip installing as root is intended.
ENV PIP_ROOT_USER_ACTION=ignore
WORKDIR /app
COPY serving/requirements.txt application/serving/
RUN pip install --no-cache-dir -r application/serving/requirements.txt
EXPOSE 8000

FROM node:20-slim AS node

# ── screen: the production bundle ─────────────────────────────────────────
FROM node AS screen
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── dev: Node beside Python, source mounted at runtime ────────────────────
FROM base AS dev
COPY --from=node /usr/local/bin/node /usr/local/bin/
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY serving/requirements-dev.txt application/serving/
RUN pip install --no-cache-dir -r application/serving/requirements-dev.txt
COPY frontend/package.json frontend/package-lock.json application/frontend/
RUN cd application/frontend && npm ci
# A screen that won't build leaves the API up, and / says the screen isn't built.
CMD npm --prefix application/frontend run build || echo "the screen didn't build: see above"; \
    exec uvicorn application.serving.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir application/serving

# ── tables: the find-care tables, passed in with --build-context ──────────
# A build that passes them replaces this stage; one that doesn't stops here.
FROM base AS tables
RUN echo "pass the find-care tables: docker build --build-context tables=<a data/serving folder> ..." >&2; exit 1

# ── prod: the API, the screen and the tables, as a user that can't change them ──
FROM base AS prod
COPY __init__.py application/
COPY serving/ application/serving/
COPY --from=screen /app/dist application/frontend/dist/
COPY --from=tables / data/serving/
# The image stops building on an incomplete set of tables. Its code is compiled
# here, since the app's user can't write bytecode and a cold start shouldn't.
RUN python3 -c "import sys; from application.serving.data_sources import missing_build as m; \
sys.exit(f'the find-care tables are missing {m()}') if m() else None" \
 && python3 -m compileall -q application
RUN useradd --system --no-create-home app
USER app
CMD ["uvicorn", "application.serving.main:app", "--host", "0.0.0.0", "--port", "8000"]
