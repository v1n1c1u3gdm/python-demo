FROM python:3.14.0-slim AS api-app

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
FROM node:lts-alpine AS ui-build
WORKDIR /app
COPY ui/package*.json ./
RUN npm install
COPY ui/ .
ARG VUE_APP_ARTICLES_URL=http://localhost:3000/articles
ENV VUE_APP_ARTICLES_URL=${VUE_APP_ARTICLES_URL}
RUN npm run build

# -------- Vue runtime stage --------
FROM nginx:stable-alpine AS ui-app
COPY --from=ui-build /app/dist /usr/share/nginx/html
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
