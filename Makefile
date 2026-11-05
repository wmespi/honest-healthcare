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
#   make discover TEST=1        # write to data-test/ instead of data/
#   make discover SCHEMA=1      # stream the index, write index_schema.json only
#   make parse FILE=GA_JBNKMED0001.json.gz   # parse one file by name, now
#   make parse TARGETS=<path>   # a different target-plan list (default etl/targets.yaml)
#   make reference STEP=mpfs    # run one reference builder (ARGS="--year 2025" passes through)
#   make reference FORCE=1      # rebuild every reference output even if it exists
#   make check LOCAL=1          # the same gate on host toolchains, no Docker
#   make test  LOCAL=1          # ... incl. pytest + vitest
#   make sh S=serving           # shell into a container

.PHONY: help \
        start up down logs \
        worktree worktree-rm \
        discover parse reference build refresh \
        check test test-live \
        footprint clean \
        fixture seed \
        sh \
        _require-etl-running

## ── Help ─────────────────────────────────────────────────────────────────────

help: ## Show all available make targets
	@echo ""
	@echo "Usage: make <target>   (variables like TEST=1 / FILE= / LOCAL=1 are in the Makefile header)"
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

discover: ## Phase 1 - write this month's file list and plan links (index_files + index_file_plans Parquet) from Anthem's index. TEST=1 · SCHEMA=1 (index_schema.json only) · KEEP_INDEX=1 (keep the multi-GB index download)
	docker compose exec etl go run . discover \
	  $(if $(filter 1,$(SCHEMA)),-index-schema,) \
	  $(if $(filter 1,$(TEST)),-test,) \
	  $(if $(LIMIT),-limit $(LIMIT),) \
	  $(if $(filter 1,$(NO_CACHE)),-no-cache,) \
	  $(if $(filter 1,$(KEEP_INDEX)),-keep-index,) \
	  $(if $(INDEX_URL),-index-url "$(INDEX_URL)",)

parse: ## Phase 2 - stream the files etl/targets.yaml names into Parquet, when they're new or Anthem updated them. FILE=<name>[,<name>] · TARGETS=<path> · TEST=1 · LIMIT=n
	docker compose exec etl go run . parse \
	  $(if $(FILE),-files "$(FILE)",) \
	  $(if $(TARGETS),-targets "$(TARGETS)",) \
	  $(if $(filter 1,$(TEST)),-test,) \
	  $(if $(LIMIT),-limit $(LIMIT),) \
	  $(if $(FIXTURE),-fixture "$(FIXTURE)",)

reference: ## Phase 3 — build every reference dataset (NPPES, labels, CMS, MPFS, OPPS, geocode, MIPS, ZIP centroids, exchange plans, hospitals), skipping what exists. STEP=<name> · FORCE=1 · ARGS="--flag v"
	@bash scripts/reference.sh

build: ## Phase 4 — raw + reference parquet -> the find-care tables in data/serving/, for the plans etl/targets.yaml names. TEST=1
	docker compose exec -T -w /app -e DUCKDB_TMP= serving python3 -m build.build --data-dir /app/data $(if $(filter 1,$(TEST)),--test,)

refresh: ## The monthly job: discover → reference (what's missing; NPPES gates parse) → parse → geocode (new billing IDs) → build
	$(MAKE) discover
	$(MAKE) reference
	$(MAKE) parse
	$(MAKE) reference STEP=geocode
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

test: check ## Every hermetic suite - check + pytest (build, reference, serving) + vitest (+ ETL e2e when not LOCAL). LOCAL=1: host toolchains, no Docker
	@if [ "$(LOCAL)" = 1 ]; then \
	  test -x .venv/bin/python || { echo "no .venv — run scripts/dev-setup.sh"; exit 1; }; \
	  test -d frontend/node_modules || { echo "no frontend/node_modules — run scripts/dev-setup.sh"; exit 1; }; \
	  .venv/bin/python -m pytest -q && \
	  cd frontend && npx vitest run; \
	else \
	  bash tests/e2e/discover.sh && bash tests/e2e/parse.sh && bash tests/e2e/nppes.sh && \
	  docker compose exec -T serving sh -c "pip install -q -r /app/serving/requirements-dev.txt && cd /app && python -m pytest -q" && \
	  docker compose exec -T frontend sh -c "cd /app && npx vitest run"; \
	fi

test-live: ## Golden answers + user journeys against the live API + real corpus (docs/journeys.md). API_URL= targets a host-run API instead of the stack
	@if [ -n "$(API_URL)" ]; then \
	  .venv/bin/python -m pytest tests/live -v && python3 tests/live/journeys.py; \
	else \
	  docker compose exec -T serving sh -c "pip install -q -r /app/serving/requirements-dev.txt && cd /app && python -m pytest tests/live -v" && \
	  API_URL="http://$$(docker compose port serving 8000)" python3 tests/live/journeys.py; \
	fi

## ── Housekeeping ─────────────────────────────────────────────────────────────

footprint: ## Disk footprint — this worktree + all worktrees + toolchains + Docker + host volume
	@bash scripts/disk_footprint.sh

clean: ## Reclaim disk in THIS worktree (stray .tmp, data-test, caches). DOCKER=1 prunes Docker cache/dangling; DOCKER=all also drops unused images; DATA_LOCAL=1 drops data-local/.
	@bash scripts/clean.sh

## ── Dev ──────────────────────────────────────────────────────────────────────

fixture: ## Build a truncated *.json.gz fixture from a file in this month's index - usage: make fixture FILE=GA_JBNKMED0001.json.gz NAME=ga_small
	docker compose exec etl go run . fixture -file "$(FILE)" $(if $(NAME),-name $(NAME),)

seed: ## Populate data/ with the committed synthetic MRF (fresh-clone bootstrap; idempotent)
	bash scripts/seed.sh

## ── Shells ────────────────────────────────────────────────────────────────────

sh: ## Open a shell inside a container - usage: make sh S=etl|serving|frontend
	@case "$(S)" in \
	  etl)      svc=etl ;; \
	  serving)  svc=serving ;; \
	  frontend) svc=frontend ;; \
	  *) echo "usage: make sh S=etl|serving|frontend" && exit 1 ;; \
	esac; \
	echo "Entering $$svc shell — type 'exit' to quit"; \
	docker compose exec $$svc sh

## ── Internal ─────────────────────────────────────────────────────────────────

_require-etl-running:
	@docker compose ps etl | grep -q "Up" || \
	  (echo "Error: the etl container is not running — run 'make up' first" && exit 1)
