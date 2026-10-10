#!/bin/sh
set -eu

case "${BOOKSTACK_BOOTSTRAP_CONFIRMED:-false}" in
    true)
        route=enabled
        ;;
    false)
        route=disabled
        ;;
    *)
        echo "BOOKSTACK_BOOTSTRAP_CONFIRMED must be true or false." >&2
        exit 1
        ;;
esac

cp "/etc/nginx/templates/bookstack-route-${route}.conf" /etc/nginx/routes/bookstack.conf
cp "/etc/nginx/templates/bookstack-root-${route}.conf" /etc/nginx/routes/bookstack-root.conf
