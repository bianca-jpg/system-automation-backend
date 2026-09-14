---
phase: quick/260827-f8o
plan: 01
subsystem: api
tags: [fastapi, health-check, logging, information-disclosure]

# Dependency graph
requires: []
provides:
  - "GET /health/ready sanitizado: cada check devolve só 'ok'/'fail'/'unavailable', nunca o nome da classe da exceção"
  - "Helper _run_check centraliza o ponto onde o detalhe da exceção é descartado da resposta e logado no servidor"
affects: [health, observability]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Sanitizar corpo de resposta pública + logar detalhe no servidor via logger.error(\"...: %s\", type(exc).__name__) — mesmo padrão de ingestao/infrastructure/http/routes.py:115"

key-files:
  created: []
  modified:
    - app/modules/health/routes.py
    - app/tests/test_health.py

key-decisions:
  - "Valor de falha é a string \"fail\" (não bool) — o corpo já é dict[str, str]/Literal e carrega 'unavailable'"
  - "databaseSchema = \"unavailable\" preservado quando Postgres falha (não é detalhe de exceção)"
  - "logger.error sem traceback (sem logger.exception) — probe roda a cada 10s, traceback completo viraria ruído de log"
  - "Helper _run_check extraído em vez de sanitizar os três except no lugar — único ponto que uma regressão futura poderia reabrir"

patterns-established:
  - "CheckStatus = Literal[\"ok\", \"fail\", \"unavailable\"] como enum fechado para corpo de readiness"

requirements-completed: [QUICK-260827-f8o]

duration: ~20min
completed: 2026-08-27
---

# Quick Task 260827-f8o: Sanitizar GET /health/ready Summary

**GET /health/ready deixa de vazar nome de classe de exceção (ConnectionError, DatabaseSchemaNotReadyError etc.) no corpo público; detalhe migra para logger.error no servidor via helper único `_run_check`.**

## Performance

- **Duration:** ~20min
- **Tasks:** 2 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments
- `_run_check` centraliza a execução dos três checks (`check_database`, `check_database_schema`, `check_redis`), devolvendo um `CheckStatus` fechado (`Literal["ok", "fail", "unavailable"]`) em vez de propagar `str(exc.__class__.__name__)` para a resposta HTTP
- Nome da classe da exceção continua recuperável — agora em `logger.error("Readiness check %s falhou: %s", nome, type(exc).__name__)`, sem traceback
- Guarda de regressão parametrizada em `test_health.py` prova, sobre o corpo bruto (`response.text`), que nenhuma das strings sensíveis (`ConnectionError`, `ConnectionRefusedError`, `RuntimeError`, `DatabaseSchemaNotReadyError`, `Traceback`, mensagens de exceção) sobrevive em dois cenários de falha combinada
- Códigos HTTP (200/503), campo `status` de topo (`ok`/`degraded`) e `GET /health` (liveness) permanecem byte a byte como antes

## Task Commits

Each task was committed atomically:

1. **Task 1: RED — testes afirmam status resumido, ausência de vazamento e log server-side** - `632b995` (test)
2. **Task 2: GREEN — helper _run_check devolve status resumido e loga a classe da exceção** - `f6ca053` (fix)

_TDD: RED confirmado com 5 falhas por asserção (2 testes reescritos + 2 parametrizações da guarda de vazamento + 1 teste de caplog), zero erro de import/fixture; GREEN fechou todos os 9 testes do arquivo sem editar Task 1._

## Files Created/Modified
- `app/modules/health/routes.py` - Helper `_run_check` + `CheckStatus` (Literal fechado); `health_readiness` reescrito para usar o helper; `health_liveness` intocado
- `app/tests/test_health.py` - Dois testes existentes reescritos para esperar `"fail"`; helper `_readiness()` para os testes novos; guarda de vazamento parametrizada (2 cenários) sobre `response.text`; teste de `caplog` provando que o detalhe foi para o log

## Decisions Made
Nenhuma decisão nova além das já travadas no PLAN.md (D-01 a D-10) — plano seguido como escrito, incluindo a docstring de `_run_check` escrita em prosa (sem citar `__class__.__name__` como token de código, para não invalidar o próprio gate de grep negativo).

**Nota sobre `caplog`:** funcionou com `caplog.at_level(logging.ERROR)` direto (sem precisar do fallback `caplog.set_level(..., logger="app.modules.health.routes")` cogitado no plano) — o `TestClient` propaga através do handler do pytest normalmente.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Verification Results

1. `uv run pytest app/tests/test_health.py -v` — 9/9 verdes (6 originais + 3 novos casos: 2 parametrizações da guarda de vazamento + 1 de caplog)
2. `uv run ruff check app/modules/health/routes.py app/tests/test_health.py` — limpo
3. `uv run ruff format --check app/modules/health/routes.py app/tests/test_health.py` — limpo
4. `uv run pytest app/tests -q` (ignorando `test_pedidos_processing_sem_adequar_memory_024.py`, que falha na coleta por `ModuleNotFoundError: No module named 'resource'` — módulo stdlib Unix-only, ambiente é Windows, pré-existente e fora do escopo desta tarefa) — **926 passed, 18 skipped, 2 failed**. As 2 falhas (`test_auth_flows.py::test_delete_user_preserva_change_request_zerando_requested_by` e `test_schema_guard.py::test_colunas_do_model_existem_no_banco[parametro_change_requests]`) são pré-existentes e não relacionadas — mesmo padrão de drift de schema já documentado no STATE.md (quick task 260827-efy: "916 passed/18 skipped/2 failed, ambos pre-existentes"); nenhum failure novo introduzido por esta tarefa
5. Gate de sanitização: `! grep -q '__class__.__name__' app/modules/health/routes.py` (OK, ausente) e `grep -q 'Literal\[' app/modules/health/routes.py` (OK, `CheckStatus` presente)
6. Gate de escopo: `git diff --stat` dos dois commits desta tarefa lista exatamente `app/modules/health/routes.py` (64 linhas alteradas) e `app/tests/test_health.py` (88 linhas alteradas) — nada em `app/shared/`, `docs/`, `.docker/`, `app/main.py`
7. Gate de liveness intocado: diff de `health_liveness` vazio — a única mudança adjacente foi a inserção do alias `CheckStatus` logo antes da função, sem tocar seu corpo

## Next Phase Readiness
Tarefa isolada e fechada; nenhum bloqueio para o próximo passo do milestone v1.3 (`/gsd-plan-phase 13` ou `14`).

---
*Quick task: 260827-f8o*
*Completed: 2026-08-27*

## Self-Check: PASSED

- FOUND: app/modules/health/routes.py
- FOUND: app/tests/test_health.py
- FOUND: .planning/quick/260827-f8o-sanitizar-get-health-ready-para-nao-vaza/260827-f8o-SUMMARY.md
- FOUND commit: 632b995
- FOUND commit: f6ca053
