#!/usr/bin/env bash
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
config="${CI_GATES_FILE:-$repo_root/ci/gates.json}"
gate="${1:-}"
if [[ -z "$gate" ]]; then
  printf 'Usage: %s <gate>\n' "$0" >&2
  exit 2
fi
if [[ ! "$gate" =~ ^[A-Za-z0-9_-]+$ ]]; then
  printf 'Invalid gate name: %s\n' "$gate" >&2
  exit 2
fi
if [[ ! -f "$config" ]]; then
  printf 'Gate %s cannot run: configuration is missing: %s\n' "$gate" "$config" >&2
  exit 2
fi

argv_output=$(python3 -m ci.gate_config --config "$config" "$gate")
status=$?
if (( status != 0 )); then
  exit "$status"
fi
mapfile -t argv <<< "$argv_output"
if ! command -v -- "${argv[0]}" >/dev/null 2>&1; then
  printf 'Gate %s cannot run: required tool is missing: %s\n' "$gate" "${argv[0]}" >&2
  exit 127
fi

reports_root="${CI_REPORTS_ROOT:-$repo_root/.ci-reports}"
commit="${CI_COMMIT_SHA:-$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || printf 'local')}"
run="${CI_RUN_ID:-${CI_PIPELINE_NUMBER:-$(date -u +%Y%m%dT%H%M%SZ)-$$}}"
if [[ ! "$commit" =~ ^[A-Za-z0-9._-]+$ || ! "$run" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf 'Gate %s cannot run: invalid commit or execution identifier\n' "$gate" >&2
  exit 2
fi
report_dir="$reports_root/$commit-$run"
if [[ -L "$reports_root" || -L "$report_dir" ]]; then
  printf 'Gate %s cannot write reports through a symlink\n' "$gate" >&2
  exit 2
fi
mkdir -p -- "$report_dir" || exit 2
export CI_REPORT_DIR="$(cd -- "$report_dir" && pwd)"
status_file="$report_dir/$gate.status.json"
log_file="$report_dir/$gate.log"
if [[ -e "$status_file" || -L "$status_file" || -e "$log_file" || -L "$log_file" ]]; then
  printf 'Gate %s already has output for this execution identity\n' "$gate" >&2
  python3 -m ci.gate_status conflict "$report_dir/$gate.conflict" "$gate" || true
  exit 2
fi
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_status() {
  local gate_status="$1" exit_code="$2" finished_at="$3" temporary
  python3 -m ci.gate_status write "$status_file" "$gate" "$commit" "$run" \
    "$gate_status" "$exit_code" "$started_at" "$finished_at"
}
if ! write_status running 125 ""; then
  printf 'Gate %s cannot record its start status\n' "$gate" >&2
  exit 74
fi
printf '[gate:%s] %s\n' "$gate" "${argv[*]}"
set +e
"${argv[@]}" 2>&1 | tee "$log_file"
statuses=("${PIPESTATUS[@]}")
set -e
if (( statuses[0] != 0 )); then
  gate_status=failed
  gate_exit="${statuses[0]}"
elif (( statuses[1] != 0 )); then
  gate_status=failed
  gate_exit=74
  printf 'Gate %s failed to save its report: %s\n' "$gate" "$report_dir/$gate.log" >&2
else
  gate_status=passed
  gate_exit=0
fi
finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! write_status "$gate_status" "$gate_exit" "$finished_at"; then
  printf 'Gate %s cannot record its final status\n' "$gate" >&2
  exit 74
fi
exit "$gate_exit"
