#!/bin/sh
set -eu

test -s /run/local-secrets/tls/ca.crt
install -m 0644 /run/local-secrets/tls/ca.crt /usr/local/share/ca-certificates/python-demo-local.crt
update-ca-certificates >/dev/null
exec /usr/bin/entrypoint "$@"
