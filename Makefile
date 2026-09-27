# book2course — every docker command is scoped to compose project b2c (CLAUDE.md rule 7).
SHELL := /bin/bash
export PATH := $(HOME)/.local/bin:$(PATH)

COMPOSE := docker compose -p b2c --env-file .env -f docker/compose.yml -f docker/compose.observability.yml
PYTEST  := uv run pytest
PHASE   ?= 0

# pytest exits 5 when a directory has no tests yet (golden / e2e before their phases)
define allow_empty
	$(1) || { rc=$$?; [ $$rc -eq 5 ] && echo "(no tests yet)" || exit $$rc; }
endef

.PHONY: help env data-init build up down ps logs limits lint-imports test test-unit test-contract \
	test-golden test-integration test-e2e eval bench-llm

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | sort | tr '\n' ' '; echo

env:                 ## create .env with random secrets (never overwrites)
	@bash scripts/gen-env.sh

build:
	$(COMPOSE) build worker

data-init:           ## once, after the data VHDX is mounted (D-020)
	@bash scripts/data-disk.sh init

up: env              ## start the b2c stack (refuses if the dedicated data disk is not mounted)
	@bash scripts/data-disk.sh check
	$(COMPOSE) up -d --build

down:                ## stop the b2c stack (volumes kept; never -v here)
	$(COMPOSE) down

ps:
	$(COMPOSE) ps -a

logs:
	$(COMPOSE) logs --tail=100 $(S)

limits:              ## T-P0-04 view: limits of every b2c container
	@bash eval/suites/phase0/t_p0_04_limits.sh

lint-imports:        ## T-P0-01
	uv run lint-imports

test: test-contract test-unit lint-imports test-golden test-integration test-e2e

test-unit:
	$(PYTEST) tests/unit -q

test-contract:
	$(PYTEST) tests/contract -q

test-golden:
	$(call allow_empty,$(PYTEST) tests/golden -q)

test-integration:    ## runs inside the b2c network (postgres/minio/temporal are not on the host)
	$(COMPOSE) run --rm --no-deps -e B2C_RUN_INTEGRATION=1 worker pytest tests/integration -v

test-e2e:
	$(call allow_empty,$(PYTEST) tests/e2e -q)

eval:                ## run the phase's eval suite; evidence goes to eval/reports/phase-$(PHASE)-evidence/
	@test -x eval/suites/phase$(PHASE)/run.sh || { echo "no eval suite for phase $(PHASE)"; exit 1; }
	eval/suites/phase$(PHASE)/run.sh

bench-llm:           ## Phase 1 benchmark protocol (not implemented in Phase 0)
	@echo "bench-llm is implemented in Phase 1 (docs/05 T-B-01..06)"; exit 1
