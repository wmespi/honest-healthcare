# Honest Healthcare — convenience wrappers around docker compose
# Always use `exec` (not `run --rm`) so logs appear in Docker Desktop.
#
# Usage: make <target>
#        make help   ← list all available targets

.PHONY: help \
        start up down logs \
        etl-discover etl-discover-test etl-index-schema \
        etl-parse etl-parse-test etl-parse-file etl-size \
        etl-fmt etl-vet etl-build etl-unit etl-check etl-test etl-fixture \
        nppes nppes-test \
        db-psql db-migrate db-reset-processing db-reset-failed \
        backend-test coverage-probe coverage-report \
        sh-etl sh-backend \
        check \
        _require-etl-running

## ── Help ─────────────────────────────────────────────────────────────────────

help: ## Show all available make targets
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'
	@echo ""

## ── Infrastructure ───────────────────────────────────────────────────────────

start: ## Launch Docker Desktop (if needed) then start all containers detached
	@docker info > /dev/null 2>&1 || (echo "Starting Docker Desktop..." && open -a Docker && \
	  until docker info > /dev/null 2>&1; do sleep 1; done && echo "Docker ready.")
	docker compose up -d

up: ## Start all containers (attached — shows live logs)
	docker compose up

down: ## Stop all containers
	docker compose down

logs: ## Follow logs from all services
	@bash -c 'trap "echo \"\nLogs stopped — run make logs to resume\"" EXIT; docker compose logs -f'

## ── ETL: Discovery ───────────────────────────────────────────────────────────

etl-discover: ## Phase 1 — populate index_files from the Anthem master index
	docker compose exec etl_go go run . -discover

etl-discover-test: ## Phase 1 in test isolation (test schema + data-test/)
	docker compose exec etl_go go run . -discover -test

etl-index-schema: ## Stream master index, write data/anthem/index_schema.json (no DB writes)
	docker compose exec etl_go go run . -index-schema

## ── ETL: Parsing ─────────────────────────────────────────────────────────────

etl-parse: ## Phase 2 — stream pending files into Parquet
	docker compose exec etl_go go run . -parse

etl-parse-test: ## Phase 2 in test isolation (test schema + data-test/)
	docker compose exec etl_go go run . -parse -test

etl-parse-file: ## Parse a single file by ID — usage: make etl-parse-file ID=10065
	docker compose exec etl_go go run . -parse -file-ids $(ID)

etl-size: ## Backfill file_size_bytes via concurrent HEAD requests
	docker compose exec etl_go go run . -size

## ── ETL: Quality ─────────────────────────────────────────────────────────────

etl-fmt: _require-etl-running ## Format all Go source with gofmt
	docker compose exec etl_go gofmt -w .

etl-vet: _require-etl-running ## Run go vet static analysis on etl-go
	docker compose exec etl_go go vet ./...

etl-build: _require-etl-running ## Verify etl-go compiles cleanly
	docker compose exec etl_go go build ./...

etl-unit: _require-etl-running ## Run Go unit tests (hermetic — fixture-driven, no network/DB)
	docker compose exec etl_go go test ./...

etl-check: _require-etl-running etl-fmt etl-vet etl-build etl-unit ## Run all ETL static checks (fmt + vet + build + unit)

etl-test: _require-etl-running ## Hermetic e2e: parse a committed fixture in test isolation, with teardown
	bash scripts/etl_e2e_test.sh

etl-fixture: _require-etl-running ## Build a fixture from a file id — usage: make etl-fixture ID=5043 NAME=ga_small
	docker compose exec etl_go go run . -make-fixture -file-ids $(ID) $(if $(NAME),-fixture-name $(NAME),)

## ── NPPES (Georgia provider subset) ──────────────────────────────────────────

nppes: _require-etl-running ## Download NPPES national file, write data/nppes/ga_providers.parquet (GA subset). URL= to override.
	docker compose exec etl_go go run . -nppes $(if $(URL),-nppes-url "$(URL)",) $(if $(FILE),-nppes-file "$(FILE)",)

nppes-test: _require-etl-running ## Hermetic NPPES test: extract GA rows from the committed CSV fixture, with teardown
	bash scripts/nppes_test.sh

## ── Backend ──────────────────────────────────────────────────────────────────

backend-test: ## Backend contract + coverage tests (pytest, against the running API)
	docker compose exec -T backend sh -c "pip install -q pytest httpx && cd /app/backend && python -m pytest tests/ -q"

coverage-probe: ## Run the coverage scorecard — usage: make coverage-probe LABEL=before
	python3 scripts/coverage_probe.py --label $(or $(LABEL),probe)

coverage-report: ## Aggregate coverage_log — what we've ingested so far, per file
	python3 scripts/coverage_report.py --schema $(or $(SCHEMA),public)

## ── Database ─────────────────────────────────────────────────────────────────

db-psql: ## Open a psql shell on honest_healthcare
	docker compose exec db psql -U postgres -d honest_healthcare

db-migrate: ## Apply db/migrations/*.sql to the running database (idempotent)
	@for f in db/migrations/*.sql; do \
	  echo "→ $$f"; \
	  docker compose exec -T db psql -U postgres -d honest_healthcare -v ON_ERROR_STOP=1 < "$$f" || exit 1; \
	done

db-reset-processing: ## Reset stale 'processing' rows → 'pending'
	docker compose exec db psql -U postgres -d honest_healthcare \
	  -c "UPDATE index_files SET status = 'pending' WHERE status = 'processing';"

db-reset-failed: ## Reset transiently-failed rows → 'pending' (keeps bad-gzip/EOF/HTTP 4xx failures failed)
	docker compose exec db psql -U postgres -d honest_healthcare \
	  -c "UPDATE index_files SET status = 'pending', failure_reason = NULL \
	      WHERE status = 'failed' \
	        AND (failure_reason IS NULL \
	          OR failure_reason NOT SIMILAR TO '%(gzip|unexpected EOF|invalid header|HTTP 4%)%');"

## ── Shells ────────────────────────────────────────────────────────────────────

sh-etl: ## Open a shell inside the etl_go container
	@echo "Entering etl_go shell — type 'exit' to quit"
	docker compose exec etl_go sh

sh-backend: ## Open a shell inside the backend container
	@echo "Entering backend shell — type 'exit' to quit"
	docker compose exec backend sh

## ── Top-level gate ───────────────────────────────────────────────────────────

check: etl-check ## Run all pre-commit checks (expands as backend/frontend checks are added)

## ── Internal ─────────────────────────────────────────────────────────────────

_require-etl-running:
	@docker compose ps etl_go | grep -q "Up" || \
	  (echo "Error: etl_go is not running — run 'make up' first" && exit 1)
