# Arquitetura

## Módulos e direção de dependência

O backend é modular e os fluxos novos de `pedidos`, `ingestao` e `realtime`
separam domínio, aplicação e infraestrutura. `routes.py`/`schemas.py` são o
adaptador HTTP; `service.py` permanece como fachada de compatibilidade para os
consumidores existentes.

```text
app/
  main.py
  modules/
    auth/             # SSO Microsoft Entra ID, JWT, sessão, usuários e RBAC
    comunicacoes/
      domain/         # estados, Message-ID e política bounded
      application/    # agendamento/delivery somente por ports
      infrastructure/ # SQLAlchemy, SMTP e métricas
    core/             # /v1/status
    health/           # liveness e readiness
    ingestao/
      domain/         # parse, normalização e agregação financeira
      application/    # casos de uso do full refresh
      infrastructure/ # Databricks e persistência dos snapshots
    parametros/       # parâmetros e fluxo de aprovação
    pedidos/
      domain/         # consultas, versão, grade, estoque e motor
      application/    # casos de uso e portas
      infrastructure/ # queries SQL e repositórios de escrita
      processing/
        domain/         # intenção, limites, plano e resultado
        application/    # submit/claim/checkpoint somente por ports
        infrastructure/ # ledger/header/plan SQL, lease e session lock
    realtime/
      domain/         # evento e erros do protocolo
      application/    # casos de uso/fachada transacional
      infrastructure/ # outbox, Redis Stream, relay e sockets locais
  shared/
    config/           # Settings validados
    database/         # engine/sessões/opções asyncpg
    errors/           # handlers de exceção da aplicação
    infrastructure/   # clientes Databricks/Redis e rate limit
    jobs/             # ledger durável genérico, ports e reconciliação
    logging/          # configuração de log estruturado
    metrics/          # Prometheus
    pagination/       # cursor opaco assinado
    security/         # JWT e dependências RBAC
  workers/            # Celery worker/beat
alembic/              # schema e correções de dados versionadas
.docker/              # imagem e compose
```

A direção é transporte → aplicação → domínio. Regras como identidade
produto+canal, versão otimista, invariantes da grade e rateio financeiro ficam
fora das rotas. SQLAlchemy, Redis, SMTP e Databricks são detalhes de
infraestrutura. Os módulos legados ainda sem subcamadas seguem a convenção
existente `routes → service → models/schemas`; não se cria uma segunda pilha
paralela para a mesma regra.

## Persistência

PostgreSQL é acessado de forma assíncrona por `asyncpg`. As tabelas centrais são:

| Tabela | Origem | Papel |
|--------|--------|-------|
| `pedidos` | Databricks | Snapshot aberto, uma linha por pedido/produto/tamanho |
| `pedidos_processados_erp` | Databricks | Snapshot processado no ERP, incluindo flags e ajustes financeiros signed |
| `pedido_produto_read` | Derivada dos dois snapshots | Projeção por fonte/pedido/produto usada nas leituras bounded |
| `estoque` | Databricks | Foto por produto/tamanho/canal |
| `produto_tamanho_posicao` | Databricks | Ordem oficial dos tamanhos por produto |
| `faturamento_colecao` | Databricks | Agregado das 3 coleções mais recentes por canal |
| `ordens_reserva` / `ordens_reserva_linx` | Aplicação | ORs e integração Linx |
| `pedidos_processados` | Aplicação | Estado local de processamento |
| `pedido_modificacoes` | Aplicação | Grade editada por pedido/produto |
| `estoque_virtual` | Aplicação | Consumo/reserva da foto de estoque |
| `realtime_outbox` / `realtime_topic_state` | Aplicação | Eventos transacionais e watermarks |
| `realtime_read_cursors` | Aplicação | Leitura monotônica por usuário/tópico |
| `realtime_observed_entities` | Ingestão | Ledger de novidades/recorrências |
| `comunicacoes` / `communication_email_deliveries` | Aplicação | Intenção idempotente por ator e delivery outbox SMTP |
| `durable_jobs` / `durable_job_idempotency_bindings` | Aplicação compartilhada | Ledger de status/lease e bindings duráveis, inclusive para chaves coalescidas entre atores |
| `pedido_processamentos` / `pedido_processamento_plan` | Pedidos | Header 1:1 e plano imutável/checkpointável do job |

O full refresh substitui somente snapshots e projeções derivadas. Estado do
fluxo (`ordens_reserva`, `pedidos_processados`, `pedido_modificacoes`, cursores e
outbox) sobrevive às sincronizações.

## Leituras bounded

O frontend não recebe a base inteira. A revisão `020` materializa
`pedido_produto_read` no grão necessário e o full refresh a reconstrói na mesma
transação dos snapshots. ERP tem precedência quando o mesmo par pedido/produto
aparece nas duas fontes.

Os repositórios SQL aplicam filtros, anti-joins de estado, agregação global,
ordenação determinística e paginação antes de hidratar o payload:

```text
snapshot por tamanho
      ↓ full refresh transacional
pedido_produto_read (pedido + produto)
      ↓ filtro/agregação SQL por produto + canal
GET /produtos (até 25 linhas)
      ↓ abertura sob demanda
GET /produtos/clientes (até 25 clientes)
```

`channel=Todos` preserva a identidade `(channel, productCode)`; valores e
tamanhos de Franquia nunca entram na linha Multimarca. Cursor, filtros e sort
formam um escopo assinado, evitando reutilizar uma página em outra consulta. O
resumo e os alertas também são calculados no banco. O histórico é lido pela
mesma projeção por produto (`/produtos` com `stage=historico`).

## Escritas consistentes

Edição de grade usa `PUT /api/v1/pedidos/produtos/grades` com até 100 mudanças.
Cada cliente envia a versão opaca lida em
`/api/v1/pedidos/produtos/clientes`. O caso de uso:

1. resolve exatamente produto e canal;
2. rejeita pedidos duplicados;
3. bloqueia linhas em ordem determinística;
4. valida todas as versões, totais e estoque por tamanho;
5. persiste modificações/OR Linx e recalcula valores no servidor;
6. anexa o evento realtime e faz um único commit.

Qualquer falha desfaz o lote inteiro. Aprovação por produto/canal também é
transacional e idempotente. O antigo `PUT /{nr}/alterar-grade`, que não carregava
versão nem canal explícito, foi removido e não é um caminho de escrita.

## Processamento durável de pedidos

`POST /api/v1/pedidos/processamentos` registra apenas `{mode,channel}` e retorna
`202`; a request nunca carrega nem processa a foto global. O ledger genérico e o
header de Pedidos são confirmados juntos. Publish no broker é best effort após o
commit, e o mesmo registry kind→task é usado pelo submitter e pelo reconciliador
do beat.

Sweep, redispatch e retenção pertencem à composição genérica
`app/workers/tasks/durable_jobs.py`. Ela usa somente `app.shared.jobs` e o
dispatcher central; nenhum bounded context é importado pela camada compartilhada.

O dispatcher roteia `orders.processing.v1` para `orders_processing`. Workers
gerais consomem somente `celery`; um worker dedicado processa a fila pesada com
concurrency/prefetch 1, reservation de 1280 MiB, limite de 1536 MiB e recycle
pós-task. O compose contém essa topologia, mas o pipeline ECS atual conhece
somente o serviço API; workers externos precisam ser provisionados com seus
identificadores reais antes de automatizar a atualização deles.

O binding ator+chave é consultado antes do rate limit, então replay persiste
disponível sem Redis. Nova chave passa por fixed-window HMAC de 3/min por usuário
e 10/min por IP; outage Redis falha fechado antes de qualquer insert.

O worker tenta primeiro o advisory lock global de sessão. Só depois do lock ele
faz claim do ledger na própria conexão presa, evitando gastar tentativas por
contenção normal. Plano e aplicação ficam protegidos pelo mesmo lock através de
vários commits. Heartbeat usa sessão independente; o plano é persistido uma vez
e cada lote de até 250 pares avança um checkpoint atomicamente com as ORs,
estoque, integração Linx e progresso. O último commit contém resultado bounded,
evento realtime determinístico e `succeeded`. Deadline de 15 minutos, três
tentativas e sweeper de lease limitam falhas/crashes.

Os endpoints síncronos `/adequar` e `/sem_adequar` foram removidos; não há um
segundo write path global. GET do job autoriza por capability (um job ativo pode
ser coalescido entre atores autorizados) e não expõe owner, digest, lease,
payload ou detalhes de exceção.

## Realtime transacional

Mudança de negócio e outbox usam a mesma sessão e o mesmo commit. Um relay líder
publica em Redis Stream com retry/backoff; cada instância FastAPI lê o fanout e
entrega apenas aos próprios sockets. A entrega é pelo menos uma vez e o cliente
deduplica por `eventId`, reconcilia via REST e confirma leitura no servidor.

Tickets são opacos, curtos e de uso único. Replay é limitado, filas por socket
são bounded, heartbeat detecta conexão morta e gaps exigem resync. Detalhes em
[Realtime](realtime.md).

## Delivery outbox de comunicações

O POST de comunicação nunca chama SMTP. O caso de uso persiste comunicação
`Pendente`, delivery e `communication.created` na mesma transação. O beat
reivindica uma row com `SKIP LOCKED`, confirma o claim e só então chama o
adapter SMTP. Rejeição segura pode usar retry bounded; resultado ambíguo vira
`Incerto` e não é repetido. A camada `application` depende somente de ports e
tipos do domínio. Detalhes e operação: [Comunicações](comunicacoes.md).

## Migrations e readiness

Alembic é a única fonte de criação/evolução de schema. API, worker e beat rodam
`alembic upgrade head` antes do processo principal; um advisory lock de sessão
serializa instâncias concorrentes inclusive através de revisões que usam
`CREATE INDEX CONCURRENTLY`.

`/health` verifica apenas o processo. `/health/ready` exige conexão PostgreSQL,
versão aplicada exatamente igual ao head do repositório e Redis disponível. A
imagem não recebe tráfego enquanto o schema estiver atrasado.

Com `ENV=prod`, a configuração também falha antes do startup se banco/Redis
forem locais ou estiverem ausentes, ou se o segredo JWT for padrão/curto. O
compose de produção consome somente as URLs remotas `PROD_*`, sem herdar
silenciosamente os destinos de desenvolvimento.
Ele é um arquivo standalone — combiná-lo com o compose local não faz parte do
contrato operacional.

A revisão `019` é uma correção financeira irreversível: distribui por tamanho o
total que a origem repetia em cada linha, em centavos e com soma exata. ERP
preserva ajustes negativos legítimos. A `020` cria uma projeção recalculável,
a `021` adiciona os índices medidos, a `022` cria o delivery outbox, a `023`
cria o ledger de jobs, a `024` cria o plano/checkpoint de Pedidos, a `025`
preserva idempotência de jobs criados ou coalescidos após o estado terminal e a
`026` indexa a reconciliação bounded do ledger realtime ativo. Baixar `019`
para `018` é recusado por desenho.

## RBAC — papéis e níveis

JWT identifica o usuário, mas o papel é relido do banco em cada request. Um
nível maior herda os menores:

| Papel | Nível | Dependência mínima |
|-------|-------|--------------------|
| `basico` | 10 | `require_viewer` — leitura |
| `operacional` | 20 | `require_actor` — criar/consultar processamento, editar, aprovar e comunicar |
| `gestor` | 30 | `require_gestor` — solicitar mudança de parâmetro |
| `administrador` | 40 | `require_admin` — ingestão, parâmetros e usuários |
| `admin_tecnico` | 50 | `require_tecnico` |

Papéis legados (`operador`, `admin`, `hype_*`) são normalizados pelo mapa de
compatibilidade do módulo de autenticação.

## Avisos de integração crítica

O módulo `health` (antes só `/health*`) ganhou um bounded context pequeno para
avisar quando as duas integrações críticas falham: conexão com o Postgres e
leitura da view de estoque no Databricks. `domain/avisos.py` é puro (sem
SQLAlchemy/Redis/FastAPI); `application/casos_uso.py` aplica dedupe/resolução;
`service.py` é a fachada best-effort (nunca levanta) que expõe
`registrar_falha_integracao`/`registrar_sucesso_integracao`/`listar_linhas_de_alerta`.

**Estado em Redis, histórico no log.** O estado atual do aviso vive em Redis
(`RepositorioAvisosRedis`, hash por fonte) — nunca no Postgres, porque um aviso
que diz "o banco está instável" não pode depender do próprio banco para ser
gravado ou lido. O log estruturado (`logger app.avisos.integracao`) é a trilha
histórica, escrito sempre, independente do Redis estar disponível.

**Resolução sem scheduler novo.** Dois mecanismos periódicos já existentes
resolvem o aviso: o healthcheck do compose bate em `/health/ready` a cada 10s
(resolve o aviso de banco de dados); o Celery beat roda a ingestão a cada 2h
(a próxima leitura bem-sucedida da view de estoque resolve o aviso de estoque).

**Três pontos de instrumentação:** o check `database` de `/health/ready`; a
leitura de estoque em `DatabricksIngestionSource.estoque()` (propaga o erro
original sem engolir); e o handler global de exceção, só para
`OperationalError`/`InterfaceError`/`TimeoutError` do SQLAlchemy (erro de
conexão real durante um request, não `IntegrityError`/`ProgrammingError`).

**Live-update para `estoque`, não para `banco_de_dados`.** A falha de estoque
publica um evento realtime (`topic="alerts"`) numa sessão própria
(`async_session_factory`), fora da transação de sincronização — a falha
acontece na coleta, antes de qualquer transação de escrita existir. Best-effort:
uma falha nessa publicação nunca mascara o erro original nem impede o aviso de
existir via Redis. Para `banco_de_dados` não há live-update: publicar "o
Postgres está instável" através do próprio Postgres não se sustenta; esse
aviso aparece só ao recarregar a aba Alertas.

**Discriminador `kind` em `GET /api/v1/alertas`.** `AlertaOut.kind` distingue
`"negocio"` (as 8 categorias operacionais já existentes, default) de
`"integracao"` (os dois avisos acima, com `category` `integracao_banco_de_dados`
/`integracao_estoque`). As linhas de integração são semeadas no início de
`rows` antes das consultas SQL, então entram sempre na primeira página e
contam em `total`. Mensagens sempre em linguagem não-técnica — nunca nome de
exceção, tabela/view interna, "Databricks" ou "SQLAlchemy".
