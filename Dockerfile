FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS api-app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_ENV=production

WORKDIR /app/api

COPY api/requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY api/ .
COPY ci/stack_config.py /opt/python-demo/stack_config.py

RUN mkdir -p /app/api/logs

EXPOSE 3000
CMD ["/app/api/scripts/start-gunicorn.sh", "--config", "/app/api/gunicorn.conf.py", "-b", "0.0.0.0:3000", "-w", "4", "--threads", "4", "app:app"]

# Local bootstrap reuses the API's hash-locked cryptography and requests dependencies.
FROM api-app AS local-bootstrap
COPY ci/__init__.py ci/local_bootstrap.py ci/local_keycloak.py ci/local_http.py \
     ci/local_bookstack.py ci/local_gitea.py ci/local_woodpecker.py /opt/python-demo/ci/
ENV PYTHONPATH=/opt/python-demo

# -------- Vue build stage --------
FROM node:24.11.1-alpine3.22@sha256:2867d550cf9d8bb50059a0fff528741f11a84d985c732e60e19e8e75c7239c43 AS ui-build
WORKDIR /app
COPY ui/package*.json ./
RUN npm ci
COPY ui/ .
ARG VITE_API_BASE_URL=http://localhost:3000
ARG VITE_ARTICLES_URL=http://localhost:3000/articles
ARG VITE_AUTHORS_URL=http://localhost:3000/authors
ARG VITE_ARTICLES_COUNT_URL=http://localhost:3000/articles/count_by_author
ARG VITE_SOCIALS_URL=http://localhost:3000/socials
ARG VITE_ARTICLE_PUBLIC_BASE_URL=https://viniciusmenezes.com
ARG VITE_SSO_ENABLED=false
ARG VITE_KEYCLOAK_URL=
ARG VITE_KEYCLOAK_REALM=
ARG VITE_KEYCLOAK_CLIENT_ID=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_ARTICLES_URL=${VITE_ARTICLES_URL} \
    VITE_AUTHORS_URL=${VITE_AUTHORS_URL} \
    VITE_ARTICLES_COUNT_URL=${VITE_ARTICLES_COUNT_URL} \
    VITE_SOCIALS_URL=${VITE_SOCIALS_URL} \
    VITE_ARTICLE_PUBLIC_BASE_URL=${VITE_ARTICLE_PUBLIC_BASE_URL} \
    VITE_SSO_ENABLED=${VITE_SSO_ENABLED} \
    VITE_KEYCLOAK_URL=${VITE_KEYCLOAK_URL} \
    VITE_KEYCLOAK_REALM=${VITE_KEYCLOAK_REALM} \
    VITE_KEYCLOAK_CLIENT_ID=${VITE_KEYCLOAK_CLIENT_ID}
RUN npm run build

# -------- Vue runtime stage --------
FROM nginx:stable-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94 AS ui-app
COPY --from=ui-build /app/dist /usr/share/nginx/html
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
