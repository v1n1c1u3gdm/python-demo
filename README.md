# Python Demo (Flask API + MySQL)

O repositório foi recriado mantendo o mesmo contrato da aplicação Ruby original, mas agora a API é escrita em **Flask 3 + SQLAlchemy 2**, servida pelo **Gunicorn** e containerizada sobre **python:3.14.0-slim**. A raiz continua dividida em dois módulos:

- `api/` – API Flask em modo modular (blueprints) com SQLAlchemy, Flask-Migrate, seeds determinísticos, OpenTelemetry e documentação Swagger.
- `ui/` – front-end Vue 2 + BootstrapVue, com Jest e Vue Test Utils (cobertura mínima de 85%) consumindo exatamente os mesmos endpoints.

## Arquitetura & Tecnologias

- **API (`api/`)**
  - Flask 3.0, Gunicorn, SQLAlchemy 2, Flask-Migrate/Alembic e PyMySQL.
  - Organização por blueprints: autores, artigos (com `/articles/count_by_author`), redes sociais, `/tech`, `/liveness`, `/metrics` e `/api-docs`.
  - Seeds idempotentes replicam o mesmo conteúdo do projeto Ruby (autores, sociais e artigos) a partir de `seeds/article_seed_data.json`.
  - OpenTelemetry SDK + `opentelemetry-instrumentation-flask` expõem métricas em formato Prometheus (contadores de requisições, duração acumulada e gauge de liveness).
  - Tech report em `/tech` renderiza o “tabelaço” com host/runtime/banco/config/env/pacotes/licença.
  - Logs centralizados em `api/logs/` (middleware HTTP + SQLAlchemy) e expostos também via Docker bind.

- **UI (`ui/`)**
  - Vue 2 (Vue CLI) + BootstrapVue, mesma codebase que já consome `/authors`, `/articles`, `/socials` e `/articles/count_by_author`.
  - Testes unitários com Jest (threshold ≥ 85%) em `ui/tests/unit`.
  - Build multi-stage (Node → NGINX) reutilizando o mesmo `Dockerfile`.

- **Banco (`db`)**
  - MySQL 8.4 containerizado, credenciais fixas (`ruby-demo` / `2u8y-c0d3`) e volume persistente `mysql_data`.

- **Orquestração**
  - `docker-compose.yml` compõe `api` (Gunicorn), `ui` (NGINX com bundle Vue) e `db`.
  - A API sobe executando migrations e seeds automaticamente no primeiro boot (via `app.py`).
  - Variáveis como `DATABASE_URL`, `VINICIUS_PUBLIC_KEY`, `LOG_DIR` e `FLASK_ENV` são injetadas pelo Compose.

## Endpoints principais

- `GET /api-docs` – Swagger UI (servido pelo `flask-swagger-ui` reaproveitando `swagger/v1/swagger.yaml`).
- `GET /openapi.yaml` – Spec OpenAPI 3.0.
- CRUD completo para `/authors`, `/articles`, `/socials` (payloads com root keys `author`, `article`, `social`), incluindo `/articles/count_by_author`.
- `GET /liveness` – resposta JSON com status e timestamp (para healthchecks).
- `GET /metrics` – payload OpenMetrics/Prometheus com counters/durations/liveness.
- `GET /tech` – diagnóstico HTML completo (tabelaço).
- `GET /` – redirect padrão para `/api-docs`.

## Como subir via Docker

### Pré-requisitos
- Docker 24+ e Docker Compose v2.
- Portas livres: 3000 (API), 3306 (MySQL) e 8080 (UI).

### Passos
```bash
docker compose build
docker compose up
```

- A imagem `api` usa o estágio `api-app` do `Dockerfile` (python:3.14.0-slim + Gunicorn).
- As migrations do Flask-Migrate e os seeds são executados automaticamente no startup (não é necessário rodar comandos extras).
- A UI sobe após a API estar pronta e fica disponível em `http://localhost:8080/`.
- A documentação/Swagger continua em `http://localhost:3000/api-docs`.

### Variáveis relevantes
| Variável | Padrão | Descrição |
| --- | --- | --- |
| `DATABASE_URL` | `mysql+pymysql://ruby-demo:2u8y-c0d3@db:3306/ruby_demo_development` | String SQLAlchemy para MySQL. |
| `VINICIUS_PUBLIC_KEY` | chave fake usada no seed | Pode ser sobrescrita para rodar seeds com outra chave. |
| `LOG_DIR` | `/app/api/logs` | Diretório onde `app.log` e `sqlalchemy.log` são gravados. |

## Desenvolvimento local (fora do Docker)

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=mysql+pymysql://ruby-demo:2u8y-c0d3@127.0.0.1:3306/ruby_demo_development
flask --app app.py shell  # opcional
gunicorn -b 0.0.0.0:3000 app:app
```

- As migrations são executadas automaticamente no boot; para rodá-las manualmente use `flask db upgrade`.
- Seeds podem ser disparados manualmente em um shell Flask executando `from seeds import bootstrap_seed_data; bootstrap_seed_data()`.

## Testes

- **API**: a suíte Pytest (equivalente aos antigos specs em Rails) fica em `api/tests/` e roda com `pytest`. Também pode ser executada via Docker: `docker compose run --rm api pytest`.
- **UI**: permaneceu com Jest. Execute `cd ui && npm install && npm run test:unit`.

## UI (Vue + BootstrapVue)

O front continua igual ao projeto Ruby:

```bash
cd ui
npm install          # primeira execução
npm run serve        # dev server em http://localhost:8080
npm run build        # gera dist/ usada pelo estágio NGINX
```

Os serviços auxiliares (articlesService, authorsService, socialsService) continuam configurados para apontar para `http://localhost:3000`, então nenhuma mudança é necessária no front-end.