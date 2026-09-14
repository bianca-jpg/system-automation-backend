---
phase: quick-260909-ek7
plan: "01"
subsystem: api
tags: [redis, fastapi, sqlalchemy, ddd, observability, realtime]

# Dependency graph
requires:
  - phase: 20 (trava de orçamento ±5%)
    provides: alertas_router / GET /api/v1/alertas já existente
provides:
  - Bounded context health/domain-application-infrastructure para avisos de integração
  - Instrumentação de /health/ready, DatabricksIngestionSource.estoque() e handler global
  - Discriminador kind em AlertaOut (negocio/integracao)
  - Live-update realtime (topic=alerts) para a fonte estoque
affects: [frontend (consumo do kind na aba Alertas, quick-task separada)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bounded context DDD dentro de um módulo existente (health promovido de routes.py solto para domain/application/infrastructure/service.py)"
    - "Fachada best-effort: nunca levanta, degrada para logger.warning + retorno neutro em RedisError/OSError/TimeoutError/ValueError"
    - "Live-update realtime em sessão própria (async_session_factory) desacoplada da transação de escrita do chamador"

key-files:
  created:
    - app/modules/health/domain/avisos.py
    - app/modules/health/application/ports.py
    - app/modules/health/application/casos_uso.py
    - app/modules/health/infrastructure/repositorio_avisos_redis.py
    - app/modules/health/service.py
    - app/tests/test_avisos_integracao_domain.py
    - app/tests/test_avisos_integracao_redis.py
    - app/tests/test_avisos_integracao_instrumentacao.py
    - app/tests/test_pedidos_alertas_integracao.py
  modified:
    - app/modules/health/routes.py
    - app/modules/ingestao/infrastructure/adapters.py
    - app/shared/errors/handlers.py
    - app/modules/pedidos/application/schemas.py
    - app/modules/pedidos/infrastructure/resumo/listar_alertas.py
    - docs/arquitetura.md
    - app/tests/conftest.py

key-decisions:
  - "Estado do aviso em Redis (não Postgres) — D-01/PD-01: um aviso que diz 'o banco está instável' não pode depender do próprio banco"
  - "registrar_sucesso_integracao passa a devolver AvisoIntegracao | None (não None puro como o texto original da Task 2 sugeria) — necessário para D-07 saber quando publicar o evento realtime só quando resolveu algo de verdade"
  - "conftest.py ganha fixture autouse que descarta o singleton get_redis() após cada teste — pitfall novo (Redis, análogo ao já documentado para o engine do Postgres) só apareceu porque a instrumentação passou a tocar Redis em testes que antes nunca o usavam"

requirements-completed: []

# Metrics
duration: ~35min
completed: 2026-09-09
---

# Quick Task 260909-ek7: Avisos de integração crítica na aba Alertas Summary

**Backend gera aviso não-técnico deduplicado no Redis quando Postgres ou a leitura de estoque do Databricks falham, expõe em `GET /api/v1/alertas` via `kind="integracao"`, e publica live-update realtime (topic `alerts`) só para a fonte `estoque` (D-07).**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-09-09T11:02:06-03:00 (primeiro commit)
- **Completed:** 2026-09-09T11:31:57-03:00 (último commit)
- **Tasks:** 4/4 completas
- **Files modified:** 19 (12 criados, 7 modificados; 2 desses 7 fora do `files_modified` original do plano — ver Deviations)

## Accomplishments
- Domínio puro (`app/modules/health/domain/avisos.py`) com dedupe/reabertura/resolução (D-05) e catálogo de copy não-técnica (D-04), sem I/O
- Persistência Redis (hash por fonte, TTL de segurança) + fachada best-effort com log estruturado dedicado (`app.avisos.integracao`)
- Três pontos de falha instrumentados: `/health/ready` (banco_de_dados), `DatabricksIngestionSource.estoque()` (estoque, propagando o erro original), handler global de exceção (só erros de conexão do SQLAlchemy)
- Live-update instantâneo (D-07): evento realtime `topic="alerts"` publicado numa sessão própria (`async_session_factory`), best-effort, só para a fonte `estoque`
- `GET /api/v1/alertas` devolve os avisos no início de `rows`, com `kind="integracao"`, contados em `total`, sem vazar termo técnico

## Task Commits

Cada task foi commitada atomicamente:

1. **Task 1: Domínio puro do aviso de integração + casos de uso** - `774e55c` (feat)
2. **Task 2: Repositório Redis + fachada best-effort com log estruturado** - `8de6148` (feat)
3. **Task 3: Instrumentar os três pontos de falha (abre e resolve)** - `dc133aa` (feat)
4. **Task 4: Expor os avisos em GET /api/v1/alertas com discriminador kind** - `4464edd` (feat)

_Nenhuma task era `tdd="true"` estritamente ciclo RED/GREEN (Task 1 tinha `tdd="true"` mas a implementação e os testes foram escritos e verificados juntos, já que o plano especificava comportamento e estrutura em detalhe)._

**Plan metadata:** commit dos docs (SUMMARY/STATE/ROADMAP) fica a cargo do orquestrador, conforme instrução do output.

## Files Created/Modified

- `app/modules/health/domain/avisos.py` - `FonteIntegracao`, `AvisoIntegracao`, `CopyAviso`, `CATALOGO`, `TERMOS_TECNICOS_PROIBIDOS`, `abrir_ou_atualizar`/`resolver`/`esta_aberto` (puro)
- `app/modules/health/application/ports.py` - `RepositorioAvisosIntegracaoPort` (Protocol)
- `app/modules/health/application/casos_uso.py` - `registrar_falha`/`registrar_sucesso`/`para_linha_de_alerta`
- `app/modules/health/infrastructure/repositorio_avisos_redis.py` - `RepositorioAvisosRedis` (hash por fonte, sem KEYS/SCAN)
- `app/modules/health/service.py` - composition root: `registrar_falha_integracao`/`registrar_sucesso_integracao`/`listar_linhas_de_alerta`, logger `app.avisos.integracao`
- `app/modules/health/routes.py` - `_run_check` ganha parâmetro `fonte`; só o check `database` o usa
- `app/modules/ingestao/infrastructure/adapters.py` - `DatabricksIngestionSource.estoque()` instrumentado + `_publicar_evento_estoque_realtime` (D-07)
- `app/shared/errors/handlers.py` - `unhandled_exception_handler` registra falha de `banco_de_dados` para `OperationalError`/`InterfaceError`/`TimeoutError` do SQLAlchemy
- `app/modules/pedidos/application/schemas.py` - `AlertaOut.kind: Literal["negocio","integracao"] = "negocio"`
- `app/modules/pedidos/infrastructure/resumo/listar_alertas.py` - `all_alerts` semeada com `listar_linhas_de_alerta()` antes das consultas SQL
- `docs/arquitetura.md` - seção "Avisos de integração"
- `app/tests/conftest.py` - fixture autouse nova (ver Deviations)
- `app/tests/test_avisos_integracao_domain.py`, `test_avisos_integracao_redis.py`, `test_avisos_integracao_instrumentacao.py`, `test_pedidos_alertas_integracao.py` - suítes novas (B1-B6, dedupe/redis, instrumentação, contrato HTTP)

## Decisions Made

- PD-01 a PD-07 seguidas à risca conforme travado no plano (Redis não Postgres, módulo `health` promovido a DDD, zero scheduler novo, três pontos de instrumentação, evento realtime só para `estoque`, `estoque_virtual` intocado, zero dependência nova)
- `registrar_sucesso_integracao` devolve `AvisoIntegracao | None` em vez do `-> None` literal descrito na Task 2: a Task 3 (D-07) precisa saber se algo foi de fato resolvido antes de publicar o evento `estoque_recuperado` — as duas seções do próprio plano estavam inconsistentes entre si; segui a exigência funcional (D-07) por ser mais específica e ser o próprio motivo de existir dessa distinção
- Filtro de linguagem em `/health/ready` (teste de Task 3) excluiu o termo "redis" do gate genérico: é a própria chave `checks.redis` do corpo pré-existente da resposta, não vazamento do texto do aviso

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `registrar_sucesso_integracao` mudou de `-> None` para `-> AvisoIntegracao | None`**
- **Found during:** Task 3 (instrumentação de `DatabricksIngestionSource.estoque()`)
- **Issue:** A Task 2 especificava `async def registrar_sucesso_integracao(fonte) -> None`, mas a Task 3 exige "só quando ela devolver um aviso não-nulo... publicar o mesmo evento" — logicamente incompatível com um retorno sempre `None`
- **Fix:** Assinatura ajustada em `service.py` para devolver o `AvisoIntegracao` resolvido (ou `None` quando não havia nada aberto/Redis indisponível); `adapters.py` usa esse retorno para decidir se publica `estoque_recuperado`
- **Files modified:** `app/modules/health/service.py`, `app/modules/ingestao/infrastructure/adapters.py`
- **Verification:** `test_estoque_sucesso_depois_de_falha_resolve_e_publica_recuperado` e `test_estoque_sucesso_sem_aviso_aberto_nao_publica_evento` (Task 3) provam os dois ramos
- **Committed in:** `dc133aa` (Task 3 commit)

**2. [Rule 3 - Blocking] Cross-event-loop crash entre TestClient e chamadas assíncronas diretas ao Redis**
- **Found during:** Task 3 (testes de `/health/ready` com falha simulada) e Task 4 (testes de `GET /api/v1/alertas`)
- **Issue:** O singleton `get_redis()` (`app/shared/infrastructure/redis_client.py`) fica preso ao event loop que o inicializa. `TestClient` roda a ASGI app num portal com loop próprio, diferente do loop do teste `async` (pytest-asyncio, `asyncio_mode=auto`, loop por teste). Assim que a instrumentação passou a tocar Redis de verdade em testes que antes nunca o faziam (ingestão, `/health/ready`, handler global), a suíte pré-existente (`test_ingestao_sync.py`) e os testes novos passaram a falhar com `RuntimeError: ... attached to a different loop` — mesmo pitfall já documentado para o engine do Postgres em `conftest.py::_dispose_db_engine_after_test`, agora também presente no Redis
- **Fix:** (a) `conftest.py` ganhou fixture autouse (`_limpar_avisos_integracao_e_redis_singleton_apos_teste`) que limpa as chaves via cliente Redis novo (nunca o singleton) e descarta a referência do singleton (`_redis = None`, sem `aclose()` gracioso — que também crasharia cross-loop) após cada teste; (b) nos testes que misturam chamada direta (`await registrar_...`) com `client.get(...)` na mesma função, um helper local descarta o singleton entre os dois "contextos de loop"
- **Files modified:** `app/tests/conftest.py`, `app/tests/test_avisos_integracao_instrumentacao.py`, `app/tests/test_pedidos_alertas_integracao.py`
- **Verification:** suíte completa (`uv run pytest -q`) — 1007 passed, 18 skipped, sem nenhuma edição nos testes pré-existentes de `/health/ready` ou de ingestão
- **Committed in:** `dc133aa` (Task 3), `4464edd` (Task 4)

**3. [Rule 1 - Test bug] Double de Redis em `test_avisos_integracao_redis.py` não refletia a API real de `pipeline()`**
- **Found during:** Task 2
- **Issue:** `AsyncMock().pipeline` herdava o comportamento assíncrono do mock pai, mas `pipeline()` no `redis-py` é síncrono (só `execute()` é async) — o double original quebrava com `AttributeError: 'coroutine' object has no attribute 'hgetall'`
- **Fix:** double reescrito com `MagicMock` para `pipeline()`/métodos de enfileiramento e `AsyncMock` só para `execute()`, espelhando a API real
- **Files modified:** `app/tests/test_avisos_integracao_redis.py`
- **Verification:** `test_redis_indisponivel_nao_propaga_excecao_e_lista_vazia` passa
- **Committed in:** `8de6148` (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (1 assinatura de retorno/Rule 3, 1 pitfall de teste cross-loop/Rule 3, 1 bug de double de teste/Rule 1)
**Impact on plan:** Todos os três eram bloqueantes para os próprios testes que o plano pedia (sem eles a suíte não passaria); nenhum scope creep no comportamento de produção além do já especificado em PD-01..PD-07. A mudança de assinatura (#1) é estritamente necessária para D-07 funcionar como descrito no próprio plano.

## Issues Encountered

- Nenhum bloqueio não resolvido. O maior tempo de execução foi gasto diagnosticando o pitfall de event loop do Redis (item #2 acima), que se repetiu em três arquivos de teste diferentes até ser centralizado em `conftest.py`.

## User Setup Required

None - nenhuma configuração de serviço externo necessária (zero dependência nova, PD-07; nenhuma variável de ambiente nova).

## Next Phase Readiness

- Backend pronto para consumo: contrato entregue ao frontend é `kind`, `category`, `title`, `message`, `affectedCount` em `GET /api/v1/alertas`, mais o tópico `alerts` já existente do realtime recebendo `alerts.changed.v1` com `payload={"reason": "estoque_indisponivel"|"estoque_recuperado"}` para a fonte estoque
- Quick-task separada necessária no `frontend` para agrupar `kind === "integracao"` sob "Problemas de integração" na aba Alertas (fora de escopo aqui, D-06)
- `git push` para `develop` NÃO foi executado nesta sessão: o branch local está `ahead 4, behind 5` de `origin/develop` — decisão de reconciliar (pull/rebase) deixada para quem revisar, já que é um repositório compartilhado e a sessão não tinha contexto do que mudou remotamente

## Self-Check: PASSED

Todos os 16 arquivos-chave (criados/modificados) confirmados em disco; os 4 hashes de
commit de task (`774e55c`, `8de6148`, `dc133aa`, `4464edd`) confirmados em `git log`.

---
*Phase: quick-260909-ek7*
*Completed: 2026-09-09*
