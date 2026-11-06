# Ingestion (ingestion/): the Go CLI that streams Anthem's index and rate files
# and NPPES (ingestion/etl/, one Go module), and the Python steps that build the
# public datasets and publish the payer rates (ingestion/reference/,
# ingestion/anthem_rates.py). Built from this folder alone, so no image can hold
# another layer's code. Two targets:
#   dev  - Python, the test deps and the Go toolchain; docker-compose bind-mounts
#          ingestion/ and runs `go run .` and `python3 -m ...` on demand (see the Makefile).
#   prod - the static etl binary beside the Python code; the scheduled-refresh artifact.
#
# `go.mod` pins `toolchain go1.24.13`; the Go image tags match so no toolchain
# is auto-downloaded.

FROM python:3.14-slim AS base
# The container is the isolation, so pip installing as root is intended.
ENV PIP_ROOT_USER_ACTION=ignore
WORKDIR /app
COPY requirements.txt ingestion/
RUN pip install --no-cache-dir -r ingestion/requirements.txt

# ── dev: toolchains, source mounted at runtime ─────────────────────────────
FROM base AS dev
COPY requirements-dev.txt ingestion/
RUN pip install --no-cache-dir -r ingestion/requirements-dev.txt
COPY --from=golang:1.24.13 /usr/local/go /usr/local/go
ENV GOPATH=/go PATH=/go/bin:/usr/local/go/bin:$PATH
WORKDIR /app/ingestion/etl
CMD ["tail", "-f", "/dev/null"]

# ── build: compile the static binary ──────────────────────────────────────
FROM golang:1.24.13-alpine AS build
WORKDIR /src
COPY etl/go.mod etl/go.sum ./
RUN go mod download
COPY etl/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/etl .

# ── prod: the binary and the Python steps ─────────────────────────────────
FROM base AS prod
COPY --from=build /out/etl /usr/local/bin/etl
COPY . ingestion/
# data/ is a runtime mount; the binary resolves its paths from the etl module dir
WORKDIR /app/ingestion/etl
CMD ["etl", "--help"]
