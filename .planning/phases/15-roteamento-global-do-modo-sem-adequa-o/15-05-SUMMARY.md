---
phase: 15-roteamento-global-do-modo-sem-adequa-o
plan: 05
subsystem: docs
tags: [markdown, documentation]

# Dependency graph
requires:
  - phase: 15-03
    provides: "roteamento global de sem_adequar (build_processing_plan/_plan_once com caminho único, sem streaming) e fechamento de ALOC-09"
  - phase: 14-06
    provides: "OrcamentoPedido (ledger de orçamento ±5% por pedido completo), duas passadas (adequar_grade_produto/conceder_adicao_pedido) e recálculo financeiro via ratear_hamilton"
provides:
  - "docs/adequacao.md atualizado para o estado real pós-Fase 14 e pós-15-03: sem menção a streaming/paginação exclusiva de sem_adequar, sem menção a tolerância ceil/floor por produto isolado"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns: []

key-files:
  created: []
  modified:
    - docs/adequacao.md

key-decisions:
  - "Não foi citado nenhum número de pico de memória para sem_adequar pós-roteamento: 15-04 ainda não tem SUMMARY (não executado nesta sessão), então a instrução do plano de 'não inventar número' foi seguida à risca — o item 3 de 'Orquestração durável' ficou só qualitativo"
  - "Seção renomeada de 'Comparação pedido × estoque — adequar_grade_produto' para 'Alocação por pedido — ledger e duas passadas', conforme sugestão do plano, porque o motor não compara mais grade-a-grade isolada: itera por pedido completo"

patterns-established: []

requirements-completed: []

# Metrics
duration: ~25min
completed: 2026-08-20
---

# Phase 15 Plan 05: Atualização de docs/adequacao.md Summary

**`docs/adequacao.md` reescrito para descrever o caminho de planejamento global único (sem streaming) e o ledger `OrcamentoPedido` de duas passadas, substituindo as duas descrições que a Fase 14 e o plano 15-03 tinham tornado falsas.**

## Performance

- **Duration:** ~25min
- **Tasks:** 3/3
- **Files modified:** 1 (`docs/adequacao.md`)

## Accomplishments

- Item 3 de "Orquestração durável" reescrito: `sem_adequar` não materializa mais chaves em tabela temporária transacional nem pagina em blocos de 250 pares — hidrata a mesma foto global do canal que `adequar` já usava, via `load_pending_items` + `build_processing_plan`. Os números de benchmark do caminho removido (17,668 s / 8,9 MiB) saíram sem serem substituídos por um número inventado.
- Item 5 de "Regras puras — `processar_pedidos`" explicita o despacho por modo: `sem_adequar` → `aplicar_tudo_ou_nada`; `adequar` → `adequar_grade_produto` (passada 1) + `conceder_adicao_pedido` (passada 2, só para produtos com folga total).
- Seção "Comparação pedido × estoque — `adequar_grade_produto`" (que descrevia a regra `ceil`/`floor` por produto isolado, extinta desde o 14-06) substituída por "Alocação por pedido — ledger e duas passadas": `OrcamentoPedido` por pedido completo, dois orçamentos independentes (adição/corte) que não se compensam, `total_original` somando todos os produtos do pedido com o consumido prévio persistindo entre execuções (ALOC-09), as duas passadas e o recálculo financeiro via rateio de Hamilton (FIX-02).
- "Ordenação de tamanhos" ajustada para atribuir o uso dos "extremos" à passada 2 (`conceder_adicao_pedido`), não mais a `adequar_grade_produto`.
- "Resultado" ganhou frase explícita: `deferredCount`/`blockedCreditCount` refletem valores reais nos dois modos desde o roteamento global da Fase 15 (ARCH-01) — antes, `sem_adequar` sempre devolvia os dois zerados.

## Task Commits

1. **Task 1: Reescrever a orquestração durável** - `8e73b90` (docs)
2. **Task 2: Reescrever a alocação por pedido e a abrangência dos contadores** - `1230212` (docs)
3. **Task 3: Gate de suíte completa** - sem commit próprio (task de verificação, nenhuma edição de arquivo)

## Files Created/Modified

- `docs/adequacao.md` - item 3 de "Orquestração durável", item 5 de "Regras puras", seção "Alocação por pedido — ledger e duas passadas" (substitui "Comparação pedido × estoque"), "Ordenação de tamanhos" e "Resultado" reescritos

## Decisions Made

- Ver `key-decisions` no frontmatter: nenhum número de pico de memória citado para `sem_adequar` (15-04 sem SUMMARY nesta sessão); seção renomeada para refletir que a decisão é por pedido completo, não por grade isolada.

## Deviations from Plan

None - plano executado exatamente como escrito. As três tasks corresponderam 1:1 às edições descritas no `15-05-PLAN.md`, e todos os critérios de aceitação automatizados (`grep`) passaram na primeira tentativa.

## Issues Encountered

**A suíte completa via Docker retornou `826 passed, 16 skipped, 1 failed`, e não os `825 passed` do baseline citado no plano.** Investigado antes de commitar (conforme instrução da Task 3): `git status --short` mostra um arquivo de teste novo e **não commitado**, `app/tests/test_pedidos_processing_sem_adequar_memory_024.py`, que não faz parte de nenhum commit deste plano (nenhum arquivo de teste foi tocado por 15-05, escopo estrito de `docs/adequacao.md`). Esse arquivo corresponde ao plano `15-04` (medição de pico de memória de `sem_adequar`), cujo `15-04-PLAN.md` existe no diretório da fase mas cujo `15-04-SUMMARY.md` **não** existe — ou seja, há trabalho de 15-04 em andamento/não commitado na mesma working tree compartilhada, adicionando exatamente 1 teste à coleção (`826 = 825 + 1`). A falha conhecida continua sendo a mesma e única (`test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql`, mesmo motivo de teardown/poluição de dados de `test_auth_flows.py` já documentado em `15-03-SUMMARY.md`). Nenhum arquivo de teste ou produção foi commitado por este plano além de `docs/adequacao.md` (confirmado por `git show --stat` de cada um dos 2 commits) — a divergência de contagem é inteiramente explicada por esse arquivo alheio ao escopo de 15-05, não por regressão introduzida pela edição de documentação.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- **Fase 15 completa (5/5 planos)**: 15-01 (leitura de orçamento) → 15-02 (FIX-01) → 15-03 (roteamento global + ALOC-09 + remoção do streaming) → 15-04 (medição de memória, PENDENTE — há um teste não commitado na working tree que sugere execução em andamento por outra sessão) → 15-05 (esta entrega, docs).
- **Atenção:** `15-04-PLAN.md` ainda não tem `15-04-SUMMARY.md`. Este plano (15-05) só dependia formalmente de `15-03`, por isso pôde prosseguir sem esperar o fechamento de `15-04` — mas a Fase 15 como um todo só fecha quando `15-04` também estiver commitado e resumido.
- Nenhum bloqueio introduzido por esta entrega: mudança é doc-only, suíte completa sem regressão atribuível a este plano.

## Self-Check: PASSED

- `docs/adequacao.md` - FOUND, contém `OrcamentoPedido`, `não se compensam`, `Hamilton`, `mesma foto global`; não contém `tabela temporária transacional`, `17,668 s`, `8,9 MiB`, `tolerância padrão de 5%`
- Commits `8e73b90`, `1230212` - FOUND in `git log --oneline --all`

---
*Phase: 15-roteamento-global-do-modo-sem-adequa-o*
*Completed: 2026-08-20*
