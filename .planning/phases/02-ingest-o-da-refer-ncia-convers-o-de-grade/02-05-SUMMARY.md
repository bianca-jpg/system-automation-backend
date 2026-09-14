---
phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
plan: 05
subsystem: pedidos
tags: [pytest, grade-linx, dados-reais]

# Dependency graph
requires:
  - phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
    provides: "converter_grade_para_posicoes (02-02) + amostra real capturada no checkpoint (02-04)"
provides:
  - "Critério 4 do ROADMAP fechado: função pura testada sem banco, com dados reais já ingeridos nesta fase"
  - "Fase 2 completa: 5 critérios de sucesso do ROADMAP e requirements ING-01/LINX-03 cobertos"
affects: [fase-3-geracao-de-or-linx]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Fixture de dados reais transcrita literalmente da evidência do checkpoint (02-04-SUMMARY.md), com comentário de origem, em vez de valores inventados"

key-files:
  created: []
  modified:
    - app/tests/test_grade_linx.py

key-decisions:
  - "Amostra usada: AC.02.0002|163 (6 tamanhos numéricos 17-27, posições contíguas 1-6), copiada literalmente de 02-04-SUMMARY.md"

requirements-completed: [LINX-03]

# Metrics
duration: ~15min
completed: 2026-08-05
---

# Phase 2 Plan 05: Fixture de dados reais e teste do critério 4 (LINX-03) Summary

**5º teste em `test_grade_linx.py` usa a amostra real `AC.02.0002|163` capturada via `psql` no checkpoint do Plano 02-04, provando o critério 4 do ROADMAP sem qualquer acesso a banco no teste — suíte completa da Fase 2 em 241 passed.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-08-05 (após 02-04 concluído com evidência real)
- **Completed:** 2026-08-05
- **Tasks:** 1/1
- **Files modified:** 1

## Accomplishments
- `_CD_PROD_COR_REAL` (`"AC.02.0002|163"`) e `_REFERENCIA_REAL` (`{"17": 1, "19": 2, "21": 3, "23": 4, "25": 5, "27": 6}`) adicionadas a `test_grade_linx.py`, transcritas literalmente da evidência capturada via `psql` no Plano 02-04 (nenhum valor inventado)
- `test_converter_grade_para_posicoes_com_dados_reais_da_referencia` adicionado: monta uma grade sintética com 2 dos tamanhos reais (`{"17": 2, "23": 10}`), chama `converter_grade_para_posicoes` com a fixture real e confirma que cada tamanho cai na posição real correspondente, sem itens ignorados
- Nota de bloqueio deixada no Plano 02-02 (apontando para este plano) removida
- Critério 4 do ROADMAP ("função pura de conversão testada sem acesso a banco usando os dados reais já ingeridos nesta fase") provado
- Suíte completa da Fase 2: **241 passed, 1 warning** (240 baseline + 1 novo teste; warning pré-existente do FastAPI em `test_auth_flows`), sem regressão

## Task Commits

Each task was committed atomically:

1. **Task 1: Fixture de dados reais e teste do critério 4 (LINX-03)** - `1e23b4a` (test)

## Files Created/Modified
- `app/tests/test_grade_linx.py` - fixture `_CD_PROD_COR_REAL`/`_REFERENCIA_REAL` (dados reais do checkpoint 02-04) + 5º teste (`_com_dados_reais_da_referencia`); nota de bloqueio removida

## Decisions Made
- Nenhuma decisão nova. A escolha da amostra e o formato da fixture já estavam definidos pelo checkpoint 02-04 (única amostra com ≥3 tamanhos capturada) e pelo `<action>` do próprio plano.

## Deviations from Plan

None - plan executado exatamente como escrito. `02-04-SUMMARY.md` tinha evidência real (não houve bloqueio `sem-acesso`), então a fixture real foi usada conforme previsto, sem valores inventados.

**Nota fora do escopo desta plano (não corrigida, apenas registrada):** ao iniciar a execução, o working tree já continha mudanças não commitadas em `app/modules/ingestao/*` (refatoração de `agregar_estoque` para diagnóstico com `dt_estoque`, incluindo uma migration nova `alembic/versions/016_estoque_dt_estoque.py`), sem relação com este plano. Não tocado, registrado em `deferred-items.md` na pasta da fase (Scope Boundary do executor).

## Issues Encountered
Nenhum.

## User Setup Required
None - função pura, sem dependência de `.env` ou infraestrutura nova.

## Next Phase Readiness
- Fase 2 completa: os 5 critérios de sucesso do ROADMAP e os requirements ING-01/LINX-03 estão cobertos.
- `converter_grade_para_posicoes` (via `app.modules.pedidos.service`) e `produto_tamanho_posicao` (mantida pela 5ª fonte) estão prontos para a Fase 3 (escrita da OR no formato Linx) preencher `e1..e48` de `ordens_reserva_linx`.
- Nenhum bloqueio conhecido para a Fase 3. Ver nota de escopo acima sobre trabalho não relacionado pendente no working tree — recomenda-se revisar/commitar separadamente antes de iniciar a Fase 3, para não misturar diffs.

## Checklist final — 5 critérios de sucesso da Fase 2 (ROADMAP)

- [x] 1. A 5ª fonte grava em `produto_tamanho_posicao` os registros da view Databricks — provado fora de mock no checkpoint 02-04 (569.726 linhas, tabela partindo de 0)
- [x] 2. `sincronizar_tudo` chama a 5ª fonte no mesmo commit único das outras 4 — implementado no Plano 02-03, testado em `test_ingestao_sync.py`
- [x] 3. Duas sincronizações sucessivas substituem completamente as linhas antigas (sem duplicar) — provado fora de mock no checkpoint 02-04 (569.726 estável após a 2ª sincronização)
- [x] 4. Função pura de conversão testada sem banco usando dados reais já ingeridos nesta fase — fechado por este plano (02-05)
- [x] 5. `sg_tamanho` sem posição correspondente: aviso no log, restante da grade convertido, sem exceção — testado no Plano 02-02 (`test_converter_grade_para_posicoes_tamanho_sem_posicao_nao_lanca_excecao`)

## Self-Check: PASSED

- `app/tests/test_grade_linx.py` encontrado no filesystem, com o 5º teste presente ✅
- Commit `1e23b4a` encontrado em `git log` ✅
- `grep -c "NOTA: o teste com dados REAIS"` → `0` (nota removida) ✅
- `grep -c "_REFERENCIA_REAL"` → `3` (definição + uso no teste) ✅
- Suíte completa: `241 passed, 1 warning` ✅

---
*Phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade*
*Completed: 2026-08-05*
