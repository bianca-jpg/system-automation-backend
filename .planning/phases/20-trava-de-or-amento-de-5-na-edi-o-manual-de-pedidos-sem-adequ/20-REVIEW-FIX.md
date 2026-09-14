---
phase: 20-trava-de-orcamento-de-5-na-edicao-manual-de-pedidos-sem-adequacao
fixed_at: 2026-09-08T15:41:11Z
review_path: .planning/phases/20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ/20-REVIEW.md
iteration: 1
findings_in_scope: 3
fixed: 3
skipped: 0
status: partial
---

# Phase 20: Code Review Fix Report

**Fixed at:** 2026-09-08T15:41:11Z
**Source review:** .planning/phases/20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ/20-REVIEW.md
**Iteration:** 1

**Summary:**

- Findings in scope (critical + warning): 3
- Fixed (code-complete, verified, **not yet git-committed**): 3
- Skipped: 0

**IMPORTANT — commits withheld, not applied to `develop`:** All three fixes below were applied, verified with syntax checks and by running their tests (and the full backend suite), and then **copied uncommitted into the main working tree** (`backend`, branch `develop`). No `git commit` was made. This deliberately departs from the usual per-finding auto-commit flow:

The isolated worktree used to apply these fixes resolved `commit_docs: false` for this project (`gsd-tools` config-loader: `commit_docs` defaults to `false` whenever `.planning/` matches a `.gitignore` rule via `git check-ignore --no-index`, which is the case here — `.gitignore:66` lists `.planning/`, even though it is currently force-tracked). The `gsd-tools commit` helper therefore refused every commit attempt (`reason: "skipped_commit_docs_false"`). Rather than bypass that gate with a raw `git commit`, this was treated as a hard stop, consistent with this project's known git policy (only `develop`/`main`, no new branches left behind, and — per standing instruction — no commit ever goes out without the user personally writing/approving the message first).

**What is on disk right now:** the 6 files below are modified, uncommitted, on `develop` in the real `backend` working tree. Review the diff (`git diff`) and commit with your own message(s) when ready — suggested messages are included per finding below.

## Fixed Issues

### CR-01: Rewind+recharge in `validar_edicao_orcamento` locks out any edit (including pure resubmission) on a "sem adequação" pair whose current contribution already exceeds the pedido's ±5% limit

**Files modified:** `app/modules/pedidos/domain/orcamento_edicao.py`, `app/tests/test_pedidos_orcamento_edicao.py`, `app/tests/test_pedidos_product_writes.py`
**Commit:** none yet — suggested message: `fix(20): CR-01 grandfather par's own committed budget contribution in validar_edicao_orcamento`
**Applied fix:** Changed `validar_edicao_orcamento` to grandfather the pair's own already-committed `contribuicao_atual` instead of fully rewinding and recharging it. Only the *increment* (`contribuicao_nova` above `contribuicao_atual`) is now checked against the remaining rewound budget; a same-or-lower `contribuicao_nova` always passes (incremento = 0), even when the pair's legacy contribution already exceeds the pedido's limit. Verified algebraically equivalent to the old behavior for every case where `contribuicao_atual` is within budget (all pre-existing tests still pass unmodified), and diverges only in the pathological legacy-over-limit case that CR-01 identified.
- Added 3 unit regression tests to `test_pedidos_orcamento_edicao.py`: no-op resave of an over-limit legacy pair passes, a reduction of an over-limit pair passes, and a further *increase* of an already over-limit pair still correctly raises (grandfathering does not become a loophole for new deviation).
- Added 1 real-DB regression test to `test_pedidos_product_writes.py` (`test_orcamento_resave_par_legado_acima_do_limite_nao_bloqueia`): seeds a legacy "sem adequação" `OrdemReserva` whose `qt_liquida` (108) already deviates from `qt_solicitada` (100, baseline) by more than `floor(100 × 0.05) = 5`, then confirms a no-op resave succeeds end-to-end through `executar_alteracao_grades_produto`.
- Full backend suite run after the fix: `975 passed, 18 skipped` before WR-01/WR-02 were added, `981 passed, 18 skipped` after all three findings — **0 regressions** (`--ignore=app/tests/test_pedidos_processing_sem_adequar_memory_024.py` per known-flaky baseline).
- This is a logic fix to a documented idempotency claim, not a business-rule change — the module docstring already asserted this behavior was idempotent; the fix makes the code match that claim. Flagged as `fixed: requires human verification` per the fixer's own rules for logic-bug findings — please double check the grandfathering semantics against the actual tolerance policy before merging.

### WR-01: No test exercises the HTTP-level 409 contract for `OrcamentoPedidoExcedidoError` or the `OrcamentoPedidoOut` camelCase serialization

**Files modified:** `app/tests/test_pedidos_routes.py`, `app/tests/test_pedidos_read_projection.py`
**Commit:** none yet — suggested message: `test(20): WR-01 cover 409 orcamento contract and camelCase serialization`
**Applied fix:**
- Added `test_batch_grade_orcamento_excedido_retorna_409_camelcase` to `test_pedidos_routes.py`: mocks `service.executar_alteracao_grades_produto` to raise `service.OrcamentoPedidoExcedidoError(...)` and asserts `response.status_code == 409` plus the exact camelCase body (`code`, `message`, `nrPedido`, `orcamento`, `restanteAdicao`, `restanteCorte`).
- Extended `test_read_projection_orcamento_pedido_cobre_todos_os_produtos_do_pedido` in `test_pedidos_read_projection.py` to also run the fetched row's `order` dict through `PedidoCardOut.model_validate(...)` and `model_dump(mode="json", by_alias=True)`, asserting the JSON uses `orcamentoPedido`/`nrPedido`/`limiteAdicao`/`limiteCorte`/`consumidoAdicao`/`restanteAdicao`/`consumidoCorte`/`restanteCorte`. (Used `PedidoCardOut` rather than `ProdutoClientesPageOut` — the raw repository dict shape (`next_key`, no `page_size`) doesn't match the page-level schema without the service-layer pagination adapter in between; `PedidoCardOut` validates the same `orcamento_pedido` field and is the more surgically-scoped choice.)

### WR-02: `_linha_base_par` duplicates `_qt_solicitada_ou_fallback`'s logic verbatim across two files with no shared source of truth

**Files modified:** `app/tests/test_pedidos_edicao_grade_variacao_total.py`
**Commit:** none yet — suggested message: `test(20): WR-02 add cross-file consistency test for baseline fallback logic`
**Applied fix:** Added a parametrized cross-file consistency test (`test_linha_base_par_e_qt_solicitada_ou_fallback_concordam`) that calls `casos_uso._linha_base_par` and `edicao_grade._qt_solicitada_ou_fallback` (summed) against 5 shared fixtures — explicit `qt_solicitada`, explicit `0`, `None`, empty string, and empty list — and asserts identical output. This fails loudly and locally if either copy of the fallback rule drifts from the other, per the review's minimum recommendation. No production code was duplicated/merged (the review accepted the duplication as a deliberate cross-plan-coupling tradeoff); this only adds the missing safety net.

## Skipped Issues

None — all 3 in-scope findings (CR-01, WR-01, WR-02) were fixed. IN-01 was out of scope (`fix_scope: critical_warning`).

---

_Fixed: 2026-09-08_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
