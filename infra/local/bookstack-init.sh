#!/usr/bin/with-contenv bash
set -Eeuo pipefail

state=/run/local-state
intent="$state/bookstack-oidc.json"
generation=$(tr -d '\n' < "$state/bootstrap-generation")
intent_generation=$(jq -er '.generation' "$intent")
subject=$(jq -er '.subject' "$intent")
[[ "$generation" =~ ^[a-f0-9]{32}$ && "$intent_generation" == "$generation" ]]
[[ "$subject" =~ ^[A-Za-z0-9._:-]{1,255}$ ]]

for path in /run/local-secrets/bookstack_app_key /run/local-secrets/bookstack_client_secret \
  /run/local-secrets/tls/ca.crt; do
  test -s "$path"
done

cd /app/www
set +e
/usr/bin/s6-setuidgid abc /usr/bin/php artisan bookstack:create-admin --initial --email=admin@app.localhost \
  --name='Local Administrator' --external-auth-id="$subject"
result=$?
set -e
if [ "$result" -ne 0 ] && [ "$result" -ne 2 ]; then
  printf 'BookStack initial administrator provisioning failed.\n' >&2
  exit "$result"
fi

# Use BookStack's application repository/model layer to verify its persisted
# admin role and exact OIDC external ID; exit 2 is accepted only for that state.
HOME=/tmp BOOKSTACK_ADMIN_SUBJECT="$subject" BOOKSTACK_ADMIN_GROUP="local-admins" \
  /usr/bin/s6-setuidgid abc /usr/bin/php artisan tinker --execute='
$user = app(\BookStack\Users\UserRepo::class)->getByEmail("admin@app.localhost");
$default = app(\BookStack\Users\UserRepo::class)->getByEmail("admin@admin.com");
$role = \BookStack\Users\Models\Role::getSystemRole("admin");
if (!$user || !$user->hasSystemRole("admin") || $user->external_auth_id !== getenv("BOOKSTACK_ADMIN_SUBJECT") || $default || !$role) { exit(1); }
$role->external_auth_id = getenv("BOOKSTACK_ADMIN_GROUP");
$role->save();
$role = \BookStack\Users\Models\Role::getSystemRole("admin");
if (!$role || $role->external_auth_id !== getenv("BOOKSTACK_ADMIN_GROUP")) { exit(1); }
'

# The command is an application-supported initial-admin upsert/skip operation.
# Verify the persisted intent still matches this exact startup before opening the route.
current=$(tr -d '\n' < "$state/bootstrap-generation")
test "$current" = "$generation"
test "$(jq -er '.subject' "$intent")" = "$subject"
umask 077
temporary=$(mktemp "$state/ready/.bookstack.XXXXXX")
trap 'rm -f "$temporary"' EXIT
printf '%s' "$generation" > "$temporary"
mv -f "$temporary" "$state/ready/bookstack"
trap - EXIT
