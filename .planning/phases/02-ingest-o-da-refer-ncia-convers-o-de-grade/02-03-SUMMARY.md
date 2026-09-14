---
phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
plan: 03
subsystem: ingestao
tags: [sqlalchemy, pytest, full-refresh, pydantic, databricks]

# Dependency graph
requires:
  - phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
    provides: "_parse_posicao, agregar_referencia_tamanhos, ler_referencia_tamanhos, substituir_referencia_tamanhos (Plano 02-01) prontas para orquestrar"
provides:
  - "sincronizar_referencia_tamanhos — 5ª fonte ligada ao sync, com guard D-02 (agregação vazia mantém snapshot anterior)"
  - "sincronizar_tudo estendido para 5 etapas, todas dentro do commit único"
  - "SincronizacaoResponse.referencia_tamanho_posicao (campo novo) + .faturamento_colecoes (fix de bug pré-existente confirmado em 02-RESEARCH.md)"
  - "6 testes de integração novos + orquestração de sincronizar_tudo estendida para 5 mocks"
affects: [02-04-checkpoint-verificacao-real, fase-3-geracao-de-or-linx]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Guard D-02 vive inteiramente no caso de uso (sincronizar_referencia_tamanhos), nunca no repositório — substituir_referencia_tamanhos permanece incondicional"
    - "Contador de fallback do guard usa SELECT COUNT(*) na própria tabela (não 0), consistente com o significado 'quantos itens existem agora' dos outros 4 contadores"
    - "Mock de settings.databricks_tabela_tamanho_ref via patch de get_settings quando a env local não tem a var configurada (mesmo padrão dos testes irmãos de sql_exato)"

key-files:
  created: []
  modified:
    - app/modules/ingestao/application/casos_uso.py
    - app/modules/ingestao/schemas.py
    - app/modules/ingestao/service.py
    - app/tests/test_ingestao_sync.py

key-decisions:
  - "Mensagem exata do warning D-02 confirmada: 'Referência tamanho->posição: 0 linhas válidas (%d linhas brutas) - mantendo snapshot anterior de produto_tamanho_posicao.'"
  - "Bug de faturamento_colecoes (Pitfall 1 do RESEARCH.md, extra='ignore' silencioso do Pydantic v2) corrigido de carona, junto do campo novo referencia_tamanho_posicao, no mesmo arquivo já em edição — decisão do orquestrador registrada em 02-CONTEXT.md"
  - "3 dos 5 testes de integração da 5ª fonte precisaram mockar settings.databricks_tabela_tamanho_ref (env local do container não tem DATABRICKS_TABELA_TAMANHO_REF configurada, só documentada em .env.example desde o Plano 02-01) — mesmo padrão já usado no teste sql_exato do próprio plano"

patterns-established:
  - "Falha dentro da 5ª etapa (referência) não deixa as 4 fontes anteriores parcialmente sincronizadas — provado com sessão real nunca commitada, mesmo mecanismo do teste irmão da 2ª etapa"

requirements-completed: [ING-01]

# Metrics
duration: ~25min
completed: 2026-08-05
---

# Phase 2 Plan 03: Ligação da 5ª fonte ao sync e ao contrato HTTP (ING-01) Summary

**`sincronizar_referencia_tamanhos` (guard D-02) integrada como 5ª chamada de `sincronizar_tudo`, dentro do mesmo commit único das outras 4 fontes, com `SincronizacaoResponse` corrigindo o bug pré-existente de `faturamento_colecoes` e expondo o novo `referencia_tamanho_posicao` — 236 testes passando (baseline 230 + 6 novos), sem regressão.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-08-05 (após 02-02)
- **Completed:** 2026-08-05
- **Tasks:** 2/2
- **Files modified:** 4

## Accomplishments
- `sincronizar_referencia_tamanhos(db)` lê a view, agrega (D-01/D-03/D-04) e, quando a agregação vem vazia (0 linhas brutas OU todas descartadas pelo parse), loga warning e devolve a contagem atual de `produto_tamanho_posicao` **sem chamar** `substituir_referencia_tamanhos` — snapshot anterior nunca é zerado por erro na origem (D-02, critério 1 do ROADMAP)
- `sincronizar_tudo` estendido para 5 etapas: a 5ª chamada acontece imediatamente antes de `await db.commit()`, no mesmo commit único das outras 4 (critério 2)
- Duas sincronizações sucessivas substituem completamente `produto_tamanho_posicao` sem duplicar linhas, provado por teste de integração com sessão real nunca commitada (critério 3)
- `SincronizacaoResponse` ganha `faturamento_colecoes: int = 0` (corrige o bug confirmado do Pitfall 1 do `02-RESEARCH.md` — Pydantic v2 descartava o campo silenciosamente da resposta HTTP) e `referencia_tamanho_posicao: int = 0` (campo novo da 5ª fonte)
- 6 testes de integração novos: erro de tabela não configurada, SQL exato, persiste e descarta inválidas, guard D-02 (view vazia mantém snapshot), duas chamadas sucessivas sem duplicar, e falha na 5ª etapa não persiste nada das 4 anteriores
- `test_sincronizar_tudo_orquestra_as_quatro_etapas_e_soma_resultado` renomeado para `..._as_cinco_etapas...` com o 5º mock e o campo `referencia_tamanho_posicao` no dict esperado

## Task Commits

Each task was committed atomically:

1. **Task 1: sincronizar_referencia_tamanhos (D-02) + sincronizar_tudo estendido + contrato HTTP** - `4b85279` (feat)
2. **Task 2: Testes de integração da 5ª fonte (ING-01 critérios 1-3, D-01, D-02)** - `b448bb4` (test)

## Files Created/Modified
- `app/modules/ingestao/application/casos_uso.py` - `sincronizar_referencia_tamanhos` (guard D-02) + `sincronizar_tudo` com a 5ª chamada antes do commit
- `app/modules/ingestao/schemas.py` - `SincronizacaoResponse.faturamento_colecoes` (fix) e `.referencia_tamanho_posicao` (novo)
- `app/modules/ingestao/service.py` - re-export de `sincronizar_referencia_tamanhos`
- `app/tests/test_ingestao_sync.py` - 5 testes novos de `sincronizar_referencia_tamanhos` + orquestração renomeada/estendida + 1 teste novo de falha na 5ª etapa

## Decisions Made
- Nenhuma decisão nova além das já travadas em `02-CONTEXT.md` (D-02) e `02-PLAN.md` (`<decisoes_travadas>` 1-4). Implementação seguiu exatamente o corpo pronto do `02-RESEARCH.md` Pattern 1 e `02-PATTERNS.md`.
- Mensagem do warning D-02 usada literalmente como especificado no plano.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Testes de integração da 5ª fonte precisaram mockar `settings.databricks_tabela_tamanho_ref`**
- **Found during:** Task 2 (verificação dos testes `persiste_e_descarta_invalidas`, `view_vazia_mantem_snapshot_anterior`, `duas_chamadas_sucessivas_substitui_sem_duplicar`)
- **Issue:** O `.env` real do container Docker deste ambiente não tem `DATABRICKS_TABELA_TAMANHO_REF` configurada (só existe documentada em `.env.example` desde o Plano 02-01 — o valor real cabe à usuária preencher quando for testar contra o Databricks real). Os 3 testes que chamam `service.sincronizar_referencia_tamanhos` sem mockar `get_settings` primeiro falhavam com `DatabricksError` antes mesmo de `executar_consulta` (mockado) ser chamado, pois o guard de tabela vazia em `ler_referencia_tamanhos` dispara primeiro.
- **Fix:** Adicionado um helper `_fake_settings_tamanho_ref()` que mocka `get_settings()` com um nome de tabela fake (mesmo padrão já usado no teste `sql_exato` do próprio plano) e aplicado nos 3 testes afetados.
- **Files modified:** `app/tests/test_ingestao_sync.py`
- **Verification:** `docker compose exec api uv run pytest app/tests/test_ingestao_sync.py -q` → 20 passed; suíte completa → 236 passed, sem regressão.
- **Committed in:** `b448bb4` (Task 2)

---

**Total deviations:** 1 auto-fixed (1 blocking - Rule 3)
**Impact on plan:** Ajuste necessário apenas para viabilizar os testes contra o ambiente Docker local, sem tocar a lógica de produção. Nenhum scope creep.

## Issues Encountered
Nenhum além do documentado em "Deviations from Plan".

## User Setup Required
None - nenhuma configuração de serviço externo necessária para este plano. `DATABRICKS_TABELA_TAMANHO_REF` já documentada em `.env.example` desde o Plano 02-01; a usuária preenche o valor real no `.env` local quando for testar a ingestão de fato contra o Databricks (fora do escopo deste plano, que prova a lógica via mocks). Verificação funcional completa contra Postgres/Databricks reais fica para o Plano 02-04 (checkpoint).

## Next Phase Readiness
- A 5ª fonte agora roda dentro do sync de 2h (Celery beat) e do endpoint `POST /api/v1/ingestao/sincronizar` — `produto_tamanho_posicao` deixa de ficar vazia para sempre.
- `SincronizacaoResponse` expõe corretamente os 5 contadores, incluindo o fix do bug pré-existente de `faturamento_colecoes`.
- Nenhum bloqueio conhecido para o Plano 02-04 (checkpoint de verificação funcional contra ambiente real). Suíte completa verde (236 passed, 1 warning, ~4min02s no container).

## Self-Check: PASSED

Os 4 arquivos modificados encontrados no filesystem; ambos os commits de task (`4b85279`, `b448bb4`) encontrados em `git log`.

---
*Phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade*
*Completed: 2026-08-05*
