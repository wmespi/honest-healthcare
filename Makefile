# Honest Healthcare — convenience wrappers around docker compose
# Always use `exec` (not `run --rm`) so logs appear in Docker Desktop.
#
# Usage: make <target>
#        make help   ← the only list of targets; it lives nowhere else
#
# One name per workflow: the make target, the etl subcommand, and the
# helper doc next to the code all share it. Test isolation and
# single-item selection are variables, not separate targets:
#
#   make discover TEST=1        # run in the test schema + data-test/
#   make discover SCHEMA=1      # stream the index, write index_schema.json only
#   make parse ID=10065         # parse one file by index_files.id
#   make parse TARGETS=<path>   # a different target-plan list (default etl/targets.yaml)
#   make reference STEP=mpfs    # run one reference builder (ARGS="--year 2025" passes through)
#   make reference FORCE=1      # rebuild every reference output even if it exists
#   make check LOCAL=1          # the same gate on host toolchains, no Docker
#   make test  LOCAL=1          # ... incl. pytest + vitest; the worktree gate
#   make db-reset WHAT=failed   # reset transiently-failed rows → pending
#   make sh S=serving           # shell into a container

.PHONY: help \
        start up down logs \
        worktree worktree-rm \
        discover parse reference build refresh \
        check test test-live \
        footprint clean \
        fixture seed \
        psql migrate db-reset db-snapshot db-restore \
        sh \
        _require-etl-running

## ── Help ─────────────────────────────────────────────────────────────────────

help: ## Show all available make targets
	@echo ""
	@echo "Usage: make <target>   (variables like TEST=1 / ID= / LOCAL=1 are in the Makefile header)"
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

worktree: ## New sibling worktree + branch off origin/main, fully set up. TOPIC=<name> [TYPE=feat]
	@test -n "$(TOPIC)" || { echo "usage: make worktree TOPIC=<name> [TYPE=feat]"; exit 1; }
	bash scripts/worktree-new.sh "$(TOPIC)" "$(or $(TYPE),feat)"

worktree-rm: ## Remove a sibling worktree (refuses if dirty; FORCE=1 to override). TOPIC=<name>
	@test -n "$(TOPIC)" || { echo "usage: make worktree-rm TOPIC=<name>"; exit 1; }
	git worktree remove $(if $(filter 1,$(FORCE)),--force,) "$$(dirname "$$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -1)")/hh-$(TOPIC)"

## ── Pipeline: discover → parse → reference → build ───────────────────────────

discover: ## Phase 1 — sync the Anthem index into index_files + index_file_plans, then backfill file sizes. TEST=1 · SCHEMA=1 (index_schema.json only)
	docker compose exec etl go run . discover \
	  $(if $(filter 1,$(SCHEMA)),-index-schema,) \
	  $(if $(filter 1,$(TEST)),-test,) \
	  $(if $(LIMIT),-limit $(LIMIT),) \
	  $(if $(filter 1,$(NO_CACHE)),-no-cache,) \
	  $(if $(INDEX_URL),-index-url "$(INDEX_URL)",)
	$(if $(or $(filter 1,$(SCHEMA)),$(filter 1,$(TEST))),@:,docker compose exec etl go run . size)

parse: ## Phase 2 — stream pending files that serve a target plan into Parquet. ID=<index_files.id> · TARGETS=<path> · TEST=1 · LIMIT=n
	docker compose exec etl go run . parse \
	  $(if $(ID),-file-ids $(ID),) \
	  $(if $(TARGETS),-targets "$(TARGETS)",) \
	  $(if $(filter 1,$(TEST)),-test,) \
	  $(if $(LIMIT),-limit $(LIMIT),) \
	  $(if $(FIXTURE),-fixture "$(FIXTURE)",)

reference: ## Phase 3 — build every reference dataset (NPPES, labels, CMS, MPFS, geocode), skipping what exists. STEP=<name> · FORCE=1 · ARGS="--flag v"
	@bash scripts/reference.sh

build: ## Phase 4 — raw + reference parquet -> data/serving/ tables. NET=<slug,slug> for a subset. TEST=1
	docker compose exec -T -w /app -e DUCKDB_TMP= serving python3 -m build.build --data-dir /app/data $(if $(NET),--networks "$(NET)",) $(if $(filter 1,$(TEST)),--test,)

refresh: ## The monthly job — discover → reference (what's missing; NPPES gates parse) → parse → code-labels (needs parsed codes) → build
	$(MAKE) discover
	$(MAKE) reference
	$(MAKE) parse
	$(MAKE) reference STEP=code-labels
	$(MAKE) build

## ── Quality gates ────────────────────────────────────────────────────────────

check: ## Gate — docs drift + Go fmt/vet/build/unit tests (etl container). LOCAL=1: host toolchains, no Docker
	@python3 scripts/check_docs.py
	@if [ "$(LOCAL)" = 1 ]; then \
	  command -v go >/dev/null || { echo "no host 'go' — run scripts/dev-setup.sh"; exit 1; }; \
	  out=$$(gofmt -l etl); [ -z "$$out" ] || { printf 'gofmt needed:\n%s\n' "$$out"; exit 1; }; \
	  cd etl && go vet ./... && go build ./... && go test ./...; \
	else \
	  $(MAKE) --no-print-directory _require-etl-running && \
	  docker compose exec etl gofmt -w . && \
	  docker compose exec etl go vet ./... && \
	  docker compose exec etl go build ./... && \
	  docker compose exec etl go test ./...; \
	fi

test: check ## Every hermetic suite — check + serving pytest + vitest (+ ETL e2e when not LOCAL). LOCAL=1: host toolchains, no Docker
	@if [ "$(LOCAL)" = 1 ]; then \
	  test -x .venv/bin/python || { echo "no .venv — run scripts/dev-setup.sh"; exit 1; }; \
	  test -d frontend/node_modules || { echo "no frontend/node_modules — run scripts/dev-setup.sh"; exit 1; }; \
	  .venv/bin/python -m pytest serving/tests --ignore=serving/tests/test_golden.py -q && \
	  cd frontend && npx vitest run; \
	else \
	  bash scripts/etl_e2e_test.sh && bash scripts/nppes_test.sh && \
	  docker compose exec -T serving sh -c "pip install -q -r /app/serving/requirements-dev.txt && cd /app/serving && python -m pytest tests/ --ignore=tests/test_golden.py -q" && \
	  docker compose exec -T frontend sh -c "cd /app && npx vitest run"; \
	fi

test-live: ## Golden answers + user journeys against the live API + real corpus (docs/journeys.md). API_URL= targets a host-run API instead of the stack
	@if [ -n "$(API_URL)" ]; then \
	  .venv/bin/python -m pytest serving/tests/test_golden.py -v && python3 scripts/journeys.py; \
	else \
	  docker compose exec -T serving sh -c "pip install -q -r /app/serving/requirements-dev.txt && cd /app/serving && python -m pytest tests/test_golden.py -v" && \
	  API_URL="http://$$(docker compose port serving 8000)" python3 scripts/journeys.py; \
	fi

## ── Housekeeping ─────────────────────────────────────────────────────────────

footprint: ## Disk footprint — this worktree + all worktrees + toolchains + Docker + host volume
	@bash scripts/disk_footprint.sh

clean: ## Reclaim disk in THIS worktree (stray .tmp, data-test, caches). DOCKER=1 prunes Docker cache/dangling; DOCKER=all also drops unused images; DATA_LOCAL=1 drops data-local/.
	@bash scripts/clean.sh

## ── Dev ──────────────────────────────────────────────────────────────────────

fixture: ## Build a truncated *.json.gz fixture from a file id — usage: make fixture ID=5043 NAME=ga_small
	docker compose exec etl go run . fixture -file-ids $(ID) $(if $(NAME),-name $(NAME),)

seed: ## Populate data/ with the committed synthetic MRF (fresh-clone bootstrap; idempotent)
	bash scripts/seed.sh

## ── Database ─────────────────────────────────────────────────────────────────

psql: ## Open a psql shell on honest_healthcare
	docker compose exec db psql -U postgres -d honest_healthcare

migrate: ## Apply db/migrations/*.sql to the running database (idempotent; each file in one transaction)
	@for f in db/migrations/*.sql; do \
	  echo "→ $$f"; \
	  docker compose exec -T db psql -U postgres -d honest_healthcare -v ON_ERROR_STOP=1 --single-transaction < "$$f" || exit 1; \
	done

db-snapshot: ## pg_dump the queue tables (data only) to db/snapshots/<utc>.dump — take one before a risky migration
	@mkdir -p db/snapshots
	@ts=$$(date -u +%Y%m%dT%H%M%SZ); \
	docker compose exec -T db pg_dump -U postgres -d honest_healthcare -Fc --data-only \
	  -t index_files -t index_file_plans -t billing_codes -t coverage_log > db/snapshots/$$ts.dump && \
	echo "→ db/snapshots/$$ts.dump ($$(du -h db/snapshots/$$ts.dump | cut -f1))"

db-restore: ## Replace the queue tables' data from a snapshot. FILE=db/snapshots/<utc>.dump (default: newest)
	@f="$(or $(FILE),$$(ls -t db/snapshots/*.dump 2>/dev/null | head -1))"; \
	[ -n "$$f" ] || { echo "no snapshot — run 'make db-snapshot' first"; exit 1; }; \
	echo "→ truncating + restoring from $$f"; \
	docker compose exec -T db psql -U postgres -d honest_healthcare -v ON_ERROR_STOP=1 \
	  -c "TRUNCATE index_files, index_file_plans, billing_codes, coverage_log RESTART IDENTITY CASCADE;"; \
	docker compose exec -T db pg_restore -U postgres -d honest_healthcare \
	  --data-only --disable-triggers --no-owner < "$$f"

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
	              OR failure_reason NOT SIMILAR TO '%(gzip|unexpected EOF|invalid header|HTTP 4%|malformed MRF)%');" ;; \
	  *) echo "usage: make db-reset WHAT=processing|failed" && exit 1 ;; \
	esac

## ── Shells ────────────────────────────────────────────────────────────────────

sh: ## Open a shell inside a container — usage: make sh S=etl|serving|frontend|db
	@case "$(S)" in \
	  etl)      svc=etl ;; \
	  serving)  svc=serving ;; \
	  frontend) svc=frontend ;; \
	  db)       svc=db ;; \
	  *) echo "usage: make sh S=etl|serving|frontend|db" && exit 1 ;; \
	esac; \
	echo "Entering $$svc shell — type 'exit' to quit"; \
	docker compose exec $$svc sh

## ── Internal ─────────────────────────────────────────────────────────────────

_require-etl-running:
	@docker compose ps etl | grep -q "Up" || \
	  (echo "Error: the etl container is not running — run 'make up' first" && exit 1)
