---
phase: quick/260826-fer
plan: 01
subsystem: infra
tags: [ruff, pyright, alembic, lint, dead-code, ci]

# Dependency graph
requires: []
provides:
  - "pyproject.toml como fonte única da config do ruff (select E4,E7,E9,F,B,UP,I,SIM,C4 + target-version py313 + extend-immutable-calls do FastAPI)"
  - "alembic/script.py.mako gerando migrations já em PEP 604 / collections.abc"
  - "pipeline.yml lendo a seleção do ruff do pyproject (sem --select na CLI)"
affects: [ci, alembic, pedidos, parametros, realtime, workers]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ruff.lint.flake8-bugbear.extend-immutable-calls para silenciar B008 dos callables de injeção do FastAPI sem editar rotas"
    - "Generics PEP 695 (`def f[T](...)`) em vez de `TypeVar` solto"
    - "contextlib.suppress(...) em vez de try/except/pass"

key-files:
  created: []
  modified:
    - app/modules/pedidos/infrastructure/repositorio_consultas.py
    - app/modules/pedidos/infrastructure/repositorio_ordens.py
    - pyproject.toml
    - alembic/script.py.mako
    - alembic/env.py
    - alembic/versions/*.py (19 migrations, boilerplate de tipos via autofix)
    - app/modules/pedidos/domain/motor_adequacao.py
    - app/shared/config/settings.py
    - app/modules/parametros/domain/registro.py
    - app/modules/parametros/domain/leitura_resiliente.py
    - app/modules/pedidos/infrastructure/repositorio_ordens_linx.py
    - app/modules/realtime/infrastructure/connection_manager.py
    - app/modules/realtime/infrastructure/http/routes.py
    - app/workers/tasks/ingestao.py
    - app/workers/tasks/pedidos.py
    - app/tests/test_pedidos_motor.py
    - app/tests/test_pedidos_processing_repository.py
    - app/tests/test_pedidos_processing_service.py
    - app/tests/test_auth_flows.py
    - app/tests/test_ingestao_sync.py
    - app/tests/test_pedidos_product_writes.py
    - .github/workflows/pipeline.yml

key-decisions:
  - "B905 zip() sem strict: usado strict=True nos dois pares comparados em test_pedidos_motor.py (comprimentos devem bater; se algum quebrar é bug de teste legítimo)"
  - "B904 nas 6 raise AssertionError('unreachable') de pedidos.py: usado 'from None' (substituição deliberada da exceção original, não encadeamento)"
  - "B904 no raise _JobLeaseLostError de ingestao.py:305: usado 'from None', consistente com o raise idêntico já existente em ingestao.py:333"
  - "B017 nos 3 pytest.raises(Exception) de test_auth_flows.py: estreitado para jose.JWTError (exceção concreta que decode_access_token/decode_refresh_token/decode_session_token levantam via domain/tokens.py)"
  - "UP047 aplicado sem precisar de ignore: get_param_value e _chunks converteram para PEP 695 sem reclamação do pyright"

requirements-completed: [QUICK-260826-FER]

# Metrics
duration: ~25min
completed: 2026-08-26
---

# Quick Task 260826-fer: Consolidar cleanup de dead code no backend Summary

**Removidos 2 símbolos mortos (`_like_pattern`, `carregar_modificacoes` + Protocol órfão), config do ruff formalizada no `pyproject.toml` com a régua ampliada (E4/E7/E9/F → +B,UP,I,SIM,C4), template de migration corrigido para PEP 604/`collections.abc`, e os 20 achados manuais remanescentes resolvidos — CI agora lê a seleção do pyproject em vez de `--select` na linha de comando.**

## Performance

- **Duration:** ~25min
- **Tasks:** 3/3 completos
- **Files modified:** 33 (2 na Task 1, 26 na Task 2, 12 na Task 3 — `pyproject.toml` conta em Task 2)

## Accomplishments
- Dead code real removido (`_like_pattern`, `carregar_modificacoes`, `_PedidoModificacaoResult`), zero referências residuais
- `pyproject.toml` virou fonte única da config do ruff; `[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.lint.flake8-bugbear]` com `extend-immutable-calls` justificado por comentário
- `alembic/script.py.mako` corrigido — migrations novas nascem compatíveis com a régua nova
- 90 achados corrigidos por autofix (`ruff check --fix` + `ruff format`), sem `--unsafe-fixes`
- 20 achados manuais resolvidos: B904 x7 (`from None`/`from err`), SIM102 x2 (if fundido), SIM105 x2 (`contextlib.suppress`), SIM117 x2 (`with` fundido), B905 x2 (`strict=True`), B017 x3 (`JWTError` concreto), UP047 x2 (generics PEP 695)
- `pipeline.yml` atualizado: `uv run ruff check app alembic` sem `--select`
- `uv run ruff check app alembic` → "All checks passed!"; suite em 889 passed / 18 skipped / 0 failed em todas as 3 rodadas (Tasks 1, 2 e 3)
- Zero alteração nos 3 arquivos intocáveis da outra sessão (`app/modules/auth/infrastructure/http/routes.py`, `app/modules/auth/infrastructure/repositorio_otp.py`, `app/tests/test_docs_seguranca.py`) — confirmados ` M`, não staged, não commitados, em todas as verificações

## Task Commits

Each task was committed atomically:

1. **Task 1: Remover os 2 símbolos mortos e o Protocol órfão** - `cfbfb21` (refactor)
2. **Task 2: Config do ruff no pyproject, template de migration corrigido e autofix** - `f127429` (chore)
3. **Task 3: Resolver os 20 achados manuais e subir o gate do CI** - `cb48480` (refactor) + `50b4287` (ci)

_Nenhuma task usou TDD; todas type="auto"._

## Files Created/Modified

- `app/modules/pedidos/infrastructure/repositorio_consultas.py` - remove `_like_pattern` (helper sem chamador)
- `app/modules/pedidos/infrastructure/repositorio_ordens.py` - remove `carregar_modificacoes` + `_PedidoModificacaoResult` (Protocol órfão); docstring de módulo atualizado
- `pyproject.toml` - `[tool.ruff]`/`[tool.ruff.lint]`/`[tool.ruff.lint.flake8-bugbear]` novos
- `alembic/script.py.mako` - `Union`/`typing.Sequence` → `str | None` / `collections.abc.Sequence`
- `alembic/env.py` + `alembic/versions/*.py` - reagrupamento de import (I001) e anotações PEP 604/PEP 585 (UP007/UP035/C420) via autofix; os 8 `noqa: F401` de `env.py` preservados
- `app/modules/pedidos/domain/motor_adequacao.py`, `app/shared/config/settings.py` - autofix (UP007/UP035)
- `app/modules/parametros/domain/registro.py` - SIM102: dois `if` aninhados fundidos com `and`
- `app/modules/parametros/domain/leitura_resiliente.py` - UP047: `get_param_value[T]`, remove `TypeVar`
- `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py` - UP047: `_chunks[T]`, remove `TypeVar`
- `app/modules/realtime/infrastructure/connection_manager.py`, `app/modules/realtime/infrastructure/http/routes.py` - SIM105: `try/except/pass` → `contextlib.suppress(RuntimeError, TimeoutError)`
- `app/workers/tasks/ingestao.py`, `app/workers/tasks/pedidos.py` - B904: `raise ... from None`/`from err` em 7 pontos
- `app/tests/test_pedidos_motor.py` - B905: `zip(..., strict=True)` em 2 laços; autofix (UP007) também tocou o arquivo
- `app/tests/test_auth_flows.py` - B017: `pytest.raises(Exception)` → `pytest.raises(JWTError)` em 3 pontos
- `app/tests/test_ingestao_sync.py`, `app/tests/test_pedidos_product_writes.py` - SIM117: `with` aninhados fundidos
- `app/tests/test_pedidos_processing_repository.py`, `app/tests/test_pedidos_processing_service.py` - autofix (I001/UP007)
- `.github/workflows/pipeline.yml` - `ruff check --select E4,E7,E9,F app alembic` → `ruff check app alembic`

## Decisions Made

- Base de comparação B905: os pares de `zip` em `test_pedidos_motor.py` comparam grades geradas a partir de ordens de entrada diferentes mas do mesmo resultado — comprimentos devem sempre bater, então `strict=True` é a escolha correta (não `strict=False`)
- `from None` nos 6 `raise AssertionError("unreachable")` de `pedidos.py`: cada um sucede uma chamada a `_transition_failure` que já tratou a causa original; a nova exceção é só para satisfazer o type checker após um caminho terminal de retry/fail, então encadear a causa (`from err`) só adicionaria ruído
- `from None` em `ingestao.py:305`: replicado o padrão já existente em `ingestao.py:333` (mesmo `_JobLeaseLostError("ingestion_job_lease_lost")`, mesmo formato de código pré-existente)
- B017 estreitado para `jose.JWTError` em vez de `# noqa: B017` — os três `decode_*_token` do domínio (`app/modules/auth/domain/tokens.py`) sempre levantam `JWTError` explicitamente para rejeição de tipo/purpose, então não há necessidade de manter o `Exception` cego
- UP047 não precisou de fallback para `ignore` — pyright aceitou a sintaxe PEP 695 nos dois pontos sem reclamar

## Deviations from Plan

None - plan executado exatamente como escrito. Os 20 achados manuais foram resolvidos exatamente na distribuição prevista (B904 x7, B017 x3, B905 x2, SIM102 x2, SIM105 x2, SIM117 x2, UP047 x2), sem necessidade do fallback de `ignore` para UP047 previsto como plano B.

## Issues Encountered

None.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- `uv run ruff check app alembic` (sem `--select`) responde "All checks passed!"; CI e local agora leem a mesma fonte de verdade
- Migrations novas (ex.: a 11-02 pendente da Victoria) nascerão compatíveis com a régua nova a partir do template corrigido
- Suíte, pyright e `alembic check` seguem exatamente no baseline medido no planejamento (889 passed / 18 skipped / 0 failed; 0 errors/warnings; sem drift)
- Trabalho não commitado de outra sessão (auth `routes.py`, `repositorio_otp.py`, `test_docs_seguranca.py`) permanece intacto e disponível para a sessão original retomar

---
*Phase: quick/260826-fer*
*Completed: 2026-08-26*

## Self-Check: PASSED

- Commits `cfbfb21`, `f127429`, `cb48480`, `50b4287` — todos encontrados em `git log --all`
- `app/modules/pedidos/infrastructure/repositorio_consultas.py` existe; `_like_pattern` confirmado removido
- `pyproject.toml` contém `[tool.ruff.lint]`
- `.github/workflows/pipeline.yml` contém `uv run ruff check app alembic` (sem `--select`)
