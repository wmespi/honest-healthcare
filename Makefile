# Honest Healthcare — convenience wrappers around docker compose
# Always use `exec` (not `run --rm`) so logs appear in Docker Desktop.
#
# Usage: make <target>
#        make help   ← list all available targets
#
# One name per workflow: the make target, the etl-go subcommand, and the
# helper doc next to the code all share it. Test isolation and single-item
# selection are variables, not separate targets:
#
#   make discover TEST=1        # run in the test schema + data-test/
#   make discover SCHEMA=1      # stream the index, write index_schema.json only
#   make parse ID=10065         # parse one file by index_files.id
#   make parse GA=1             # GA / individual-market files first
#   make parse TEST=1           # parse in test isolation
#   make db-reset WHAT=failed   # reset transiently-failed rows → pending
#   make sh S=backend           # shell into a container

.PHONY: help \
        start up down logs \
        discover parse size fixture \
        nppes code-labels taxonomy-labels \
        fmt lint test test-e2e test-api test-web check test-all \
        cov-probe cov-report smoke-web \
        psql migrate db-reset \
        sh \
        _require-etl-running

## ── Help ─────────────────────────────────────────────────────────────────────

help: ## Show all available make targets
	@echo ""
	@echo "Usage: make <target>   (see the Makefile header for TEST=1 / ID= / etc.)"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
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

## ── Pipeline: discover → parse ───────────────────────────────────────────────

discover: ## Phase 1 — sync the Anthem master index into index_files. TEST=1 · SCHEMA=1 (index_schema.json only, no DB)
	docker compose exec etl_go go run . -discover \
	  $(if $(filter 1,$(SCHEMA)),-index-schema,) \
	  $(if $(filter 1,$(TEST)),-test,) \
	  $(if $(LIMIT),-limit $(LIMIT),) \
	  $(if $(filter 1,$(NO_CACHE)),-no-cache,) \
	  $(if $(INDEX_URL),-index-url "$(INDEX_URL)",)

parse: ## Phase 2 — stream pending files into Parquet. ID=<index_files.id> · GA=1 (priority) · TEST=1 · LIMIT=n
	docker compose exec etl_go go run . -parse \
	  $(if $(ID),-file-ids $(ID),) \
	  $(if $(filter 1,$(GA)),-priority,) \
	  $(if $(filter 1,$(TEST)),-test,) \
	  $(if $(LIMIT),-limit $(LIMIT),) \
	  $(if $(FIXTURE),-fixture "$(FIXTURE)",)

size: ## Backfill index_files.file_size_bytes via concurrent HEAD requests
	docker compose exec etl_go go run . -size

fixture: ## Build a truncated *.json.gz fixture from a file id — usage: make fixture ID=5043 NAME=ga_small
	docker compose exec etl_go go run . -make-fixture -file-ids $(ID) $(if $(NAME),-fixture-name $(NAME),)

## ── Reference data ──────────────────────────────────────────────────────────

nppes: _require-etl-running ## Download the NPPES national file, write data/nppes/ga_providers.parquet (GA subset). URL= / FILE= to override.
	docker compose exec etl_go go run . -nppes $(if $(URL),-nppes-url "$(URL)",) $(if $(FILE),-nppes-file "$(FILE)",)

code-labels: ## Build data/reference/code_labels.parquet (RBCS categories + synonyms for every parsed code)
	docker compose exec -T backend python3 /app/scripts/build_code_labels.py --data-dir /app/data $(if $(RBCS_URL),--rbcs-url "$(RBCS_URL)",)

taxonomy-labels: ## Build data/reference/nucc_taxonomy.parquet (NUCC specialty labels for provider taxonomy codes)
	docker compose exec -T backend python3 /app/scripts/build_taxonomy_labels.py --data-dir /app/data $(if $(NUCC_URL),--nucc-url "$(NUCC_URL)",)

## ── Quality gate ────────────────────────────────────────────────────────────

fmt: _require-etl-running ## Format all Go source (gofmt -w)
	docker compose exec etl_go gofmt -w .

lint: _require-etl-running ## Static checks — go vet + go build (non-mutating)
	docker compose exec etl_go go vet ./...
	docker compose exec etl_go go build ./...

test: _require-etl-running ## Go unit tests (hermetic — fixture-driven, no network/DB)
	docker compose exec etl_go go test ./...

test-e2e: _require-etl-running ## Hermetic end-to-end: parse + NPPES fixtures in test isolation, with teardown
	bash scripts/etl_e2e_test.sh
	bash scripts/nppes_test.sh

test-api: ## Backend contract + coverage tests (pytest, against the running API)
	docker compose exec -T backend sh -c "pip install -q pytest httpx && cd /app/backend && python -m pytest tests/ -q"

test-web: ## Rate-explorer component tests (vitest + Testing Library, hermetic — mocks the API)
	docker compose exec -T frontend sh -c "cd /app && npx vitest run"

check: fmt lint test ## Pre-commit gate — fmt + vet + build + Go unit tests

test-all: check test-e2e test-api test-web ## Full sweep — gate + e2e + backend + frontend (stack must be up)

## ── Coverage feedback loop ──────────────────────────────────────────────────

cov-probe: ## Coverage scorecard for the target plan — usage: make cov-probe LABEL=before
	python3 scripts/coverage_probe.py --label $(or $(LABEL),probe)

cov-report: ## Aggregate coverage_log — what each parsed file contributed. SCHEMA=test to read test.*
	python3 scripts/coverage_report.py --schema $(or $(SCHEMA),public)

smoke-web: ## Exercise the rate-explorer's API routes for the target plan across a procedure basket
	python3 scripts/frontend_smoke.py

## ── Database ─────────────────────────────────────────────────────────────────

psql: ## Open a psql shell on honest_healthcare
	docker compose exec db psql -U postgres -d honest_healthcare

migrate: ## Apply db/migrations/*.sql to the running database (idempotent)
	@for f in db/migrations/*.sql; do \
	  echo "→ $$f"; \
	  docker compose exec -T db psql -U postgres -d honest_healthcare -v ON_ERROR_STOP=1 < "$$f" || exit 1; \
	done

db-reset: ## Reset index_files rows → pending. WHAT=processing (stale) | failed (transient only)
	@case "$(WHAT)" in \
	  processing) \
	    docker compose exec db psql -U postgres -d honest_healthcare \
	      -c "UPDATE index_files SET status = 'pending' WHERE status = 'processing';" ;; \
	  failed) \
	    docker compose exec db psql -U postgres -d honest_healthcare \
	      -c "UPDATE index_files SET status = 'pending', failure_reason = NULL \
	          WHERE status = 'failed' \
	            AND (failure_reason IS NULL \
	              OR failure_reason NOT SIMILAR TO '%(gzip|unexpected EOF|invalid header|HTTP 4%)%');" ;; \
	  *) echo "usage: make db-reset WHAT=processing|failed" && exit 1 ;; \
	esac

## ── Shells ────────────────────────────────────────────────────────────────────

sh: ## Open a shell inside a container — usage: make sh S=etl|backend|frontend|db
	@case "$(S)" in \
	  etl)      svc=etl_go ;; \
	  backend)  svc=backend ;; \
	  frontend) svc=frontend ;; \
	  db)       svc=db ;; \
	  *) echo "usage: make sh S=etl|backend|frontend|db" && exit 1 ;; \
	esac; \
	echo "Entering $$svc shell — type 'exit' to quit"; \
	docker compose exec $$svc sh

## ── Internal ─────────────────────────────────────────────────────────────────

_require-etl-running:
	@docker compose ps etl_go | grep -q "Up" || \
	  (echo "Error: etl_go is not running — run 'make up' first" && exit 1)
