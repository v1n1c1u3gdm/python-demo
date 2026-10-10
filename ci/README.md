# CI local e plano de controle

A pipeline em `.woodpecker/quality.yaml` reutiliza `ci/bootstrap.sh` e os sete gates do runner local. As decisões
aplicáveis são ADR-0058–0066 e ADR-0027. Esta configuração é uma entrega local revisável; não instala serviços na VPS,
não cria OAuth/webhooks/rules do GitHub e não comprova a integração com PRs reais.

## Configuração

`compose.yaml` fixa servidor/agente Woodpecker 3.18.0 por digest, SQLite persistente e uma workflow por agente. O
serviço `agent` só entra em execução com o profile explícito `runner`; `docker compose up` inicia o servidor e não o
runner. O exporter usa o profile `logs`, sem o socket Docker. Profiles reduzem ativações acidentais por `up`, mas não
são um interlock de política live.
O socket Docker é montado somente no agente de controle, conforme ADR-0060. Jobs não recebem esse socket, volumes do
host ou secrets de produção. A rede de controle é adicional às redes dinâmicas que o backend cria por workflow;
essas bridges não comprovam isolamento dos serviços da aplicação. A proteção do host depende da etapa 6C.

Na stack portátil, `infra/compose/ci.yaml` é aplicado junto deste arquivo. Ele mantém `all_events`, conecta o servidor à
rede web externa criada pelo gateway sob o alias `ci-server`, remove a porta de host do backend e constrói o exporter
com seus módulos Python dentro da imagem. Apenas o arquivo de token e o volume de cursor permanecem montados no
exporter. A composição standalone continua independente desse overlay. `infra/scripts/up-local.sh` inicia o exporter
com o servidor e rejeita `COMPOSE_PROFILES=runner`; habilitar o agente continua bloqueado pelas provas operacionais
pendentes. O agente é o único serviço de controle que recebe o socket; os passos da pipeline não recebem socket,
segredos do servidor/exporter ou volumes de dados das aplicações.

`.env.example` contém credenciais vazias. `ci/.env` e `ci/credentials/` são ignorados pelo Git e pelo build Docker;
manter a credencial futura do exportador fora do checkout e nunca copiá-la para snapshots de testes ou jobs.
Antes de renderizar ou iniciar, fornecer por canal autorizado os três secrets obrigatórios, o host HTTPS com `/ci`,
exatamente um login de mantenedor, o ID do repositório Woodpecker e o caminho absoluto do arquivo privado da API.
O valor do token não vai em `.env`. Com as variáveis exportadas, validar:

```sh
python -c 'import os; from ci.control_config import load_control_config; load_control_config(os.environ)'
docker compose --env-file ci/.env -f ci/compose.yaml config --quiet
```

A validação Python rejeita ausência, placeholders e URLs com credenciais. A interpolação Compose rejeita valores
obrigatórios vazios, mas não identifica placeholders: executar as duas verificações. Não publicar o resultado completo
de `compose config`, que contém secrets. Ainda não há instalação externa autorizada; comandos de start, registro de
repositório e configuração de secrets reais ficam pendentes.

Registro fechado impede criar novas contas comuns; **não revoga contas antigas**. Auditar `/ci/api/users` com a conta
de controle antes de habilitar o agente. O único usuário permitido é o mantenedor com `admin: true`. A rota de aprovação
na versão fixada verifica permissão de push do repositório, não exclusividade do admin: a auditoria de contas é
necessária, assim como a verificação real de permissões no GitHub.

## Aprovação e auditoria de política

O default do servidor é `all_events`; esse default não corrige repositórios existentes. Exportar settings reais de
`/ci/api/repos/{repo_id}`, usuários de `/ci/api/users` e crons de `/ci/api/repos/{repo_id}/cron`, com credencial fora
dos jobs. Salvar somente os campos abaixo, sem tokens, em snapshot local e executar:

```sh
python -m ci.policy policy-snapshot.json --maintainer LOGIN
```

```json
{
  "repository": {
    "require_approval": "all_events",
    "approval_allowed_users": [],
    "trusted": {"network": false, "volumes": false, "security": false},
    "allow_deploy": false,
    "private": false,
    "netrc_trusted": [],
    "config_extension_endpoint": "",
    "registry_extension_endpoint": "",
    "secret_extension_endpoint": ""
  },
  "users": [{"login": "LOGIN", "admin": true}],
  "crons": [],
  "manual_cron_blocked": true
}
```

`approval_allowed_users` é um bypass do **autor** da pipeline e deve ficar vazio, inclusive para PRs do mantenedor.
`trusted` usa o objeto real da API; booleano legado é rejeitado. Deploy, extensões e compartilhamento de credenciais
ficam desabilitados. `manual_cron_blocked` registra uma verificação administrativa do proxy, não um campo da API nem
uma prova automática da configuração implantada. O snapshot é uma auditoria pontual; não constitui monitoramento
contínuo ou interlock de execução. Drift posterior à auditoria ainda exige controle operacional antes de implantação.

A versão 3.18 exclui manual/cron da aprovação. `proxy/woodpecker.conf`, incluído no servidor HTTPS existente, bloqueia
escritas de criação/restart em `/ci/api/repos/{repo_id}/pipelines[/number]` e criação/execução/edição de cron.
Leituras, exclusão de cron/pipelines e ações approve/decline/cancel permanecem encaminhadas, sujeitas à autorização do
Woodpecker; exclusão de pipeline continua sujeita ao estado terminal no backend. Crons preexistentes devem ser removidos
antes da ativação. O backend publica somente `127.0.0.1:8000`; operadores locais que acessam loopback podem contornar o
proxy e são parte da fronteira de confiança do host. Não publicar backend direto.

O proxy preserva `/ci`, `/ci/authorize` e `/ci/api/hook`. Prova HTTP local com NGINX e upstream fake:

Os testes que usam Docker são opt-in locais; jobs não precisam de CLI Compose ou socket Docker.

```sh
CI_CONTROL_COMPOSE_PROOF=1 python -m unittest ci.tests.test_control_compose -q
CI_PROXY_HTTP_PROOF=1 python -m unittest ci.tests.test_proxy_http -q
```

Esse teste usa rede Docker descartável `--internal`, sem portas, volumes ou credenciais da aplicação. Não comprova
OAuth real, webhook assinado, status no SHA correto, nova espera para SHA B ou rejeição de operador no GitHub.

## Clone e secrets

O clone oficial `woodpeckerci/plugin-git:2.10.0` fica fixado por digest com `depth: 0` e submodules desabilitados.
O plugin escreve `.netrc` quando recebe credenciais; por isso autenticação de repositórios públicos, plugins trusted
para clone e `netrc_trusted` ficam vazios/desabilitados. Não há PAT global nem token nos passos. A configuração é para
repositório público; repositório privado requer nova avaliação. O histórico completo é necessário ao Gitleaks.
Configuração YAML de um PR não é a fronteira de aprovação; os controles ficam no servidor/projeto/proxy.

## Interfaces pendentes 6B/6C e capacidade

6B implementa essa interface; detalhes de exportação, limites e retenção estão abaixo.

### Exportação de stdout e retenção (Task 6B)

O serviço exporter usa a API do Woodpecker 3.18.0 em `http://server:8000/ci`, no profile logs, e consulta pipelines e
seus workflows/steps antes de baixar a saída real de cada step concluído. O endpoint de download é
/ci/api/repos/{repo_id}/logs/{pipeline_number}/{step_id}/download. O endpoint de lista é escopado ao repo_id; o
pipeline não serializa repo_id, então o cliente valida o número e SHA entre a lista e o detalhe, e valida que cada
workflow/step pertence ao pipeline_id retornado e cada step ID está no endpoint do pipeline/repositório escopado.
Estados success, failure, error, killed, canceled, skipped e declined são mantidos conforme a API;
pipelines ativos/aguardando aprovação não são rotulados como concluídos. `success` com step `pending` permanece
incompleto; steps `pending` em pipelines cancelados também permanecem incompletos porque o contrato consultado não
permite comprovar que nunca iniciaram. Saídas API incompletas e erros permanecem visíveis como tais; timeout preserva o
estado e mensagens fornecidas pelo Woodpecker sem inferir um status novo. O exporter exige página curta ou vazia para
confirmar o fim do inventário; ao atingir o limite interno de 10.000 páginas cheias, aborta antes de exportar ou excluir
logs com base em uma lista incompleta.

Cada linha real vira um registro JSON em stdout com repo, pipeline, SHA, status, step e conteúdo sanitizado. Linhas
separadas, NUL, CR/LF e controles de terminal não podem injetar registros do journal; o token carregado do arquivo,
valores com rótulos token/password/secret/api_key e formatos conhecidos de token GitHub são redigidos. O exporter não tem
como reconhecer valores secretos arbitrários que não estejam etiquetados; não forneça segredos de produção aos jobs.
Downloads limitam-se a 16 MiB por step e API JSON a 16 MiB; quando Content-Length é informado, o corpo precisa ter o
tamanho declarado. Excesso, EOF incompleto, timeout, erro HTTP ou schema inválido não avança o cursor daquele step e
não emite um resultado sintético de sucesso.

A credencial Woodpecker fica em arquivo 0600 fora do checkout e dos jobs; apenas o exporter monta o arquivo como
somente leitura. A conta API precisa ler logs e ter permissão push no repositório para a rota DELETE
/api/repos/{repo_id}/logs/{pipeline_number}; a rota de 3.18 verifica que o pipeline já é terminal. A limpeza remove
somente logs do pipeline, mantendo seu registro, repositórios, usuários e configuração. Não é um GitHub PAT global.

A retenção lógica mede bytes únicos de saída de steps persistidos pelo Woodpecker, mantém até 14 dias e remove os logs
terminais mais antigos quando ultrapassa 1 GiB. A unidade systemd de exemplo usa LogNamespace=python-demo-ci;
`journald@python-demo-ci.conf` define MaxRetentionSec=14day e SystemMaxUse=1G só para essa namespace. A cópia de
stdout no journal e a cópia do Woodpecker são armazenamento físico duplicado; esses limites separados não equivalem
a um único teto físico de 1 GiB. O journal inclui envelope/metadados, e SQLite/WAL pode manter espaço após excluir linhas;
limites de journald incidem em arquivos arquivados e podem exceder o valor enquanto o arquivo ativo cresce. Medir
armazenamento físico e custo/necessidade de VACUUM continua pendente (ADR-0061 e ADR-0065).

O cursor fica em volume privado, modo 0600, atualizado por escrita temporária + fsync + rename, e o arquivo salvo fica
limitado a 16 MiB para nunca gravar um estado que o loader rejeitaria. A lista de pipelines e steps ainda não é
compactada automaticamente; se o cursor exceder esse limite, a gravação falha preservando o estado anterior e o
exporter requer intervenção operacional. A gravação de stdout e a gravação do cursor não formam uma transação; uma queda
entre ambas pode repetir o step inteiro após restart. Não se afirma entrega exactly-once. Nenhuma configuração de host
foi aplicada. Instalação desta unidade/namespace ainda aguarda a revisão e autorização operacional, além da auditoria
de drift/API real, prova de rede e medição de capacidade.

Fontes da tag Woodpecker: [pipeline API](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/api/pipeline.go),
[schema pipeline](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/model/pipeline.go),
[schema workflow](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/model/workflow.go),
[schema step e pipeline_id](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/model/step.go),
[status values](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/model/const.go),
[rotas e autorização de exclusão de logs](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/router/api.go),
[proteção contra exclusão de logs ativos](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/api/pipeline.go).
Fontes systemd: [namespaces e limites de journald](https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html)
e [LogNamespace](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#LogNamespace=).

A preparação 6C está em [firewall_policy.py](firewall_policy.py), com inventário de exemplo em
[firewall.example.json](firewall.example.json). O exemplo tem
`inventory_complete: false` e não pode gerar política até que o operador faça e revise inventário atual. O inventário
válido requer nomes exatos de bridges confiáveis e CIDRs de todas as redes Docker de aplicação/controle que devem ser
protegidas. `docker0` nunca pode ser exceção. A checagem exige que cada bridge confiável esteja observada e rejeita
interfaces fora dos padrões cobertos; bridges Docker dinâmicas `br-*` são aceitas e continuam sujeitas ao default deny.
Os CIDRs são informados manualmente no JSON: o script de preparação não os deriva nem os compara com endereços atuais
das bridges. O operador deve confirmá-los contra um inventário de redes atual. Não inferir exceções por corrida de
monitoramento.
Esse preflight é uma fotografia, não um monitor contínuo nem prova de quais redes o Woodpecker anexará aos containers.
O operador também deve verificar que nenhum job usa bridge listada como confiável; bridges customizadas fora de
`br-*`/`docker0` são bloqueadas pela pré-condição e exigem nova revisão, nunca uma exceção automática.

As tabelas próprias `inet python_demo_ci` e `bridge python_demo_ci_l2` não limpam o ruleset nem alteram tabelas
Docker/firewalld. INPUT bloqueia bridges de jobs para o host. FORWARD descarta destinos locais, faixas privadas e
especiais e CIDRs inventariados; permite DNS TCP/UDP 53 e HTTPS TCP 443 para endereços públicos, descartando o restante
sem bypass geral de `established`. A chain bridge bloqueia encaminhamento L2 direto entre portas veth na mesma bridge.
Exceções são interfaces exatas de aplicações/controle, nunca curingas. Jobs precisam usar bridge distinta e não podem
ser anexados às redes confiáveis.

Para IPv6, “público” nesta política significa destino no prefixo Global Unicast `2000::/3`, ainda sujeito aos drops
explícitos de endereços especiais e não globalmente alcançáveis: incluem dummy `100:0:0:1::/64`, documentação
`3fff::/20`, segmento `5f00::/16`, protocolo `2001::/23`, 6to4 `2002::/16`, ULA, link-local, site-local legado e
multicast. O guard `ip6 daddr != 2000::/3 drop` também bloqueia destinos como `::2`; não usa `ipaddress.is_global` como
autoridade. As redes são uma política estática revisável com base nos [registros IANA de IPv6 Global Unicast](https://www.iana.org/assignments/ipv6-unicast-address-assignments)
e de [endereços IPv6 especiais](https://www.iana.org/assignments/iana-ipv6-special-registry), que devem ser revistos
quando esses registros mudarem. CIDRs protegidos precisam ser strings com prefixo (`address/prefix`); o validador
normaliza bits de host para o endereço de rede com `strict=False`.

Pré-condições antes de habilitar o runner: conferir na VPS backend Docker `iptables+firewalld` e `userlandproxy`,
inventariar com ferramentas somente leitura todas as interfaces e subnets Docker, completar/revisar o JSON, testar as
duas tabelas em conjunto com Docker/firewalld e comprovar a cobertura do caminho real de DNS do Docker. O daemon DNS
embutido pode encaminhar consultas por um proxy do host, sem `iifname` da bridge; se isso ocorrer, a regra de egress
por bridge não limita esse caminho e a ativação fica bloqueada até haver outro controle. Validar também conexões ao
host publicadas via userland proxy, rotas após DNAT, acesso IPv4/IPv6, tráfego de resposta, registries/redirecionamentos
e comportamento após reboot/reload de firewalld/Docker. HTTPS em qualquer IP público e DNS para qualquer IP público
são permissões por porta, não allowlists de registry.

Para persistência futura, integrar o arquivo revisado ao carregamento existente de nftables, antes de iniciar Docker;
o início do runner deve exigir uma checagem de integridade que confirme as duas tabelas e o inventário. Rollback remove
somente `inet python_demo_ci` e `bridge python_demo_ci_l2`. A configuração inclui `destroy table` dessas tabelas e
recriação transacional para tornar reaplicação idempotente. Não executar `flush ruleset`. Nenhuma regra, serviço,
systemd, sysctl, Docker, firewalld ou VPS foi alterado nesta preparação. Teste de sintaxe/transação em namespace
descartável não demonstra isolamento real.

Para preparar e revisar o arquivo sem aplicar regras, crie primeiro um inventário completo e revisado fora do checkout
(por exemplo, `/etc/python-demo/ci-firewall-inventory.json`) com os CIDRs protegidos e as bridges confiáveis esperadas.
O inventário e o destino precisam ser caminhos absolutos. Execute o script a partir do checkout:

```bash
ci/setup-firewall.sh \
  --config /etc/python-demo/ci-firewall-inventory.json \
  --output /etc/python-demo/python-demo-ci.nft
```

O comando lê bridges com `ip -o link`, confere as exceções de interface do inventário e valida a sintaxe com `nft -c` dentro
de namespace descartável de usuário/rede via `unshare`; as regras entram por stdin (`nft -c -f -`). O checker não abre
nem aplica o arquivo de saída no host. Depois grava o arquivo com modo `0600` por substituição atômica. O diretório
destino deve existir, pertencer ao operador e não permitir escrita por usuários não confiáveis. Não execute duas
preparações concorrentes para o mesmo destino; o script não coordena outros escritores. O arquivo temporário é
sincronizado antes do rename e o diretório depois. Uma queda entre o rename e o `fsync` do diretório pode reverter o
nome; se o `fsync` falhar, o comando avisa que a troca pode não sobreviver a uma queda abrupta de energia. Se `ip`,
`nft`, `unshare`, a criação do namespace ou a sintaxe não estiverem disponíveis, informa a ação necessária e termina
sem instalar ferramentas nem substituir o arquivo existente. O padrão é somente preparação: não há modo `--apply`,
execução de regras, instalação, systemd, SSH ou atualização de distribuição. O arquivo `firewall.example.json` é
intencionalmente incompleto (`inventory_complete: false`) e deve falhar.

A VPS Fedora 34 atual não é o destino: conforme ADR-0068, a migração futura foi escolhida para Ubuntu Server 26.04 LTS
em VPS nova de 8 GB. O script detecta capacidades reais e não declara compatibilidade com uma versão de nftables ou
imagem do provedor sem prova. A disponibilidade da imagem 26.04 e a instalação permanecem pendentes.

Referências técnicas: [Docker com backend nftables](https://docs.docker.com/engine/network/firewall-nftables/)
e [manual nftables](https://netfilter.org/projects/nftables/manpage.html) (tabelas próprias, prioridades, interfaces,
família bridge e `destroy table`).

Limites de recursos/timeouts ainda dependem da medição na Task 7; defaults do Woodpecker não são limites aprovados pelo
usuário. Merge permanece manual.

## Fontes fixadas

- [Flags do servidor 3.18.0](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/cmd/server/flags.go).
- [Schema de repositório](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/model/repo.go).
- [Gating](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/pipeline/gated.go).
- [Rotas API](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/router/api.go).
- [Registro fechado](https://github.com/woodpecker-ci/woodpecker/blob/v3.18.0/server/api/login.go).
- [Clone 2.10.0](https://github.com/woodpecker-ci/plugin-git/blob/2.10.0/plugin.go).

Para operação aprovada, configure `/etc/python-demo/woodpecker-exporter.env` com `CI_WOODPECKER_REPO_ID` e o caminho
`CI_WOODPECKER_TOKEN_FILE=/etc/python-demo/woodpecker-api-token`; o conteúdo do token fica apenas nesse segundo arquivo,
modo 0600. O host deve iniciar primeiro `docker compose -f ci/compose.yaml up -d server`. O exemplo
[python-demo-woodpecker-exporter.service](systemd/python-demo-woodpecker-exporter.service) mantém o exporter anexado
para que o próprio systemd encaminhe stdout à namespace. Instale o exemplo de configuração como
`/etc/systemd/journald@python-demo-ci.conf`. Os arquivos são exemplos revisáveis; não foram instalados nem ativados.
