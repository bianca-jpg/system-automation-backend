---
phase: 16-persist-ncia-do-motivo-de-stand-by
plan: 05
subsystem: database
tags: [postgresql, sqlalchemy, ingestao, full-refresh, standby]

# Dependency graph
requires:
  - phase: 16-persist-ncia-do-motivo-de-stand-by (16-02)
    provides: migration 032_pedido_standby_motivo + model ORM PedidoStandbyMotivo
provides:
  - "Limpeza automática de linhas órfãs de pedido_standby_motivo a cada full refresh de ingestão (reconstruir_pedido_produto_read)"
affects: [17-visibilidade-do-stand-by-na-ui]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "DELETE ... WHERE NOT EXISTS como constante de módulo SQL cru, no mesmo estilo de _PENDING_READ_INSERT_SQL/_ERP_READ_INSERT_SQL"
    - "Limpeza de tabela derivada posicionada estritamente depois dos INSERTs que repopulam a tabela-fonte, nunca na janela em que ela está vazia"

key-files:
  created: []
  modified:
    - app/modules/ingestao/infrastructure/repositorio_snapshot.py
    - app/tests/test_pedidos_read_projection.py

key-decisions:
  - "Predicado do DELETE usa exatamente source='pending' + igualdade do par (nr_pedido, cd_prod_cor), literal já revisado no 16-RESEARCH.md/16-PATTERNS.md — não reinventado"
  - "DELETE de órfãos não conta no retorno de reconstruir_pedido_produto_read (continua exclusivamente pending_dml.rowcount + erp_dml.rowcount)"

patterns-established:
  - "RED manual para tasks tdd=true cuja implementação já existe: comentar a linha crítica, confirmar falha, restaurar e confirmar git diff vazio antes de prosseguir"

requirements-completed: []  # Este plano cobre só o critério 4 do ROADMAP da Fase 16; sem REQ-ID formal (STANDBY-01/06 são de 16-01..16-04)

duration: ~25min
completed: 2026-08-21
---

# Phase 16 Plan 05: Limpeza de órfãos de pedido_standby_motivo no full refresh Summary

**`reconstruir_pedido_produto_read` agora apaga automaticamente linhas de `pedido_standby_motivo` cujo par deixou de estar pendente (ex.: confirmado direto no ERP), rodando o `DELETE ... NOT EXISTS` estritamente depois dos dois INSERTs pending/erp, na mesma transação do full refresh.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-08-21T16:34:04Z
- **Tasks:** 3 (feat, test, gate de suíte completa)
- **Files modified:** 2

## Accomplishments
- Constante de módulo `_STANDBY_ORPHAN_CLEANUP_SQL` adicionada em `repositorio_snapshot.py`, executada dentro de `reconstruir_pedido_produto_read` imediatamente depois do INSERT erp — nunca na janela vazia entre o `DELETE FROM pedido_produto_read` e os INSERTs subsequentes.
- Teste automatizado dedicado provando os dois lados do comportamento no mesmo full refresh: par órfão apagado, par ainda pendente preservado.
- RED confirmado manualmente (comentando a linha de limpeza, rodando o teste, restaurando e confirmando `git diff` vazio) antes de prosseguir — a implementação da Task 1 já satisfazia o teste da Task 2, então o ciclo RED/GREEN completo do padrão TDD não se aplicava normalmente (implementação e teste vieram em tasks separadas do mesmo plano); a confirmação manual de RED supre essa lacuna.
- Suíte completa via Docker: **849 passed** (848 baseline + 1 novo), 17 skipped, exatamente 1 failed (a mesma falha conhecida e pré-existente, sem relação com este plano).
- **Fase 16 fechada (5/5 planos): 16-01, 16-02, 16-03, 16-04, 16-05.**

## Task Commits

Each task was committed atomically:

1. **Task 1: Limpar linhas órfãs de pedido_standby_motivo no full refresh** - `01278bd` (feat)
2. **Task 2: Teste automatizado — par órfão apagado, par pendente sobrevive** - `2b0312a` (test)
3. **Task 3: Gate de suíte completa** - sem commit próprio (nenhuma alteração de código; apenas execução da suíte completa para confirmar ausência de regressão)

**Plan metadata:** commit deste SUMMARY.md + STATE.md + ROADMAP.md (docs)

## Files Created/Modified
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py` - nova constante `_STANDBY_ORPHAN_CLEANUP_SQL` + chamada dentro de `reconstruir_pedido_produto_read`, depois dos INSERTs pending/erp; docstring atualizado
- `app/tests/test_pedidos_read_projection.py` - novo teste `test_reconstruir_pedido_produto_read_apaga_standby_orfao_mas_preserva_par_pendente`; novos imports `uuid4` e `PedidoStandbyMotivo`

## Decisions Made
- Predicado do `DELETE` mantido literal e fechado desde `16-RESEARCH.md`/`16-PATTERNS.md` (`source='pending'` + igualdade do par), sem enfraquecer nem aproximar.
- Ordem de execução (depois dos dois INSERTs) documentada explicitamente no docstring da função, não só em comentário inline, para sobreviver a futuras edições sem contexto do plano original.

## Deviations from Plan

None - plan executado exatamente como escrito. A única nuance foi a confirmação manual de RED exigida explicitamente pelo próprio `16-05-PLAN.md` (Task 2, acceptance criteria) — não é uma deviation, é o próprio plano pedindo essa verificação.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 16 (Persistência do motivo de stand by) está completa: `pedido_standby_motivo` é fonte real, viva, e agora também auto-limpa órfãos a cada full refresh de ingestão — pronta para a Phase 17 (visibilidade do stand by na UI) consultar sem risco de mostrar tags de pares que já saíram do fluxo de pendentes.
- **Achado registrado, não corrigido (fora do escopo deste plano):** `REQUIREMENTS.md` ainda lista `STANDBY-01` e `STANDBY-06` como "Pending", apesar da nota de sessão de `16-04` afirmar que ambos foram concluídos por aquele plano. Este plano tem `requirements: []` no frontmatter e escopo estrito de 2 arquivos, então não altera `REQUIREMENTS.md` — recomenda-se que uma sessão futura reabra e feche esse checkbox junto de `16-04`.
- Nenhum bloqueio para `/gsd-plan-phase 17`.

---
*Phase: 16-persist-ncia-do-motivo-de-stand-by*
*Completed: 2026-08-21*

## Self-Check: PASSED

- FOUND: app/modules/ingestao/infrastructure/repositorio_snapshot.py
- FOUND: app/tests/test_pedidos_read_projection.py
- FOUND: .planning/phases/16-persist-ncia-do-motivo-de-stand-by/16-05-SUMMARY.md
- FOUND commit: 01278bd
- FOUND commit: 2b0312a
