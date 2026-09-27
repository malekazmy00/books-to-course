#!/usr/bin/env bash
# `make eval PHASE=0`: runs every Phase 0 gate check; evidence -> eval/reports/phase-0-evidence/
source "$(dirname "$0")/lib.sh"
D="$(dirname "$0")"
{
  stamp "uv run pytest tests/contract tests/unit -v"
  uv run pytest tests/contract tests/unit -v 2>&1 | tail -80
} | tee "$EV/pytest_contract_unit.txt"
{
  stamp "make test-integration"
  make -s test-integration 2>&1 | tail -40
} | tee "$EV/pytest_integration.txt"
bash "$D/t_p0_01_imports.sh"
bash "$D/t_p0_02_langfuse.sh"
bash "$D/t_p0_03_durability.sh"
bash "$D/t_p0_04_limits.sh"
bash "$D/idle_stats.sh"
bash "$D/t_p0_05_isolation.sh" | tee "$EV/t_p0_05_isolation.txt"
