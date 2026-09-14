---
phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio
plan: 03
subsystem: domain
tags: [motor-adequacao, prioridade, determinismo, plan_hash, ALOC-06]

# Dependency graph
requires:
  - phase: 14-02
    provides: "Factories e helpers de invariante (não usados diretamente aqui — a task explicitou reuso do helper `_item` local, mais específico e homogêneo com o resto do arquivo)"
provides:
  - "_prioridade (motor_adequacao.py) com chave de 3 campos totalmente ordenada: (score, len(prods_disp), -nr_pedido) — sem empate residual possível"
affects: ["14-04", "14-05", "14-06", "14-07", "19 (testes de propriedade)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Negativar campo de desempate (`-nr`) quando o sort usa reverse=True e a decisão de negócio pede ordem crescente naquele campo especificamente — evita inverter a semântica ao adicionar um total order"

key-files:
  created: []
  modified:
    - app/modules/pedidos/domain/motor_adequacao.py
    - app/tests/test_pedidos_motor.py

key-decisions:
  - "Testes novos reusam o helper `_item` local (não as factories de app/tests/factories_pedidos_motor.py), seguindo a instrução explícita do <action> da task e mantendo homogeneidade com os demais testes já existentes no arquivo"
  - "RED comprovado antes da edição: com o código antigo, a mesma disputa produzia vencedores diferentes conforme a ordem de entrada de `dados` — confirma que o bug de não-determinismo era real, não hipotético"

requirements-completed: [ALOC-06]

# Metrics
duration: ~20min
completed: 2026-08-17
---

# Phase 14 Plan 03: Prioridade determinística do motor Summary

**`_prioridade` ganha `nr_pedido` negativado como terceiro campo da chave de desempate, tornando o sort um total order — dois pedidos nunca mais empatam de fato, fechando a variação de `plan_hash` entre execuções "iguais".**

## Performance

- **Tasks:** 2/2 concluídas
- **Files modified:** 2

## Accomplishments

- `_prioridade`, dentro de `_processar_pedidos_canal`, passou de `(score, len(prods_disp))` para `(score, len(prods_disp), -nr)`. A negativação de `nr` é o que faz `sorted(..., reverse=True)` — que ordena a tupla inteira em ordem descendente — produzir o MENOR `nr_pedido` como vencedor do desempate final, exatamente a decisão travada em ALOC-06 (`nr_pedido` crescente). Sem a negação, o maior `nr_pedido` venceria — o oposto do decidido.
- Dois blocos de comentário atualizados para descrever o desempate em três níveis (score de negócio → produtos disponíveis → `nr_pedido` crescente como desempate final e total) e por que isso fecha a variação de `plan_hash` entre execuções empatadas.
- Teste novo `test_processar_pedidos_desempate_por_nr_pedido_crescente_independente_da_ordem_de_entrada` em `app/tests/test_pedidos_motor.py`: dois pedidos (`nr_pedido=2` e `nr_pedido=5`) com score idêntico (mesmo `vl_liquido`, mesma quantidade de produtos disponíveis) disputando um estoque insuficiente para os dois. Chama `processar_pedidos` duas vezes — com `dados` na ordem normal e na ordem invertida — e comprova que em AMBAS o pedido de menor `nr_pedido` (2) é sempre o selecionado, nunca o de maior (5); e que o conteúdo de `resultados` para o par disputado é idêntico item a item entre as duas chamadas (prova direta da invariante I6, reprodutibilidade completa do plano, não só das listas de chaves).
- RED comprovado antes da edição de produção: rodando o teste contra o código antigo, a chamada com `dados` invertido produzia um vencedor diferente da chamada normal (`(5, 'PROD1')` vs. `(2, 'PROD1')` esperado) — confirmando que o bug de não-determinismo era real.

## Task Commits

1. **Task 1: Desempate final por nr_pedido crescente em `_prioridade`** - `255af40` (feat) — RED confirmado, edição de produção, GREEN confirmado (`app/tests/test_pedidos_motor.py`: 16 passed)
2. **Task 2: Suíte completa — confirmar ausência de regressão nos consumidores de `adequar_grade_produto`** - sem commit próprio: a suíte completa passou sem exigir nenhuma alteração de código (ver `## Verificação` abaixo); nenhum arquivo teve diff nesta task

**Plan metadata:** (este commit)

## Files Created/Modified

- `app/modules/pedidos/domain/motor_adequacao.py` — `_prioridade` retorna `(score, len(prods_disp), -nr)`; dois blocos de comentário atualizados
- `app/tests/test_pedidos_motor.py` — teste novo provando desempate determinístico e reprodutibilidade completa do plano de seleção (I6)

## Decisions Made

- Reusar o helper `_item` local em vez das factories `item_pedido`/`estoque_por_canal` de `app/tests/factories_pedidos_motor.py`: o `<action>` da Task 1 pede explicitamente "reaproveitando o helper `_item` já existente", e todos os demais testes deste arquivo já usam esse padrão — introduzir a factory aqui quebraria a homogeneidade do arquivo sem ganho, já que o helper local cobre o caso perfeitamente.

## Verificação

- `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_motor.py -q` → **16 passed** (Task 1).
- `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` (suíte completa, Task 2) → **793 passed, 16 skipped, 1 failed**. O único failed é o mesmo conhecido e pré-existente de sempre, `app/tests/test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (`RuntimeError: Event loop is closed`, teardown de conexão, sem relação com esta fase). `793 = 792` (base registrada em `14-02-SUMMARY.md`) `+ 1` (o teste novo desta plano) — nenhuma regressão. `test_pedidos_motor_performance_024.py` e `test_pedidos_processing_cancellation_024.py` (os dois arquivos que fixam a assinatura de `adequar_grade_produto` via `monkeypatch`) passaram sem qualquer ajuste — a mudança de `_prioridade` não tocou assinatura nem forma de retorno de nenhuma função pública.
- Diff de `motor_adequacao.py` restrito à função `_prioridade` e aos dois blocos de comentário adjacentes — confirmado via `git diff` antes do commit; nenhuma regra de furo de grade, orçamento ou política de quantidade foi tocada.

## Deviations from Plan

None - plan executado exatamente como escrito.

## Issues Encountered

Nenhum. A suíte completa demorou ~2min19s no Docker e excedeu o timeout padrão de 120s do shell, sendo automaticamente movida para background; aguardada até a conclusão antes de prosseguir (nenhum comando extra disparado em paralelo).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `_prioridade` agora é um total order; `14-04` (furo de grade), `14-05` (política de quantidade) e `14-06` (orçamento) podem assumir ordem de prioridade 100% determinística sem depender da ordem de chegada do snapshot.
- ALOC-06 concluído.
- Nenhum bloqueio conhecido para `14-04`.

## Self-Check: PASSED

- FOUND: app/modules/pedidos/domain/motor_adequacao.py
- FOUND: app/tests/test_pedidos_motor.py
- FOUND: .planning/phases/14-motor-de-aloca-o-puro-regras-de-neg-cio/14-03-SUMMARY.md
- FOUND: commit 255af40

---
*Phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio*
*Completed: 2026-08-17*
