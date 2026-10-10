#!/bin/sh
set -eu

read_secret() {
  secret_path="$1"
  secret_name="$2"
  if [ ! -r "$secret_path" ]; then
    printf 'Keycloak secret file is unavailable: %s\n' "$secret_name" >&2
    exit 2
  fi
  secret_value=$(cat -- "$secret_path") || {
    printf 'Keycloak secret file could not be read: %s\n' "$secret_name" >&2
    exit 2
  }
  if [ -z "$secret_value" ]; then
    printf 'Keycloak secret file is empty: %s\n' "$secret_name" >&2
    exit 2
  fi
  printf '%s' "$secret_value"
}

KC_DB_PASSWORD=$(read_secret "${KC_DB_PASSWORD_FILE:?KC_DB_PASSWORD_FILE is required}" KC_DB_PASSWORD_FILE)
KC_BOOTSTRAP_ADMIN_PASSWORD=$(read_secret \
  "${KC_BOOTSTRAP_ADMIN_PASSWORD_FILE:?KC_BOOTSTRAP_ADMIN_PASSWORD_FILE is required}" \
  KC_BOOTSTRAP_ADMIN_PASSWORD_FILE)
export KC_DB_PASSWORD KC_BOOTSTRAP_ADMIN_PASSWORD
unset KC_DB_PASSWORD_FILE KC_BOOTSTRAP_ADMIN_PASSWORD_FILE

exec /opt/keycloak/bin/kc.sh "$@"
