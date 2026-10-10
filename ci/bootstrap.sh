#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
tool_cache="${CI_TOOLCACHE:-$repo_root/.ci-tools}"
bash ci/allow_checkout_git.sh "$repo_root"
export UV_LINK_MODE=copy
export UV_PROJECT_ENVIRONMENT="$tool_cache/python"
export UV_CACHE_DIR="$tool_cache/uv-cache"
gate="${1:-}"
if [[ -z "$gate" ]]; then
  printf 'Usage: %s <gate>\n' "$0" >&2
  exit 2
fi
python -m ci.bootstrap_tools --install-dir "$tool_cache"
export PATH="$tool_cache/bin:$PATH"
uv sync --locked --project api --python 3.14.7 --all-groups
export PATH="$tool_cache/python/bin:$PATH"
exports="$(mktemp -d)"
trap 'rm -rf -- "$exports"' EXIT
uv export --locked --project api --no-emit-project --no-dev --no-header --no-annotate > "$exports/requirements.txt"
uv export --locked --project api --no-emit-project --all-groups --no-header --no-annotate > "$exports/requirements-dev.txt"
diff -u api/requirements.txt "$exports/requirements.txt"
diff -u api/requirements-dev.txt "$exports/requirements-dev.txt"
npm ci
npm ci --prefix ui
[[ "$(node --version)" == "v24.11.1" ]] || { printf 'Node.js 24.11.1 is required.\n' >&2; exit 2; }
[[ "$(uv --version)" == *"0.12.20"* ]] || { printf 'uv 0.12.20 is required.\n' >&2; exit 2; }
gitleaks_version="$(gitleaks version 2>/dev/null | head -n1)"
gitleaks_version="${gitleaks_version#v}"
[[ "$gitleaks_version" == "8.30.0" ]] || { printf 'Gitleaks 8.30.0 is required; found %s.\n' "$gitleaks_version" >&2; exit 2; }
[[ "$(ruff --version)" == *"0.16.10"* ]] || { printf 'Ruff 0.16.10 is required.\n' >&2; exit 2; }
[[ "$(bandit --version 2>&1 | head -n1)" == *"1.9.4"* ]] || { printf 'Bandit 1.9.4 is required.\n' >&2; exit 2; }
[[ "$(npx --no-install jscpd --version 2>&1 | tail -n1)" == *"5.4.0"* ]] || { printf 'jscpd 5.4.0 is required.\n' >&2; exit 2; }
exec ci/run-gate.sh "$gate"
