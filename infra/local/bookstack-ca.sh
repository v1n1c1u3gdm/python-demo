#!/usr/bin/with-contenv bash
set -Eeuo pipefail

ca=/run/local-secrets/tls/ca.crt
test -s "$ca"
install -m 0644 "$ca" /usr/local/share/ca-certificates/python-demo-local.crt
update-ca-certificates >/dev/null
