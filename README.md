# Python Demo (Flask 3 API + MySQL)

Aplicação com uma API Flask 3 rodando sobre **python:3.14.7-slim**, servida pelo Gunicorn e usando MySQL
containerizado. A raiz está organizada em dois módulos:

- `api/` – API Flask modular (blueprints) com SQLAlchemy 2, Flask-Migrate, seeds determinísticos e documentação Swagger.
- `ui/` – front-end Vue 3 + Bootstrap 4, com Vite, Vitest e Vue Test Utils 2 (cobertura mínima de linhas de 85%)
  consumindo os mesmos endpoints.

## Arquitetura & Tecnologias

- **API (`api/`)**: Flask 3.1 + Gunicorn, SQLAlchemy 2 com PyMySQL, migrations Alembic via Flask-Migrate, seeds
  idempotentes a partir de `seeds/article_seed_data.json` e documentação Swagger reutilizando `swagger/v1/swagger.yaml`.
  A instrumentação usa `opentelemetry-sdk` + `opentelemetry-instrumentation-flask` para expor `GET /metrics` em
  formato Prometheus/OpenMetrics. Logs HTTP e SQLAlchemy são gravados em `api/logs/`.
- **UI (`ui/`)**: Vue 3 com Vite e Bootstrap 4, Build multi-stage (Node → NGINX) compartilhando o mesmo `Dockerfile`.
  Testes unitários com Vitest + Vue Test Utils 2 garantindo ≥85% de cobertura de linhas.
- **Banco (serviço `db`)**: MySQL 8.4 em container dedicado com volume `mysql_data` e credenciais fixas (`ruby-demo` /
  `2u8y-c0d3`).
- **Identidade (`keycloak/`)**: Keycloak 26.4.7 sobe via Docker com realm importado automaticamente, dois papéis
  (`admin`, `author`) e usuários seeded (`admin`, `vinicius`).
- **Orquestração**: `docker-compose.yml` define `api`, `ui`, `db` e `keycloak`, injeta `DATABASE_URL`, `VINICIUS_PUBLIC_KEY`,
  `LOG_DIR`, `FLASK_ENV` e garante que migrations + seeds executem automaticamente no primeiro boot.

## Stack

- Python 3.14.7 (venv + pip)
- Flask 3.1 + Gunicorn
- SQLAlchemy 2.x + Flask-Migrate
- MySQL 8.4 (dockerizado)
- OpenTelemetry SDK + Prometheus/OpenMetrics
- Vue 3, Bootstrap 4, Vite, Vitest, Vue Test Utils 2
- Node 24 para build/testes e tooling Markdown
- Docker 24 + Docker Compose v2

## Endpoints principais

- `GET /api-docs` – Swagger UI servida pelo `flask-swagger-ui`.
- `GET /openapi.yaml` – Spec OpenAPI 3.0.
- CRUD completo para `/authors`, `/articles`, `/socials` (payloads com root keys `author`, `article`, `social`) e
  `/articles/count_by_author`.
- `GET /liveness` – healthcheck com status e timestamp.
- `GET /metrics` – counters/latency/liveness em OpenMetrics.
- `GET /tech` – relatório HTML (“tabelaço”) com host/runtime/banco/config/env/pacotes/licenças.
- `GET /` – redirect para `/api-docs`.
- `POST /login` – proxy para o Keycloak (Resource Owner Password) retornando tokens + roles.
- `GET /admin/profile` – endpoint protegido que exige o role `admin`.

## Como iniciar com Docker

### Pré-requisitos

- Docker 24+ e Docker Compose v2.
- Portas livres: 3000 (API), 3306 (MySQL) e 8080 (UI).
- Porta 8081 para o Keycloak e sua console administrativa.

### Passo a passo rápido

```bash
docker compose build
docker compose up
```

- O estágio `api-app` do `Dockerfile` gera a imagem da API (python:3.14.7-slim + Gunicorn).
- As migrations Flask-Migrate e os seeds são executados automaticamente no startup do container `api`.
- A UI depende do início do container da API e fica disponível em `http://localhost:8080/`; essa dependência não
  verifica a saúde da API.
- Swagger continua em `http://localhost:3000/api-docs`.
- Keycloak fica disponível em `http://localhost:8081/` (Admin Console) com `KEYCLOAK_ADMIN=admin` e
  `KEYCLOAK_ADMIN_PASSWORD=admin!123`. A importação do realm (`keycloak/realm-python-demo.json`) ocorre no primeiro
  boot.

Na validação da atualização, o startup em banco vazio apresentou concorrência entre os quatro workers ao aplicar
migrations. O startup com quatro workers passou no banco já migrado. A coordenação do bootstrap para banco vazio ou
novas migrations continua pendente de decisão arquitetural.

### Variáveis relevantes

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `DATABASE_URL` | `mysql+pymysql://ruby-demo:2u8y-c0d3@db:3306/ruby_demo_development` | DSN SQLAlchemy utilizado pela API. |
| `VINICIUS_PUBLIC_KEY` | chave fake usada no seed | Pode ser trocada para regenerar os dados seeded. |
| `LOG_DIR` | `/app/api/logs` | Diretório de `app.log` e `sqlalchemy.log`. |
| `KEYCLOAK_BASE_URL` | `http://keycloak:8080` | Host usado pela API para conversar com o Keycloak. |
| `KEYCLOAK_REALM` | `python-demo` | Realm importado a partir de `keycloak/realm-python-demo.json`. |
| `KEYCLOAK_CLIENT_ID` | `python-demo-api` | Client confidencial usado no fluxo de senha. |
| `KEYCLOAK_CLIENT_SECRET` | `python-demo-api-secret` | Segredo do client confidencial. |
| `KEYCLOAK_ADMIN_ROLE` | `admin` | Papel necessário para acessar `/admin/profile`. |

### Autenticação Keycloak & área `/admin`

- Usuários seeded:
  - `john.doe` / `john.doe!123` – John Doe, perfil completo para testes, roles `admin` e `author`.
  - `admin` / `admin!123` – roles `admin` e `author`.
  - `vinicius` / `vinicius!123` – role `author`.
- Console administrativa: `http://localhost:8081/` (usar `admin` / `admin!123` configurado via `KEYCLOAK_ADMIN*`).
- Fluxo de login:
  1. UI em `http://localhost:8080/admin` envia `username` + `password` para `POST http://localhost:3000/login`.
  2. A API repassa as credenciais ao Keycloak (`Resource Owner Password Credentials`) e retorna `access_token`,
     `refresh_token`, `roles`, `expires_in`.
  3. O token fica salvo em `sessionStorage` e pode ser usado para bater em `GET http://localhost:3000/admin/profile`,
     que só responde para quem possui o role `admin`.

As contas anteriores `admin` e `vinicius` ainda não têm `firstName` e `lastName`, exigidos pelo perfil padrão do
Keycloak 26.4.7. Nessas condições, o login de demonstração retorna `Account is not fully set up`.
O usuário de testes `john.doe` tem perfil completo para validar o fluxo. As contas anteriores continuam pendentes
de correção.

- Para testar manualmente com uma conta de perfil completo:

  ```bash
  curl -s -X POST http://localhost:3000/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"john.doe","password":"john.doe!123"}' | jq .

  curl -s http://localhost:3000/admin/profile \
    -H "Authorization: Bearer <access_token>" | jq .
  ```

Se quiser ajustar o realm, edite `keycloak/realm-python-demo.json` e recomece o container `keycloak` com
`docker compose up -d --force-recreate keycloak`.

## Desenvolvimento fora do Docker

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=mysql+pymysql://ruby-demo:2u8y-c0d3@127.0.0.1:3306/ruby_demo_development
gunicorn -b 0.0.0.0:3000 app:app
```

- O bootstrap do `app.py` executa migrations automaticamente; para rodá-las manualmente use `flask db upgrade`.
- Seeds podem ser disparados manualmente abrindo um shell Flask (`flask --app app.py shell`) e executando
  `from seeds import bootstrap_seed_data; bootstrap_seed_data()`.

## Testes automatizados

- **API**: instale `requirements-dev.txt` e execute
  `python -m pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=85` dentro de `api/`. A
  configuração coleta somente linhas do código de produção, incluindo migrations; testes são excluídos da medição.
- **UI**: `cd ui && npm ci && npm run test:unit` (Vitest + Vue Test Utils 2, com threshold de linhas ≥85%).

## UI (Vue 3 + Vite)

```bash
cd ui
npm ci               # primeira execução
npm run serve        # dev server em http://localhost:5173
npm run build        # gera dist/ usada pelo estágio NGINX
```

Os serviços auxiliares (`articlesService`, `authorsService`, `socialsService`) continuam apontando para
`http://localhost:3000`, então nenhuma configuração adicional é necessária para consumir a API Flask.

## Linters

```bash
# Na raiz: documentação, incluindo planos locais em docs/
npm ci
npm run lint:md

# Na API, com requirements-dev.txt instalado
cd api
ruff check .

# Na UI
cd ../ui
npm run lint -- --no-fix
```

Markdown usa limite de 120 colunas em prosa; tabelas e blocos de código preservam
a largura necessária para leitura. Cabeçalhos iguais são permitidos sob pais
diferentes, como as categorias do changelog.

## Variáveis do build da UI

Vite usa `import.meta.env.VITE_*`. As variáveis são resolvidas no build,
e as URLs precisam ser acessíveis pelo navegador. `http://api:3000` é um
hostname interno do Compose, inadequado como endereço público da SPA.

| Variável | Valor padrão |
| --- | --- |
| `VITE_API_BASE_URL` | `http://localhost:3000` |
| `VITE_ARTICLES_URL` | `http://localhost:3000/articles` |
| `VITE_AUTHORS_URL` | `http://localhost:3000/authors` |
| `VITE_ARTICLES_COUNT_URL` | `http://localhost:3000/articles/count_by_author` |
| `VITE_SOCIALS_URL` | `http://localhost:3000/socials` |
| `VITE_ARTICLE_PUBLIC_BASE_URL` | `https://viniciusmenezes.com` |

Para Docker, sobrescreva as variáveis com argumentos de build, por exemplo:

```bash
docker compose build --build-arg VITE_API_BASE_URL=https://api.example.com ui
```

Esse argumento configura autenticação; os endpoints de conteúdo têm argumentos
próprios e devem ser configurados juntos quando a API estiver em outro host.

A estrutura e o escopo dos módulos estão em [ARCHITECTURE.md](ARCHITECTURE.md);
as decisões cumulativas ficam em [adrs/](adrs/). Planos e specs em `docs/`
são locais e não devem ser versionados.
