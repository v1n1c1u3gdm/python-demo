# Python Demo (Flask 3 API + MySQL)

Repositório inaugurado com uma API Flask 3 rodando sobre **python:3.14.0-slim**, servida pelo Gunicorn e usando MySQL containerizado. A raiz está organizada em dois módulos:

- `api/` – API Flask modular (blueprints) com SQLAlchemy 2, Flask-Migrate, seeds determinísticos e documentação Swagger.
- `ui/` – front-end Vue 2 + BootstrapVue, com Jest e Vue Test Utils (cobertura mínima de 85%) consumindo exatamente os mesmos endpoints.

## Arquitetura & Tecnologias

- **API (`api/`)**: Flask 3.0 + Gunicorn, SQLAlchemy 2 com PyMySQL, migrations Alembic via Flask-Migrate, seeds idempotentes a partir de `seeds/article_seed_data.json` e documentação Swagger reutilizando `swagger/v1/swagger.yaml`. A instrumentação usa `opentelemetry-sdk` + `opentelemetry-instrumentation-flask` para expor `GET /metrics` em formato Prometheus/OpenMetrics. Logs HTTP e SQLAlchemy são gravados em `api/logs/`.
- **UI (`ui/`)**: Vue 2 (Vue CLI) com BootstrapVue, Build multi-stage (Node → NGINX) compartilhando o mesmo `Dockerfile`. Testes unitários com Jest + Vue Test Utils garantindo ≥85% de cobertura.
- **Banco (`db/`)**: MySQL 8.4 em container dedicado com volume `mysql_data` e credenciais fixas (`ruby-demo` / `2u8y-c0d3`).
- **Orquestração**: `docker-compose.yml` define `api`, `ui` e `db`, injeta `DATABASE_URL`, `VINICIUS_PUBLIC_KEY`, `LOG_DIR`, `FLASK_ENV` e garante que migrations + seeds executem automaticamente no primeiro boot.

## Stack

- Python 3.14.0 (venv + pip)
- Flask 3.0 + Gunicorn
- SQLAlchemy 2.x + Flask-Migrate
- MySQL 8.4 (dockerizado)
- OpenTelemetry SDK + Prometheus/OpenMetrics
- Vue 2, BootstrapVue, Jest, Vue Test Utils
- Docker 24 + Docker Compose v2

## Endpoints principais

- `GET /api-docs` – Swagger UI servida pelo `flask-swagger-ui`.
- `GET /openapi.yaml` – Spec OpenAPI 3.0.
- CRUD completo para `/authors`, `/articles`, `/socials` (payloads com root keys `author`, `article`, `social`) e `/articles/count_by_author`.
- `GET /liveness` – healthcheck com status e timestamp.
- `GET /metrics` – counters/latency/liveness em OpenMetrics.
- `GET /tech` – relatório HTML (“tabelaço”) com host/runtime/banco/config/env/pacotes/licenças.
- `GET /` – redirect para `/api-docs`.

## Como iniciar com Docker

### Pré-requisitos

- Docker 24+ e Docker Compose v2.
- Portas livres: 3000 (API), 3306 (MySQL) e 8080 (UI).

### Passo a passo rápido

```bash
docker compose build
docker compose up
```

- O estágio `api-app` do `Dockerfile` gera a imagem da API (python:3.14.0-slim + Gunicorn).
- As migrations Flask-Migrate e os seeds são executados automaticamente no startup do container `api`.
- A UI sobe após a API estar pronta e fica disponível em `http://localhost:8080/`.
- Swagger continua em `http://localhost:3000/api-docs`.

### Variáveis relevantes

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `DATABASE_URL` | `mysql+pymysql://ruby-demo:2u8y-c0d3@db:3306/ruby_demo_development` | DSN SQLAlchemy utilizado pela API. |
| `VINICIUS_PUBLIC_KEY` | chave fake usada no seed | Pode ser trocada para regenerar os dados seeded. |
| `LOG_DIR` | `/app/api/logs` | Diretório de `app.log` e `sqlalchemy.log`. |

## Desenvolvimento fora do Docker

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=mysql+pymysql://ruby-demo:2u8y-c0d3@127.0.0.1:3306/ruby_demo_development
gunicorn -b 0.0.0.0:3000 app:app
```

- O bootstrap do `app.py` executa migrations automaticamente; para rodá-las manualmente use `flask db upgrade`.
- Seeds podem ser disparados manualmente abrindo um shell Flask (`flask --app app.py shell`) e executando `from seeds import bootstrap_seed_data; bootstrap_seed_data()`.

## Testes automatizados

- **API**: rode `pytest` dentro de `api/` (também disponível via `docker compose run --rm api pytest`).
- **UI**: `cd ui && npm install && npm run test:unit` (Jest + Vue Test Utils com threshold ≥85%).

## UI (Vue + BootstrapVue)

```bash
cd ui
npm install          # primeira execução
npm run serve        # dev server em http://localhost:8080
npm run build        # gera dist/ usada pelo estágio NGINX
```

Os serviços auxiliares (`articlesService`, `authorsService`, `socialsService`) continuam apontando para `http://localhost:3000`, então nenhuma configuração adicional é necessária para consumir a API Flask.
