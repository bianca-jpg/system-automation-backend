---
phase: quick/260831-ieq
plan: 01
subsystem: api
tags: [python, dataclass, decimal, pytest, motor-adequacao]

# Dependency graph
requires:
  - phase: 14 (Motor de alocação de OR fiel às regras de negócio)
    provides: "OrcamentoPedido (ledger de orçamento ±5% por pedido) e o parâmetro tolerancia já roteado até _processar_pedidos_canal"
provides:
  - "tolerancia_adequacao (parâmetro de negócio lido do banco por load_adequation_config) agora chega de verdade ao teto de ±5% do orçamento por pedido — deixou de ser parâmetro morto"
  - "OrcamentoPedido.tolerancia: float, obrigatório, validado em [0.0, 1.0], derivando limite_adicao/limite_corte por floor(total_original × tolerancia) exato via Decimal"
  - "assert_orcamento_nao_excedido (invariante I5) agora exige tolerancia keyword-only, sem default, calculando o limite a partir da tolerância real da execução"
affects: [motor-adequacao, parametros, testes-de-propriedade-fase-19]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Campo de negócio obrigatório sem default em dataclass (mesmo padrão já usado para consumido_previo_*): omissão vira TypeError do Python, nunca comportamento silenciosamente errado"
    - "Decimal(str(float)) + int() para floor exato de porcentagem sobre inteiro, evitando ULP de ponto flutuante alterar o resultado do caso default"

key-files:
  created: []
  modified:
    - app/modules/pedidos/domain/orcamento_pedido.py
    - app/modules/pedidos/domain/motor_adequacao.py
    - app/tests/test_pedidos_orcamento_pedido.py
    - app/tests/test_pedidos_motor.py
    - app/tests/invariantes_motor_adequacao.py
    - app/tests/test_invariantes_motor_adequacao.py

key-decisions:
  - "tolerancia é campo posicional sem default em OrcamentoPedido, igual aos outros 4 campos obrigatórios — um default reintroduziria em silêncio o bug que este plano corrige"
  - "Decimal(str(tolerancia)) * total_original em vez de tolerancia * total_original: evita que erro de ULP do float 0.05 mude o floor do caso default, provado por paridade numérica em range(0, 1001)"
  - "assert_orcamento_nao_excedido (I5) ganhou tolerancia keyword-only e obrigatória, sem default, para o oráculo nunca voltar a mentir silenciosamente contra um 5% fixo"

patterns-established:
  - "Wiring de parâmetro de negócio: quando um valor já viaja por várias camadas mas é ignorado na borda final, a correção é expor o campo como obrigatório (não opcional) para que o compilador/runtime denuncie qualquer chamador que esqueça de passá-lo"

requirements-completed: [QUICK-260831-ieq]

# Metrics
duration: ~20min
completed: 2026-08-31
---

# Quick Task 260831-ieq: Conectar tolerância de adequação ao parâmetro Summary

**`tolerancia_adequacao` deixou de ser parâmetro morto: `OrcamentoPedido` ganhou o campo obrigatório `tolerancia`, o motor passou a repassá-lo ao ledger, e a invariante I5 passou a medir contra a tolerância real de cada execução — com paridade numérica exata provada para o default 0.05.**

## Performance

- **Duration:** ~20min
- **Tasks:** 3 (2 de código + 1 gate de verificação)
- **Files modified:** 6

## Accomplishments

- `OrcamentoPedido.tolerancia: float` obrigatório, validado em `[0.0, 1.0]`, com `limite_adicao`/`limite_corte` derivando dele por `floor(total_original × tolerancia)` via `Decimal(str(tolerancia))` — aritmética exata, sem regressão de ponto flutuante.
- `_processar_pedidos_canal` repassa `tolerancia=tolerancia` na construção do ledger — a única mudança de produção em `motor_adequacao.py`, sem nova assinatura de função.
- Invariante I5 (`assert_orcamento_nao_excedido`) exige `tolerancia` como argumento keyword-only sem default, calculando o limite a partir da tolerância real da execução, independente do código de produção.
- Prova ponta a ponta: mesmo pedido de 100 peças com falta de 8 sai como stand-by com `tolerancia=0.05` e como `Gerar OR` (qt_liquida 92) com `tolerancia=0.10`.
- Paridade numérica formal: `range(0, 1001)` inteiro provando que `tolerancia=0.05` produz exatamente `(total_original * 5) // 100`, o mesmo resultado de hoje.
- Suíte relevante (orçamento, motor, invariantes, cenário da mantenedora, performance, cancelamento) e suíte completa fecharam sem regressão: 887 passed / 18 skipped / 0 failed (baseline 881/18/0 + 6 testes novos deste plano).

## Task Commits

Each task was committed atomically:

1. **Task 1: `tolerancia` vira campo do ledger e passa a derivar os dois limites** - `6ce3ab4` (fix)
2. **Task 2: motor repassa a tolerância e a invariante I5 passa a medir contra ela** - `ec50e5f` (fix)
3. **Task 3: gate de suíte — nenhuma regressão no default 0.05** - sem commit de código (task de verificação pura; suíte relevante e completa confirmadas verdes)

## Files Created/Modified

- `app/modules/pedidos/domain/orcamento_pedido.py` - campo `tolerancia` obrigatório + validação de faixa + `limite_adicao`/`limite_corte` derivados via `Decimal`
- `app/modules/pedidos/domain/motor_adequacao.py` - `OrcamentoPedido(..., tolerancia=tolerancia)` em `_processar_pedidos_canal`
- `app/tests/test_pedidos_orcamento_pedido.py` - `tolerancia=0.05` nas 11 construções existentes + testes novos de paridade/tolerância viva/faixa
- `app/tests/test_pedidos_motor.py` - `tolerancia=0.05` na construção existente + nos 3 call sites de I5 + teste ponta a ponta de duas tolerâncias
- `app/tests/invariantes_motor_adequacao.py` - `assert_orcamento_nao_excedido` ganha `tolerancia` keyword-only obrigatória
- `app/tests/test_invariantes_motor_adequacao.py` - 3 call sites de I5 ganham `tolerancia=0.05` + teste novo provando que I5 segue a tolerância recebida

## Decisions Made

- Nenhuma decisão fora do que o plano já especificava — execução seguiu o `<action>` de cada task à risca, incluindo a escolha de `Decimal(str(...))` em vez de multiplicação direta de float, já justificada no plano.

## Deviations from Plan

None - plan executado exatamente como escrito.

## Known Debt (fora de escopo, registrado no plano)

Duas imprecisões deliberadamente deixadas de fora deste plano, porque corrigi-las mudaria o que testes existentes hoje verificam:

1. **`motor_adequacao.py` linha ~36** — a constante `_MOTIVO_ORCAMENTO_CORTE` diz literalmente "excede o restante do orçamento de 5%". Com `tolerancia_adequacao != 0.05` o texto fica impreciso (ex.: com 10% o teto real é 10%, mas a mensagem de stand-by continua dizendo "5%"). Dois testes (`test_pedidos_motor.py` linhas ~156 e ~253) verificam esse texto literal.
2. **`docs/adequacao.md` linha ~101** — descreve o teto como `floor(total_original_do_pedido × 5%)`, mesma imprecisão pelo mesmo motivo.

Ambas continuam sendo apenas rótulo/documentação — não afetam o cálculo real do teto, que já usa a tolerância correta a partir deste plano.

## Issues Encountered

None.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- `tolerancia_adequacao` alterado no painel de parâmetros agora move o teto real do motor de alocação — mudança operacional visível, sem trabalho adicional pendente.
- A dívida de mensagem/documentação (`motor_adequacao.py` linha ~36 e `docs/adequacao.md` linha ~101) fica registrada para uma quick task futura que também ajuste os dois testes que hoje fixam o texto "5%".

---
*Quick task: 260831-ieq*
*Completed: 2026-08-31*

## Self-Check: PASSED

All modified files and both task commits (`6ce3ab4`, `ec50e5f`) verified present.
