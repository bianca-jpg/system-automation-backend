---
phase: 16-persist-ncia-do-motivo-de-stand-by
plan: 02
subsystem: database
tags: [alembic, sqlalchemy, postgres, migration, pedidos]

# Dependency graph
requires:
  - phase: 16-01
    provides: "standby_motivo.py (vocabulário canônico SEM_CREDITO/SEM_ESTOQUE/FURO_GRADE) — usado como fonte de verdade textual do CHECK constraint, sem import direto"
provides:
  - "Tabela pedido_standby_motivo (migration 032), grão (nr_pedido, cd_prod_cor), CHECK ck_psm_motivo com os 3 motivos desde a criação"
  - "Model ORM PedidoStandbyMotivo em app/modules/pedidos/infrastructure/models.py, espelhando a migration (alembic check sem diff)"
  - "Import registrado em alembic/env.py sem remover nenhum noqa: F401 existente"
  - "Teste de round-trip upgrade/CHECK/downgrade/re-upgrade (test_migration_032_pedido_standby_motivo.py)"
affects: [16-03, 16-04, 16-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Migration de projeção recalculável (mesmo padrão de estoque_virtual/pedido_produto_read): downgrade() de 1 linha, sem tocar tabela fonte"
    - "job_id informativo/auditoria sem FK — evita acoplamento a durable_jobs, que é efêmero e sujeito a expurgo"
    - "3 CheckConstraints nomeados (ck_psm_canal, ck_psm_motivo, ck_psm_execucoes_consecutivas) como última linha de defesa contra vocabulário fora do domínio"

key-files:
  created:
    - alembic/versions/032_pedido_standby_motivo.py
    - app/tests/test_migration_032_pedido_standby_motivo.py
  modified:
    - app/modules/pedidos/infrastructure/models.py
    - alembic/env.py

key-decisions:
  - "DDL derivado de 16-RESEARCH.md (3 motivos), nunca de ARCHITECTURE.md §3.2 (desatualizado, só 2 motivos) — evita reproduzir o próprio bug que a Fase 16 existe para resolver"
  - "PK composta (nr_pedido, cd_prod_cor) no model, sem id autoincrement — diferente do padrão de EstoqueVirtual, mas correto para o grão de upsert futuro (16-04)"
  - "CHECK constraints vivem só na migration, não replicados no lado do model — mesmo padrão já usado por EstoqueVirtual/OrdemReserva/PedidoModificacao"

patterns-established:
  - "Teste de migration destrutivo com banco descartável dedicado (MIGRATION_TEST_DATABASE_URL, prefixo automation_migration_test), harness copiado 1:1 de test_migration_030_auth_otp_code_hash.py — 9º arquivo test_migration_*.py do repositório"

requirements-completed: [STANDBY-01]  # Parcial: só o schema (Task 1-2). STANDBY-01 só fica de fato satisfeito ("motivo é persistido a cada processamento") quando 16-04 ligar o wiring real de escrita/upsert. REQUIREMENTS.md permanece com o checkbox aberto por esse motivo, no mesmo critério já aplicado em 16-01.

# Metrics
duration: ~20min (só a Task 3 desta sessão; Tasks 1-2 foram de uma sessão anterior interrompida por queda de conexão)
completed: 2026-08-21
---

# Phase 16 Plan 02: Migration + Model `pedido_standby_motivo` Summary

**Migration Alembic 032 cria `pedido_standby_motivo` com CHECK de 3 motivos (`sem_credito`, `sem_estoque`, `furo_grade`) desde a criação, model ORM espelhando-a exatamente, e teste de round-trip provando reversibilidade e o CHECK nos dois sentidos.**

## Performance

- **Duration:** ~20min (execução desta sessão, retomando após queda de conexão do executor anterior)
- **Completed:** 2026-08-21T13:35:02Z
- **Tasks:** 3/3 completas
- **Files modified:** 4 (2 novos, 2 modificados)

## Accomplishments

- Migration `032_pedido_standby_motivo.py` — tabela nova com PK composta `(nr_pedido, cd_prod_cor)` e 3 `CheckConstraint`s nomeados, aplicada com sucesso em `system_automation` e `system_automation_test`
- Model ORM `PedidoStandbyMotivo` em `app/modules/pedidos/infrastructure/models.py`, espelhando a migration (`alembic check` sem diff, `test_schema_guard.py` verde)
- Teste dedicado de migration provando, com banco descartável real: upgrade cria as 7 colunas esperadas; `motivo='furo_grade'` é aceito; `motivo='invalido'` levanta `IntegrityError`; downgrade remove a tabela; re-upgrade a reconstrói vazia

## Task Commits

Todas as tasks foram commitadas atomicamente (Tasks 1 e 2 numa sessão anterior interrompida por queda de conexão; Task 3 nesta sessão de retomada):

1. **Task 1: Migration `032_pedido_standby_motivo`** — `13047a8` (feat) — *sessão anterior*
2. **Task 2: Model ORM `PedidoStandbyMotivo` + registro em `alembic/env.py`** — `034a284` (feat) — *sessão anterior*
3. **Task 3: Teste de migration — round-trip upgrade/CHECK/downgrade/re-upgrade** — `4fd7a3c` (test) — *esta sessão*

**Plan metadata:** ver commit de fechamento abaixo (docs)

## Files Created/Modified

- `alembic/versions/032_pedido_standby_motivo.py` — DDL da tabela nova, CHECK de 3 motivos, `downgrade()` de 1 linha
- `app/modules/pedidos/infrastructure/models.py` — `+ class PedidoStandbyMotivo`, `+ UUID` no import de `sqlalchemy.dialects.postgresql`
- `alembic/env.py` — `+ PedidoStandbyMotivo` na tupla de import existente, nenhum `noqa` removido
- `app/tests/test_migration_032_pedido_standby_motivo.py` — teste de round-trip completo, harness copiado de `test_migration_030_auth_otp_code_hash.py`

## Decisions Made

- Fonte do DDL foi `16-RESEARCH.md` (3 motivos desde a criação), nunca `ARCHITECTURE.md` §3.2 (desatualizado, só 2 motivos) — decisão já tomada e executada na sessão anterior (Task 1), reconfirmada nesta retomada ao ler a migration já commitada.
- `execucoes_consecutivas` com `CheckConstraint >= 1` desde a criação, mesmo sem wiring real ainda (defesa em profundidade antes do upsert existir em 16-04).

## Deviations from Plan

None - plan executado exatamente como escrito na Task 3 (as Tasks 1 e 2, herdadas da sessão anterior, também foram verificadas linha a linha contra o `16-02-PLAN.md` nesta retomada e conferidas como corretas, sem nenhuma lacuna).

## Issues Encountered

**Continuação após queda de conexão.** Um executor anterior foi interrompido por queda de conexão (não erro técnico) depois de completar e commitar as Tasks 1 e 2. Esta sessão retomou exatamente da Task 3, verificando primeiro no próprio repositório (não confiando só na narrativa recebida) que:
- `alembic heads` = `032`, e `\d pedido_standby_motivo` confirmado em ambos os bancos (`system_automation`, `system_automation_test`);
- a migration commitada (`13047a8`) já cita `16-RESEARCH.md` como fonte e tem os 3 valores no CHECK desde a criação;
- o model ORM commitado (`034a284`) segue o estilo `Column(...)` de `EstoqueVirtual`, com PK composta correta;
- a suíte completa, antes de qualquer trabalho novo, já estava em **833 passed, 16 skipped, 1 failed** (a falha conhecida de `test_pedidos_read_projection.py`).

A partir daí, só a Task 3 foi executada nesta sessão.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- Schema de `pedido_standby_motivo` pronto para `16-03` (wiring de domínio — `PlanDraft` ganha `deferred_pairs`/`blocked_credit_pairs`) e `16-04` (persistência real: port + upsert + wiring transacional).
- `STANDBY-01` permanece com o checkbox aberto em `REQUIREMENTS.md` — este plano entrega só o schema; a persistência real (o que o requirement de fato descreve) fica para `16-04`. Nenhum bloqueio: é o comportamento esperado e documentado no objetivo do próprio `16-02-PLAN.md`.
- Suíte completa ao final: **833 passed, 17 skipped, 1 failed** — mesma falha conhecida, +1 skipped exatamente conforme o contrato de "verde" desta fase (o teste de migration novo se auto-descarta via `pytest.skip` sem `MIGRATION_TEST_DATABASE_URL`).

---
*Phase: 16-persist-ncia-do-motivo-de-stand-by*
*Completed: 2026-08-21*

## Self-Check: PASSED

- FOUND: alembic/versions/032_pedido_standby_motivo.py
- FOUND: app/tests/test_migration_032_pedido_standby_motivo.py
- FOUND: app/modules/pedidos/infrastructure/models.py contém `class PedidoStandbyMotivo`
- FOUND: alembic/env.py contém `PedidoStandbyMotivo`
- FOUND commit 13047a8 (migration, Task 1)
- FOUND commit 034a284 (model, Task 2)
- FOUND commit 4fd7a3c (teste de migration, Task 3)
