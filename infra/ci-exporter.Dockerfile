FROM docker.io/library/python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

WORKDIR /opt
COPY --chown=65532:65532 ci/__init__.py ci/log_exporter.py ci/retention.py ci/prepare_exporter_state.py /opt/ci/
RUN mkdir -p /var/lib/ci-log-exporter && chown 65532:65532 /var/lib/ci-log-exporter
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "ci.log_exporter"]
