# Preparação Docker da aplicação

O uso local padrão do repositório está em [`compose.yaml`](../compose.yaml): execute `docker compose up` a partir da raiz.
Ele mantém bootstrap, CA, certificados e segredos técnicos em `infra/.local/` e `infra/secrets/local/`, integra UI,
BookStack, Gitea e Woodpecker ao Keycloak e publica somente `https://app.localhost` em loopback. O login humano local é
`admin` / `admin!123`; o agente/runner da CI e o exporter permanecem desativados. Consulte o
[README principal](../README.md#ambiente-local-padr%C3%A3o-com-sso) para URLs, logs seguros, reinício, recriação e limites
de logout/rotação.

O restante deste documento descreve a stack **portátil/operacional** em `infra/compose/` e seu preflight separado; ela
não substitui nem configura a stack Compose local padrão. Use esses scripts somente quando o objetivo for preparar
explicitamente aquela composição portátil.

Esta pasta contém composições separadas para API, UI, MySQL, Keycloak, serviços legados e gateway HTTPS. Elas não
substituem o [`docker-compose.yml`](../docker-compose.yml), que continua atendendo ao desenvolvimento local. As stacks
de serviço não publicam portas no host; o gateway expõe somente a porta TLS configurada, em loopback por padrão, e
conecta-se às redes web declaradas.

Para preparar a stack local completa, configure `infra/.env` e `ci/.env` com valores sintéticos/locais e execute
`infra/scripts/up-local.sh`. O comando valida o ambiente efetivo do shell e dos arquivos, segredos, par certificado/chave
TLS e os quatro modelos Compose antes do primeiro `up`; todos os builds usam a raiz do repositório como project
directory. Ele inicia aplicação, serviços legados, gateway e servidor/exporter da CI. O agente não é iniciado; a opção
`COMPOSE_PROFILES=runner` é rejeitada enquanto as provas operacionais da rede e capacidade estiverem pendentes.

O collector OTLP reutiliza [a configuração existente](../api/observability/otel-collector.yaml), a imagem fixada e a
rede web privada. Para habilitar a exportação na stack portátil, defina `OTEL_METRICS_ENABLED=true` em `infra/.env` e
inicie com `infra/scripts/up-local.sh --telemetry`. O endpoint de métricas e OTLP não publica portas no host; a rota
externa da API fica em `/api/metrics`. O perfil e a configuração são opt-in e precisam ser habilitados juntos.

O exporter roda como UID/GID `65532`. O arquivo de token montado em
`/run/woodpecker-control/api-token` deve pertencer a esse UID e manter permissões privadas (`0600` ou mais restritas).
Antes de renderizar os modelos Compose, o preflight verifica metadados e executa um container temporário com a imagem
Python fixada para confirmar que UID `65532` consegue ler um token UTF-8 não vazio. O probe não tem rede, recebe o
arquivo somente leitura, usa filesystem somente leitura e não imprime o conteúdo. Se a imagem fixada não estiver
localmente disponível, prepare-a antes do preflight com `docker pull` usando a referência completa definida em
`ci/local_orchestration.py`. Crie o arquivo sintético ou operacional já com proprietário e modo corretos; o preflight
nunca altera permissões nem propriedade de credenciais.
Com o perfil `logs`, um serviço sem rede e com apenas as capabilities
`CHOWN` e `FOWNER` prepara o volume do cursor; ele preserva o estado existente, ajusta somente `state.json` e o diretório
para UID/GID `65532`, e termina antes do exporter. O diretório também vem preparado na imagem para volumes novos.

`infra/compose/ci.yaml` sobrepõe `ci/compose.yaml` apenas para a stack portátil: o servidor entra na rede web criada pelo
gateway sob o alias `ci-server`, a porta do backend não é publicada e o exporter inclui seu código na imagem. A
composição standalone `ci/compose.yaml` permanece utilizável sem a rede do gateway. O overlay usa `!override` e exige
Docker Compose 2.24.4 ou superior.

## Configuração local

Copie [`.env.example`](.env.example) para `.env` e preencha os nomes usados pelas stacks que iniciar. Crie arquivos de
segredo fora do Git. O
preflight aceita caminhos fora do repositório ou dentro de `infra/secrets/`, que está excluída do build context; rejeita
outros caminhos internos após resolver symlinks. Uma pasta de segredos fora do repositório também pode ser usada com
caminhos absolutos no `.env`.

Os cinco arquivos necessários são a senha raiz do MySQL, as senhas dos usuários da API e do Keycloak e os segredos do
cliente API e do administrador inicial do Keycloak. Gere valores novos por ambiente; não reutilize credenciais de
desenvolvimento ou da VPS. O provisionador do Keycloak aceita senhas com pontuação, espaços e aspas: PyMySQL envia a
senha como parâmetro, enquanto schema e usuário são identificadores fixos.

O usuário do container Keycloak precisa ler os arquivos de senha do banco e do administrador. A imagem fixada executa
como UID 1000; em hosts Linux, deixe esses arquivos legíveis por esse UID, mantendo permissões restritas. Os demais
segredos são lidos pelo MySQL, que inicia como root, ou pelo container da API. O helper de startup lê os arquivos em
memória, remove as variáveis `*_FILE` do ambiente da API e constrói o DSN com codificação de URL. Mensagens de erro
identificam a configuração ausente sem imprimir seu conteúdo.

O gateway também lê o certificado, a chave privada e o bundle CA por mounts somente leitura. Use arquivos externos com
permissões restritas e acessíveis ao runtime Docker; em hosts SELinux, os mounts aplicam o rótulo compartilhado `z`.
O gateway remove as demais capabilities e mantém apenas `CHOWN`, `DAC_READ_SEARCH`, `NET_BIND_SERVICE`, `SETGID` e
`SETUID`: o NGINX precisa ler a chave restrita, preparar diretórios temporários e trocar para seus workers sem
privilégios. A imagem mantém o filesystem somente leitura, com diretórios temporários em tmpfs.

Exemplo de valores não secretos para `.env`:

```dotenv
STACK_PROJECT=python-demo-local
APP_WEB_NETWORK=python-demo-local-web
APP_DATA_NETWORK=python-demo-local-data
CI_WEB_NETWORK=python-demo-local-ci-web
CI_CONTROL_NETWORK=python-demo-local-ci-control
PUBLIC_HOST=demo.example.test
KC_BOOTSTRAP_ADMIN_USERNAME=stack-admin
GATEWAY_TLS_CA_FILE=/caminho/privado/public-ca-bundle.pem
GATEWAY_TLS_CERT_FILE=/caminho/privado/fullchain.pem
GATEWAY_TLS_KEY_FILE=/caminho/privado/private-key.pem
OTEL_METRICS_ENABLED=false
MYSQL_ROOT_PASSWORD_FILE=/caminho/privado/mysql-root
API_DB_PASSWORD_FILE=/caminho/privado/api-db
KEYCLOAK_DB_PASSWORD_FILE=/caminho/privado/keycloak-db
KEYCLOAK_CLIENT_SECRET_FILE=/caminho/privado/keycloak-client
KEYCLOAK_ADMIN_PASSWORD_FILE=/caminho/privado/keycloak-admin
```

Suba a composição pela validação prévia. Ela pede ao Compose a configuração efetiva de interpolação, incluindo a
precedência do ambiente do shell, valida os arquivos correspondentes e para antes de `up` quando uma configuração está
ausente ou inválida. O comando de renderização não imprime a configuração. O script define o diretório do projeto como
a raiz do repositório; use `--project-directory .` em comandos Compose manuais para que todos os contextos de build
apontem para essa mesma raiz (o arquivo Compose fica em `infra/compose/`). Os valores conhecidos de senha demo usados
pela API em desenvolvimento também são rejeitados pelo preflight; isso aplica a restrição já existente e não define
uma política nova de força ou formato de senha.

```sh
infra/scripts/up-app.sh
```

Para inspecionar os serviços e acompanhar os logs:

```sh
docker compose --project-directory . --env-file infra/.env -f infra/compose/app.yaml ps
docker compose --project-directory . --env-file infra/.env -f infra/compose/app.yaml logs --follow
```

Para parar a stack e manter os dados persistentes:

```sh
docker compose --project-directory . --env-file infra/.env -f infra/compose/app.yaml down
```

O volume nomeado `${STACK_PROJECT}_mysql` contém os dados da API e do Keycloak. O volume `${STACK_PROJECT}_mysql_socket`
compartilha somente o socket local do MySQL com o provisionador privilegiado; a senha raiz não é exposta pela rede.
Não use `down --volumes` durante a operação normal. A senha raiz usada para inicializar o volume precisa continuar
disponível e estável. Se um operador
alterar a senha do usuário do Keycloak, deve atualizar o arquivo externo correspondente e recriar o serviço
`app-keycloak-db-init` antes do Keycloak.

## Contratos e limites

- O MySQL usa a imagem oficial 8.4.11 fixada pelo digest de índice e não publica porta. O volume é próprio desta stack.
- A API e o Keycloak usam usuários e schemas separados dentro dessa instância. O serviço `app-keycloak-db-init` recebe
  a senha raiz somente para criar/atualizar o usuário `keycloak` e conceder acesso a `keycloak.*`; a API não recebe a
  senha raiz. O provisionamento não apaga nem altera o banco da API.
- `app-init` aplica migrations e seeds antes de `app-api`, seguindo o [ADR-0048](../adrs/ADR-0048.md). Os serviços de
  API e UI não montam o código-fonte do host.
- A rede interna de dados não tem saída externa; a rede web permite ao gateway alcançar os aliases
  `app-api`, `app-ui` e `app-keycloak`. Essa separação Compose não comprova isolamento do host.
- A saúde da API consulta `/ready`, que valida o MySQL e a descoberta atual do Keycloak. A instância inicia sem o realm
  `python-demo`; configure realm e cliente antes de esperar prontidão da API. A UI pode iniciar antes disso; o teste
  isolado de integração prepara um realm e credenciais sintéticas para provar login e identidade pelo gateway.
- O Keycloak usa `start` com MySQL persistente e configuração explícita de hostname/proxy para TLS terminado na borda.
  A descoberta interna começa em `http://app-keycloak:8080/auth`; o issuer anunciado é
  `https://${PUBLIC_HOST}/auth/realms/python-demo`. O login sintético é exercitado pela rota `/auth` no teste isolado
  de integração do gateway.
- A API usa `REQUESTS_CA_BUNDLE=/run/certs/public-ca.pem` com um mount read-only apontado por `GATEWAY_TLS_CA_FILE`.
  Esse bundle precisa confiar no issuer HTTPS e nos endpoints de token/JWKS publicados pelo Keycloak. Não desative a
  validação TLS para corrigir certificados locais.
- A UI compila as chamadas para `/api`. O gateway remove esse prefixo antes de encaminhar à API e remove `/git` antes
  de encaminhar ao Gitea. `/auth` e `/ci` chegam aos serviços com o prefixo preservado; BookStack recebe caminhos sem
  `/bookstack`, enquanto `APP_URL` mantém esse prefixo nos links e redirects externos.
- O gateway usa `infra/compose/gateway.yaml`, imagem NGINX derivada de digest, certificados/key externos montados somente
  leitura e a porta `127.0.0.1:8443` por padrão. Ele conecta-se a `APP_WEB_NETWORK`, `LEGACY_WEB_NETWORK` e à rede
  `CI_WEB_NETWORK`, criada por essa composição. A configuração separada da CI deve consumir essa rede como externa após
  ela existir; `ci/compose.yaml` permanece utilizável sozinho. DNS do NGINX consulta Docker em tempo de requisição, então
  o servidor CI pode estar parado quando o gateway inicia.
- Na fixture portátil, a rota `/share` sempre responde 404. A rota `/bookstack` também fica fechada até que o bootstrap
  crie a conta,
  substitua `admin@admin.com` / `password` e valide a nova senha. Só então configure
  `BOOKSTACK_BOOTSTRAP_CONFIRMED=true`; a opção seleciona a rota no início do container e não altera senha alguma.
- Para iniciar o gateway, preencha `GATEWAY_PROJECT`, `CI_WEB_NETWORK`, certificados e host no `.env`, confirme os
  valores renderizados e use:

  ```sh
  docker compose --project-directory . --env-file infra/.env -p "$GATEWAY_PROJECT" \
    -f infra/compose/gateway.yaml config
  docker compose --project-directory . --env-file infra/.env -p "$GATEWAY_PROJECT" \
    -f infra/compose/gateway.yaml up --build --detach
  ```

- A fixture de integração gera sua própria CA/certificado sintético e porta loopback livre. Em uma instalação futura,
  substitua-os por certificado de produção confiável e aplique a política de bind/firewall previamente aprovada; este
  teste não altera DNS, hosts, firewall ou VPS.
- O digest upstream Keycloak 26.4.7 e o MySQL 8.4.11 foram resolvidos no registry em 2026-10-09. A imagem local do
  Keycloak herda o digest upstream e acrescenta apenas a leitura de segredos por arquivo.

O teste de Compose deve usar projeto, volumes e arquivos de segredo sintéticos próprios. Esta preparação não migra
dados reais, confirma a origem dos bancos, altera DNS/TLS, instala ferramentas no host nem prova capacidade da VPS.

## Serviços legados isolados

`infra/compose/legacy.yaml` prepara BookStack, MariaDB, Gitea e uma rota de arquivos fechada para validação local. Configure
`STACK_PROJECT`, `LEGACY_WEB_NETWORK`, `LEGACY_DATA_NETWORK`, `PUBLIC_HOST`, `BOOKSTACK_URL`, `GITEA_ROOT_URL`, os três
caminhos `*_FILE` e `SHARE_DATA_DIRECTORY` em `infra/.env`; os arquivos devem existir fora do build context. O segredo
`BOOKSTACK_DB_PASSWORD_FILE` deve conter os bytes exatos da senha sem newline final, pois o BookStack lê esse arquivo
verbatim enquanto a imagem MariaDB interpreta `_FILE`. O preflight rejeita a diferença antes de iniciar serviços.

Use `infra/scripts/up-legacy.sh` somente com arquivos e volumes novos e sintéticos. A imagem LinuxServer do BookStack
cria a conta inicial `admin@admin.com` com senha `password` em um banco vazio dessa fixture portátil; o Compose local
padrão usa OIDC Keycloak e não depende dessa conta. Esse comportamento confirmado torna a
stack inadequada para exposição: antes de encaminhar qualquer rota para BookStack, o operador precisa concluir o
bootstrap, trocar essa senha e validar as credenciais administrativas. A fixture de integração usa a conta apenas dentro
de um projeto isolado sem portas publicadas; ela não é uma configuração de implantação.

Gitea usa a imagem oficial `docker.gitea.com/gitea`, guarda toda a instalação em `${STACK_PROJECT}_gitea` montado em
`/data` e configura SQLite somente para uma instância nova de teste. Isso não identifica o banco de uma instalação
existente. MariaDB 11.4.13 atende ao requisito documentado pelo BookStack de MariaDB 10.6 ou superior; a validação
considera apenas bancos sintéticos novos e não afirma que dados de origem em MariaDB 10.5 possam ser atualizados ou
importados sem plano próprio.

O volume `${STACK_PROJECT}_mariadb` contém o banco BookStack e `${STACK_PROJECT}_bookstack` contém `/config`, incluindo
uploads. `down` preserva os volumes; `down --volumes` os apaga e deve ficar restrito à limpeza do projeto sintético da
fixture. A rede `legacy_data` é interna. A rede `legacy_web` permite conexão privada do gateway, mas nenhum
serviço publica porta do host. A pasta compartilhada é montada somente leitura e o NGINX responde 404 para todos os
caminhos; não existe listagem nem entrega pública de arquivos.
