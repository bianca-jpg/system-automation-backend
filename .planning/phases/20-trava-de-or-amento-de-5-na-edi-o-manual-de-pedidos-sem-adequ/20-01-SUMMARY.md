---
phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ
plan: 01
subsystem: database
tags: [sqlalchemy, postgres, jsonb-aggregation, domain-driven-design, orcamento]

# Dependency graph
requires:
  - phase: 14-motor-de-alocacao-puro
    provides: "OrcamentoPedido (ledger ±5%, floor inteiro, tudo-ou-nada/saturante) reaproveitado por este plano (D-04)"
  - phase: 15-roteamento-global-sem-adequacao
    provides: "_PEDIDO_BUDGET_SQL / load_pedido_budget original (invariante J5, missing_from_pedidos), movida e estendida por este plano"
provides:
  - "orcamento_edicao.py: domínio puro de contribuição por par + validação rewind+cobrança por estado final (PD-02)"
  - "repositorio_orcamento_pedido.py: fonte única da SQL de orçamento por pedido, com ramo novo 'sem adequação' medido pelo total do par"
  - "adapters.py::load_pedido_budget delega para o repositório novo, assinatura pública inalterada"
affects: [20-02, 20-03, 20-04, 20-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Contribuição por par medida pelo TOTAL (não por tamanho) para ORs 'sem adequação' — redistribuição pura custa zero orçamento"
    - "Validação de orçamento por rewind+recobrança: subtrai a contribuição atual do próprio par do consumido ao vivo antes de recobrar o estado final (idempotente, sem cobrar por delta)"
    - "Fonte SQL única compartilhada entre motor automático e leitura sob demanda para edição manual, evitando duas queries de orçamento divergentes"

key-files:
  created:
    - app/modules/pedidos/domain/orcamento_edicao.py
    - app/tests/test_pedidos_orcamento_edicao.py
    - app/modules/pedidos/infrastructure/repositorio_orcamento_pedido.py
  modified:
    - app/modules/pedidos/processing/infrastructure/adapters.py
    - app/tests/test_pedidos_processing_repository.py

key-decisions:
  - "PD-01: a correção do Pitfall 1 não remove o filtro de tipo — adiciona um ramo próprio para 'sem' medido pelo total do par, preservando o ramo 'com' por item byte-idêntico (D-01)"
  - "PD-02: cobrança por ESTADO FINAL do par via rewind (subtrai contribuição atual antes de recobrar), não por delta da edição — evita cobrar adição ao desfazer corte e garante idempotência em re-salvamentos"
  - "carregar_pedido_budget_tuplas implementada em termos de carregar_orcamento_pedidos — uma travessia só da SQL para os dois formatos de saída (snapshot novo e tuplas legadas)"

patterns-established:
  - "Domínio puro sem I/O para regras de orçamento/validação (orcamento_edicao.py), testável sem banco — mesmo padrão do motor (motor_adequacao.py, orcamento_pedido.py)"

requirements-completed: [GRADE-03]

# Metrics
duration: ~15min
completed: 2026-09-08
status: complete
---

# Phase 20 Plan 01: Domínio de orçamento de edição + repositório único Summary

**Ramo novo por total de par fecha o Pitfall 1 (ORs "sem adequação" agora aparecem no orçamento do pedido); domínio puro `orcamento_edicao.py` entrega a cobrança por estado final via rewind, reaproveitando `OrcamentoPedido` sem recalcular o limite.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 3/3
- **Files modified:** 5 (3 criados, 2 modificados)

## Accomplishments

- `orcamento_edicao.py`: `OrcamentoPedidoSnapshot`, `ContribuicaoOrcamento`, `calcular_contribuicao`, `validar_edicao_orcamento`, `OrcamentoPedidoExcedidoError` — domínio puro, 13 testes síncronos provando rewind (com e sem terceiro consumidor), idempotência, independência adição/corte (D-08) e mensagem de erro em português
- `repositorio_orcamento_pedido.py`: fonte única de `PEDIDO_BUDGET_SQL`, com o ramo `'com'` por item preservado byte-idêntico e o ramo novo `'sem'` agregado pelo total do par antes do `GREATEST(...)` — redistribuição pura contribui zero
- `adapters.py::load_pedido_budget` delega para `carregar_pedido_budget_tuplas`, sem tocar `processing/application/service.py` (motor intocado)
- 5 testes novos em `test_pedidos_processing_repository.py` provando adição/corte em `'sem'`, redistribuição pura custando zero (guardião de PD-01), regressão de `'com'` medida por item (guardião de D-01), e pedido misto somando sem duplicar linha

## Task Commits

1. **Task 1: Domínio puro do orçamento de edição manual** - `d080c49` (feat)
2. **Task 2: Repositório único de leitura de orçamento por pedido** - `5e8b687` (feat)
3. **Task 3: adapters.py delega para o repositório novo + cobertura dos dois tipos** - `6f584c0` (feat)

## Files Created/Modified

- `app/modules/pedidos/domain/orcamento_edicao.py` - domínio puro: contribuição por par pelo total, validação rewind+cobrança, exceção de negócio
- `app/tests/test_pedidos_orcamento_edicao.py` - 13 testes síncronos, sem banco
- `app/modules/pedidos/infrastructure/repositorio_orcamento_pedido.py` - `PEDIDO_BUDGET_SQL` (fonte única), `carregar_orcamento_pedidos`, `carregar_pedido_budget_tuplas`
- `app/modules/pedidos/processing/infrastructure/adapters.py` - `load_pedido_budget` delega para o repositório; `_PEDIDO_BUDGET_SQL` removida
- `app/tests/test_pedidos_processing_repository.py` - 5 testes novos de `pedido_budget` (adição/corte em 'sem', redistribuição pura, regressão 'com', pedido misto)

## Decisions Made

- PD-01 e PD-02 (ver `<decisoes_deste_plano>` do PLAN.md) seguidas à risca, sem reabertura — a pesquisa sugeria remover o filtro de tipo, o que teria cobrado orçamento em redistribuição pura de "sem adequação" (regressão) e teria contradito o contrato de UI já aprovado (trava por delta do total da linha).
- Nenhuma decisão nova fora do que o plano já definiu.

## Deviations from Plan

**1. [Rule 3 - Blocking] Ambiente virtual (`.venv`) sem `sentry-sdk` e outras dependências declaradas**

- **Found during:** Verificação do Task 1 (`pytest` falhava no `conftest.py` com `ModuleNotFoundError: No module named 'sentry_sdk'`)
- **Issue:** O `.venv` local estava desincronizado do `pyproject.toml`/`uv.lock` (dependência já declarada, não instalada) — bloqueava qualquer execução de teste, não só desta task
- **Fix:** `uv sync --python .venv/Scripts/python.exe` — sincronizou 13 pacotes já declarados (sentry-sdk, pre-commit, detect-secrets, etc.), nenhuma dependência NOVA foi introduzida ao projeto
- **Files modified:** nenhum arquivo de projeto (só o `.venv`, não versionado)
- **Verification:** `pytest app/tests/test_pedidos_orcamento_edicao.py -x -q` passou a rodar normalmente após o sync
- **Committed in:** N/A (mudança de ambiente local, não versionada)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Nenhum impacto de escopo — correção de ambiente local necessária para poder verificar qualquer task deste plano.

## Issues Encountered

None.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- `OrcamentoPedidoSnapshot`, `ContribuicaoOrcamento`, `calcular_contribuicao`, `validar_edicao_orcamento`, `OrcamentoPedidoExcedidoError` prontos para o caminho de escrita (plano 20-03) e a API de leitura (plano 20-04).
- `carregar_orcamento_pedidos` (via port, plano 20-03) e leitura direta (plano 20-04) já expostas pelo repositório.
- Suíte completa do backend: 920 passed, 18 skipped, 0 failed (`test_pedidos_processing_sem_adequar_memory_024.py` ignorado — falha de import `resource`, módulo Unix-only, ambiente Windows, pré-existente e não relacionada a este plano).
- `ruff check` limpo nos três arquivos de produção tocados.
- Nenhuma migration criada — fase não altera schema (D-05).
- Bloqueio conhecido do plano 20-01 sobre 20-03 (T-20-06 do threat_model): o rastro de `qt_solicitada` precisa sobreviver às edições, o que é resolvido no plano 20-02 (Pitfall 2) — este plano assume o rastro correto.

---

_Phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ_
_Completed: 2026-09-08_

## Self-Check: PASSED

- FOUND: app/modules/pedidos/domain/orcamento_edicao.py
- FOUND: app/tests/test_pedidos_orcamento_edicao.py
- FOUND: app/modules/pedidos/infrastructure/repositorio_orcamento_pedido.py
- FOUND commit: d080c49
- FOUND commit: 5e8b687
- FOUND commit: 6f584c0
