#!/usr/bin/env bash
set -euo pipefail
script_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || { printf 'Secret scan requires a Git checkout.\n' >&2; exit 2; }
if [[ "$(git -C "$repo_root" rev-parse --is-shallow-repository)" != false ]]; then
  printf 'Secret scan refused: shallow Git history is incomplete. Fetch complete history before scanning.\n' >&2
  exit 2
fi
command -v gitleaks >/dev/null || { printf 'Secret scan requires gitleaks 8.30.0.\n' >&2; exit 127; }
version="$(gitleaks version 2>/dev/null | head -n1)"
[[ "$version" == *"8.30.0"* ]] || { printf 'Secret scan requires pinned gitleaks 8.30.0; found %s.\n' "$version" >&2; exit 2; }
commit="${CI_COMMIT_SHA:-$(git -C "$repo_root" rev-parse --short HEAD)}"
reports_root="${CI_REPORTS_ROOT:-$repo_root/.ci-reports}"
run="${CI_RUN_ID:-local}"
report_dir="$reports_root/$commit-$run"
mkdir -p -- "$report_dir"
snapshot="$(mktemp -d "${TMPDIR:-/tmp}/gitleaks-source.XXXXXX")"
trap 'rm -rf -- "$snapshot"' EXIT
paths="$snapshot/.paths"
git -C "$repo_root" ls-files --cached --others --exclude-standard -z > "$paths"
while IFS= read -r -d '' path; do
  source="$repo_root/$path"
  [[ -e "$source" || -L "$source" ]] || continue
  parent="$repo_root"
  IFS='/' read -r -a components <<< "$path"
  for ((index = 0; index < ${#components[@]}; index++)); do
    parent="$parent/${components[index]}"
    if [[ -L "$parent" ]]; then
      printf 'Secret scan refused symlink in source path: %s\n' "$path" >&2
      exit 2
    fi
  done
  mkdir -p -- "$snapshot/$(dirname -- "$path")"
  cp -a -- "$source" "$snapshot/$path"
done < "$paths"
rm -f -- "$paths"
status=0
# Scan every available Git ref, then the nonignored current checkout snapshot.
gitleaks git --config "$script_root/gitleaks.toml" --log-opts=--all --redact=100 --report-format=json --report-path "$report_dir/gitleaks-history.json" "$repo_root" >/dev/null 2>&1 || status=$?
gitleaks dir --config "$script_root/gitleaks.toml" --redact=100 --report-format=json --report-path "$report_dir/gitleaks-working-tree.json" "$snapshot" >/dev/null 2>&1 || status=$?
if (( status != 0 )); then
  printf 'Secret scan found a finding. Redacted JSON reports: %s\n' "$report_dir"
  exit "$status"
fi
printf 'Secret scan passed: complete Git refs and source checkout scanned. Redacted reports: %s\n' "$report_dir"
