#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
app_env="${APP_ENV_FILE:-$repo_root/infra/.env}"
ci_env="${CI_ENV_FILE:-$repo_root/ci/.env}"
telemetry=0
for argument in "$@"; do
  case "$argument" in
    --telemetry) telemetry=1 ;;
    *) printf 'Usage: %s [--telemetry]\n' "$0" >&2; exit 2 ;;
  esac
done
case ",${COMPOSE_PROFILES:-}," in
  *,runner,*) printf 'The Woodpecker runner is blocked pending operational proofs.\n' >&2; exit 2 ;;
esac
export COMPOSE_PROFILES=

if [[ "$app_env" != /* ]]; then app_env="$repo_root/$app_env"; fi
if [[ "$ci_env" != /* ]]; then ci_env="$repo_root/$ci_env"; fi

# Resolve effective shell/dotenv values, verify every mounted file, TLS pair, and
# rendered contract before the first Compose command capable of creating resources.
python3 -m ci.local_orchestration \
  --project-directory "$repo_root" \
  --app-env "$app_env" \
  --ci-env "$ci_env"

app_compose=(docker compose --project-directory "$repo_root" --env-file "$app_env" -f "$repo_root/infra/compose/app.yaml")
legacy_compose=(docker compose --project-directory "$repo_root" --env-file "$app_env" -f "$repo_root/infra/compose/legacy.yaml")
gateway_compose=(docker compose --project-directory "$repo_root" --env-file "$app_env" -f "$repo_root/infra/compose/gateway.yaml")
ci_compose=(docker compose --project-directory "$repo_root" --env-file "$app_env" --env-file "$ci_env" \
  -f "$repo_root/ci/compose.yaml" -f "$repo_root/infra/compose/ci.yaml")

app_profiles=()
if (( telemetry )); then app_profiles+=(--profile telemetry); fi
"${app_compose[@]}" "${app_profiles[@]}" up --build --detach
"${legacy_compose[@]}" up --build --detach
"${gateway_compose[@]}" up --build --detach
"${ci_compose[@]}" --profile logs up --build --detach

printf 'Portable local stacks started. The Woodpecker runner remains disabled.\n'
