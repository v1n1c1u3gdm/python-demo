FROM nginx:stable-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94

COPY infra/local/gateway.conf /etc/nginx/conf.d/default.conf
COPY infra/local/watch-routes.sh /usr/local/bin/watch-routes.sh
COPY ci/proxy/woodpecker.conf /usr/local/share/woodpecker.conf
RUN chmod 0755 /usr/local/bin/watch-routes.sh
CMD ["/usr/local/bin/watch-routes.sh"]
