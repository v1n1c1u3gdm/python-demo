# Changelog

Todas as mudanças relevantes deste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
e o projeto segue [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Autorização por recurso com identidade Keycloak privada de autor, CRUD de artigos por proprietário/admin, configuração
  explícita de produção e validação obrigatória de issuer/audience; integração opt-in com Keycloak real e mapper de
  audience no realm de demonstração.
- ADRs 0049–0052 registram política de autorização, vínculo privado, confiança JWT e configuração explícita de
  produção. A Fase 1B do roadmap foi validada isoladamente, sem representar implantação de produção.
- Comando `flask bootstrap-db` e serviço Compose `api-init` para executar migrations e seeds antes dos workers; a API
  aguarda init bem-sucedido.
- ADRs 0046–0047 com as proteções GitHub aplicadas: PR obrigatório na main sem aprovação externa e
  prefixos semânticos obrigatórios para criação de branches no repositório.
- Roadmap em fases para operação na VPS, pipeline local reproduzível, gates CLI, cofre KDBX, manutenção,
  observabilidade, Playwright, administração de posts e documentação de uso.
- ADRs 0038–0045 com escolhas alinhadas para o desenvolvimento futuro; ferramentas e mecanismos ainda não implementados.
- Usuário de testes John Doe no realm Keycloak, com perfil completo e papéis para validar a área administrativa.
- Testes do cliente Keycloak com HTTP simulado, bootstrap de seeds e upgrade/downgrade da migration inicial.
- Testes de regressão da factory e do comando de bootstrap, além de integrações MySQL/Compose opt-in para init,
  falhas, repetição e ciclo de vida.
- Testes de inicialização Vue, router, variáveis de ambiente e navegação durante a migração.
- Configuração pytest-cov de toda a produção Python, incluindo migrations, com gate de 85% somente de linhas.
- Linters Ruff para Python e markdownlint-cli2 para documentação, com dependências e comandos versionados.
- ADRs 0031–0037 com escolhas alinhadas de Vue 3, Vite, Vitest, remoção do BootstrapVue sem uso, Ruff,
  markdownlint-cli2 e planos/specs exclusivamente locais.
- Instruções para agentes com TDD, testes fluentes em Arrange–Act–Assert, cobertura mínima de 85% e avaliação de
  cobertura antes de cada commit.
- Diretrizes para branches e commits semânticos, manutenção do changelog e fluxo de desenvolvimento com Superpowers.
- Preferência por orquestração com Sol e implementação por subagentes Luna, ambos com esforço de raciocínio médio.
- Mapa da aplicação em `ARCHITECTURE.md`, com responsabilidades de arquivos, fluxos, limites de escopo e contexto
  histórico.
- Registro cumulativo em `adrs/ADR-NNNN.md` com 30 decisões atômicas: 20 reconstruídas do histórico e dez definidas pelo
  usuário para o desenvolvimento e a manutenção dos registros, incluindo a substituição da política inicial de
  cobertura.
- Consulta e atualização obrigatória dos ADRs pelos agentes, com formato, status e regras de substituição definidos em
  `AGENTS.md`.
- Regra de lint obrigatório para Markdown, Python e JavaScript/Vue antes da conclusão de implementações e antes de
  commits; escolhas de ferramentas devem ser alinhadas previamente.
- Reconstrução das mudanças históricas abaixo a partir dos seis commits disponíveis.

### Changed

- Documentação operacional de primeiro startup, atualização de schema/imagem, reinício da API e execução Flask/Gunicorn
  local; as Fases 1A e 1B estão concluídas localmente, sem deploy de produção.
- Preferência de agentes atualizada para Luna com esforço médio em todas as funções, registrada no ADR-0027.
- Fase 0 do roadmap em validação, com investigação local de recursos e verificações em containers limitados;
  capacidade de produção e escolhas operacionais da VPS continuam pendentes.
- Dependências Python atualizadas, incluindo Flask 3.1, SQLAlchemy 2.1, Marshmallow 4 e OpenTelemetry 1.45;
  dependências de pytest/Ruff separadas em `api/requirements-dev.txt`.
- UI migrada para Vue 3, Vue Router 4, Vite e Vitest/Vue Test Utils 2; Bootstrap 4 e visual existente preservados.
- Cobertura da UI passa a incluir inicialização e router, com threshold exclusivamente de linhas.
- Docker usa Python 3.14.7, Node 24, instalação UI pelo lockfile e argumentos de build `VITE_*`.
- Planos e especificações em `docs/` mantidos apenas localmente, com a pasta ignorada pelo Git.
- Gate de cobertura definido exclusivamente por linhas, com mínimo de 85% por módulo; outras métricas não bloqueiam
  aprovação. ADR-0030 substitui ADR-0023.
- Exceção pontual autorizada pelo usuário para publicar o commit documental anterior, com cobertura da API abaixo
  da meta e ferramentas de lint então pendentes; a exceção não se aplica à atualização atual da stack.

### Fixed

- Configuração de lint dos testes JavaScript passa a reconhecer os globais da suíte Vitest.
- URLs do build público da UI usam endereços acessíveis pelo navegador, evitando o hostname interno `api` como padrão.

### Removed

- Vue CLI, Jest, compilador Vue 2, BootstrapVue e IconsPlugin da cadeia de build/runtime da UI.

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

- Descrição de origem no README: o projeto passou a ser descrito como inaugurado com a API Flask, sem afirmar uma
  migração de Ruby não demonstrada pelo histórico disponível.

Fontes: `291a07f`, `c4f0f3d`, `ec76ee3` e `329763c`.

### 2025-02-11

#### Added

- Estrutura inicial do repositório com README, `.gitignore` e licença GNU AGPL.

Fonte: `134c57e`.
