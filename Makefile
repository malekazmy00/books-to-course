# book2course — every docker command is scoped to compose project b2c (CLAUDE.md rule 7).
SHELL := /bin/bash
export PATH := $(HOME)/.local/bin:$(PATH)

# Heavy model servers live in mutually exclusive profiles so the started set fits the
# 85 GiB / 40 CPU budget (rule 8; `make budget` enforces it before every `make up`):
#   local-llm (llama.cpp, 50g)   gemma4 (Ollama, 22g)   speech (fasih + renderer)   hy-translation (Hy-MT servers)
# e.g.  make up                         -> core + local-llm
#       make up PROFILES=speech         -> core + speech
# EXTERNAL=1 adds the opt-in OpenRouter generation route (D-022); never on by default (rule 6).
PROFILES ?= local-llm
EXTERNAL ?= 0
EXTERNAL_QE ?= 0   # D-029: external semantic QE (OpenRouter) for development
KNOWLEDGE_SEARCH ?= 0   # D-034: b2c-owned SearXNG for knowledge research (also add PROFILES=... knowledge-search)
ALL_PROFILES := local-llm gemma4 gemma4-booktrans speech hy-translation surya mock
COMPOSE_FILES := -f docker/compose.yml -f docker/compose.observability.yml -f docker/compose.qa-local.yml \
	-f docker/compose.speech.yml -f docker/compose.hy-translation.yml -f docker/compose.gemma4.yml \
	$(if $(filter 1,$(EXTERNAL)),-f docker/compose.external-generation.yml) \
	$(if $(filter 1,$(EXTERNAL_QE)),-f docker/compose.external-qe.yml) \
	$(if $(filter 1,$(KNOWLEDGE_SEARCH)),-f docker/compose.knowledge-search.yml)
COMPOSE_BASE := docker compose -p b2c --env-file .env $(COMPOSE_FILES)
COMPOSE := $(COMPOSE_BASE) $(foreach p,$(PROFILES),--profile $(p))
PYTEST  := uv run pytest
PHASE   ?= 0

# P8 / audit 08 C3: every image is stamped with the commit it is built from (Dockerfile ARG ->
# OCI label org.opencontainers.image.revision + env B2C_GIT_REVISION, logged at startup and
# recorded in each stage-output registry row). `build` and `up` refuse a dirty tree (tracked
# changes or untracked files, which COPY would bake in) unless FORCE_DIRTY=1, which stamps
# "<sha>-dirty": such output can be registered but never accepted.
FORCE_DIRTY ?= 0
ifneq ($(filter build up,$(MAKECMDGOALS)),)
GIT_REVISION ?= $(shell git rev-parse HEAD 2>/dev/null)
GIT_DIRTY ?= $(shell git status --porcelain 2>/dev/null)
ifeq ($(strip $(GIT_REVISION)),)
$(error cannot stamp images: no git revision (git rev-parse HEAD failed))
endif
ifeq ($(strip $(GIT_DIRTY)),)
B2C_GIT_REVISION := $(strip $(GIT_REVISION))
else ifeq ($(FORCE_DIRTY),1)
B2C_GIT_REVISION := $(strip $(GIT_REVISION))-dirty
else
$(error refusing to build from a dirty tree (P8): commit first, or pass FORCE_DIRTY=1 to stamp $(strip $(GIT_REVISION))-dirty)
endif
endif

# pytest exits 5 when a directory has no tests yet (golden / e2e before their phases)
define allow_empty
	$(1) || { rc=$$?; [ $$rc -eq 5 ] && echo "(no tests yet)" || exit $$rc; }
endef

.PHONY: help env data-init build up start down ps logs limits budget lint check lint-imports test test-unit test-contract \
	test-golden test-integration test-e2e eval bench-llm

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | sort | tr '\n' ' '; echo

env:                 ## create .env with random secrets (never overwrites)
	@bash scripts/gen-env.sh

build:
	B2C_GIT_REVISION=$(B2C_GIT_REVISION) $(COMPOSE) build

data-init:           ## once, after the data VHDX is mounted (D-020)
	@bash scripts/data-disk.sh init

budget:              ## rule 8: fail if the selected profiles exceed the RAM/CPU budget
	@$(COMPOSE) config --format json | python3 scripts/check-budget.py

up: env budget       ## build + start (refuses if the dedicated data disk is not mounted)
	@bash scripts/data-disk.sh check
	B2C_GIT_REVISION=$(B2C_GIT_REVISION) $(COMPOSE) up -d --build

start: budget        ## boot path (scripts/windows/start-b2c-stack.ps1): no rebuild
	@bash scripts/data-disk.sh check
	$(COMPOSE) up -d --no-build

down:                ## stop every b2c service in any profile (volumes kept; never -v here)
	$(COMPOSE_BASE) $(foreach p,$(ALL_PROFILES),--profile $(p)) down

ps:
	$(COMPOSE) ps -a

logs:
	$(COMPOSE) logs --tail=100 $(S)

limits:              ## T-P0-04 view: limits of every b2c container
	@bash eval/suites/phase0/t_p0_04_limits.sh

lint:                ## ruff (config in pyproject.toml)
	uv run ruff check .

check: lint lint-imports test-contract test-unit   ## CI gate (.github/workflows/ci.yml)

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
