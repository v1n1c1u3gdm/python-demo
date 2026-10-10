#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
version="$(npx --no-install jscpd --version 2>&1 | tail -n1)"
[[ "$version" == *"5.4.0"* ]] || { printf 'jscpd 5.4.0 required; found %s.\n' "$version" >&2; exit 2; }
reports_root="${CI_REPORTS_ROOT:-$repo_root/.ci-reports}"
run="${CI_RUN_ID:-local}"
commit="${CI_COMMIT_SHA:-$(git rev-parse --short HEAD)}"
report_dir="$reports_root/$commit-$run"
mkdir -p -- "$report_dir"
paths=("api" "ui/src")
if (($# > 0)); then
  paths=()
  for candidate in "$@"; do
    if [[ "$candidate" == /* || "$candidate" =~ (^|/)\.\.(/|$) ]]; then
      printf 'Quality scan refused path outside the repository: %s\n' "$candidate" >&2
      exit 2
    fi
    resolved="$(realpath -e -- "$candidate")" || { printf 'Quality scan path does not exist: %s\n' "$candidate" >&2; exit 2; }
    case "$resolved" in
      "$repo_root"/*) paths+=("$resolved") ;;
      *) printf 'Quality scan refused path outside the repository: %s\n' "$candidate" >&2; exit 2 ;;
    esac
  done
fi
npx --no-install jscpd --config ci/jscpd.json --output "$report_dir" "${paths[@]}"
