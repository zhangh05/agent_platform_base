#!/usr/bin/env bash
# Fast quality gate for changes that touch the Python control plane.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"

[ -x "$PYTHON_BIN" ] || PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
cd "$ROOT"

"$PYTHON_BIN" -m ruff check --select F821,F811 .
"$PYTHON_BIN" scripts/check_ruff_new_violations.py
"$PYTHON_BIN" -m pytest -q \
  harness/test_startup_security.py \
  harness/test_python_runner_policy.py \
  harness/test_session_store_concurrency.py \
  harness/test_workflow_orchestration.py
