# Honest Healthcare — convenience wrappers around docker compose
# Always use `exec` (not `run --rm`) so logs appear in Docker Desktop.
#
# Usage: make <target>
#        make help   ← list all available targets
#
# One name per workflow: the make target, the etl subcommand, and the
# helper doc next to the code all share it. Test isolation and single-item
# selection are variables, not separate targets:
#
#   make discover TEST=1        # run in the test schema + data-test/
#   make discover SCHEMA=1      # stream the index, write index_schema.json only
#   make parse ID=10065         # parse one file by index_files.id
#   make parse TARGETS=<path>   # a different target-plan list (default etl/targets.yaml)
#   make parse TEST=1           # parse in test isolation
#   make db-reset WHAT=failed   # reset transiently-failed rows → pending
#   make sh S=serving          # shell into a container

.PHONY: help \
        start up down logs \
        discover parse size fixture seed build \
        nppes code-labels taxonomy-labels mpfs doctors-clinicians \
        fmt lint test test-e2e test-api test-web check test-all test-live \
        check-local \
        worktree worktree-rm stack-up stack-down promote preview preview-down tiers \
        cov-probe cov-report smoke-web journeys data-size footprint clean \
        psql migrate db-reset db-snapshot db-restore \
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

## ── Worktrees / parallel dev (GH #59 · docs/worktrees.md) ────────────────────

worktree: ## New sibling worktree + branch off origin/main, fully set up. TOPIC=<name> [TYPE=feat]
	@test -n "$(TOPIC)" || { echo "usage: make worktree TOPIC=<name> [TYPE=feat]"; exit 1; }
	bash scripts/worktree-new.sh "$(TOPIC)" "$(or $(TYPE),feat)"

worktree-rm: ## Remove a sibling worktree (refuses if dirty; FORCE=1 to override). TOPIC=<name>
	@test -n "$(TOPIC)" || { echo "usage: make worktree-rm TOPIC=<name>"; exit 1; }
	git worktree remove $(if $(filter 1,$(FORCE)),--force,) "$$(dirname "$$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -1)")/hh-$(TOPIC)"

check-local: ## Hermetic gate on host toolchains (no Docker) — gofmt, vet, build, go test, pytest contract, vitest
	@command -v go >/dev/null || { echo "no host 'go' — run scripts/dev-setup.sh"; exit 1; }
	@test -x .venv/bin/python || { echo "no .venv — run scripts/dev-setup.sh"; exit 1; }
	@test -d frontend/node_modules || { echo "no frontend/node_modules — run scripts/dev-setup.sh"; exit 1; }
	@out=$$(gofmt -l etl); [ -z "$$out" ] || { printf 'gofmt needed:\n%s\n' "$$out"; exit 1; }
	cd etl && go vet ./... && go build ./... && go test ./...
	.venv/bin/python -m pytest serving/tests/test_api_contract.py \
	  serving/tests/test_cms_utilization.py serving/tests/test_specialty_profiles.py \
	  serving/tests/test_mpfs.py serving/tests/test_doctors_clinicians.py \
	  serving/tests/test_geocode.py serving/tests/test_build.py -q
	cd frontend && npx vitest run

stack-up: ## Start THIS worktree's stack (own project + ports from .env). Build on first run.
	docker compose up -d --build

stack-down: ## Stop this worktree's stack
	docker compose down

promote: ## CANONICAL CHECKOUT ONLY — advance the tailnet stack to a ref (REF=, default origin/main). Prompts, logs, tags.
	bash scripts/promote.sh $(REF)

tiers: ## Show what the tailnet stack runs vs origin/main + the unpromoted commits
	@bash scripts/tiers.sh

preview: ## Ephemeral stack for a ref before promoting it — localhost:5183, no RAM cost once down. REF= · LAN=1
	@test -n "$(REF)" || { echo "usage: make preview REF=<branch-or-sha> [LAN=1]"; exit 1; }
	$(if $(filter 1,$(LAN)),LAN=1 ,)bash scripts/preview.sh "$(REF)"

preview-down: ## Stop the preview stack (frees the RAM; the worktree stays on disk)
	@bash scripts/preview.sh --down

## ── Pipeline: discover → parse ───────────────────────────────────────────────

discover: ## Phase 1 — sync the Anthem master index into index_files + index_file_plans. TEST=1 · SCHEMA=1 (index_schema.json only, no DB)
	docker compose exec etl go run . discover \
	  $(if $(filter 1,$(SCHEMA)),-index-schema,) \
	  $(if $(filter 1,$(TEST)),-test,) \
	  $(if $(LIMIT),-limit $(LIMIT),) \
	  $(if $(filter 1,$(NO_CACHE)),-no-cache,) \
	  $(if $(INDEX_URL),-index-url "$(INDEX_URL)",)

parse: ## Phase 2 — stream pending files that serve a target plan into Parquet. ID=<index_files.id> · TARGETS=<path> · TEST=1 · LIMIT=n
	docker compose exec etl go run . parse \
	  $(if $(ID),-file-ids $(ID),) \
	  $(if $(TARGETS),-targets "$(TARGETS)",) \
	  $(if $(filter 1,$(TEST)),-test,) \
	  $(if $(LIMIT),-limit $(LIMIT),) \
	  $(if $(FIXTURE),-fixture "$(FIXTURE)",)

size: ## Backfill index_files.file_size_bytes via concurrent HEAD requests
	docker compose exec etl go run . size

seed: ## Populate data/ with the committed synthetic MRF (fresh-clone bootstrap; idempotent)
	bash scripts/seed.sh

build: ## Raw + reference parquet -> data/serving/ tables (price-grain rates, dims, roster-weighted rate_hist). NET=<slug,slug> for a subset. TEST=1
	docker compose exec -T -w /app -e DUCKDB_TMP= serving python3 -m build.build --data-dir /app/data $(if $(NET),--networks "$(NET)",) $(if $(filter 1,$(TEST)),--test,)

fixture: ## Build a truncated *.json.gz fixture from a file id — usage: make fixture ID=5043 NAME=ga_small
	docker compose exec etl go run . fixture -file-ids $(ID) $(if $(NAME),-name $(NAME),)

## ── Reference data ──────────────────────────────────────────────────────────

nppes: _require-etl-running ## Download the NPPES national file, write data/nppes/ga_providers.parquet (GA subset). URL= / FILE= to override.
	docker compose exec etl go run . nppes $(if $(URL),-url "$(URL)",) $(if $(FILE),-file "$(FILE)",)

code-labels: ## Build data/reference/code_labels.parquet (RBCS categories + synonyms for every parsed code)
	docker compose exec -T -w /app serving python3 -m reference.code_labels --data-dir /app/data $(if $(RBCS_URL),--rbcs-url "$(RBCS_URL)",)

taxonomy-labels: ## Build data/reference/nucc_taxonomy.parquet (NUCC specialty labels for provider taxonomy codes)
	docker compose exec -T -w /app serving python3 -m reference.taxonomy_labels --data-dir /app/data $(if $(NUCC_URL),--nucc-url "$(NUCC_URL)",)

cms-utilization: ## Build data/cms/ga_provider_service.parquet (CMS Medicare Part B — did this NPI bill this code)
	docker compose exec -T -w /app serving python3 -m reference.cms_utilization --data-dir /app/data $(if $(CMS_URL),--cms-url "$(CMS_URL)",) $(if $(YEAR),--year $(YEAR),)

doctors-clinicians: ## Build data/reference/dac_ga.parquet + dac_hospital_affiliations.parquet (CMS Doctors & Clinicians — real practice identity + CCN↔NPI bridge)
	docker compose exec -T -w /app serving python3 -m reference.doctors_clinicians --data-dir /app/data $(if $(DAC_URL),--dac-url "$(DAC_URL)",) $(if $(AFFIL_URL),--affiliations-url "$(AFFIL_URL)",)

specialty-profiles: ## Build data/reference/specialty_procedure_profiles.parquet (what each specialty typically bills — needs cms-utilization + nppes + taxonomy-labels)
	docker compose exec -T -w /app serving python3 -m reference.specialty_profiles --data-dir /app/data

mpfs: ## Build data/reference/mpfs_ga.parquet (CMS Medicare Physician Fee Schedule allowed $ per code — the per-code benchmark). CMS_URL= / YEAR= / CF= to override.
	docker compose exec -T -w /app serving python3 -m reference.mpfs --data-dir /app/data $(if $(CMS_URL),--cms-url "$(CMS_URL)",) $(if $(YEAR),--year $(YEAR),) $(if $(CF),--cf $(CF),)

geocode: ## Build data/reference/pcp_geocode.parquet (lat/long for GA PCP-eligible NPIs, via the free Census batch geocoder — distance ranking, #87)
	docker compose exec -T -w /app serving python3 -m reference.geocode --data-dir /app/data

## ── Quality gate ────────────────────────────────────────────────────────────

fmt: _require-etl-running ## Format all Go source (gofmt -w)
	docker compose exec etl gofmt -w .

lint: _require-etl-running ## Static checks — go vet + go build (non-mutating)
	docker compose exec etl go vet ./...
	docker compose exec etl go build ./...

test: _require-etl-running ## Go unit tests (hermetic — fixture-driven, no network/DB)
	docker compose exec etl go test ./...

test-e2e: _require-etl-running ## Hermetic end-to-end: parse + NPPES fixtures in test isolation, with teardown
	bash scripts/etl_e2e_test.sh
	bash scripts/nppes_test.sh

test-api: ## Backend contract + coverage tests (pytest, against the running API)
	# --ignore test_golden.py: it's not hermetic to *this* stack's build state
	# (skips cleanly on a partial reference build, #96) — it only runs under
	# `make test-live`, never as part of the contract/coverage gate.
	docker compose exec -T serving sh -c "pip install -q -r /app/serving/requirements-dev.txt && cd /app/serving && python -m pytest tests/ --ignore=tests/test_golden.py -q"

test-web: ## Rate-explorer component tests (vitest + Testing Library, hermetic — mocks the API)
	docker compose exec -T frontend sh -c "cd /app && npx vitest run"

check: fmt lint test ## Pre-commit gate — fmt + vet + build + Go unit tests

test-all: check test-e2e test-api test-web ## Full sweep — gate + e2e + serving + frontend (stack must be up)

test-live: ## Golden-answer + user-journey checks against the live API + real corpus (docs/journeys.md, #96). Skips cleanly if the target network isn't loaded
	docker compose exec -T serving sh -c "pip install -q -r /app/serving/requirements-dev.txt && cd /app/serving && python -m pytest tests/test_golden.py -v"
	# `docker compose port` resolves *this* project's actual host binding —
	# a worktree's own API_PORT, not a hardcoded 8000 that would silently
	# test the canonical checkout's stack instead of the one just built.
	API_URL="http://$$(docker compose port serving 8000)" python3 scripts/journeys.py

## ── Coverage feedback loop ──────────────────────────────────────────────────

cov-probe: ## Coverage scorecard for the target plan — usage: make cov-probe LABEL=before
	python3 scripts/coverage_probe.py --label $(or $(LABEL),probe)

cov-report: ## Aggregate coverage_log + flag partial-looking completions (exit 1 if any). SCHEMA=test · NO_FAIL=1
	python3 scripts/coverage_report.py --schema $(or $(SCHEMA),public) $(if $(filter 1,$(NO_FAIL)),--no-fail,)

smoke-web: ## Exercise the rate-explorer's API routes for the target plan across a procedure basket
	python3 scripts/frontend_smoke.py

journeys: ## Assert the named user journeys + report per-journey latency (docs/journeys.md). Needs the real corpus. JSON=1
	python3 scripts/journeys.py $(if $(filter 1,$(JSON)),--json,)

data-size: ## Data-consumption scorecard — rows + bytes per Parquet table + Postgres queue tables. JSON=1 for machine output
	@bash scripts/data_size.sh $(if $(JSON),--json,)

footprint: ## Disk footprint — this worktree + all worktrees + toolchains + Docker + host volume. Paste into every PR body.
	@bash scripts/disk_footprint.sh

clean: ## Reclaim disk in THIS worktree (stray .tmp, data-test, caches). DOCKER=1 prunes Docker cache/dangling; DOCKER=all also drops unused images; DATA_LOCAL=1 drops data-local/.
	@bash scripts/clean.sh

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
