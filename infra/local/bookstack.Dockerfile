FROM lscr.io/linuxserver/bookstack:version-v26.09.1@sha256:9b5cd22653b503a53a764671ed96e2e87ea3f62bc3fba6250de86a73d642c426

COPY infra/local/bookstack-init.sh /usr/local/bin/bookstack-init.sh
COPY infra/local/bookstack-ca.sh /custom-cont-init.d/10-local-ca
RUN chmod 0755 /usr/local/bin/bookstack-init.sh /custom-cont-init.d/10-local-ca
