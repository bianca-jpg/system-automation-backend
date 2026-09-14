---
phase: 20-trava-de-orcamento-de-5-na-edicao-manual-de-pedidos-sem-adequacao
reviewed: 2026-09-08T00:00:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - app/modules/pedidos/application/casos_uso.py
  - app/modules/pedidos/application/ports.py
  - app/modules/pedidos/application/schemas.py
  - app/modules/pedidos/domain/edicao_grade.py
  - app/modules/pedidos/domain/orcamento_edicao.py
  - app/modules/pedidos/domain/rateio.py
  - app/modules/pedidos/infrastructure/http/routes.py
  - app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py
  - app/modules/pedidos/infrastructure/repositorio_orcamento_pedido.py
  - app/modules/pedidos/infrastructure/write_adapter.py
  - app/modules/pedidos/processing/infrastructure/adapters.py
  - app/modules/pedidos/service.py
  - app/tests/test_pedidos_edicao_grade_variacao_total.py
  - app/tests/test_pedidos_orcamento_edicao.py
  - app/tests/test_pedidos_processing_repository.py
  - app/tests/test_pedidos_product_writes.py
  - app/tests/test_pedidos_read_projection.py
  - app/tests/test_pedidos_routes.py
findings:
  critical: 1
  warning: 2
  info: 1
  total: 4
status: issues_found
---

# Phase 20: Code Review Report

**Reviewed:** 2026-09-08
**Depth:** standard
**Files Reviewed:** 18
**Status:** issues_found

## Summary

Reviewed the domain ledger (`orcamento_edicao.py`, `orcamento_pedido.py`, `edicao_grade.py`, `rateio.py`), the shared budget SQL (`repositorio_orcamento_pedido.py`), the write path (`casos_uso.py`, `write_adapter.py`, `ports.py`, `routes.py`), the read-side projection (`listar_clientes_produto.py`, `schemas.py`), and the full associated test suite.

Both pre-existing bugs this phase set out to fix are genuinely fixed and well covered by tests:
- The SQL `WHERE op.tipo = 'com'` filter that made "sem adequação" pairs invisible to the pedido budget is fixed by adding a `consumido_por_total_par` branch (measured by par TOTAL, not by item) and unioning it with the untouched `'com'` branch — verified byte-identical for `'com'` via `test_load_pedido_budget_com_adequacao_medicao_por_item_permanece_intacta` and diffed directly against the pre-phase SQL in git history.
- The `qt_solicitada` reset-to-new-quantity bug (which made every "sem" edit silently zero its own contribution, defeating the trava on repeated edits) is fixed by the `permitir_variacao_total`/baseline-allocation logic in `edicao_grade.py`, proven by `test_duas_edicoes_sucessivas_soma_qt_solicitada_permanece_100` and the real-DB `test_orcamento_persiste_entre_execucoes_sucessivas`.
- The transaction/lock story around the new budget check + auto-approval fusion is sound: both run inside the same `pg_try_advisory_xact_lock`-guarded transaction as the grade mutation, budget validation happens strictly before any write statement (so a rejection never leaves a partial write), and the fusion's approval calls are scoped per-`(nr_pedido, cd_prod_cor)` (verified in `repositorio_produto_writes.py`), matching the "escopo não vaza" test suite.
- "Com adequação" regression: the `'com'` SQL branch, `montar_grade_atualizada` with `permitir_variacao_total=False`, and the manual-edit budget check (skipped entirely for `tipo == "com"`) are all confirmed unchanged/untouched, with explicit guardian tests on both sides.

However, one critical, reproducible defect was found in the rewind+recharge logic itself (CR-01) that can permanently lock legitimate edits out of exactly the orders this phase is meant to protect (pre-existing "sem adequação" orders whose live deviation already exceeds ±5%, which is likely given the very SQL bug this phase fixes meant such orders were never checked before). Two coverage gaps are also flagged.

## Critical Issues

### CR-01: Rewind+recharge in `validar_edicao_orcamento` locks out any edit (including pure resubmission) on a "sem adequação" pair whose current contribution already exceeds the pedido's ±5% limit

**File:** `app/modules/pedidos/domain/orcamento_edicao.py:147-183`

**Issue:** `validar_edicao_orcamento` "rewinds" the pair's own current contribution out of the live consumed total, then fully recharges `contribuicao_nova` against the resulting ledger (`consumido_previo_X = max(snapshot.consumido_X - contribuicao_atual.X, 0)`, then `ledger.consumir_adicao/consumir_corte(contribuicao_nova.X)`). This is only proven idempotent for the case where the pair's own current contribution is **within** the pedido's limit (the only case covered by `test_validar_edicao_orcamento_e_idempotente_para_o_mesmo_estado_final`, which uses `contribuicao_atual=5` against `limite=5`).

When a pair's current contribution already **exceeds** the pedido's limit — which is the exact situation that existed for every "sem adequação" order before this phase, since the pre-phase SQL never measured them at all (the bug this phase fixes) — the rewind sets `consumido_previo_X = 0` (because `contribuicao_atual.X >= snapshot.consumido_X` for a pair that is the sole/dominant contributor), giving the ledger a fresh `restante_X = limite_X`. Recharging `contribuicao_nova.X` (which for a pure resave or a total-preserving size redistribution equals `contribuicao_atual.X`, i.e. the already-over-limit amount) then fails because `contribuicao_atual.X > limite_X`.

Net effect: once this phase ships, **any** save on a legacy over-budget "sem adequação" order — including a byte-identical resubmission or a redistribution that does not change the total at all — is rejected with 409, even though the edit introduces zero additional deviation. The only way to save such an order again is to reduce it, in a single edit, all the way back within ±5%. This is a functional regression for exactly the population of orders this phase exists to bring under control.

Reproduced directly against the shipped code:

```python
from app.modules.pedidos.domain.orcamento_edicao import (
    OrcamentoPedidoSnapshot, ContribuicaoOrcamento, validar_edicao_orcamento,
)

snapshot = OrcamentoPedidoSnapshot(
    nr_pedido=1, total_original=100, consumido_adicao=8, consumido_corte=0
)  # legacy "sem" order, 8 peças de adição já vivas (nunca validadas antes desta fase)

validar_edicao_orcamento(
    snapshot=snapshot,
    tolerancia=0.05,  # limite_adicao = 5
    contribuicao_atual=ContribuicaoOrcamento(adicao=8, corte=0),
    contribuicao_nova=ContribuicaoOrcamento(adicao=8, corte=0),  # no-op resave
)
# raises OrcamentoPedidoExcedidoError: "restam 5 peça(s) ... mas a edição pede 8 peça(s)."
```

**Fix:** Grandfather the pair's already-committed contribution instead of fully rewinding it. For example, only require budget for the *increase* beyond what the pair already holds, and treat a same-or-lower `contribuicao_nova` as always permitted:

```python
# Nunca cobrar de novo o que este par já detém; só o incremento acima do
# que já estava consumido por ele mesmo precisa caber no restante.
incremento_adicao = max(contribuicao_nova.adicao - contribuicao_atual.adicao, 0)
if incremento_adicao > 0:
    usado = ledger.consumir_adicao(incremento_adicao)
    if usado < incremento_adicao:
        raise OrcamentoPedidoExcedidoError(...)
# idem para corte
```

or, more surgically, short-circuit when `contribuicao_nova == contribuicao_atual` (no functional change to this pair, so no new deviation is being introduced) before doing the rewind/recharge at all. Whichever approach is chosen, add a regression test with `contribuicao_atual` **above** `limite_X` (not just at it) to lock in the fix, and add a real-DB test seeding a legacy "sem" `ordens_reserva` row whose current `qt_liquida` already deviates from `qt_solicitada` by more than `floor(total_original * tolerancia)`, then confirming a no-op or redistribution save succeeds.

## Warnings

### WR-01: No test exercises the HTTP-level 409 contract for `OrcamentoPedidoExcedidoError` or the `OrcamentoPedidoOut` camelCase serialization

**File:** `app/modules/pedidos/infrastructure/http/routes.py:204-215`, `app/modules/pedidos/application/schemas.py:163-177`

**Issue:** `routes.py` maps `service.OrcamentoPedidoExcedidoError` to a structured 409 body (`code`, `message`, `nrPedido`, `orcamento`, `restanteAdicao`, `restanteCorte`) — this is a new API contract explicitly built for the frontend (per D-12 in the plan). The only HTTP-level test for this route, `test_batch_grade_http_camelcase_limites_e_channel_exato` in `test_pedidos_routes.py`, mocks `service.executar_alteracao_grades_produto` to always return a success dict; it never makes the mock raise `OrcamentoPedidoExcedidoError`, so the except-branch that builds the 409 `detail` dict is never executed by any test. Likewise, `OrcamentoPedidoOut`'s `serialization_alias` mapping (`limiteAdicao`, `restanteAdicao`, etc.) is exercised only at the dict/application layer in `test_pedidos_read_projection.py`, never through `PedidoCardOut.model_validate(...)` + actual JSON serialization. A future alias typo, a renamed exception attribute, or a wrong status code would not be caught by the existing suite.

**Fix:** Add an HTTP-level test that mocks `service.executar_alteracao_grades_produto` to raise `service.OrcamentoPedidoExcedidoError(...)` and asserts `response.status_code == 409` and the exact camelCase JSON body (`code`, `nrPedido`, `orcamento`, `restanteAdicao`, `restanteCorte`). Add one route-level (or `ProdutoClientesPageOut.model_validate`) test that serializes a row containing `orcamento_pedido` and asserts the response JSON uses `orcamentoPedido`/`limiteAdicao`/`restanteAdicao` keys.

### WR-02: `_linha_base_par` duplicates `_qt_solicitada_ou_fallback`'s logic verbatim across two files with no shared source of truth

**File:** `app/modules/pedidos/application/casos_uso.py:41-56` vs `app/modules/pedidos/domain/edicao_grade.py:20-31`

**Issue:** The budget check's baseline computation (`_linha_base_par`) and the grade-rebuild's baseline computation (`_qt_solicitada_ou_fallback`) implement the exact same fallback rule (`qt_solicitada` → `qt_liquida` when absent/empty, floored at 0, explicit `0` preserved) in two separate functions, by explicit design choice documented in the docstring ("este plano (20-03) não altera aquele arquivo... então o mesmo cálculo é reproduzido aqui em vez de importado"). This is an accepted tradeoff to avoid cross-plan coupling, and today's integration tests (`test_orcamento_sucessivo_edicoes_acumulam` and friends) would likely catch a behavioral drift between the two copies since they exercise the whole write path end-to-end. Still, it is a maintenance hazard: a future change to either fallback rule in isolation (e.g. a unit-level fix to `edicao_grade.py` alone) has no compile-time or lint-time signal tying it back to `casos_uso.py`'s copy, and could silently desynchronize the budget ledger's notion of "baseline" from the grade actually persisted.

**Fix:** At minimum, add a cross-file consistency test that calls both functions with the same fixture data and asserts identical output, so any future edit to one without the other fails loudly and locally instead of relying on end-to-end coverage to catch it.

## Info

### IN-01: `AlterarGradesProdutoResponse.approved_count` is omitted (relying on Pydantic default) in the early-return "no changes" branch

**File:** `app/modules/pedidos/application/casos_uso.py:459-471`

**Issue:** The early-return branch when `updates` is empty returns a dict without an `approved_count` key, relying on `AlterarGradesProdutoResponse`'s `default=0` to fill it in during `model_validate`. Every other return path in the same function explicitly includes `"approved_count": approved_count`. This is not a bug (the Pydantic default correctly fills the gap), but the inconsistency makes the dict shape harder to reason about at a glance and is easy to break if the field ever becomes required.

**Fix:** Include `"approved_count": 0` explicitly in the early-return dict for consistency with the rest of the function.

---

_Reviewed: 2026-09-08_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
