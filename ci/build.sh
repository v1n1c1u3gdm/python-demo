#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
report_dir="${CI_REPORT_DIR:?CI_REPORT_DIR must be set by ci/run-gate.sh}"
cd "$repo_root"
npm --prefix ui run build
coverage erase
coverage run --source=ci --omit='ci/tests/*' -m unittest discover -s ci/tests -v
coverage xml -o "$report_dir/ci-coverage.xml"
coverage report --include='ci/*.py' --fail-under=85
python -m ci.coverage_gate --api "$report_dir/api-coverage.xml" \
  --ui "$report_dir/ui/coverage-summary.json" --ci "$report_dir/ci-coverage.xml"
