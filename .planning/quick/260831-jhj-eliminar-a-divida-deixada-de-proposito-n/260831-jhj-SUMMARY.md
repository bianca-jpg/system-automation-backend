---
phase: quick/260831-jhj
plan: 01
subsystem: pedidos
tags: [motor-adequacao, decimal, standby, docs]

# Dependency graph
requires:
  - phase: quick/260831-ieq
    provides: "OrcamentoPedido.tolerancia conectado ao motor (ledger recebe e propaga a tolerância real de negócio)"
provides:
  - "Mensagem de stand-by por orçamento de corte formatada dinamicamente com a tolerância real do ledger, sem literal '5%' hardcoded"
  - "docs/adequacao.md descreve o teto do orçamento em função de tolerancia_adequacao, com 5% identificado como default"
affects: [pedidos, motor_adequacao, standby-ui]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Formatação de percentual: Decimal(str(tolerancia)) * 100 -> .normalize() -> format spec :f (evita float drift, zeros à direita e notação científica), mesmo padrão de OrcamentoPedido.limite_adicao/.limite_corte"

key-files:
  created: []
  modified:
    - app/modules/pedidos/domain/motor_adequacao.py
    - app/tests/test_pedidos_motor.py
    - docs/adequacao.md

key-decisions:
  - "_MOTIVO_ORCAMENTO_CORTE (constante de módulo) virou _motivo_orcamento_corte(tolerancia) (função), sem mudar a assinatura de adequar_grade_produto — a tolerância já vinha via ledger.tolerancia"

patterns-established: []

requirements-completed: [QUICK-260831-jhj]

# Metrics
duration: ~20min
completed: 2026-08-31
---

# Quick Task 260831-jhj: Eliminar a dívida deixada de propósito pela 260831-ieq — Summary

**Mensagem de stand-by por orçamento de corte deixa de dizer "5%" fixo e passa a formatar o percentual real de `ledger.tolerancia` via `Decimal` (sem float drift, zeros à direita ou notação científica); `docs/adequacao.md` descreve o teto em função de `tolerancia_adequacao`.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3 (2 com commit de código + 1 gate de verificação sem código)
- **Files modified:** 3

## Accomplishments
- `_MOTIVO_ORCAMENTO_CORTE` (constante) substituída por `_motivo_orcamento_corte(tolerancia: float) -> str`, formatando o percentual a partir de `ledger.tolerancia` no ponto de uso em `adequar_grade_produto`
- Teste novo (`test_processar_pedidos_motivo_de_stand_by_reflete_a_tolerancia_configurada`) prova que o mesmo cenário produz "…de 5%" com `tolerancia=0.05` e "…de 10%" com `tolerancia=0.10`, sem importar a função no teste (esperados são literais escritos à mão)
- `docs/adequacao.md:101` reescrita: `floor(total_original_do_pedido × tolerancia_adequacao)`, com 5% rotulado como default
- Suíte completa fechou em **888 passed / 18 skipped / 0 failed** (baseline 887 + 1 teste novo, 0 regressão)

## Task Commits

Each task was committed atomically:

1. **Task 1a: mensagem de stand-by formatada com a tolerância real (produção)** - `acd68ff` (fix)
2. **Task 1b: teste provando 0.05→"5%" / 0.10→"10%"** - `546cc20` (test)
3. **Task 2: docs/adequacao.md deixa de afirmar 5% como valor fixo** - `6f0d2ea` (docs)
4. **Task 3: gate de suíte completa** - sem commit de código (task de verificação apenas; resultado registrado abaixo)

## Files Created/Modified
- `app/modules/pedidos/domain/motor_adequacao.py` - `_MOTIVO_ORCAMENTO_CORTE` removida; `_motivo_orcamento_corte(tolerancia)` adicionada e usada no ramo `not concedido` de `adequar_grade_produto`
- `app/tests/test_pedidos_motor.py` - 1 teste novo provando a formatação dinâmica do percentual (0.05→"5%", 0.10→"10%"); as 3 asserções de literal preexistentes (~157, ~260, ~535) permaneceram intactas
- `docs/adequacao.md` - linha 101 parametrizada por `tolerancia_adequacao`, com 5% marcado como default

## Decisions Made
- Seguiu-se exatamente o padrão já usado em `OrcamentoPedido.limite_adicao`/`.limite_corte` para formatação de `Decimal` (`Decimal(str(tolerancia)) * 100`), em vez de inventar um padrão novo — consistência dentro do mesmo domínio.
- `.normalize()` seguido de format spec `:f` foi necessário porque `.normalize()` sozinho devolve notação científica para valores redondos (`Decimal("1E+1")` para tolerância 0.10) — confirmado pelo teste novo, que é justamente o caso que pegaria essa regressão.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Dívida declarada pela quick task 260831-ieq (mensagem "5%" hardcoded + `docs/adequacao.md:101`) está quitada — STATE.md pode parar de carregá-la como débito conhecido.
- Suíte completa: **887 passed / 18 skipped / 0 failed** (antes) → **888 passed / 18 skipped / 0 failed** (depois), +1 teste novo, 0 regressão.
- Nenhum outro motivo de stand-by (`SEM_ESTOQUE`, `FURO_GRADE`, `BLACKLIST`, crédito, canal) foi tocado.

---
*Phase: quick/260831-jhj*
*Completed: 2026-08-31*

## Self-Check: PASSED

All created/modified files found on disk; all task commit hashes (`acd68ff`, `546cc20`, `6f0d2ea`) found in git log.
