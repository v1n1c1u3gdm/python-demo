#!/bin/sh
set -eu

state=/run/local-state
runtime=/run/local-secrets
canonical=/run/local-canonical
intent="$state/gitea-oidc.env"
generation=$(sed -n 's/^generation=//p' "$intent")
subject=$(sed -n 's/^subject=//p' "$intent")
issuer=$(sed -n 's/^issuer=//p' "$intent")
current_generation=$(tr -d '\n' < "$state/bootstrap-generation")
test "$generation" = "$current_generation"
test "$issuer" = "https://app.localhost/auth/realms/python-demo"
case "$generation:$subject" in *[!a-f0-9:A-Za-z0-9._-]*) exit 1 ;; esac
test -s "$runtime/gitea_admin_password"
test -s "$runtime/gitea_client_secret"
test -s "$runtime/tls/ca.crt"
install -m 0644 "$runtime/tls/ca.crt" /usr/local/share/ca-certificates/python-demo-local.crt
update-ca-certificates >/dev/null

gitea=/usr/local/bin/gitea
run_gitea() { su-exec git "$gitea" --config "$config" "$@"; }
config="/data/gitea/conf/app.ini"
user_line=$(run_gitea admin user list | awk '$2 == "admin" { print $1; exit }')
if [ -z "$user_line" ]; then
  password=$(cat "$runtime/gitea_admin_password")
  run_gitea admin user create --username admin --password "$password" \
    --email admin@app.localhost --admin --must-change-password=false >/dev/null
fi

token_file="$canonical/gitea_bootstrap_api_token"
test -d "$canonical" && test ! -L "$canonical"
if [ ! -s "$token_file" ]; then
  test ! -e "$token_file"
  token=$(run_gitea admin user generate-access-token --username admin \
    --token-name local-bootstrap --scopes all --raw)
  case "$token" in ''|*[!A-Za-z0-9_-]*) printf 'Gitea returned an invalid bootstrap token.\n' >&2; exit 1 ;; esac
  umask 077
  temporary=$(mktemp "$canonical/.gitea-token.XXXXXX")
  printf '%s' "$token" > "$temporary"
  mv -f "$temporary" "$token_file"
fi
chmod 0600 "$token_file"
token=$(cat "$token_file")
case "$token" in ''|*[!A-Za-z0-9_-]*) printf 'Stored Gitea bootstrap token is invalid.\n' >&2; exit 1 ;; esac

cp "$token_file" /run/bootstrap-output/gitea_bootstrap_api_token
chmod 0600 /run/bootstrap-output/gitea_bootstrap_api_token
source_id=$(run_gitea admin auth list | awk '$2 == "keycloak" && $1 ~ /^[0-9]+$/ { print $1; exit }')
client_secret=$(cat "$runtime/gitea_client_secret")
configure_source() {
  operation=$1
  shift
  run_gitea admin auth "$operation" "$@" --name keycloak --provider openidConnect --key gitea \
    --secret "$client_secret" --auto-discover-url "$issuer/.well-known/openid-configuration" \
    --group-claim-name groups --admin-group local-admins --scopes openid --scopes email --scopes profile
}
if [ -n "$source_id" ]; then
  configure_source update-oauth --id "$source_id" >/dev/null
else
  configure_source add-oauth >/dev/null
  source_id=$(run_gitea admin auth list | awk '$2 == "keycloak" && $1 ~ /^[0-9]+$/ { print $1; exit }')
fi
test -n "$source_id"

curl_config=$(mktemp /tmp/gitea-auth.XXXXXX)
response=$(mktemp /tmp/gitea-user.XXXXXX)
trap 'rm -f "$curl_config" "$response"' EXIT
chmod 0600 "$curl_config" "$response"
printf 'header = "Authorization: token %s"\n' "$token" > "$curl_config"
curl --fail --silent --show-error --cacert "$runtime/tls/ca.crt" --config "$curl_config" --request PATCH \
  --header 'Content-Type: application/json' \
  --data "{\"source_id\":$source_id,\"login_name\":\"$subject\"}" \
  --output "$response" http://legacy-gitea:3000/api/v1/admin/users/admin
user_id=$(sed -n 's/.*"id"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$response" | head -n 1)
returned_source_id=$(sed -n 's/.*"source_id"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$response" | head -n 1)
login_name=$(sed -n 's/.*"login_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$response" | head -n 1)
case "$user_id" in ''|*[!0-9]*) exit 1 ;; esac
test "$user_id" -gt 0
test "$returned_source_id" = "$source_id"
test "$login_name" = "$subject"
grep -Eq '"is_admin"[[:space:]]*:[[:space:]]*true' "$response"

umask 077
temporary=$(mktemp "$state/.gitea-identity.XXXXXX")
printf '{"generation":"%s","subject":"%s","source_id":%s,"user_id":%s}' \
  "$generation" "$subject" "$source_id" "$user_id" > "$temporary"
mv -f "$temporary" "$state/gitea-identity.json"
printf '%s' "$generation" > "$state/ready/.gitea.$$"
mv -f "$state/ready/.gitea.$$" "$state/ready/gitea"
