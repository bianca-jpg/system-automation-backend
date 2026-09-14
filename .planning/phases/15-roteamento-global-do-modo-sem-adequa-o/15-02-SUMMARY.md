---
phase: 15-roteamento-global-do-modo-sem-adequa-o
plan: 02
subsystem: database
tags: [sqlalchemy, postgres, pytest-asyncio, tdd]

# Dependency graph
requires: []
provides:
  - "_assert_stock_capacity revalida capacidade de estoque para tipo in {'com', 'sem'} — o desvio que só olhava 'com' foi removido"
  - "test_sem_adequar_revalidates_stock_before_writes prova, por integração real com Postgres, que aplicar um plano tipo='sem' acima do estoque disponível é rejeitado com ProcessingPlanConflict('processing_plan_stale') sem gravar OrdemReserva"
affects: [15-03-roteamento-fechamento-aloc-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Teste espelho: test_sem_adequar_revalidates_stock_before_writes copia test_adequation_revalidates_stock_before_writes linha a linha, trocando só tipo='com' por tipo='sem' — mesmo molde estrutural para provar paridade entre os dois modos"

key-files:
  created: []
  modified:
    - app/modules/pedidos/processing/infrastructure/repository.py
    - app/tests/test_pedidos_processing_repository.py

key-decisions:
  - "Removidas as duas linhas 'if tipo != \"com\": continue' sem substituto — _payload já rejeita qualquer tipo fora de {'com', 'sem'} com ProcessingPlanConflict('processing_plan_payload_invalid') antes de chegar em _assert_stock_capacity, então o desvio removido não deixa nenhum tipo inválido escapar"
  - "Variável de desestruturação renomeada tipo -> _tipo (não lida no corpo do laço após a remoção), seguindo a convenção já usada para _linx/_targets na mesma linha"

patterns-established: []

requirements-completed: [FIX-01]

# Metrics
duration: ~15min
completed: 2026-08-17
---

# Phase 15 Plan 02: FIX-01 — revalidação de estoque também para tipo="sem" Summary

**`_assert_stock_capacity` deixou de pular a revalidação de capacidade de estoque quando `tipo == "sem"`, fechando o gap antes do plano 15-03 rotear `sem_adequar` pelo caminho global.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 1/1 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments

- `_assert_stock_capacity` (`app/modules/pedidos/processing/infrastructure/repository.py`) revalida capacidade de estoque para `tipo in {"com", "sem"}` sem exceção — o desvio `if tipo != "com": continue` foi removido.
- Teste novo `test_sem_adequar_revalidates_stock_before_writes` prova, por integração real com Postgres (`processing_database`), que aplicar um par `tipo="sem"` com `qty=2` contra `stock_qty=1` é rejeitado com `ProcessingPlanConflict("processing_plan_stale")` e não grava nenhuma `OrdemReserva`.
- RED confirmado antes do fix: o teste falhava com `DID NOT RAISE ProcessingPlanConflict` contra o código antigo — evidência de que o teste exercita comportamento real, não um mock.
- Nenhuma regressão: `test_adequation_revalidates_stock_before_writes` (caso `tipo="com"` pré-existente) e os dois testes de writer que usam `tipo="sem"` com estoque suficiente (`test_writer_applies_pair_and_checkpoint_in_same_transaction`, `test_writer_rolls_back_all_outputs_before_checkpoint`) continuam `passed`.
- Suíte completa via Docker: **829 passed** (828 baseline pós-15-01 + 1 novo), 16 skipped, 1 failed (a falha conhecida e pré-existente de teardown asyncpg em `test_pedidos_read_projection.py`).

## Task Commits

Cada fase do ciclo TDD foi commitada atomicamente:

1. **Task 1 (RED): teste novo prova o gap para `tipo="sem"`** - `58e38e3` (test)
2. **Task 1 (GREEN): remover o desvio e revalidar estoque para `tipo in {"com", "sem"}`** - `52fd2c7` (fix)

## Files Created/Modified

- `app/modules/pedidos/processing/infrastructure/repository.py` - `_assert_stock_capacity`: removidas as linhas `if tipo != "com": continue`; variável de desestruturação renomeada para `_tipo`. Nenhuma outra linha da função mudou (chunking, `carregar_estoque_disponivel_alvos`, comparação final permanecem idênticos).
- `app/tests/test_pedidos_processing_repository.py` - `test_sem_adequar_revalidates_stock_before_writes` adicionado logo após `test_adequation_revalidates_stock_before_writes`, espelhando o molde com `tipo="sem"` no lugar de `tipo="com"`.

## Decisions Made

- Nenhum substituto para o `if` removido: `_payload` já levanta `ProcessingPlanConflict("processing_plan_payload_invalid")` para qualquer `tipo` fora de `{"com", "sem"}`, então o laço passa a tratar os dois tipos válidos igualmente sem risco de aceitar um tipo desconhecido.
- Variável renomeada para `_tipo` em vez de manter `tipo` morto no escopo, seguindo a convenção já usada para `_linx`/`_targets` na mesma linha de desestruturação.

## Deviations from Plan

None - plan executado exatamente como escrito.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- FIX-01 concluído: paridade de revalidação de estoque entre `adequar` e `sem_adequar` na escrita, provada por teste, antes do plano 15-03 rotear `sem_adequar` pelo caminho global e o tornar dependente de estoque compartilhado.
- Nenhum arquivo fora do escopo (`repository.py` e `test_pedidos_processing_repository.py`) foi tocado; `apply_pairs`, `_assert_plan_still_current` e `_payload` permanecem byte-idênticos.
- Trabalho não commitado de SSO Microsoft (`app/modules/auth/**`, `alembic/versions/031_*`, `.env.example`, `app/shared/config/settings.py`) permanece intocado — confirmado via `git status --short` antes e depois dos commits deste plano.
- Sem bloqueios para o plano 15-03 (que depende de 15-01 e 15-02, ambos concluídos agora).

## Self-Check: PASSED

- FOUND: app/modules/pedidos/processing/infrastructure/repository.py (`_tipo, items, _linx, _targets = self._payload(row)` presente, `if tipo != "com"` ausente)
- FOUND: app/tests/test_pedidos_processing_repository.py (`test_sem_adequar_revalidates_stock_before_writes` presente)
- FOUND: commit `58e38e3` (test)
- FOUND: commit `52fd2c7` (fix)

---
*Phase: 15-roteamento-global-do-modo-sem-adequa-o*
*Completed: 2026-08-17*
