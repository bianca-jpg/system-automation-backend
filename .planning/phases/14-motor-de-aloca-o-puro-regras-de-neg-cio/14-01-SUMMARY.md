---
phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio
plan: 01
subsystem: domain
tags: [python, decimal, rateio, hamilton, ddd, pedidos]

# Dependency graph
requires: []
provides:
  - "ratear_hamilton: função pública de domínio para rateio de maior resto (Hamilton), reutilizável fora de edicao_grade.py"
  - "app/modules/pedidos/domain/rateio.py como módulo folha (sem dependências de outros módulos de pedidos/domain)"
affects: [14-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Módulo de domínio folha com __all__ explícito, extraído por promoção de função privada existente"

key-files:
  created:
    - app/modules/pedidos/domain/rateio.py
    - app/tests/test_pedidos_rateio.py
  modified:
    - app/modules/pedidos/domain/edicao_grade.py

key-decisions:
  - "ratear_hamilton extraída byte-a-byte de _allocate (mesma quantização ROUND_HALF_UP/ROUND_DOWN, mesmo desempate) para eliminar qualquer risco de drift de centavo na promoção"
  - "edicao_grade.py mantém o alias `as _allocate` no import, preservando os dois call-sites de montar_grade_atualizada sem tocá-los"

patterns-established:
  - "Módulo de domínio novo e pequeno (função única) termina com __all__ explícito, seguindo o padrão já usado em edicao_grade.py"

requirements-completed: [FIX-02]

# Metrics
duration: 10min
completed: 2026-08-14
---

# Phase 14 Plan 01: Extração do utilitário de rateio Hamilton Summary

**`ratear_hamilton` promovida a função pública de domínio em `rateio.py`, reproduzindo exatamente a aritmética do antigo `_allocate` privado de `edicao_grade.py`, com teste dedicado e isolado.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-08-14T18:38:00Z
- **Completed:** 2026-08-14T18:47:49Z
- **Tasks:** 3 completed
- **Files modified:** 3 (1 novo módulo, 1 módulo editado, 1 novo arquivo de teste)

## Accomplishments
- `app/modules/pedidos/domain/rateio.py` criado como módulo folha (sem import de outros módulos de `pedidos/domain`), expondo `ratear_hamilton(total: Decimal, sizes: dict[str, int]) -> dict[str, Decimal]` com `__all__ = ["ratear_hamilton"]`
- `edicao_grade.py` não duplica mais a aritmética de rateio — importa `ratear_hamilton as _allocate`, preservando os dois call-sites de `montar_grade_atualizada` sem alteração
- `test_pedidos_rateio.py` cobre as quatro invariantes centrais (soma exata com desempate, sinal negativo, quantidade zero, caso sem remainder) sem depender de `edicao_grade.py`
- FIX-02 metade 1/2 fechada (a ligação no motor de adequação fica para o plano 14-06)

## Task Commits

Cada task foi commitada atomicamente:

1. **Task 1: Criar domain/rateio.py com ratear_hamilton (extração literal)** - `2faae22` (feat)
2. **Task 2: Ligar edicao_grade.py ao novo utilitário, sem duplicar código** - `acb6bee` (refactor)
3. **Task 3: Teste dedicado de ratear_hamilton, isolado de edicao_grade.py** - `286c4f9` (test)

_Nenhuma task exigiu ciclo TDD RED/GREEN — o plano não tem `tdd="true"`._

## Files Created/Modified
- `app/modules/pedidos/domain/rateio.py` - Novo módulo folha com `ratear_hamilton`, extraído literalmente de `_allocate`
- `app/modules/pedidos/domain/edicao_grade.py` - `_allocate` local removida; agora importa `ratear_hamilton as _allocate` de `rateio.py`
- `app/tests/test_pedidos_rateio.py` - 4 testes unitários de `ratear_hamilton`, isolados de `edicao_grade.py`

## Decisions Made
- Extração byte-a-byte (mesma constante `_CENT`, mesma ordem de arredondamento `ROUND_HALF_UP`/`ROUND_DOWN`, mesma chave de desempate `(-fração, nome)`) para garantir zero drift de centavo na promoção — exigência explícita do plano e do threat register (T-14-01)
- Alias `as _allocate` no import de `edicao_grade.py` evitou qualquer alteração nos call-sites existentes dentro de `montar_grade_atualizada`

## Deviations from Plan

None - plano executado exatamente como escrito.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `ratear_hamilton` está pronta para ser importada pelo motor de adequação no plano `14-06` (segunda metade de FIX-02)
- Suíte completa via Docker verde: 762 passed (758 baseline + 4 novos testes), 16 skipped, 1 failed — a falha conhecida e pré-existente (`test_read_projection_empty_contracts_execute_real_sql`, `RuntimeError: Event loop is closed`), sem nenhuma regressão introduzida por este plano
- `app/modules/pedidos/domain/rateio.py` continua módulo folha — nenhuma dependência circular criada, seguro para `motor_adequacao.py` importar diretamente no plano 14-06

---
*Phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio*
*Completed: 2026-08-14*

## Self-Check: PASSED

- FOUND: app/modules/pedidos/domain/rateio.py
- FOUND: app/tests/test_pedidos_rateio.py
- FOUND: commit 2faae22
- FOUND: commit acb6bee
- FOUND: commit 286c4f9
