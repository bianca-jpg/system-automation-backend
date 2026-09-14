# Realtime

O realtime avisa o frontend de que uma fonte canônica mudou. Ele não substitui
os endpoints REST nem envia pedidos completos pelo WebSocket: ao receber um
evento, o cliente atualiza a consulta REST correspondente.

## Arquitetura e garantias

```text
caso de uso + outbox (mesma transação PostgreSQL)
                    │
                    ▼
          relay com retry/backoff
                    │
                    ▼
              Redis Stream
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
   instância FastAPI A  instância FastAPI B
          │                   │
      WebSockets locais   WebSockets locais
```

- `record_event()` apenas adiciona o evento e faz `flush`; o caso de uso que
  alterou o negócio continua sendo o único responsável pelo `commit` ou
  `rollback`. Assim, não existe mudança de negócio sem o respectivo outbox.
- A entrega é **pelo menos uma vez**. Uma queda entre publicar no Redis e marcar
  o outbox como publicado pode repetir um evento; consumidores deduplicam por
  `eventId`.
- `sequence` é global, crescente e ordenada pelo commit. Cada tópico também tem
  uma sequência interna usada para calcular a quantidade não lida.
- Falha no Redis não desfaz a operação de negócio. O relay mantém o evento no
  PostgreSQL e tenta novamente com backoff exponencial, limitado a 5 minutos.
- Um advisory lock elege somente um relay líder. Em cada ciclo ele processa no
  máximo `REALTIME_RELAY_BATCH_SIZE` eventos, reclamando o head um a um; falha
  em N encerra o ciclo e impede N+1 de ultrapassá-lo.
- Cada instância lê o Stream sem consumer group, recebe todo o fanout e entrega
  somente às suas conexões locais.
- As filas por conexão são limitadas. Cliente lento é fechado com código `1013`
  (`backpressure`) para não consumir memória sem controle.

O primeiro ticket de cada usuário cria os cursores ausentes no watermark atual.
Isso estabelece um baseline server-side e impede que o primeiro acesso marque
todo o histórico anterior como novidade.

Para fontes que representam um conjunto ativo, o ledger mantém `active` e
`last_seen_at`: uma chave que sai do snapshot fica inativa e, se reaparecer em
uma sincronização posterior, volta a ser tratada como entrada nova. Snapshot
vazio só desativa tudo quando o chamador o declara autoritativo, evitando falsos
alertas durante falhas transitórias da origem.

## Contrato do evento

```json
{
  "version": 1,
  "eventId": "3e84e878-9ae8-4fb2-85d3-bef13d93346d",
  "sequence": 42,
  "type": "orders.grade_changed.v1",
  "topic": "orders",
  "occurredAt": "2026-08-08T04:02:03.456000Z",
  "payload": { "orderId": 123 }
}
```

Tópicos e tipos usam minúsculas e aceitam apenas `a-z`, `0-9`, `.`, `_` e `-`.
O payload precisa ser um objeto JSON e respeitar `REALTIME_PAYLOAD_MAX_BYTES`.
Os tópicos públicos padrão são `orders`, `alerts`, `communications` e `history`.

Eventos emitidos pelos fluxos atuais incluem `orders.changed.v1`,
`alerts.changed.v1`, `history.changed`, `alerts.new.v1`,
`orders.processed.v1`, `orders.grade_changed.v1`, `orders.approved.v1`,
`history.changed.v1` e `communication.created`. O payload é apenas um sinal de
invalidação com identificadores mínimos; a tela sempre reconcilia via REST.

No full sync, os ledgers contam as entidades novas, mas não produzem um evento
por pedido ou alerta. Cada sincronização emite no máximo uma invalidação
agregada por tópico alterado: `orders.changed.v1` traz `newOrderCount` e
`newProductCount`, `alerts.changed.v1` traz `newAlertCount` e
`history.changed` traz `count`.

Para emitir um evento dentro de outro bounded context:

```python
from app.modules.realtime import record_event

await alterar_dados(db)
await record_event(
    db,
    topic="orders",
    event_type="orders.grade_changed.v1",
    payload={"orderId": nr_pedido},
    event_id=idempotency_uuid,
)
await db.commit()
```

Quando o chamador fornece `event_id`, repetir exatamente o mesmo contrato é
idempotente e devolve o evento já persistido. Reusar esse UUID com tópico, tipo,
instante ou payload diferente é recusado.

## API de controle

Todas as rotas HTTP abaixo exigem `Authorization: Bearer <access token>` e papel
mínimo de visualização.

### Emitir ticket

`POST /api/v1/realtime/tickets`

```json
{
  "ticket": "token-opaco-de-uso-unico",
  "expiresIn": 30,
  "websocketUrl": "/api/v1/realtime/ws"
}
```

O ticket fica no Redis somente pelo TTL configurado, vincula o usuário, a
expiração do access token e o conjunto completo de tópicos, é consumido de forma
atômica na abertura do socket e não pode ser reutilizado. A emissão tem rate
limit por usuário e IP; excesso retorna `429` com `Retry-After: 60`. Redis
indisponível retorna `503` com `Retry-After: 2`.

### Consultar watermarks e não lidos

`GET /api/v1/realtime/status?topics=orders,alerts`

```json
{
  "lastSequence": 42,
  "topics": {
    "orders": {
      "latestSequence": 42,
      "lastReadSequence": 40,
      "unseen": 2,
      "latest": null
    }
  }
}
```

`topics` é opcional; sem ele, a API devolve todos os tópicos permitidos.

### Marcar tópico como lido

`POST /api/v1/realtime/read`

```json
{ "topic": "orders", "throughSequence": 42 }
```

O cursor é monotônico: uma confirmação atrasada não o move para trás. Confirmar
além do watermark atual retorna `422`. Se a sequência ficou anterior à janela
retida, retorna `409` e o cliente deve reconciliar via `/status` e REST.

## WebSocket, replay e reconexão

O navegador abre diretamente o backend:

```text
wss://<api>/api/v1/realtime/ws
  ?ticket=<ticket>
  &lastSequence=40
  &topics=orders,alerts,communications,history
```

O bearer token não vai na URL. O handshake exige um `Origin` presente em
`CORS_ORIGINS`, consome o ticket e revalida o usuário no banco. O parâmetro
`topics`, quando enviado, deve ser exatamente o conjunto completo vinculado ao
ticket; seleção parcial não é suportada no WS v1. Depois do aceite, o servidor
envia:

```json
{
  "type": "hello",
  "version": 1,
  "lastSequence": 40,
  "latestSequence": 42,
  "heartbeatSeconds": 20,
  "topics": {
    "orders": {
      "latestSequence": 42,
      "lastReadSequence": 40,
      "unseen": 2,
      "latest": null
    }
  }
}
```

`lastSequence` no `hello` repete o cursor de retomada informado pelo cliente; ele
não deve ser avançado para `latestSequence` antes do replay. Em uma reconexão, o
servidor reproduz, em ordem, os eventos posteriores a esse cursor até o
watermark e, ao terminar, envia:

```json
{ "type": "replay_complete", "lastSequence": 42 }
```

Somente então o cliente pode consolidar o watermark. Se o cursor já foi
removido pela retenção ou o limite seria excedido, envia:

```json
{
  "type": "resync_required",
  "reason": "cursor anterior à janela de replay retida",
  "lastSequence": 42
}
```

Nesse caso o servidor fecha com `1012`; o cliente consulta `/status`, refaz as
leituras REST e abre uma nova conexão a partir do watermark reconciliado.

O servidor envia frames `ping`; o único frame aceito do cliente é
`{"type":"pong"}`. Mensagens acima de 1 KiB fecham com `1009`; JSON inválido ou
mensagem não suportada usam `1007`/`1003`. Ticket expirado ou reutilizado usa
`4408` e pede emissão de um novo ticket. Origem, tópicos ou autorização inválidos
usam `1008` (terminal). Indisponibilidade, limite de conexões, gap no Stream e
backpressure usam `1013`. Autorização e expiração do access token são
revalidadas durante a conexão e sockets ociosos são encerrados.

## Configuração

| Variável | Padrão | Uso |
|----------|--------|-----|
| `REALTIME_STREAM_KEY` | `automation:realtime:v1` | Chave do Redis Stream |
| `REALTIME_REDIS_CONNECT_TIMEOUT_SECONDS` | `2.0` | Timeout para conectar ao Redis |
| `REALTIME_REDIS_SOCKET_TIMEOUT_SECONDS` | `5.0` | Timeout das operações Redis |
| `REALTIME_STREAM_MAXLEN` | `20000` | Retenção aproximada do Stream |
| `REALTIME_PAYLOAD_MAX_BYTES` | `16384` | Limite do payload JSON |
| `REALTIME_TICKET_TTL_SECONDS` | `30` | Validade do ticket de uso único |
| `REALTIME_TICKET_USER_RATE_PER_MINUTE` | `12` | Limite de tickets por usuário |
| `REALTIME_TICKET_IP_RATE_PER_MINUTE` | `60` | Limite de tickets por IP |
| `REALTIME_CONNECTION_QUEUE_SIZE` | `128` | Fila bounded por socket |
| `REALTIME_MAX_CONNECTIONS_PER_USER` | `4` | Sockets simultâneos por usuário |
| `REALTIME_MAX_CONNECTIONS_PER_INSTANCE` | `1000` | Sockets por instância FastAPI |
| `REALTIME_HEARTBEAT_SECONDS` | `20` | Intervalo do heartbeat |
| `REALTIME_IDLE_TIMEOUT_SECONDS` | `65` | Prazo sem `pong` |
| `REALTIME_AUTH_REVALIDATE_SECONDS` | `60` | Intervalo da revalidação do usuário |
| `REALTIME_RELAY_INTERVAL_MS` | `250` | Intervalo base do relay |
| `REALTIME_RELAY_BATCH_SIZE` | `100` | Máximo por ciclo do líder; o head é reclamado um a um |
| `REALTIME_REPLAY_MAX_EVENTS` | `250` | Máximo reproduzido por conexão |
| `REALTIME_SEND_TIMEOUT_SECONDS` | `5` | Timeout de um frame enviado ao socket |
| `REALTIME_REPLAY_TIMEOUT_SECONDS` | `15` | Prazo total do replay inicial |
| `REALTIME_OUTBOX_RETENTION_DAYS` | `14` | Retenção de eventos publicados no Postgres |
| `REALTIME_CLEANUP_INTERVAL_SECONDS` | `3600` | Intervalo da limpeza do outbox |
| `REALTIME_ALLOWED_TOPICS` | `orders,alerts,communications,history` | Allowlist pública |

Os valores e limites aceitos estão centralizados em `Settings`; use
`.env.example` como molde e nunca versione tickets, JWTs ou credenciais Redis.

## Operação e observabilidade

Antes de subir uma versão que usa realtime, aplique a migration:

```bash
uv run alembic upgrade head
```

O lifespan da API inicia relay, listener e limpeza; no shutdown cancela essas
tasks, fecha sockets locais, Redis e PostgreSQL. O endpoint `/metrics` expõe:

- `automation_realtime_ws_active`;
- `automation_realtime_events_delivered_total`;
- `automation_realtime_ws_backpressure_total`;
- `automation_realtime_ws_closed_total`;
- `automation_realtime_outbox_relay_failures_total`;
- `automation_realtime_outbox_pending`.

Alertas operacionais devem observar crescimento contínuo do outbox pendente,
falhas do relay e fechamentos por backpressure. Logs registram somente classe de
erro e contagens; payloads, tickets e dados do usuário não devem ser logados.
