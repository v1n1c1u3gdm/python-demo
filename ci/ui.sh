#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
report_dir="${CI_REPORT_DIR:?CI_REPORT_DIR must be set by ci/run-gate.sh}"
VITEST_COVERAGE_DIR="$report_dir/ui" npm --prefix "$repo_root/ui" run test:unit
