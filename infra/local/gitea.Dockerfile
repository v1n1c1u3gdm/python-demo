FROM docker.gitea.com/gitea:1.27.3@sha256:d584940b7143982682ac4541509dd628506c2b568221e6af41e5a2ead1de4fe7

USER root
COPY infra/local/gitea-init.sh /usr/local/bin/gitea-init.sh
COPY infra/local/gitea-start.sh /usr/local/bin/gitea-start.sh
RUN chmod 0755 /usr/local/bin/gitea-init.sh /usr/local/bin/gitea-start.sh
ENTRYPOINT ["/usr/local/bin/gitea-start.sh"]
