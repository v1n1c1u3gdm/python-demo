FROM docker.io/woodpeckerci/woodpecker-server:v3.18.0@sha256:5192aee400df23671de8ddffb906670e93d07ae8967c7f9e50efefca3a2deea5 AS upstream

FROM docker.io/library/alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8
RUN addgroup -S -g 10001 woodpecker \
    && adduser -S -D -H -u 10001 -G woodpecker woodpecker \
    && mkdir -p /var/lib/woodpecker \
    && chown woodpecker:woodpecker /var/lib/woodpecker
COPY --from=upstream /bin/woodpecker-server /bin/woodpecker-server
COPY --chmod=0755 infra/local/woodpecker-start.sh /usr/local/bin/woodpecker-start.sh
USER woodpecker
ENTRYPOINT ["/usr/local/bin/woodpecker-start.sh"]
