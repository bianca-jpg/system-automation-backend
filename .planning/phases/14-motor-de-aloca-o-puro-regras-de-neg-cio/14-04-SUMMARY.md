---
phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio
plan: 04
subsystem: domain
tags: [python, pytest, domain-driven-design, business-rules]

# Dependency graph
requires:
  - phase: 14-03
    provides: "_prioridade com desempate determinístico por nr_pedido (ordem de processamento por par estável)"
provides:
  - "tem_furo_de_grade(itens, estoque_local, cd_prod_cor) -> tuple[bool, str | None], predicado puro em novo módulo domain/furo_de_grade.py"
  - "Guarda de furo de grade ligada dentro de _processar_pedidos_canal, entre crédito e adequar_grade_produto"
  - "ALOC-04 fechado nos dois modos de processamento (guarda roda antes de qualquer despacho por modo)"
affects: ["14-05 (política de quantidade por modo)", "14-06 (orçamento ±5%)", "16 (persistência do motivo de stand by, reusa a string 'Furo de grade: ...')"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Módulo de domínio folha sem import de motor_adequacao.py, evitando dependência circular (motor_adequacao.py importa dele, não o contrário)"
    - "Guarda de early-exit dentro do laço por par, no mesmo molde da guarda de crédito: marcar_stand_by + continue, sem tocar estoque_local"

key-files:
  created:
    - app/modules/pedidos/domain/furo_de_grade.py
    - app/tests/test_pedidos_motor_furo_grade.py
  modified:
    - app/modules/pedidos/domain/motor_adequacao.py

key-decisions:
  - "Furo de grade entra na chave preteridos já existente via marcar_stand_by — nenhuma chave nova no contrato de retorno de processar_pedidos"
  - "get_tamanho_idx == 999 (desconhecido) é descartado ANTES de calcular idx_min/idx_max, corrigindo a divergência que adequar_grade_produto tinha (calculava min/max sem filtrar 999)"

patterns-established:
  - "Predicado puro (bool, motivo) para regra de negócio nova, testável sem banco e sem importar o orquestrador"

requirements-completed: [ALOC-04]

# Metrics
duration: ~35min
completed: 2026-08-17
---

# Phase 14 Plan 04: Furo de grade (ALOC-04) Summary

**Predicado puro `tem_furo_de_grade` em módulo de domínio novo, ligado dentro do laço de `_processar_pedidos_canal` entre a guarda de crédito e `adequar_grade_produto`, provado por teste a depender do estoque decrementado dentro do próprio laço.**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-08-17T12:30:00Z (aprox.)
- **Completed:** 2026-08-17T13:09:34Z
- **Tasks:** 2
- **Files modified:** 3 (1 novo módulo, 1 novo arquivo de teste, 1 arquivo editado)

## Accomplishments
- `tem_furo_de_grade(itens, estoque_local, cd_prod_cor) -> tuple[bool, str | None]` criado em `app/modules/pedidos/domain/furo_de_grade.py`, cobrindo os 5 casos de borda travados pela mantenedora (tamanho único, dois adjacentes, tamanho não pedido, tamanho desconhecido descartado da ordenação, `qt_liquida <= 0` não conta como pedido) mais determinismo do motivo relatado quando há mais de um tamanho interior zerado.
- Guarda ligada dentro de `_processar_pedidos_canal`, na posição exata da cadeia `crédito → furo de grade → política de quantidade`, sem alterar a assinatura de `adequar_grade_produto`.
- Teste de sensibilidade à ordem prova que a checagem lê o estoque já decrementado pelo pedido prioritário processado antes dela na mesma passagem — não uma foto anterior ao laço.
- Suíte completa (803 testes) permanece verde, incluindo os dois arquivos que fazem `monkeypatch` na assinatura de `adequar_grade_produto`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Criar tem_furo_de_grade em domain/furo_de_grade.py, com testes unitários dos 5 casos de borda** - `4fd0920` (feat)
2. **Task 2: Ligar tem_furo_de_grade ao laço de decisão por par, com teste de sensibilidade à ordem** - `fba2939` (feat)

**Plan metadata:** (pendente — commit final desta narrativa)

## Files Created/Modified
- `app/modules/pedidos/domain/furo_de_grade.py` - novo módulo de domínio, função pública `tem_furo_de_grade`, `__all__ = ["tem_furo_de_grade"]`
- `app/tests/test_pedidos_motor_furo_grade.py` - 8 testes unitários do predicado + 2 testes de integração via `processar_pedidos` (shim de `service.py`)
- `app/modules/pedidos/domain/motor_adequacao.py` - novo import de `tem_furo_de_grade`; nova guarda dentro de `_processar_pedidos_canal`, entre a guarda de crédito e a chamada a `adequar_grade_produto`

## Decisions Made
- Furo de grade usa a chave `preteridos` já existente (via `marcar_stand_by`), no mesmo padrão que canal desconhecido e falta de estoque genérica já usam — nenhuma chave nova no contrato de `processar_pedidos`.
- `get_tamanho_idx == 999` é filtrado ANTES do cálculo de `idx_min`/`idx_max`, não depois — provado por dois testes dedicados com o mesmo tamanho desconhecido (`"XPTO"`) em posições estruturalmente diferentes (entre extremos reconhecidos vs. como o maior idx bruto da lista).
- Import de `furo_de_grade` colocado antes de `value_objects` no bloco de imports de `motor_adequacao.py` para respeitar a ordenação alfabética exigida pelo `ruff` (`I001`), em vez de "logo após value_objects" ao pé da letra — mesmo efeito de posição relativa (import novo próximo aos demais imports de domínio), sem violar lint.

## Deviations from Plan

None - plan executado exatamente como escrito. O único ajuste foi de formatação (posição relativa dos dois imports de domínio dentro do bloco, para satisfazer `ruff check`/`ruff format`), sem alteração de comportamento.

## Issues Encountered

`ruff` não está disponível dentro do container Docker (`Permission denied` ao invocar o binário) nem como módulo Python (`No module named ruff`). Contornado rodando `uv run ruff check`/`uv run ruff format` **localmente** (fora do container) — o binário Rust do ruff não depende de `pyicu`, então não sofre da limitação documentada de `uv run pytest` no Windows. `ruff --fix` e `ruff format` aplicados sobre os 3 arquivos tocados; suíte re-executada por Docker depois para confirmar que a reformatação não alterou comportamento.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- ALOC-04 concluído nos dois modos de processamento (a guarda roda antes de qualquer despacho por modo, que só chega em 14-05).
- `preteridos`/`resultados` já carregam o motivo `"Furo de grade: tamanho {X} sem estoque reservável"` no formato que a Phase 16 (persistência do motivo de stand by) vai consumir sem reformatação.
- Próximo plano da fase: `14-05` (política de quantidade por modo — tudo-ou-nada vs. adequação), que roda depois da guarda de furo no mesmo laço.

---
*Phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio*
*Completed: 2026-08-17*

## Self-Check: PASSED

- FOUND: app/modules/pedidos/domain/furo_de_grade.py
- FOUND: app/tests/test_pedidos_motor_furo_grade.py
- FOUND: app/modules/pedidos/domain/motor_adequacao.py
- FOUND: .planning/phases/14-motor-de-aloca-o-puro-regras-de-neg-cio/14-04-SUMMARY.md
- FOUND commit: 4fd0920
- FOUND commit: fba2939
