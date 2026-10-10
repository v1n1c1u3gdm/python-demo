#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="${LEGACY_ENV_FILE:-$repo_root/infra/.env}"
if [[ "$env_file" != /* ]]; then
  env_file="$repo_root/$env_file"
fi
compose_file="$repo_root/infra/compose/legacy.yaml"

python3 "$repo_root/ci/stack_config.py" validate-legacy \
  --env-file "$env_file" \
  --compose-file "$compose_file" \
  --project-directory "$repo_root"
docker compose --project-directory "$repo_root" --env-file "$env_file" -f "$compose_file" config --quiet
exec docker compose --project-directory "$repo_root" --env-file "$env_file" -f "$compose_file" up -d "$@"
