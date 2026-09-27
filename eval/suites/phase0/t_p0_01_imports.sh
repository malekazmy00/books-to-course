#!/usr/bin/env bash
# T-P0-01: import-linter, plus a negative control (a planted violation MUST break a contract).
source "$(dirname "$0")/lib.sh"
out="$EV/t_p0_01_import_linter.txt"
{
  stamp uv run lint-imports
  uv run lint-imports; echo "exit=$?"
  echo
  echo "## negative control: plant 'import b2c_kg' inside b2c_ingest, expect BROKEN + non-zero exit"
  f=packages/ingest/src/b2c_ingest/_negative_control.py
  echo "import b2c_kg  # planted violation" > "$f"
  stamp uv run lint-imports
  uv run lint-imports; rc=$?
  rm -f "$f"
  echo "exit=$rc"
  [ $rc -ne 0 ] && echo "NEGATIVE CONTROL OK (violation detected)" || echo "NEGATIVE CONTROL FAILED"
} 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | tee "$out"
