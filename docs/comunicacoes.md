# Comunicações por e-mail

O envio usa um **delivery outbox transacional**. A API não abre conexão SMTP:
ela persiste a comunicação, a entrega pendente e o evento realtime no mesmo
commit e responde `202 Accepted`. O Celery beat encontra as entregas pendentes;
por isso uma indisponibilidade do broker durante o POST não perde a intenção
nem prende a resposta HTTP.

```text
POST /api/v1/comunicacoes
      │  uma transação PostgreSQL
      ├── comunicacoes (Pendente)
      ├── communication_email_deliveries (pending)
      └── realtime_outbox (communication.created)
             │
             ▼ Celery beat
        claim SKIP LOCKED → commit → SMTP → transição + realtime
```

## Contrato HTTP e idempotência

`POST /api/v1/comunicacoes` exige papel `operacional` ou superior e o header
`Idempotency-Key`. A chave aceita 8–128 caracteres ASCII (`A-Z`, `a-z`,
`0-9`, `.`, `_`, `:`, `-`), sem espaços. Ela é isolada pelo identificador do
ator autenticado:

- mesma chave, ator e payload: `202` com o mesmo recurso e estado atual;
- mesma chave/ator com payload diferente: `409`;
- a mesma chave usada por outro ator representa outra intenção e nunca expõe
  o payload do primeiro;
- replay não cria outra entrega, outro evento ou outro envio.

Novas intenções usam rate limit atômico no Redis (5/min por ator e 20/min por
IP). Se esse controle estiver indisponível, o endpoint falha fechado com `503`
e `Retry-After`, sem persistir uma nova intenção. Um replay já persistido não
consulta o Redis e continua retornando `202`.

A resposta inclui `Location: /api/v1/comunicacoes/{id}`. Esse endpoint e o feed
paginado exigem `basico`, preservando o RBAC de leitura existente. O feed
continua expondo destinatário e conteúdo aos viewers autorizados por
compatibilidade funcional.

Os campos de acompanhamento são `attemptCount`, `maxAttempts`, `createdAt`,
`updatedAt`, `sentAt` e, enquanto pendente, `nextAttemptAt`. Erro bruto, resposta
SMTP e credenciais nunca entram no contrato.

## Estados

| API | Delivery | Significado | Reenvio automático |
|-----|----------|-------------|----------------------|
| `Pendente` | `pending`/`processing` | aguardando ou em uma tentativa com lease | somente falha comprovadamente transitória |
| `Enviado` | `sent` | servidor SMTP confirmou aceite | não |
| `Falhou` | `failed` | rejeição permanente comprovada ou tentativas seguras esgotadas | não |
| `Incerto` | `unknown` | o processo perdeu a confirmação e o SMTP pode ter aceitado | **não**; verificar antes de qualquer ação |

SMTP não oferece exatamente-uma-vez no caso de queda depois do aceite. Cada
entrega usa um `Message-ID` determinístico para reduzir duplicatas, mas isso não
é uma garantia do protocolo. Timeout de leitura, desconexão ambígua, exceção
inesperada e lease de `processing` expirado viram `Incerto` em vez de serem
reenviados. Erros 4xx explicitamente recusados e falhas de conexão anteriores
ao envio usam backoff exponencial limitado; 5xx e configuração inválida vão
para `Falhou`.

Transições terminais geram, na mesma transação, `communication.sent`,
`communication.failed` ou `communication.unknown` no tópico realtime
`communications`. Retry transitório e replay HTTP não geram evento.

## Concorrência, retry e reconciliação

O worker reivindica uma entrega de cada vez com `FOR UPDATE SKIP LOCKED`, muda
para `processing` e faz commit **antes** do SMTP. O lease é maior que o timeout
wall-clock da conversa SMTP completa, com margem obrigatória de 30 segundos.
Uma falha transitória volta a
`pending` com jitter determinístico e backoff bounded; o número máximo de
tentativas é persistido por entrega.

O beat executa `app.workers.tasks.comunicacoes.processar_entregas` no intervalo
configurado. Ele cobre tanto novas intenções quanto jobs anteriores que não
chegaram a iniciar. Não há publish Celery inline no POST.

Variáveis:

- `COMMUNICATION_DELIVERY_INTERVAL_SECONDS` (default `30`);
- `COMMUNICATION_DELIVERY_BATCH_SIZE` (`1..100`);
- `COMMUNICATION_DELIVERY_MAX_ATTEMPTS` (`1..20`);
- `COMMUNICATION_DELIVERY_LEASE_SECONDS`;
- `COMMUNICATION_DELIVERY_RETRY_BASE_SECONDS`;
- `COMMUNICATION_DELIVERY_RETRY_MAX_SECONDS`.

## Observabilidade e operação

`/metrics` publica claims, resultados e duração SMTP com labels de
cardinalidade fixa. Logs do worker contêm apenas contagens e classes/códigos
normalizados; não incluem destinatário, conteúdo nem texto devolvido pelo
provider. A tabela persiste `last_error_code` sanitizado para diagnóstico
interno, mas esse campo não é exposto pela API.

Uma verificação operacional segura por estado:

```sql
SELECT status, count(*)
FROM communication_email_deliveries
GROUP BY status
ORDER BY status;
```

A revision `022` mantém comunicações legadas como `Enviado` e não cria
entregas retroativas. O downgrade é recusado se chaves iguais já tiverem sido
usadas por atores diferentes, pois voltar à unicidade global perderia dados.
