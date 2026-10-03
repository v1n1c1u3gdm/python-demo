FROM python:3.14.7-slim AS api-app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_ENV=production

WORKDIR /app/api

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
      build-essential \
      curl \
      git \
      libffi-dev \
      libssl-dev && \
    rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/ .

RUN mkdir -p /app/api/logs

EXPOSE 3000
CMD ["gunicorn", "-b", "0.0.0.0:3000", "-w", "4", "--threads", "4", "app:app"]

# -------- Vue build stage --------
FROM node:24-alpine AS ui-build
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
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_ARTICLES_URL=${VITE_ARTICLES_URL} \
    VITE_AUTHORS_URL=${VITE_AUTHORS_URL} \
    VITE_ARTICLES_COUNT_URL=${VITE_ARTICLES_COUNT_URL} \
    VITE_SOCIALS_URL=${VITE_SOCIALS_URL} \
    VITE_ARTICLE_PUBLIC_BASE_URL=${VITE_ARTICLE_PUBLIC_BASE_URL}
RUN npm run build

# -------- Vue runtime stage --------
FROM nginx:stable-alpine AS ui-app
COPY --from=ui-build /app/dist /usr/share/nginx/html
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
