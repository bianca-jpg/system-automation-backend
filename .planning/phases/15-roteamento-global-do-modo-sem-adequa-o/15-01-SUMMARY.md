---
phase: 15-roteamento-global-do-modo-sem-adequa-o
plan: 01
subsystem: database
tags: [sqlalchemy, postgres, jsonb, asyncpg, pytest-asyncio]

# Dependency graph
requires:
  - phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio
    provides: "processar_pedidos aceitando total_original_por_pedido/consumido_previo_*_por_pedido como parametros opcionais keyword-only"
provides:
  - "Metodo load_pedido_budget no Protocol ProcessingPlannerSource (assinatura pronta para o plano 15-03 desempacotar direto nos 3 argumentos do motor)"
  - "Implementacao SQL agregada em SqlAlchemyProcessingPlannerSource, com a correcao do denominador que encolhe (invariante J5) provada por teste com os numeros reais do banco"
affects: [15-03-roteamento-fechamento-aloc-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CTE _PEDIDO_BUDGET_SQL: pedido_scope (unnest de bind nomeado) como base de todo LEFT JOIN, garantindo que todo nr_pedido pedido aparece no resultado mesmo com zeros"
    - "GREATEST(diff, 0) somado em cada direcao (adicao/corte) para nunca compensar um pelo outro — mesmo padrao ja usado em repositorio_produtos.py::_AVAILABLE_STOCK_CTE"
    - "Chunking de 5.000 nr_pedido por viagem ao banco via ANY/unnest sobre integer[], acima do chunk de 200 de load_stock porque o limite pratico do Postgres para ANY(int[]) e bem mais alto"

key-files:
  created: []
  modified:
    - app/modules/pedidos/processing/application/ports.py
    - app/modules/pedidos/processing/infrastructure/adapters.py
    - app/tests/test_pedidos_processing_repository.py

key-decisions:
  - "Query unica com 5 CTEs (pedido_scope, or_pairs, from_pedidos, missing_from_pedidos, consumido) em vez de duas queries separadas — evita repetir o JOIN/coalesce de ordens_reserva duas vezes"
  - "missing_from_pedidos soma qt_solicitada sem filtro de tipo (conta pares 'com' e 'sem' que sumiram de pedidos), enquanto consumido filtra WHERE tipo='com' — sao contagens com propositos diferentes: uma mede o denominador, a outra mede o que ja foi gasto do orcamento ±5%"

patterns-established:
  - "Toda leitura agregada por conjunto de nr_pedido (ou codigo de produto) parte de uma CTE 'scope' com unnest(:bind::tipo[]) e faz LEFT JOIN para os agregados, para que o chamador sempre receba uma entrada por chave pedida, mesmo com zero"

requirements-completed: []

# Metrics
duration: ~30min
completed: 2026-08-17
---

# Phase 15 Plan 01: Leitura de orçamento por pedido (aditiva) Summary

**Novo método `load_pedido_budget` no port `ProcessingPlannerSource` e implementação SQL agregada que corrige o denominador que encolhe (invariante J5) — puramente aditivo, nada ainda chama o método.**

## Performance

- **Duration:** ~30 min
- **Tasks:** 2/2
- **Files modified:** 3

## Accomplishments

- `ProcessingPlannerSource.load_pedido_budget(nr_pedidos: set[int]) -> tuple[dict[int,int], dict[int,int], dict[int,int]]` adicionado ao Protocol, mesma convenção de retorno primitivo de `load_stock`/`load_size_reference`.
- `SqlAlchemyProcessingPlannerSource.load_pedido_budget` implementado com uma única query agregada (`_PEDIDO_BUDGET_SQL`), chunked em lotes de 5.000 `nr_pedido`, sem nenhuma interpolação de dado de entrada via f-string (bind nomeado `:nr_pedidos` via `ANY`/`unnest`).
- A correção obrigatória do "denominador que encolhe" (PREMISSA FALSA/invariante J5, verificada em produção em 2026-08-17) está implementada: o total original soma `pedidos.qt_entregar` **mais** o `qt_solicitada` de qualquer par que já tem OR mas sumiu de `pedidos`.
- Adição e corte acumulados em ORs anteriores são somados separadamente com `GREATEST(diff, 0)` em cada direção — nunca se compensam.
- 3 testes de integração novos, isolados (instanciam `SqlAlchemyProcessingPlannerSource(db)` direto, sem passar pelo pipeline de job), incluindo o caso obrigatório J5 com os números reais: pedido `1591091`, par ausente `(1591091, "ML.18.0315|001")` com `qt_solicitada=3`, 6 produtos restantes somando `qt_entregar=6`, `total_original[1591091] == 9`.
- Suíte completa via Docker mantida verde na mesma linha de base: **828 passed** (825 + 3 novos), 16 skipped, 1 failed (a falha conhecida e pré-existente de teardown asyncpg).

## Task Commits

1. **Task 1: Adicionar `load_pedido_budget` ao Protocol e implementar a leitura agregada em SQL** - `7462d64` (feat)
2. **Task 2: Teste de integração isolado — agregação de orçamento e o caso obrigatório J5** - `ee523a8` (test)

## Files Created/Modified

- `app/modules/pedidos/processing/application/ports.py` - Adiciona `load_pedido_budget` ao Protocol `ProcessingPlannerSource` (só adição, nenhuma assinatura existente mudou)
- `app/modules/pedidos/processing/infrastructure/adapters.py` - Constante `_PEDIDO_BUDGET_SQL` (WITH de 5 CTEs) e método `SqlAlchemyProcessingPlannerSource.load_pedido_budget`
- `app/tests/test_pedidos_processing_repository.py` - 3 testes de integração novos cobrindo agregação básica, separação adição/corte sem compensação, e o caso J5 com os números reais

## Decisions Made

- Query única com CTEs em vez de duas queries separadas (RESEARCH.md item 3 sugeria "podem ser uma única viagem ao banco via `db.execute` sequencial" — optei por uma única viagem em uma única query, reaproveitando `or_pairs` como CTE compartilhada entre `missing_from_pedidos` e `consumido`, evitando repetir o `JOIN`/`coalesce(itens)` duas vezes).
- `missing_from_pedidos` não filtra por `tipo` (conta `'com'` e `'sem'`, porque qualquer par que sumiu de `pedidos` conta para o denominador, independente de como a OR foi gerada); `consumido` filtra `WHERE tipo='com'` (só ORs com adequação afetam o orçamento ±5% consumido) — decisão explícita per RESEARCH.md item 2 e action da Task 1.
- Assinatura implementada exatamente como o item 3 do RESEARCH.md resolve (`load_pedido_budget(nr_pedidos: set[int])`, sem `channel`), não a CTE ilustrativa do item 1 que reaproveitaria `_PENDING_BASE_SQL`/`eligible_pairs` — evita acoplar a leitura de orçamento ao filtro de elegibilidade por canal, que o chamador (plano 15-03) já resolve antes de montar `nr_pedidos`.

## Deviations from Plan

None - plan executado exatamente como escrito.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `load_pedido_budget` está pronto para o plano 15-03 ligar em `build_processing_plan`/`_plan_once`, desempacotando a tupla direto nos 3 argumentos nomeados de `processar_pedidos` (mesma ordem: `total_original_por_pedido`, `consumido_previo_adicao_por_pedido`, `consumido_previo_corte_por_pedido`).
- Verificação estática confirmada: `load_pedido_budget` só aparece em `ports.py`, `adapters.py` e `test_pedidos_processing_repository.py` — nenhum chamador novo foi criado, plano permanece puramente aditivo.
- Nenhum bloqueio para o plano 15-02 (FIX-01, arquivos diferentes) nem para o 15-03 (depende deste plano estar concluído, o que está).

---
*Phase: 15-roteamento-global-do-modo-sem-adequa-o*
*Completed: 2026-08-17*
