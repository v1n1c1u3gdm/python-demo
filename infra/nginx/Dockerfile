FROM nginx:stable-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94

COPY infra/nginx/gateway.conf /etc/nginx/conf.d/default.conf
COPY infra/nginx/bookstack-route-disabled.conf /etc/nginx/templates/bookstack-route-disabled.conf
COPY infra/nginx/bookstack-route-enabled.conf /etc/nginx/templates/bookstack-route-enabled.conf
COPY infra/nginx/bookstack-root-disabled.conf /etc/nginx/templates/bookstack-root-disabled.conf
COPY infra/nginx/bookstack-root-enabled.conf /etc/nginx/templates/bookstack-root-enabled.conf
COPY infra/nginx/40-bookstack-route.sh /docker-entrypoint.d/40-bookstack-route.sh
