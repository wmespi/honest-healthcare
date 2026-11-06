# The application (application/): the find-care API (serving/, FastAPI + DuckDB
# over data/serving/) and the screen (frontend/, React + Vite). Built from this
# folder alone, so no image can hold another layer's code. Four targets:
#   serving-dev   - the API's deps and test deps; docker-compose bind-mounts
#                   serving/ and runs uvicorn with --reload.
#   serving-prod  - the API, served by uvicorn without reload, single worker.
#   frontend-dev  - the vite dev server; docker-compose bind-mounts frontend/.
#   frontend-prod - nginx serving the built dist/.
# The API's dependencies are pure-Python wheels (fastapi, uvicorn, duckdb,
# pydantic), so no build toolchain is needed.

FROM python:3.14-slim AS serving-base
# The container is the isolation, so pip installing as root is intended.
ENV PIP_ROOT_USER_ACTION=ignore
WORKDIR /app
COPY serving/requirements.txt application/serving/
RUN pip install --no-cache-dir -r application/serving/requirements.txt
EXPOSE 8000

# ── serving-dev: source mounted at runtime ────────────────────────────────
FROM serving-base AS serving-dev
COPY serving/requirements-dev.txt application/serving/
RUN pip install --no-cache-dir -r application/serving/requirements-dev.txt
CMD ["uvicorn", "application.serving.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── serving-prod: the code; data/serving/ is a runtime mount ──────────────
FROM serving-base AS serving-prod
COPY __init__.py application/
COPY serving/ application/serving/
CMD ["uvicorn", "application.serving.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── frontend-dev: the vite dev server, source mounted at runtime ──────────
FROM node:20-slim AS frontend-dev
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ── frontend-build: the production bundle ─────────────────────────────────
FROM frontend-dev AS frontend-build
COPY frontend/ ./
RUN npm run build

# ── frontend-prod: nginx serving the bundle ───────────────────────────────
FROM nginx:alpine AS frontend-prod
COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
