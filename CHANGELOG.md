# Changelog

Todas as mudanças relevantes deste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
e o projeto segue [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Instruções para agentes com TDD, testes fluentes em Arrange–Act–Assert, cobertura mínima de 85% e avaliação de cobertura antes de cada commit.
- Diretrizes para branches e commits semânticos, manutenção do changelog e fluxo de desenvolvimento com Superpowers.
- Preferência por orquestração com Sol e implementação por subagentes Luna, ambos com esforço de raciocínio médio.
- Mapa da aplicação em `ARCHITECTURE.md`, com responsabilidades de arquivos, fluxos, limites de escopo e contexto histórico.
- Registro cumulativo em `adrs/ADR-NNNN.md` com 30 decisões atômicas: 20 reconstruídas do histórico e dez definidas pelo usuário para o desenvolvimento e a manutenção dos registros, incluindo a substituição da política inicial de cobertura.
- Consulta e atualização obrigatória dos ADRs pelos agentes, com formato, status e regras de substituição definidos em `AGENTS.md`.
- Regra de lint obrigatório para Markdown, Python e JavaScript/Vue antes da conclusão de implementações e antes de commits; escolha dos linters ainda ausentes depende de discussão prévia.
- Reconstrução das mudanças históricas abaixo a partir dos seis commits disponíveis.

### Changed

- Gate de cobertura definido exclusivamente por linhas, com mínimo de 85% por módulo; outras métricas não bloqueiam aprovação. ADR-0030 substitui ADR-0023.
- Exceção pontual autorizada pelo usuário para publicar este lote exclusivamente documental, com cobertura da API abaixo da meta e ferramentas de lint ainda pendentes; as regras permanecem obrigatórias para os próximos trabalhos.

## Histórico reconstruído (sem releases identificados)

Não há tags de versão no histórico consultado até `b459944`. As datas abaixo
são datas de commits e não representam releases. As entradas seguem as categorias
de Keep a Changelog; futuras versões publicadas devem usar `[X.Y.Z] - YYYY-MM-DD`.

### 2025-12-19

#### Added

- Serviço Keycloak com importação de realm, cliente, papéis e usuários de demonstração.
- Login via Keycloak, validação de tokens e perfil administrativo protegido na API.
- Página `/admin`, serviço de autenticação e sessão em `sessionStorage` na UI.
- Testes da API e UI para autenticação e documentação da integração.
- Regras legadas do Cursor e relatório JUnit versionado.

Fonte: `b459944`.

### 2025-11-30

#### Added

- API Flask com CRUD de autores, artigos e perfis sociais e contagem de artigos por autor.
- Modelos SQLAlchemy, validação Marshmallow, migration inicial e seeds com controle de execução.
- Healthcheck, métricas OpenTelemetry/Prometheus, logs HTTP/SQLAlchemy e relatório técnico HTML.
- Especificação OpenAPI estática e Swagger UI.
- SPA Vue 2/BootstrapVue com páginas de artigos, autor e componentes de perfis sociais.
- Serviços HTTP, testes pytest e Jest/Vue Test Utils e thresholds de cobertura de 85% na UI.
- Dockerfile com estágios API/build/runtime UI e Compose com API, UI e MySQL.
- CORS global na API com Flask-CORS.

#### Changed

- Licença de GNU AGPL para MIT.
- Dependências Python e README com stack, organização e instruções Docker/local.

#### Fixed

- Descrição de origem no README: o projeto passou a ser descrito como inaugurado com a API Flask, sem afirmar uma migração de Ruby não demonstrada pelo histórico disponível.

Fontes: `291a07f`, `c4f0f3d`, `ec76ee3` e `329763c`.

### 2025-02-11

#### Added

- Estrutura inicial do repositório com README, `.gitignore` e licença GNU AGPL.

Fonte: `134c57e`.
