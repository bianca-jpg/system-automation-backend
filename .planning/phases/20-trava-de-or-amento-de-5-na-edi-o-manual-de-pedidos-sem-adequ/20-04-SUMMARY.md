---
phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ
plan: 04
subsystem: api
tags: [pydantic, fastapi, sqlalchemy, orcamento, read-projection]

# Dependency graph
requires:
  - phase: 20-01
    provides: "OrcamentoPedidoSnapshot, carregar_orcamento_pedidos (repositorio_orcamento_pedido.py) e OrcamentoPedido (D-04) reaproveitados por este plano"
provides:
  - "OrcamentoPedidoOut: contrato de saída do orçamento ±5% do pedido inteiro, embutido opcionalmente em PedidoCardOut"
  - "listar_clientes_produto_sql enriquecido: orçamento do pedido embutido por linha 'sem adequação', carregado em lote (D-03), zero query extra quando a página é só 'com adequação'"
affects: [20-06, 20-07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Campo embutido opcional (default=None, chave ausente quando não aplicável) em vez de endpoint dedicado ou objeto nulo — PD-08"
    - "Leitura em lote por página (nunca por linha) para enriquecer projeção de leitura com dado agregado de outro domínio (orçamento), seguindo o mesmo padrão já usado para estoque nesta mesma função"

key-files:
  created: []
  modified:
    - app/modules/pedidos/application/schemas.py
    - app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py
    - app/tests/test_pedidos_read_projection.py

key-decisions:
  - "PD-08 (do plano): orçamento embutido no card do cliente, não endpoint novo nem no resumo — motivo verificado no 20-UI-SPEC.md (medidor por linha) e no frontend (passthrough sem mapeamento campo a campo)"
  - "PD-09 (do plano): a projeção instancia OrcamentoPedido para derivar limite/restante, nunca reescreve floor(total * tolerancia) — gate de ruff/grep garante ausência de literal 0.05/1.05 no arquivo"
  - "Condição de inclusão do orçamento é só por tipo de OR (pair['adequacao']), sem ramo por estágio — mesmo comportamento em edicao e historico, por instrução explícita do plano"

patterns-established: []

requirements-completed: [GRADE-03]

# Metrics
duration: ~25min
completed: 2026-09-08
status: complete
---

# Phase 20 Plan 04: Orçamento do pedido embutido na projeção de clientes do produto Summary

**`GET /produtos/clientes` passa a devolver, por linha "sem adequação", o orçamento ±5% agregado do PEDIDO INTEIRO (todos os produtos, não só o da linha) — carregado em lote por página e derivado do ledger `OrcamentoPedido`, nunca recalculado na projeção.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2/2
- **Files modified:** 3

## Accomplishments

- `OrcamentoPedidoOut` (7 campos: `nrPedido` + limite/consumido/restante de adição e de corte) embutido opcionalmente em `PedidoCardOut` via `orcamentoPedido` (`default=None`), sem alterar `ProdutoClienteRowOut`/`ProdutoClientesSummaryOut`/`ProdutoClientesPageOut`
- `listar_clientes_produto_sql` decide por linha, usando o campo `pair["adequacao"]` já presente na query (D-03: zero query extra para a decisão), monta o conjunto de `nr_pedido` "sem adequação" da página, e só então chama `carregar_orcamento_pedidos` **uma vez** para o conjunto todo — pulado por completo quando a página é inteiramente "com adequação"
- Limite e restante sempre derivados de `OrcamentoPedido(...)` (D-04/PD-09); consumido vem direto do `OrcamentoPedidoSnapshot`; nenhum literal de tolerância (`0.05`/`1.05`) no arquivo
- Falha fechada (T-20-21): se um pedido do conjunto não vier na leitura em lote, a linha recebe limites zero em vez de omitir o objeto — omitir sinalizaria "com adequação" para o front
- 5 testes novos em `test_pedidos_read_projection.py` (16 → 21): orçamento cobre os DOIS produtos do mesmo pedido (prova de D-12), ausência da chave (não nula) em linha "com adequação", zero consulta de orçamento quando a página é só "com adequação" (via `patch`+`assert_not_called`), fallback de par ausente na leitura para limites zero, e consumido refletido de uma OR "sem adequação" com `qt_liquida > qt_solicitada`

## Task Commits

1. **Task 1: Contrato de saída do orçamento por pedido** - `2020293` (feat)
2. **Task 2: A projeção de clientes do produto passa a carregar o orçamento do pedido** - `1661d1f` (feat)

## Files Created/Modified

- `app/modules/pedidos/application/schemas.py` - `OrcamentoPedidoOut` (novo) + campo opcional `orcamento_pedido` em `PedidoCardOut`
- `app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py` - leitura em lote de orçamento por página, instanciação de `OrcamentoPedido` por pedido "sem adequação", embutido condicional no dict `order`
- `app/tests/test_pedidos_read_projection.py` - 5 testes aditivos cobrindo D-12, ausência de chave, custo zero de query, fallback de par órfão e consumido refletido

## Nome camelCase exato dos campos (para o plano 20-06)

Objeto `orcamentoPedido` (dentro do card do pedido, presente só quando `adequacaoAplicada === false`):

```json
{
  "nrPedido": 123456,
  "limiteAdicao": 5,
  "consumidoAdicao": 4,
  "restanteAdicao": 1,
  "limiteCorte": 5,
  "consumidoCorte": 0,
  "restanteCorte": 5
}
```

## Decisions Made

- PD-08 e PD-09 (ver `<decisoes_deste_plano>` do PLAN.md) seguidas à risca — nenhuma reabertura de decisão de shape.
- Nenhuma condição por `estagio` foi adicionada na decisão de incluir o orçamento — só o tipo de OR (`pair["adequacao"]`) decide, exatamente como o plano instruiu ("não condicione por estágio, condicione apenas por tipo de OR").
- Nenhuma decisão nova fora do que o plano já definiu.

## Deviations from Plan

None - plan executado exatamente como escrito.

## Issues Encountered

None.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- `OrcamentoPedidoOut` e o campo `orcamentoPedido` prontos para o plano 20-06 declarar o tipo espelho no frontend (nomes camelCase exatos documentados acima).
- Suíte completa do backend: 962 passed, 18 skipped (baseline do plano 20-03 era 957 passed — a diferença é exatamente os 5 testes novos deste plano; `test_pedidos_processing_sem_adequar_memory_024.py` segue ignorado, pré-existente, módulo `resource` Unix-only em ambiente Windows).
- `ruff check` limpo nos dois arquivos de produção tocados.
- Nenhuma migration criada — fase não altera schema (D-05).
- O payload das linhas "com adequação" permanece byte-idêntico **no dict retornado pela projeção** (chave ausente, testado); a serialização HTTP final (`ProdutoClientesPageOut.model_validate` em `routes.py`, sem `response_model_exclude_none`) não foi tocada por este plano — fora do escopo declarado em `files_modified` — e pode expor `orcamentoPedido: null` no JSON dessas linhas até que um plano futuro (se necessário) trate exclusão explícita de nulos na resposta HTTP.

---

_Phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ_
_Completed: 2026-09-08_

## Self-Check: PASSED

- FOUND: app/modules/pedidos/application/schemas.py
- FOUND: app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py
- FOUND: app/tests/test_pedidos_read_projection.py
- FOUND commit: 2020293
- FOUND commit: 1661d1f
