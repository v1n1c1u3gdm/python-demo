# Python Demo (Flask 3 API + MySQL)

A evolução planejada está em [ROADMAP.md](ROADMAP.md), com fases e critérios de conclusão.

Aplicação com uma API Flask 3 rodando sobre **python:3.14.7-slim**, servida pelo Gunicorn e usando MySQL
containerizado. A raiz está organizada em dois módulos:

- `api/` – API Flask modular (blueprints) com SQLAlchemy 2, Flask-Migrate, seeds determinísticos e documentação Swagger.
- `ui/` – front-end Vue 3 + Bootstrap 4, com Vite, Vitest e Vue Test Utils 2 (cobertura mínima de linhas de 85%)
  consumindo os mesmos endpoints.

## Ambiente local padrão com SSO

Na raiz do repositório, `docker compose up` inicia a stack local padrão definida em `compose.yaml`. O primeiro startup gera
configuração, certificados e segredos técnicos dentro de `infra/.local/` e `infra/secrets/local/`, provisiona o realm
`python-demo` e configura as integrações nativas. Esses arquivos persistem entre reinícios e não entram no Git.

- UI administrativa: <https://app.localhost/admin>
- Keycloak: <https://app.localhost/auth/>
- BookStack: <https://app.localhost/bookstack/>
- Gitea: <https://app.localhost/git/>
- Woodpecker: <https://app.localhost/ci/>

O único login humano local é `admin` / `admin!123`, tanto no Keycloak como nas integrações administrativas. A UI usa
Authorization Code com PKCE; BookStack e Gitea usam seus provedores OIDC nativos; Woodpecker autentica através da sessão
do Gitea. A CI mantém cadastro fechado e não inicia agente ou runner. O gateway publica apenas TLS em `127.0.0.1:443`;
API, bancos e consoles não publicam portas próprias no host.

O certificado é local e sua CA pública fica em `infra/.local/public/ca.crt`. A automação Playwright valida a CA e o nome
`app.localhost` com Node TLS estrito e permite no Chromium somente a SPKI do certificado observado. Isso não altera o
trust store do sistema nem ignora certificados de outros servidores. Para navegação manual, use um perfil de navegador
local se optar por confiar nessa CA; não instale a CA local como confiança global.

O estado dos serviços e dos inicializadores pode ser consultado sem imprimir segredos:

```sh
docker compose ps -a
docker compose logs --tail=100 local-init keycloak-init bookstack-prepare bookstack-init gitea-prepare gitea-init gitea-oauth-init ci-init app-init
```

Um inicializador com saída diferente de zero bloqueia as rotas protegidas da geração atual. Corrija a causa indicada pelo
serviço e execute `docker compose up -d` novamente. Para alterações de código ou imagens, use
`docker compose up --build -d`
desde a raiz, que reconcilia a stack completa e executa os inicializadores na ordem definida. Um simples
`docker compose restart <serviço>` reinicia somente o container indicado e não refaz o bootstrap nem as migrações.
`docker compose down` para o projeto mantendo os volumes e os arquivos de runtime; `docker compose up` inicia de novo com
a identidade persistida.

`docker compose -p python-demo-local down --volumes` remove somente os volumes do projeto padrão: MySQL da API/Keycloak,
MariaDB e estado persistente de BookStack, Gitea e Woodpecker. Isso apaga os dados locais desses serviços e o banco do
realm Keycloak. Os arquivos bind-mounted em `infra/.local/` e `infra/secrets/local/` permanecem; o próximo `up` recria o
realm e a identidade `admin` a partir deles, mas IDs internos podem mudar. Não há rotação parcial automática: trocar CA,
chaves ou segredos técnicos exige uma operação coordenada entre serviços, arquivos persistidos e dados associados. Faça
inventário e backup somente do projeto `python-demo-local` antes de qualquer reset completo; não remova diretórios locais
compartilhados nem recursos de outros projetos.

GitHub continua sendo a forge operacional existente. Gitea é a forge de desenvolvimento local conectada ao Keycloak;
Woodpecker local usa essa forge. Nada nesta configuração instala ou altera GitHub, o host, DNS global ou uma VPS.
A saída do botão de logout da UI encerra a sessão local da UI pelo adapter Keycloak; os cookies nativos de BookStack,
Gitea e Woodpecker são próprios e podem continuar ativos até o logout daquele serviço ou o encerramento do perfil do
navegador.

## Arquitetura & Tecnologias

- **API (`api/`)**: Flask 3.1 + Gunicorn, SQLAlchemy 2 com PyMySQL, migrations Alembic via Flask-Migrate, seeds
  idempotentes a partir de `seeds/article_seed_data.json` e documentação Swagger reutilizando `swagger/v1/swagger.yaml`.
  Métricas HTTP são expostas em formato Prometheus; a instrumentação OpenTelemetry pode exportar OTLP para o collector
  local opcional. O exportador fica desabilitado por padrão. Logs HTTP e SQLAlchemy são gravados em `api/logs/`.
- **UI (`ui/`)**: Vue 3 com Vite e Bootstrap 4, Build multi-stage (Node → NGINX) compartilhando o mesmo `Dockerfile`.
  Testes unitários com Vitest + Vue Test Utils 2 garantindo ≥85% de cobertura de linhas.
- **Banco portátil (`docker-compose.yml`, serviço `db`)**: MySQL 8.4 em container dedicado com volume `mysql_data`;
  essa stack não é o Compose local padrão.
- **Identidade portátil (`docker-compose.yml`)**: Keycloak 26.4.7 importa o realm de demonstração. A stack local
  padrão usa bootstrap próprio e somente a identidade humana `admin`.
- **Orquestração portátil**: `docker-compose.yml` mantém `api`, `ui`, `db` e `keycloak`. O Compose local padrão é
  `compose.yaml`, documentado acima.

## Stack

- Python 3.14.7 (venv + pip)
- Flask 3.1 + Gunicorn
- SQLAlchemy 2.x + Flask-Migrate
- MySQL 8.4 (dockerizado)
- OpenTelemetry SDK + Prometheus; collector OTLP local opcional no perfil Compose `telemetry`
- Vue 3, Bootstrap 4, Vite, Vitest, Vue Test Utils 2
- Node 24 para build/testes e tooling Markdown
- Docker 24 + Docker Compose v2

## Endpoints principais

- `GET /api-docs` – Swagger UI servida pelo `flask-swagger-ui`.
- `GET /openapi.yaml` – Spec OpenAPI 3.0.
- CRUD completo para `/authors`, `/articles`, `/socials` (payloads com root keys `author`, `article`, `social`) e
  `/articles/count_by_author`.
- `GET /up` e `GET /liveness` – liveness público, independente de MySQL, Keycloak e collector; ambos retornam somente
  `{"status":"ok"}`.
- `GET /ready` – readiness público: `200` com `{"status":"ok"}` quando MySQL e discovery Keycloak respondem dentro
  dos limites, ou `503` com `{"status":"unavailable"}`. O prazo global é três segundos e a capacidade de probes é limitada.
- `GET /metrics` – métricas Prometheus protegidas por Bearer com papel `admin`; rótulos usam template de rota e `404`
  estável para caminho não reconhecido.
- `GET /tech` – relatório HTML (“tabelaço”) com host/runtime/banco/config/env/pacotes/licenças.
- `GET /` – redirect para `/api-docs`.
- `POST /login` – proxy para o Keycloak (Resource Owner Password) retornando tokens + roles.
- `GET /admin/profile` – endpoint protegido que exige o role `admin`.

## Como iniciar com Docker

Esta seção descreve a stack portátil histórica de `docker-compose.yml`; para uso local autenticado, siga primeiro
[Ambiente local padrão com SSO](#ambiente-local-padr%C3%A3o-com-sso). Selecione explicitamente o arquivo portátil para
evitar iniciar a stack local padrão:

### Pré-requisitos

- Docker 24+ e Docker Compose v2.
- Portas livres: 3000 (API), 3306 (MySQL) e 8080 (UI).
- Porta 8081 para o Keycloak e sua console administrativa.

### Passo a passo rápido

```bash
docker compose -f docker-compose.yml up --build
```

- O serviço `api-init` aguarda o MySQL saudável e executa `flask bootstrap-db` uma vez antes dos quatro workers da API.
- Se migration ou seed falhar, o init termina com erro e a API nova não inicia.
- O serviço `api` usa a mesma imagem e configuração de build do `api-init`; ambos são definidos no `Dockerfile`.
- A UI depende do início do container da API e fica disponível em `http://localhost:8080/`; essa dependência não
  verifica a saúde da API.
- Swagger continua em `http://localhost:3000/api-docs`.
- Keycloak fica disponível em `http://localhost:8081/` (Admin Console) com `KEYCLOAK_ADMIN=admin` e
  `KEYCLOAK_ADMIN_PASSWORD=admin!123`. A importação do realm (`keycloak/realm-python-demo.json`) ocorre no primeiro
  boot.

O bootstrap coordena apenas o init de um projeto Compose. Não há exclusão entre diferentes projetos/hosts nem entre
execuções manuais concorrentes; mantenha essas execuções serializadas. O comando
`docker compose -f docker-compose.yml restart api` reinicia os workers sem recriar o init.

Para executar o bootstrap manualmente no Compose, use
`docker compose -f docker-compose.yml run --rm api-init flask bootstrap-db`; o serviço de init não recebe o diretório
multiprocess reservado à API Gunicorn.

Para habilitar exportação OTLP no ambiente local, defina a variável que ativa o exportador e inicie também o perfil do
collector:

```bash
OTEL_METRICS_ENABLED=true docker compose -f docker-compose.yml --profile telemetry up --build
```

O perfil inicia `otel-collector`, mas a exportação pela API continua desabilitada por padrão. O endpoint OTLP usa a
rede interna do Compose, sem porta publicada no host; o prazo de exportação é de dois segundos e o intervalo é de dez
segundos. A API e seus endpoints de saúde não dependem do collector. O teste Compose isolado confirmou recebimento OTLP
por workers distintos e serving durante a queda do collector; isso não comprova instalação ou capacidade na VPS. O
status local e os limites da validação constam no [roadmap](ROADMAP.md).

### Atualizar imagem ou schema

Para uma atualização que altere a imagem ou inclua migrations, pare primeiro a API para que nenhum worker use o schema
durante a mudança. Se a UI também precisar de imagem nova, pare e reconstrua-a junto:

```bash
set -e
docker compose -f docker-compose.yml stop api ui
docker compose -f docker-compose.yml build api
# Inclua ui se a atualização também mudar a imagem da UI:
# docker compose -f docker-compose.yml build api ui
docker compose -f docker-compose.yml up -d --force-recreate api-init
docker compose -f docker-compose.yml wait api-init
init_id="$(docker compose -f docker-compose.yml ps -aq api-init)"
test -n "$init_id"
init_exit_code="$(docker inspect --format='{{.State.ExitCode}}' "$init_id")"
test "$init_exit_code" -eq 0
docker compose -f docker-compose.yml up -d api ui
```

Só inicie API e UI depois de confirmar exit code 0 do init. Se esse gate falhar, confira
`docker compose -f docker-compose.yml logs api-init` e não inicie a API nova. `docker compose wait` aguarda o término; a
inspeção explícita confirma o sucesso. Faça o init em modo detached: não use
`docker compose -f docker-compose.yml up api-init` em primeiro plano com o banco já em execução, pois essa forma pode
tentar iniciar novamente as dependências. Não use `--no-deps` para contornar a ordem do Compose. Um `docker compose up
--build` normal também pode executar o init novamente, sequencialmente; migrations e seeds repetidos são
seguros.

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
| `KEYCLOAK_ISSUER` | deriva da URL local | Issuer exato esperado no token; em produção deve ser HTTPS e corresponder ao `iss`. A API precisa alcançar discovery/JWKS anunciados. |
| `KEYCLOAK_AUDIENCE` | `python-demo-api` | Audience que o access token deve conter. |
| `KEYCLOAK_ADMIN_ROLE` | `admin` | Papel necessário para acessar `/admin/profile`. |
| `KEYCLOAK_AUTHOR_ROLE` | `author` | Papel de autor para escrita nos próprios artigos, após vínculo privado. |
| `OTEL_METRICS_ENABLED` | `false` | Habilita exportação periódica de métricas OTLP da API; requer iniciar também o perfil Compose `telemetry`. |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | `http://otel-collector:4318/v1/metrics` | Endpoint HTTP interno do collector no Compose. |
| `OTEL_EXPORT_INTERVAL_MS` | `10000` | Intervalo de exportação OTLP em milissegundos. |
| `OTEL_EXPORT_TIMEOUT_SECONDS` | `2` | Timeout máximo de exportação OTLP. |

### Vínculo de autor e configuração de produção (Fase 1B)

Em produção, `FLASK_ENV=production` requer `DATABASE_URL`, `KEYCLOAK_BASE_URL`, `KEYCLOAK_ISSUER`, `KEYCLOAK_REALM`,
`KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET` e `KEYCLOAK_AUDIENCE`, sem credenciais demonstrativas. Papéis
`KEYCLOAK_ADMIN_ROLE` e `KEYCLOAK_AUTHOR_ROLE` são configuráveis e têm defaults. A imagem da API define
`FLASK_ENV=production`; para executar fora dela, configure esse ambiente explicitamente. `KEYCLOAK_BASE_URL` é o
endereço usado pela API no discovery/OpenID; `KEYCLOAK_ISSUER` deve corresponder exatamente ao `iss` do token,
normalmente uma URL HTTPS pública. Os endpoints anunciados por
discovery/JWKS precisam ser alcançáveis pela API. A integração isolada validou `KC_HOSTNAME` público junto de
`KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`, com chamadas API→Keycloak pela rede interna e issuer HTTPS público; essa
configuração de teste não é um deploy de produção.

Tokens do cliente `python-demo-api` devem incluir essa audience no access token. Realm novo recebe o mapper do arquivo
[`keycloak/realm-python-demo.json`](keycloak/realm-python-demo.json) durante a importação inicial. A importação não
atualiza realms que já existem. Para um realm existente, no Admin Console abra **Clients → python-demo-api → Client
scopes → python-demo-api-dedicated → Add mapper → By configuration → Audience**; defina `Included Client Audience` como
`python-demo-api`, habilite inclusão no access token e deixe desabilitada no ID token. Salve o mapper sem apagar realm,
usuários ou volume. Confirme que novo access token contém `aud: python-demo-api` antes de habilitar clientes.

Leituras de conteúdo permanecem públicas. Escritas em autores, perfis sociais e relatório técnico exigem Bearer com
papel `admin`; artigos aceitam admin ou papel `author` com identidade previamente vinculada. Um admin vincula um
autor existente por `PUT /authors/{id}/identity` com `{ "identity": { "issuer": "<iss exato>", "sub": "<sub>" } }`;
`DELETE` no mesmo recurso revoga o vínculo. Esses campos não aparecem no modelo público do autor. Autores não vinculados
não podem escrever e não escolhem `author_id` no payload de artigo.

Esta configuração não conclui a implantação de produção. Artigos e biografias passam pela allowlist HTML na escrita e
na serialização pública; conteúdo legado é filtrado na leitura sem reescrita automática do banco. Somente admin pode
ativar o bypass de conteúdo bruto confiável. Na UI, o controle existente é um checkbox de bypass por artigo com ação
explícita `Salvar`; não há editor WYSIWYG nem CRUD editorial completo. O CRUD/editor permanece planejado na Fase 7.
A validação local isolada confirmou a sanitização e bypass, a observabilidade dos quatro workers e o exportador OTLP,
incluindo continuidade de serving durante queda do collector. Isso não representa deploy nem prova capacidade na VPS.
Deploy e cofre de produção continuam pendentes no [roadmap](ROADMAP.md); collector OTLP segue opcional e não é
dependência de serving.

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
`docker compose -f docker-compose.yml up -d --force-recreate keycloak`.

## Desenvolvimento fora do Docker

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=mysql+pymysql://ruby-demo:2u8y-c0d3@127.0.0.1:3306/ruby_demo_development
flask --app app bootstrap-db
gunicorn -b 0.0.0.0:3000 app:app
```

- Em execução fora do Compose, rode `flask --app app bootstrap-db` explicitamente antes de iniciar Gunicorn ou Flask.
- Esse comando aplica migrations pendentes e seeds idempotentes. Se falhar, corrija a causa antes de iniciar a API.

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
docker compose -f docker-compose.yml build --build-arg VITE_API_BASE_URL=https://api.example.com ui
```

Esse argumento configura autenticação; os endpoints de conteúdo têm argumentos
próprios e devem ser configurados juntos quando a API estiver em outro host.

A estrutura e o escopo dos módulos estão em [ARCHITECTURE.md](ARCHITECTURE.md);
as decisões cumulativas ficam em [adrs/](adrs/). Planos e specs em `docs/`
são locais e não devem ser versionados.
