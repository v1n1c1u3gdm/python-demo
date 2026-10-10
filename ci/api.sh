#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
report_dir="${CI_REPORT_DIR:?CI_REPORT_DIR must be set by ci/run-gate.sh}"
cd "$repo_root/api"
uv run --locked --no-sync --project "$repo_root/api" python -m pytest \
  --cov --cov-report=term-missing \
  --cov-report="xml:$report_dir/api-coverage.xml" \
  --cov-fail-under=85
