# API / Endpoints

Todos os endpoints de negócio ficam sob `/api/...` e exigem JWT, exceto os
fluxos públicos de autenticação e saúde. A coluna "Papel" indica o nível mínimo;
níveis superiores herdam a permissão. Consulte [Arquitetura](arquitetura.md#rbac-papeis-e-niveis).

## Documentação interativa

Com a API local no ar, o contrato OpenAPI fica em `http://localhost:8000/docs`.
Ele é a referência de campos e códigos HTTP; as tabelas abaixo resumem os fluxos.

## Paginação e consistência

As telas principais usam paginação keyset. A resposta segue
`{rows,total,pageSize,nextCursor,hasMore}` e o cursor é opaco, assinado e
vinculado aos filtros/ordenação. Ao mudar qualquer parâmetro, comece sem cursor.
O backend agrega e ordena o conjunto completo antes do `LIMIT`; nenhuma tela
deve agrupar ou ordenar apenas a página recebida.

As telas por produto usam `/produtos` e `/produtos/clientes`.

## Pedidos — `/api/v1/pedidos`

| Método | Rota | Papel | Descrição |
|--------|------|-------|-----------|
| GET | `/produtos` | `basico` | Produtos agregados por canal nos estágios `aguardando`, `edicao` ou `historico`; filtros, sort server-side e keyset 1–25 |
| GET | `/produtos/clientes` | `basico` | Clientes de `productCode` + `channel`, resumo global e `version` opaca; keyset 1–25 |
| GET | `/lookup` | `basico` | Busca leve de pedidos; keyset 1–25 |
| GET | `/resumo` | `basico` | Agregados autoritativos do dashboard em SQL; estoque só com `includeStock=true` |
| GET | `/evolucao-faturamento` | `basico` | Planejado × distribuído das 3 coleções mais recentes, por canal |
| POST | `/processamentos` | `operacional` | Registra job durável de adequação/sem adequação; `202` + `Location` |
| GET | `/processamentos/{jobId}` | `operacional` | Status, progresso, tentativas, timestamps e contagens seguras |
| PUT | `/produtos/grades?productCode=...&channel=...` | `operacional` | Altera 1–100 clientes, com versão otimista e uma única transação |
| POST | `/produtos/aprovar?productCode=...&channel=...` | `operacional` | Aprova em lote e de forma idempotente o produto/canal |
| POST | `/{nr_pedido}/aprovar` | `operacional` | Compatibilidade: aprova ORs de um pedido, opcionalmente de um produto |

`channel=Todos` não mistura canais: o mesmo código pode aparecer em uma linha
Franquia e outra Multimarca. O filtro `status` é aceito somente em
`stage=historico`; combinações inválidas e cursores fora do escopo retornam
`422`.

### Processamento durável de ORs

`POST /processamentos` exige `Idempotency-Key` com 8–128 caracteres do conjunto
`A-Z a-z 0-9 . _ : -` e corpo bounded:

```json
{"mode": "adequar", "channel": "Todos"}
```

`mode` aceita `adequar` ou `sem_adequar`; `channel` aceita `Todos`, `Franquia`
ou `Multimarca`. A resposta `202` contém `jobId`, `status`, `replayed`,
`coalesced`, `statusUrl`, `progressCurrent` e `progressTotal`. Replay da mesma
chave/intenção é estável; chave reaproveitada com intenção diferente retorna
`409`. Jobs ativos da mesma intenção podem ser coalescidos inclusive entre
atores com a capability, por isso o GET autoriza pelo papel e não pelo owner.

O GET nunca expõe owner, digest, lease, payload de pedido ou exceção. Um job
concluído devolve somente `plannedCount`, `appliedCount`, `deferredCount` e
`blockedCreditCount`. Falha do broker após o commit não altera o `202`: o beat
reconcilia rows `queued`/`retrying`. Não existe endpoint de retry manual.

Novas intenções têm fixed-window Redis de 3/min por usuário e 10/min por IP;
identidades entram nas chaves somente como HMAC. Excesso retorna `429` com
`Retry-After`. Se Redis não puder avaliar a política, a API falha fechado com
`503` + `Retry-After` e não cria o job. O binding idempotente é consultado antes
do limiter: replay persistido continua disponível mesmo durante outage Redis.

### Edição versionada de grade

O corpo de `PUT /produtos/grades` é:

```json
{
  "changes": [
    {
      "orderId": 123,
      "expectedVersion": "versao-opaca-devolvida-pela-consulta",
      "sizes": {"P": 2, "M": 3}
    }
  ]
}
```

Cada pedido pode aparecer uma vez; o lote e cada grade têm no máximo 100
entradas. Todos os pedidos precisam pertencer exatamente ao produto/canal
solicitado. O servidor bloqueia as linhas em ordem determinística, valida todas
as versões e o estoque por tamanho antes de escrever e recalcula valores
financeiros. Conflito de versão, estoque ou processamento retorna `409` e faz
rollback total; o cliente deve recarregar `/produtos/clientes` antes de tentar
novamente.

## Alertas — `/api/v1/alertas`

| Método | Rota | Papel | Descrição |
|--------|------|-------|-----------|
| GET | `(raiz)` | `basico` | Alertas por canal/busca; keyset, `pageSize` 1–100 |

## Comunicações — `/api/v1/comunicacoes`

| Método | Rota | Papel | Descrição |
|--------|------|-------|-----------|
| GET | `(raiz)` | `basico` | Feed recente; keyset, `pageSize` 1–100 |
| GET | `/{id}` | `basico` | Estado atual e metadados seguros de uma comunicação |
| POST | `(raiz)` | `operacional` | Persiste a intenção no delivery outbox; `202` + `Location` |

`Idempotency-Key` é obrigatória, isolada por ator e aceita 8–128 caracteres
ASCII (`A-Z`, `a-z`, `0-9`, `.`, `_`, `:`, `-`). Repetir chave e payload
devolve o mesmo recurso/estado; reutilizá-la com conteúdo diferente retorna `409`.
Novas tentativas têm rate limit Redis de 5/min por usuário e 20/min por IP
(`429` + `Retry-After`); replay já persistido não consome quota. Se o controle
estiver indisponível, uma nova intenção falha fechado com `503` + `Retry-After`
e não cria delivery; replay persistido continua retornando `202`.

`202` significa agendado, não entregue. O status evolui entre `Pendente`,
`Enviado`, `Falhou` e `Incerto`; este último representa aceite possível sem
confirmação e nunca é reenviado automaticamente. A resposta pode incluir
`attemptCount`, `maxAttempts`, `createdAt`, `updatedAt`, `sentAt` e
`nextAttemptAt`, mas nunca erro bruto do provider. Fluxo completo:
[Comunicações](comunicacoes.md).

## Ingestão — `/api/v1/ingestao`

| Método | Rota | Papel | Descrição |
|--------|------|-------|-----------|
| POST | `/sincronizar` | `administrador` | Registra job durável (`202` + `Location`); exige `Idempotency-Key` ASCII 8–128 e coalesce full refresh ativo |
| GET | `/sincronizar/{jobId}` | `administrador` | Estado, progresso, tentativas, timestamps e resultado bounded do job UUID |

O PostgreSQL é a autoridade. Falha do broker depois do commit mantém o job
`queued` e ainda retorna `202`; o reconciliador faz o dispatch posterior. Além
do disparo manual, o Celery beat registra uma intenção idempotente por janela no
intervalo de `INGESTAO_INTERVALO_SEGUNDOS` (padrão 7200 s / 2 h). Veja
[Ingestão](ingestao.md).

## Realtime — `/api/v1/realtime`

| Método | Rota | Papel | Descrição |
|--------|------|-------|-----------|
| POST | `/tickets` | `basico` | Ticket WebSocket de uso único e TTL curto |
| GET | `/status` | `basico` | Watermarks e não lidos por tópico |
| POST | `/read` | `basico` | Confirma leitura monotônica até `throughSequence` |
| WS | `/ws` | ticket | Eventos, replay, heartbeat e sinal de resync |

O WebSocket é um sinal de invalidação; os dados canônicos continuam nos
endpoints REST. Protocolo e operação: [Realtime](realtime.md).

## Parâmetros — `/api/v1/parametros`

| Método | Rota | Papel | Descrição |
|--------|------|-------|-----------|
| GET | `(raiz)` | `basico` | Lista parâmetros em páginas numeradas |
| POST / PUT / DELETE | `...` | `administrador` | CRUD direto |
| POST | `/change-requests` | `gestor` | Solicita alteração |
| GET | `/change-requests` | `basico` | Lista paginada conforme o escopo do usuário |
| POST | `/change-requests/{id}/approve` ou `/reject` | `administrador` | Revisa solicitação |

Chaves usadas pelo motor: `criterio_selecao` e `tolerancia_adequacao`.

As duas listagens recebem `page` (a partir de 1), `pageSize` (1–100),
`search` (até 120 caracteres), `sort` e `order`. A resposta é
`{rows,total,page,pageSize,totalPages}`. `change-requests` também aceita
`statusFilter=pending|approved|rejected|resolved`.

`GET /api/auth/users` segue o mesmo envelope e parâmetros de paginação; exige
`administrador`.

## Auth, saúde e métricas

| Prefixo | Papel | Descrição |
|---------|-------|-----------|
| `/api/auth/*` | público / `administrador` na gestão | Login por SSO Microsoft, refresh/logout e usuários/papéis |
| `/v1/status` | `basico` | Stub do módulo core |
| `/health` | público | Liveness do processo |
| `/health/ready` | público | Readiness: PostgreSQL conectado, Alembic exatamente no head e Redis disponível |
| `/metrics` | header `X-Metrics-Key` | Métricas Prometheus, incluindo realtime — 401 sem o header, 403 com chave inválida, 503 quando `METRICS_API_KEY` não está configurada |
