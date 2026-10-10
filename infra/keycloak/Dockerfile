FROM quay.io/keycloak/keycloak:26.4.7@sha256:9409c59bdfb65dbffa20b11e6f18b8abb9281d480c7ca402f51ed3d5977e6007

COPY --chown=keycloak:keycloak infra/keycloak/start-with-secrets.sh /opt/keycloak/bin/start-with-secrets.sh
ENTRYPOINT ["/opt/keycloak/bin/start-with-secrets.sh"]
CMD ["start"]
