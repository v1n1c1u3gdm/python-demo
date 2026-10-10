#!/bin/sh
set -eu

read_secret() {
  file=$1
  test -s "$file"
  bytes=$(od -An -t x1 "$file" | tr -d ' \n')
  case "$bytes" in *0a*|*0d*) printf 'Malformed Woodpecker secret file.\n' >&2; exit 1 ;; esac
  value=$(cat "$file")
  printf '%s' "$value"
}

export WOODPECKER_GITEA_CLIENT="$(read_secret /run/local-secrets/gitea_client_id)"
export WOODPECKER_GITEA_SECRET="$(read_secret /run/local-secrets/gitea_client_secret)"
export WOODPECKER_AGENT_SECRET="$(read_secret /run/local-secrets/woodpecker_agent_secret)"
test -s /run/local-secrets/tls/ca.crt
export SSL_CERT_FILE=/run/local-secrets/tls/ca.crt
export GIT_SSL_CAINFO=/run/local-secrets/tls/ca.crt
exec /bin/woodpecker-server "$@"
