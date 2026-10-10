# Instruções para agentes

Estas regras se aplicam a todo o repositório e a todos os agentes e subagentes.

## Branches e commits semânticos

- Todo desenvolvimento deve começar em uma branch semântica, antes de alterar arquivos. Não desenvolver diretamente em
  `main` ou `master`.
- Usar `<tipo>/<descricao-em-kebab-case>`, por exemplo: `feat/article-search`, `fix/login-validation`,
  `docs/agent-development-guidelines` ou `chore/test-tooling`.
- Verificar a branch e o estado do Git antes de começar; preservar alterações existentes do usuário.
- Usar Conventional Commits: `<tipo>(<escopo opcional>): <descricao>`. Exemplos: `feat(api): add article search`,
  `fix(ui): validate login` e `docs: document agent workflow`.
- Indicar alterações incompatíveis com `!` e explicar em `BREAKING CHANGE:` no corpo do commit.
- Criar commits apenas quando autorizados e depois das verificações obrigatórias abaixo.

## TDD e testes fluentes

- Toda implementação de funcionalidade, correção de bug ou mudança de comportamento deve seguir TDD, inclusive quando
  executada por subagentes.
- Seguir ciclos pequenos de **Red → Green → Refactor**:
  1. Escrever primeiro um teste que descreva o comportamento esperado.
  2. Executá-lo e verificar que falha pelo comportamento ausente, e não por erro de configuração.
  3. Implementar o mínimo necessário para fazê-lo passar.
  4. Executar os testes e confirmar o resultado verde.
  5. Refatorar mantendo os testes verdes.
- Bugs exigem teste de regressão que reproduza o problema antes da correção.
- Escrever testes fluentes, com nomes que expressem intenção, vocabulário de domínio e asserções legíveis. Preferir a
  API fluente da biblioteca de asserções adotada, quando disponível; não adicionar dependências apenas para encadear
  asserções.
- Organizar cada teste no formato **Arrange–Act–Assert**, com fases claramente identificáveis; usar comentários
  `Arrange`, `Act` e `Assert` quando ajudarem a leitura.
- Arrange prepara dados e dependências; Act executa o comportamento; Assert verifica o resultado observável. Manter um
  comportamento principal por teste.
- Cobrir caminhos de sucesso, erros relevantes e limites; evitar testes que apenas espelhem detalhes internos da
  implementação.
- Alterações exclusivamente documentais não exigem testes artificiais, mas devem ser revisadas e continuam sujeitas à
  avaliação de cobertura antes de qualquer commit.

## Cobertura e verificação antes de commit

- A cobertura mínima aceitável de **linhas** do código de produção é **85%** em cada módulo (`api/` e `ui/`); não usar
  a média entre módulos para ocultar cobertura insuficiente.
- Sempre avaliar exclusivamente cobertura de **linhas** para o gate. Statements, funções e branches podem aparecer nos
  relatórios, mas não são critérios de aprovação nem devem bloquear commits. Consultar [ADR-0030](adrs/ADR-0030.md).
- Antes de **cada commit**, executar as suítes completas com coleta de cobertura, conferir os relatórios e confirmar o
  mínimo de 85%. Reexecutar após mudanças posteriores à medição.
- Não aceitar como evidência um relatório antigo, apenas testes selecionados ou a afirmação de um subagente.
- Não reduzir thresholds nem excluir código de produção da medição para atingir a meta. Justificar exclusões legítimas,
  como arquivos gerados.
- Na API, usar pytest com medição de todo o código de produção. Se `pytest-cov` estiver disponível, usar
  `python -m pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=85` dentro
  de `api/`; `api/pyproject.toml` mede toda a produção, incluindo migrations, com `branch = false`. Não
  usar `--cov-branch` para o gate, pois combina cobertura de linhas e branches.
- Na UI, executar `npm run test:unit` dentro de `ui/`; o script já coleta cobertura. Verificar se a
  configuração do Vitest inclui todo o código de produção e usa somente `lines: 85` como threshold. Se houver thresholds
  legados de outras métricas, alinhar a configuração ao ADR-0030 ao trabalhar nas ferramentas de testes; não confundir
  essas métricas com a regra de aceitação.
- Se faltar ferramenta, configuração ou dependência para medir corretamente, resolver isso antes de fazer commit. Se
  houver impedimento externo, relatar o bloqueio e não fazer commit.
- Executar também lint, build e verificações pertinentes à alteração. Informar comandos, resultados e percentuais
  medidos ao entregar o trabalho; nunca declarar verificações que não foram executadas.

## Linters obrigatórios

- Toda implementação deve passar pelos linters das tecnologias afetadas antes de ser considerada concluída. Isso também
  se aplica a testes, configuração e documentação, inclusive entregas de subagentes.
- Antes de cada commit, executar todos os linters aplicáveis ao conjunto de alterações e confirmar sucesso. Reexecutar
  após alterações posteriores à validação.
- **Markdown:** validar arquivos `.md`, incluindo `AGENTS.md`, `ARCHITECTURE.md`, `CHANGELOG.md` e ADRs, com o linter
  Markdown adotado pelo projeto.
- **Python:** validar o código da API e seus testes com o linter Python adotado pelo projeto.
- **UI:** a aplicação usa **Vue 3 com JavaScript e Bootstrap 4**, com build Vite e testes Vitest. Validar JavaScript e
  componentes `.vue` com ESLint e `eslint-plugin-vue`, já configurados em `ui/eslint.config.js`.
  Executar `npm run lint -- --no-fix`
  dentro de `ui/` para verificação sem correção automática.
- Os linters escolhidos são **Ruff** para Python ([ADR-0035](adrs/ADR-0035.md)) e **markdownlint-cli2** para Markdown
  ([ADR-0036](adrs/ADR-0036.md)). Usar `ruff check .` dentro de `api/` e `npm run lint:md` na raiz para verificar os
  arquivos Markdown, incluindo `docs/` local. A ausência de ferramenta não dispensa o gate: relatar o bloqueio e não
  declarar a validação concluída nem fazer commit. Discutir
  mudanças dessas escolhas com o usuário antes de registrá-las.
- Corrigir violações; não desativar regras, ampliar exclusões ou ignorar erros apenas para obter sucesso. Justificar
  exceções específicas e respeitar as decisões registradas nos ADRs.
- Linters não substituem testes ou medição de cobertura. O orquestrador deve conferir os resultados, informar os
  comandos executados e distinguir validações aprovadas de verificações bloqueadas.

## CHANGELOG

- Manter `CHANGELOG.md` na raiz no formato **Keep a Changelog**, com histórico voltado a pessoas e versões em ordem
  cronológica inversa.
- Registrar mudanças relevantes na seção `[Unreleased]` durante o desenvolvimento, no mesmo conjunto de alterações.
- Usar as categorias aplicáveis: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed` e `Security`; omitir
  categorias vazias.
- Ao publicar uma versão, transferir as entradas para `[X.Y.Z] - YYYY-MM-DD`, seguindo versionamento semântico, e
  manter `[Unreleased]` para o próximo ciclo.
- Não inventar versões, datas de release ou links de comparação. Incluir links quando houver referências reais
  disponíveis.

## Decisões arquiteturais (ADRs)

- Antes de delimitar ou implementar qualquer desenvolvimento, consultar `ARCHITECTURE.md` e os ADRs em `adrs/`,
  identificar as decisões aplicáveis e incluir seus identificadores no plano e no contexto dos subagentes.
- Nunca deixar de atualizar os ADRs quando o trabalho introduzir, alterar, depreciar ou substituir uma decisão. Avaliar
  esse impacto no planejamento e novamente na revisão final; se não houver mudança de decisão, registrar essa conclusão
  na entrega, sem criar ADR artificial.
- Antes de criar um novo ADR ou alterar o sentido de uma decisão existente, discutir com o usuário a necessidade, o
  contexto, a escolha, as consequências e as alternativas; só registrar a nova decisão após alinhamento explícito.
  Identificar a necessidade é obrigatório, mas não autoriza decidir ou criar ADRs automaticamente. Uma solicitação
  explícita de reconstrução histórica autoriza documentar escolhas comprovadas, sem inventar decisões novas.
- Cada ADR deve registrar **uma única decisão atômica**, que possa ser aceita ou substituída independentemente. Separar
  escolhas de framework, persistência, autenticação, infraestrutura e processo quando forem decisões distintas.
- Usar exclusivamente nomes `adrs/ADR-0001.md`, `adrs/ADR-0002.md` e assim por diante. Reservar o próximo número
  disponível, sem reutilizar ou renumerar registros publicados. Não manter `README.md` em `adrs/`.
- Todo ADR deve conter título, `Status:`, `Contexto`, `Decisão`, `Consequências` e `Alternativas Consideradas`. Usar
  somente os status **Proposto**, **Aceito**, **Depreciado** ou **Substituído**.
- Contexto explica a situação que levou à decisão; Decisão descreve a escolha; Consequências explicita impactos
  positivos e negativos; Alternativas Consideradas registra opções realmente avaliadas e motivos de descarte.
- Não inventar justificativas, alternativas ou deliberações históricas. Quando não houver evidência, declarar que a
  avaliação não foi registrada; indicar fontes e distinguir registros retrospectivos de propostas novas.
- ADRs são cumulativos. Para mudar uma decisão aceita, criar novo registro e, quando sua substituição estiver aceita,
  marcar o anterior como `Substituído`, com links recíprocos. Usar `Depreciado` para decisão abandonada sem
  substituição; preservar o conteúdo histórico.
- Propostas começam como `Proposto`; mudar para `Aceito` quando a decisão tiver sido aceita. Registros retrospectivos
  podem usar `Aceito` para adoção comprovada, identificando expressamente essa condição.
- Atualizar os ADRs afetados no mesmo conjunto de alterações da implementação, junto de `ARCHITECTURE.md` e
  `CHANGELOG.md` quando pertinente. Na revisão, conferir atomicidade, status, evidências e links.

## Modelos e subagentes

- Quando Sol for usado para orquestração, planejamento, revisão, integração ou validação, preferir esforço de raciocínio
  **baixo**. Manter Luna com esforço **médio** para implementação e subagentes de implementação, e preservar Luna médio
  nas demais funções em que já for a preferência aplicável.
- Usar os identificadores de modelo suportados pelo ambiente; definir explicitamente modelo e esforço ao despachar
  subagentes. Não presumir que seja possível trocar o modelo da sessão atual.
- Se o modelo preferido para uma função não estiver disponível, comunicar a limitação e usar a alternativa disponível
  mais próxima, mantendo as mesmas regras de qualidade; não presumir que seja possível trocar o modelo da sessão atual.
- Fornecer a cada subagente requisitos, escopo, arquivos sob sua responsabilidade, critérios de aceitação e estas
  instruções. Evitar alterações concorrentes nos mesmos arquivos.
- O orquestrador deve revisar o diff, integrar as entregas e validar os testes e a cobertura por conta própria.

## Fluxo obrigatório de Superpowers

Manter planos e especificações em `docs/` apenas localmente. Essa pasta está ignorada pelo Git por decisão do usuário;
não adicionar seus arquivos à força, nem criar commits de planos/specs, mesmo que uma skill recomende versioná-los.
Documentação permanente e ADRs continuam nos caminhos versionados definidos acima.

Usar a suíte `/superpowers` por meio das skills disponíveis no ambiente, lendo as instruções de cada skill aplicável.
Aplicar o fluxo conforme o tipo de tarefa:

1. **`using-superpowers`**: identificar as skills aplicáveis ao iniciar o trabalho.
2. **`brainstorming`**: esclarecer requisitos e desenho antes de criar funcionalidades ou modificar comportamento;
   **`systematic-debugging`** para investigar bugs antes de propor correções.
3. **`using-git-worktrees`**: avaliar isolamento e usar worktree quando necessário, sempre garantindo uma branch
   semântica antes das alterações.
4. **`writing-plans`**: elaborar um plano para trabalhos com múltiplas etapas, incluindo ADRs aplicáveis e seu impacto,
   testes, cobertura e atualização do changelog.
5. **`subagent-driven-development`**: preferir implementação por subagentes Luna com esforço médio; usar
   **`dispatching-parallel-agents`** apenas para tarefas independentes. Quando subagentes não estiverem disponíveis,
   usar **`executing-plans`**.
6. **`test-driven-development`**: aplicar Red–Green–Refactor e Arrange–Act–Assert em cada tarefa de implementação.
7. **`requesting-code-review`** e **`receiving-code-review`**: solicitar revisão conforme a skill e tratar feedback com
   verificação técnica.
8. **`verification-before-completion`**: verificar requisitos, linters aplicáveis, testes, cobertura mínima de 85%,
   atualização necessária dos ADRs, changelog e diff antes de declarar conclusão ou fazer commit.
9. **`finishing-a-development-branch`**: concluir a branch após as verificações, respeitando a autorização do usuário
   para commit, merge, push ou PR.

Não executar etapas inaplicáveis mecanicamente. Se uma skill necessária estiver indisponível, informar a limitação e
seguir os passos equivalentes destas instruções, sem declarar que a skill foi executada.
