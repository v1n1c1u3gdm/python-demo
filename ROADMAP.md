# Roadmap de produção

Este documento registra uma direção futura para preparar a aplicação para operação em uma VPS Fedora com 4 GB de RAM e
2 vCPU. **As fases estão planejadas; as escolhas já alinhadas não representam implementação concluída.** Cada fase
será detalhada e aprovada antes de começar, com branch semântica, especificação/plano local em
`docs/`, TDD, revisão e gates de qualidade.

## Premissas e limites

- A infraestrutura de produção será própria e local. Ferramentas e serviços previstos devem ser software livre,
  executáveis na VPS e reproduzíveis em Docker no desenvolvimento local.
- CI deve usar a mesma definição de pipeline em execução local e remota, imagens fixadas por digest, lockfiles e as
  mesmas regras. Avaliar consumo real de memória, CPU e disco no limite de 4 GB/2 vCPU; não desativar SELinux para
  contornar problemas.
- Backup e restauração de dados estão fora do escopo deste roadmap.
- A documentação atual descreve Compose, Flask, Vue 3, MySQL e Keycloak em ambiente de desenvolvimento. Não é
  evidência de produção configurada. Consulte [ARCHITECTURE.md](ARCHITECTURE.md), [ADR-0030](adrs/ADR-0030.md) e
  [ADR-0037](adrs/ADR-0037.md).
- O gate de cobertura é **85% de linhas em cada módulo**, API e UI, conforme ADR-0030. Branches, statements e funções
  não são gates.
- A estratégia aprovada de CLI como gate substitui o Sonar nesse papel; ainda precisa ser implementada e validada.
  Bandit e jscpd também foram aprovados para adoção futura. As ferramentas de CI e distribuição abaixo permanecem
  propostas.
- Não há decisão de migração do GitHub para outra hospedagem. Woodpecker CI, Forgejo e um registry local são
  propostas sujeitas a
  validação e alinhamento do usuário.
- Mudanças arquiteturais devem receber ADR atômico após alinhamento explícito. Não criar ADR para transformar proposta
  em decisão sem aprovação.
- ADRs de decisões já aprovadas neste escopo: [ADR-0038](adrs/ADR-0038.md) (gate CLI),
  [ADR-0044](adrs/ADR-0044.md) (Bandit) e [ADR-0045](adrs/ADR-0045.md) (jscpd). Também estão alinhados
  [ADR-0039](adrs/ADR-0039.md) (KDBX), [ADR-0040](adrs/ADR-0040.md) (desbloqueio manual),
  [ADR-0041](adrs/ADR-0041.md) (janela), [ADR-0042](adrs/ADR-0042.md) (tapume) e
  [ADR-0043](adrs/ADR-0043.md) (pipeline local e VPS).

## Regras de execução das fases

- **Status inicial das fases: Planejada.** Um critério verificável só muda o status após implementação,
  evidência atual e revisão.
- Antes de cada fase, usar `using-superpowers` e `brainstorming` para fechar escopo e decisões; guardar
  especificação e plano em `docs/`
  local, conforme ADR-0037. Após aprovação da especificação, usar `writing-plans` e revisar o plano com o usuário.
- Avaliar isolamento com `using-git-worktrees` e começar em branch semântica. Executar com
  `subagent-driven-development` ou `executing-plans`; aplicar `test-driven-development` com Arrange–Act–Assert.
- Aplicar `requesting-code-review`, `receiving-code-review` e `verification-before-completion`; integrar a branch
  com `finishing-a-development-branch`, respeitando autorização de commit, push e PR.
- Preferir implementação delimitada por subagente Luna com esforço médio quando disponível, sem concorrência nos
  mesmos arquivos; o integrador revisa o diff e executa os gates por conta própria.
- Rodar linters aplicáveis, testes completos, cobertura de linhas por módulo, build e verificações específicas.
  Documentar resultados e atualizar `CHANGELOG.md`, `ARCHITECTURE.md` e ADRs quando pertinente.
- Não fazer commit sem autorização explícita. O trabalho deste roadmap não autoriza commits, publicação, migração de
  hospedagem nem implantação.

## Fase 0 — Baseline e decisões de operação

**Status:** Em validação — investigação local realizada; capacidade e decisões da VPS pendentes.

**Escopo:** medir uso de recursos e mapear as escolhas necessárias para caber em Fedora VPS de 4 GB/2 vCPU;
identificar limites atuais do Compose, Keycloak, MySQL e número de workers. Estabelecer critérios de capacidade,
degradação e janela de manutenção antes de automatizar deploy.

**Dependências:** nenhuma; usar arquitetura e configuração atuais como referência, não como aprovação de produção.

**Critério verificável:** relatório reproduzível de uso em cenário representativo local, com orçamento de
RAM/CPU/disco por serviço e lista explícita das decisões abertas e seus responsáveis.

**Evidência local:** relatório e dados em `docs/superpowers/reports/2026-10-03-capacity-baseline.md`, ignorados pelo
Git conforme ADR-0037. A stack local teve máximo simultâneo amostrado de 1.21 GiB em 600 leituras durante 30 segundos;
suítes API/UI, linters e build passaram em jobs descartáveis limitados individualmente a duas CPUs.
Isso não valida a VPS inteira sob limite agregado de 4 GB/2 vCPU, nem os futuros serviços da pipeline.

**Decisões pendentes:** aceitação da capacidade e serviços escolhidos; uso de Woodpecker/Forgejo/registry local;
versão do Fedora, arquitetura de CPU, espaço em disco, domínio e HTTPS; confirmar o fuso da janela já alinhada
(01:30–05:59, hipótese `America/Sao_Paulo`) e detalhar limites de manutenção.

## Fase 1 — Inicialização segura da aplicação

**Status:** 1A e 1B concluídas localmente; produção não implantada.

**Escopo 1A (concluído):** separar bootstrap de produção do carregamento de cada worker: executar migrations e seeds uma
vez, antes de iniciar workers; remover efeitos de banco da criação da aplicação fora de `TESTING`, preservando o modo
de testes.

**Escopo 1B (concluído):** exigir configuração explícita de produção e validar JWT RS256, issuer e audience; manter
leituras públicas, restringir alterações de autores/perfis sociais/relatório técnico a admin e permitir CRUD de artigos
por autor vinculado ou admin. Vincular identidade Keycloak privada a autor por decisão admin.

**Dependências:** baseline da Fase 0; bootstrap 1A registrado no [ADR-0048](adrs/ADR-0048.md), que substitui o
[ADR-0010](adrs/ADR-0010.md). A política 1B e as decisões atômicas estão registradas nos
[ADR-0049](adrs/ADR-0049.md)–[ADR-0052](adrs/ADR-0052.md).

**Critérios 1A verificados:** inicialização com banco MySQL vazio cria o schema e executa seeds uma única vez antes dos
quatro workers; repetição preserva edições, revision e um `SeedRun`; falha do init bloqueia a API nova e não revela a
credencial fictícia nos logs; reinício da API não recria o init. A validação Compose é opt-in por
`RUN_COMPOSE_INTEGRATION=1`.

**Critérios 1B:** testes comprovam configuração obrigatória de produção, leituras públicas, alterações administrativas,
CRUD do próprio autor, bloqueio de autor não vinculado e de acesso cruzado, audience inválida rejeitada e vínculo de
identidade não exposto nos serializers públicos. A integração opt-in usa projeto Compose e volumes exclusivos; a
validação de migration MySQL também deve comprovar preservação de dados existentes.

**Entrega 1A:** o serviço Compose `api-init` aguarda o banco saudável e executa `flask bootstrap-db`; a API aguarda
sucesso antes de iniciar. Consulte [ADR-0048](adrs/ADR-0048.md), [README](README.md) para os procedimentos operacionais
e a especificação local `docs/superpowers/specs/2026-10-03-single-bootstrap-design.md`.

**Decisões pendentes:** integração de deploy, mecanismo de segredos de runtime e operação de produção pertencem às
Fases 5–6. Embora a API exija credenciais explícitas, implantação em produção permanece pendente e a UI ainda renderiza
HTML de artigos com `v-html`; sanitização da allowlist na Fase 7 é bloqueadora antes de expor conteúdo não confiável.

## Fase 2 — Saúde e observabilidade operacional

**Status:** Planejada

**Escopo:** distinguir liveness de readiness: `/up` confirma processo responsivo; preservar `/liveness` por
compatibilidade e `/ready` valida dependências
essenciais com timeouts limitados. Disponibilizar `/metrics` de forma consistente em todos os workers e avaliar
exportação OpenTelemetry por collector local. O snapshot atual por processo é descrito no ADR-0014; discutir sua
evolução para métricas consistentes entre workers,
sem presumir agregação existente.

**Dependências:** Fase 1 para conexão e configuração estáveis; decisões de métricas e retenção compatíveis com
orçamento medido na Fase 0.

**Critério verificável:** teste automatizado demonstra que liveness não depende do banco, readiness sinaliza falha e
recuperação do banco dentro do limite configurado, e métricas agregam requisições entre workers sem duplicação;
collector, se aceito, recebe telemetria local sem serviço externo.

**Decisões pendentes:** conteúdo e proteção de métricas, limites de cardinalidade/retenção e adoção/configuração do
collector. Criar ou atualizar ADR somente após acordo.

## Fase 3 — Pipeline reproduzível e gates de qualidade

**Status:** Planejada

**Escopo:** implementar uma estratégia única de CLI para executar localmente e em CI os mesmos passos e regras em
imagens fixadas por digest e lockfiles. Manter Ruff, ESLint e markdownlint existentes; acrescentar complexidade (Ruff
C901 e ESLint `complexity`, com limites medidos e alinhados), Bandit para segurança Python, jscpd para duplicação e
Gitleaks para segredos. Rodar pytest e Vitest com cobertura integral de produção e mínimo de 85% de linhas em cada
módulo.

**Dependências:** decisão CLI aprovada (ADR-0038), Bandit aprovado (ADR-0044) e jscpd aprovado
(ADR-0045); Fase 0 para orçamento. Gitleaks e limites de complexidade exigem validação de configuração e
baseline.

**Critério verificável:** um comando documentado executado em Docker local e na CI produz os mesmos gates; falhas
artificiais controladas em cada ferramenta bloqueiam o pipeline; relatórios de API e UI provam cobertura de linhas
≥85% separadamente; imagens são referenciadas por digest e dependências resolvem pelos lockfiles.

**Decisões pendentes:** proposta e validação da plataforma CI, repositório Forgejo e registry local; limites de
complexidade/duplicação; publicação e retenção de artefatos; gestão de exceções sem enfraquecer gates.

## Fase 4 — E2E em ambiente isolado

**Status:** Planejada

**Escopo:** criar testes Playwright em ambiente isolado com MySQL, Keycloak, API e UI. Usar fixtures descartáveis e
credenciais de teste, sem dados de produção. Começar por navegação desktop/mobile, login, perfil, logout, acesso
negado e leitura dos fluxos existentes. Playwright complementa os testes unitários; não substitui o gate de cobertura.

**Dependências:** Fases 1–3; pipeline deve executar serviços e coletar logs/relatórios mesmo em falha.

**Critério verificável:** pipeline sobe os serviços isolados, executa cenários Playwright repetíveis, aguarda
readiness com prazo finito e remove o ambiente ao final; execução não pode se conectar ao banco nem ao cofre de
produção.

**Decisões pendentes:** navegadores e conjunto mínimo de cenários; duração máxima aceitável no host de 2 vCPU/4 GB;
política de artefatos de teste.

## Fase 5 — Cofre de produção e desbloqueio manual

**Status:** Planejada

**Escopo:** manter arquivo KDBX fora do Git, somente leitura no repositório de credenciais/host VPS. O operador
desbloqueia manualmente com senha mestra que não é persistida; o banco de credenciais não se autodesbloqueia. Somente
um job de deploy confiável, após todos os gates, pode receber os segredos necessários. Pull requests não recebem cofre
de produção. Isolar os jobs não confiáveis dos serviços e credenciais de produção antes de habilitar essa execução.
O mecanismo de entrega das credenciais de runtime precisa permitir reinícios após a CLI encerrar, sem persistir
a senha mestra; avaliar arquivos protegidos e limites de acesso no desenho da fase.

**Dependências:** Fases 3–4; definir fronteira confiável do runner antes de conceder qualquer acesso.

**Critério verificável:** testes com cofre bloqueado, senha incorreta, entrada ausente e execução de PR provam que o
job não obtém segredo; fixtures dummy não contêm segredos reais; logs, erros e artefatos não revelam senha, valor ou
caminho sensível. O runtime recebe somente as credenciais necessárias após a saída da CLI e após reinício, sem
manter a senha mestra.
Job confiável autorizado lê apenas as entradas requeridas após gates verdes.

**Decisões pendentes:** desenho do mecanismo e ciclo de vida de credenciais que satisfaça a disponibilidade no
runtime; procedimento de revogação; isolamento e prova de identidade do job confiável. As decisões de origem KDBX e
desbloqueio manual já estão aceitas nos [ADR-0039](adrs/ADR-0039.md) e
[ADR-0040](adrs/ADR-0040.md); os mecanismos de entrega e isolamento continuam pendentes.

## Fase 6 — Janela de manutenção e deploy com tapume

**Status:** Planejada

**Escopo:** deploy iniciado por SSH somente após gates aprovados, com artefatos/imagens reproduzíveis e registry local
se adotado. Executar todas as revisions pendentes em um único processo, sem bootstrap concorrente dos workers;
registrar estado
e compatibilidade entre imagem antiga e nova.
Respeitar a janela preferencial diária de 01:30–05:59; confirmar o fuso assumido `America/Sao_Paulo`. Exibir página
de manutenção via
NGINX independente da API, com HTTP 503; monitorar saúde e restaurar o tráfego antes do deadline. Watchdog deve
detectar regressão e disparar retorno controlado à imagem anterior.

**Dependências:** Fases 1–5; protocolo de migration compatível precisa estar documentado e testado com as versões
envolvidas.

**Critério verificável:** simulação demonstra entrada em manutenção, 503, execução de migrations por um único
processo, validação
de readiness e restauração do tráfego antes do prazo; falha antes de concluir mantém a versão atual ou retorna à
imagem anterior quando compatível; ausência de desbloqueio manual pula deploy e mantém a versão atual.

**Decisões pendentes:** confirmação de fuso, deadline, estratégia e limite de tentativas do watchdog, método de
rollback e
limites de compatibilidade. Tapume não promete recuperação mágica: falha fora da janela mantém a operação em
manutenção/erro com diagnóstico; retorno à imagem anterior não garante compatibilidade se o banco já avançou. Os
[ADR-0041](adrs/ADR-0041.md), [ADR-0042](adrs/ADR-0042.md) e [ADR-0043](adrs/ADR-0043.md)
registram as escolhas já alinhadas, sem antecipar a implementação.

## Fase 7 — CRUD editorial com autorização e segurança HTML

**Status:** Planejada

**Escopo:** permitir CRUD de artigos em UI autenticada, com editor WYSIWYG utilizável por pessoas, toolbar clara e sem
exigir edição de HTML bruto. Preservar tags, autor e slug existentes; tratar colisão/invalidez do slug, invalidação de
cache e confirmação de exclusão. A API mantém leitura pública e exige autorização explícita nas escritas, com
respostas 401/403 corretas. Sanitizar HTML por allowlist no servidor e definir tratamento para conteúdo
histórico/importado, considerando que a UI atual usa `v-html`.

**Dependências:** política de papéis definida na Fase 1; testes E2E e segurança das Fases 3–4; decisões de editor e
contrato aprovadas antes da implementação.

**Critério verificável:** testes API/UI/E2E cobrem criar, editar, ler publicamente e excluir com confirmação; usuário
sem autenticação recebe 401, autenticado sem papel recebe 403; usuário autorizado consegue editar; conflito de slug é
explicado sem sobrescrever; o HTML em `post_entry` passa pela allowlist e conteúdo legítimo mantém tags/autor/slug;
conteúdo histórico/importado ganha teste de regressão; falha não apaga nem corrompe silenciosamente o conteúdo.

**Decisões pendentes:** modelo de papéis e escopo (incluindo 401/403); editor entre Jodit (MIT) e CKEditor 5 (GPL,
verificar obrigações da licença e integração); política de HTML histórico/importado e allowlist; definição de
exclusão física versus lixeira ("apagar" e "deletar" representam a mesma operação no escopo atual); lixeira,
rascunhos e uploads ficam fora do escopo inicial salvo novo
alinhamento.

## Fase 8 — Documentação de operação

**Status:** Planejada

**Escopo:** manter README voltado a pessoas, com layout da aplicação, visão de stack, quickstart local, links para
guias de deploy, testes, monitoramento e troubleshooting. Atualizar documentação em cada fase, sem alegar que ambiente
ou comportamento futuro já existe.

**Dependências:** conteúdo confirmado pelas fases correspondentes; registrar apenas procedimentos realmente
implementados e testados.

**Critério verificável:** uma pessoa sem contexto consegue iniciar o ambiente local seguindo o quickstart e localizar
instruções de deploy, saúde, logs e diagnóstico; cada procedimento foi conferido na versão documentada.

**Decisões pendentes:** organização final dos guias e procedimento de publicação, respeitando autorização de
commit/merge/deploy.

## Referências técnicas

- [ADR-0030 — cobertura exclusivamente de linhas](adrs/ADR-0030.md)
- [ADR-0037 — planos e especificações locais](adrs/ADR-0037.md)
- [Woodpecker CI — documentação](https://woodpecker-ci.org/docs/)
- [Forgejo — documentação](https://forgejo.org/docs/latest/)
- [Playwright — documentação](https://playwright.dev/docs/intro)
- [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
