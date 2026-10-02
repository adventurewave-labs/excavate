#!/usr/bin/env bash
# The gate every change must pass: lint, types, tests. No network, no keys.
#   pip install -e ".[dev]" && scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ruff check .
mypy
pytest -q
