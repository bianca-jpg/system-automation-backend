---
phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
plan: 02
subsystem: pedidos
tags: [dominio-puro, pytest, grade-linx]

# Dependency graph
requires:
  - phase: 01-schema-linx-e-refer-ncia-de-posi-o
    provides: "Layout Linx com colunas posicionais e1..e48 (ordens_reserva_linx)"
provides:
  - "converter_grade_para_posicoes — grade interna {sg_tamanho: qtd} -> {e1..e48: qtd}, importável via app.modules.pedidos.service"
  - "Tamanho sem posição na referência: ignorado sem exceção, reportado em ignorados + aviso agregado (critério 5/D-04)"
  - "Conflito de posição na grade: último processado vence, com aviso agregado (D-03)"
affects: [fase-3-geracao-de-or-linx, 02-05-teste-com-dados-reais]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Função pura de domínio que loga (padrão de motor_adequacao.py): 'puro' = sem I/O de banco/rede, não sem logging"
    - "Retorno tuple[dict, list] para permitir asserts diretos no teste sem depender de caplog (sem precedente exato no repo antes deste plano)"

key-files:
  created:
    - app/modules/pedidos/domain/grade_linx.py
    - app/tests/test_grade_linx.py
  modified:
    - app/modules/pedidos/service.py

key-decisions:
  - "Formato exato do aviso de conflito confirmado: f'{tamanho_anterior}/{sg_tamanho}->pos{pos}' (ex.: 'M/GG->pos5'), idêntico ao usado em agregar_referencia_tamanhos (02-01)"
  - "Nota de bloqueio deixada no topo de test_grade_linx.py apontando para o Plano 02-05 (teste com dados reais depende do checkpoint humano do Plano 02-04)"

patterns-established:
  - "Conflito de posição na grade: apenas a última quantidade processada fica na posição disputada — não há 'soma' nem remoção do tamanho perdedor do dict grade original"

requirements-completed: [LINX-03]

# Metrics
duration: ~15min
completed: 2026-08-05
---

# Phase 2 Plan 02: Conversão pura da grade para posições Linx (LINX-03) Summary

**`converter_grade_para_posicoes` implementada como função pura de domínio em `app/modules/pedidos/domain/grade_linx.py`, re-exportada via `app.modules.pedidos.service`, cobrindo shape/critério 5 (tamanho sem posição não lança exceção)/D-03 (conflito de posição, último vence) com 4 testes sintéticos — 230 testes passando na suíte completa, sem regressão.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-08-05 (após 02-01)
- **Completed:** 2026-08-05
- **Tasks:** 2/2
- **Files modified:** 3 (2 novos, 1 modificado)

## Accomplishments
- `converter_grade_para_posicoes(grade, referencia, cd_prod_cor)` sempre devolve as 48 chaves `e1..e48` (preenchidas com `0` por padrão) e nunca lança exceção
- Tamanho da grade sem posição na referência é ignorado e reportado em `ignorados` (2º elemento do retorno), com `logger.warning` agregado por produto (D-04, critério 5 do ROADMAP)
- Conflito de posição (dois tamanhos apontando pra mesma posição): último processado (ordem de inserção do dict `grade`) vence, com `logger.warning` de conflito no formato `"{anterior}/{atual}->pos{pos}"` (D-03)
- Re-exportada em `app.modules.pedidos.service.__all__`, ponto único de import externo — consumida por `test_grade_linx.py` hoje e pela Fase 3 (geração de OR Linx) depois
- 4 testes sintéticos (shape/default zero, mapeamento correto, critério 5, D-03) passando sem tocar banco; suíte completa 230 passed (baseline 226 + 4 novos), sem regressão

## Task Commits

Each task was committed atomically:

1. **Task 1: Implementar converter_grade_para_posicoes + re-export (LINX-03)** - `4193d7e` (feat)
2. **Task 2: Testes sintéticos da conversão (critério 5, D-03)** - `1b11f2b` (test)

## Files Created/Modified
- `app/modules/pedidos/domain/grade_linx.py` - `converter_grade_para_posicoes`, função pura que converte grade->posições e1..e48
- `app/modules/pedidos/service.py` - import + `__all__` de `converter_grade_para_posicoes`
- `app/tests/test_grade_linx.py` - 4 testes sintéticos (sem I/O)

## Decisions Made
- Nenhuma decisão nova além das já travadas em `02-CONTEXT.md` (D-03, D-04). O corpo da função seguiu exatamente o "Pattern 4" pronto do RESEARCH.md/PATTERNS.md, sem desvios.
- Formato do aviso de conflito confirmado como `"M/GG->pos5"`, mesmo formato usado em `agregar_referencia_tamanhos` (Plano 02-01) — consistência entre a ingestão e a conversão de domínio.

## Deviations from Plan

None - plan executado exatamente como escrito. MCP `backstage_get_coding_standards` indisponível neste ambiente; seguido fallback documentado em `02-PATTERNS.md` (padrões dos análogos do repo, `motor_adequacao.py` e `test_pedidos_motor.py`).

## Issues Encountered
Nenhum.

## User Setup Required
None - nenhuma configuração de serviço externo necessária. Função pura, sem dependência de `.env` ou infraestrutura nova.

## Next Phase Readiness
- `converter_grade_para_posicoes` pronta para a Fase 3 importar via `app.modules.pedidos.service` e preencher `e1..e48` de `ordens_reserva_linx` na geração de OR.
- O 5º teste (dados reais, critério 4 do ROADMAP) fica para o Plano 02-05, após o checkpoint humano do Plano 02-04 capturar uma amostra real de `produto_tamanho_posicao` — nota de bloqueio já deixada em `test_grade_linx.py`.
- Nenhum bloqueio conhecido. Suíte completa verde (230 passed, 1 warning, ~3min26s no container).

## Self-Check: PASSED

Ambos os arquivos novos (`app/modules/pedidos/domain/grade_linx.py`, `app/tests/test_grade_linx.py`) encontrados no filesystem; ambos os commits de task (`4193d7e`, `1b11f2b`) encontrados em `git log`.

---
*Phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade*
*Completed: 2026-08-05*
