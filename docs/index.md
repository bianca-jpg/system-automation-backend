# automation - OR — Backend

API da otimização de Ordens de Reserva do automation. Ela mantém snapshots do
Databricks no PostgreSQL, executa o motor de adequação, expõe consultas
paginadas por produto/cliente e avisa mudanças por WebSocket.

## Documentação

| Página | Conteúdo |
|--------|----------|
| [Arquitetura](arquitetura.md) | Camadas, projeções SQL, transações, migrations e RBAC |
| [Ingestão](ingestao.md) | Cinco fontes Databricks, full refresh, projeção e agenda de 2 horas |
| [Motor de adequação](adequacao.md) | Estoque, tolerância, edição versionada e aprovação |
| [API / Endpoints](api.md) | Rotas, paginação, papéis e conflitos |
| [Comunicações](comunicacoes.md) | Delivery outbox, idempotência, estados SMTP e operação |
| [Realtime](realtime.md) | Outbox, Redis Stream, WebSocket, replay e operação |
| [Segurança](seguranca.md) | Login SSO-only, detecção/remoção de contas legadas, rotação da chave de métricas, revogação de sessão e reuso de refresh token |

## Stack

- Python 3.13 e `uv` com lockfile congelado;
- FastAPI, SQLAlchemy async/`asyncpg` e Alembic;
- PostgreSQL para snapshots, estado e outbox;
- Redis para Celery, rate limit, tickets e Stream realtime;
- Celery worker/beat para ingestão, processamento incremental de ORs e delivery de e-mail;
- Databricks SQL Statement Execution API como origem;
- Docker Compose para desenvolvimento local.

## Visão geral

```text
Databricks (5 fontes)
      │  job durável: beat a cada 2 h ou POST 202 /api/v1/ingestao/sincronizar
      ▼
PostgreSQL: snapshots + pedido_produto_read + outbox
      │
      ├── GET /api/v1/pedidos/produtos ─────────► página de produtos (keyset)
      ├── GET /api/v1/pedidos/produtos/clientes ► clientes sob demanda (keyset)
      ├── POST /api/v1/pedidos/processamentos ─► ledger ─► Celery ─► plano/checkpoints ─► ORs
      ├── POST /api/v1/comunicacoes ─► delivery outbox ─► Celery ─► SMTP
      └── outbox ─► Redis Stream ─► WebSocket ─► frontend reconcilia via REST
```

As leituras nunca transferem a base inteira. Agregação, filtro, ordenação e
paginação acontecem no banco; Franquia e Multimarca mantêm identidades separadas.
Escritas em lote usam versão otimista e uma única transação. Eventos realtime
são persistidos junto da mudança de negócio e servem somente como sinal de
invalidação — a resposta REST continua sendo canônica.

## Como rodar

Setup, migrations, banco/Redis isolados para testes e comandos estão no
[`README.md`](https://github.com/your-org/backend#readme) do
repositório.
