---
phase: 15-roteamento-global-do-modo-sem-adequa-o
plan: 03
subsystem: database
tags: [sqlalchemy, postgres, pytest-asyncio, asyncio]

# Dependency graph
requires:
  - phase: 15-01
    provides: "load_pedido_budget(nr_pedidos) -> (total_original, consumido_adicao, consumido_corte), 3 dicts por pedido"
  - phase: 15-02
    provides: "_assert_stock_capacity revalida capacidade de estoque também para tipo='sem' (FIX-01), pré-requisito para sem_adequar reservar estoque de verdade pelo caminho global"
provides:
  - "build_processing_plan com caminho único: os dois modos (ADEQUAR/SEM_ADEQUAR) chamam processar_pedidos, com deferred_count/blocked_credit_count derivados do resultado real do motor"
  - "_plan_once sem ramo de streaming: hidratação global, stock/criterion/tolerance/load_pedido_budget carregados incondicionalmente nos dois modos, persistência sempre por store_plan"
  - "ALOC-09 fechado: os 3 mappings de orçamento chegam ao motor pelo caminho de produção real, provado por comparação discriminante contra o placeholder de execução única (mesma foto pendente, dois resultados diferentes)"
  - "Zero símbolo de streaming no repositório: StreamingPlanBuilder, PlanSummary, PLANNING_PAIR_PAGE_SIZE, build_pair_payload, prepare_pending_pair_stream, load_pending_pair_page, append_plan_rows, finalize_streamed_plan removidos de produção e testes"
affects: [15-04-medicao-de-memoria, 15-05-docs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Teste discriminante: duas chamadas de build_processing_plan sobre a MESMA foto pendente (com orçamento real vs. sem mappings/placeholder) provam que o wiring está ligado — a recusa/aceitação isolada de uma única chamada não prova nada, só a comparação entre as duas"
    - "Remover símbolos de produção primeiro, rodar a suíte completa, e só então limpar os testes — o que quebra por ImportError/AttributeError é o raio de impacto real, mais confiável que grep prévio"

key-files:
  created: []
  modified:
    - app/modules/pedidos/processing/domain.py
    - app/modules/pedidos/processing/application/ports.py
    - app/modules/pedidos/processing/infrastructure/adapters.py
    - app/modules/pedidos/processing/infrastructure/repository.py
    - app/tests/test_pedidos_processing_domain.py
    - app/tests/test_pedidos_processing_service.py
    - app/tests/test_pedidos_processing_repository.py
    - app/tests/test_pedidos_processing_cancellation_024.py
  deleted:
    - app/tests/test_pedidos_processing_stream_repository.py

key-decisions:
  - "PlanSummary saiu junto com StreamingPlanBuilder (não estava na lista literal do CONTEXT.md, mas é consequência direta): existia só para servir StreamingPlanBuilder.finish()/finalize_streamed_plan; confirmado por grep que não sobrava consumidor fora da própria definição, __all__, e os imports em ports.py/repository.py"
  - "O teste de paginação keyset foi reescrito para provar a propriedade OPOSTA (hidratação global), não apagado — a garantia de negócio que ele cobria (sem_adequar decide com visão do canal inteiro) continua existindo, só que invertida"
  - "Rule 1 (bug auto-fix): test_callable_cancel_token_is_checked_between_payload_rows quebrou como efeito colateral não previsto da Task 1 (exigência de stock nos dois modos) — corrigido com fixture, sem alterar a asserção de cancelamento"

patterns-established: []

requirements-completed: [ARCH-01]

# Metrics
duration: retomada apos interrupcao (Tasks 1-2 de execucao anterior; Tasks 3-4 nesta sessao)
completed: 2026-08-20
---

# Phase 15 Plan 03: Roteamento global + fechamento ALOC-09 + remoção do streaming Summary

**`SEM_ADEQUAR` passa a ser planejado por `build_processing_plan`→`processar_pedidos` com orçamento real ligado nos dois modos (ALOC-09 fechado, provado por comparação discriminante contra o placeholder), e todo o código de streaming (`StreamingPlanBuilder`, paginação keyset, `PlanSummary`) foi removido do repositório.**

## Nota de continuação

Esta execução **retomou um plano interrompido por limite de gasto da conta** (não um erro técnico) em uma sessão anterior. As Tasks 1 e 2 já estavam completas e commitadas (`2c79b74`, `f7fe670`) quando esta sessão começou. Verificado no início:

- **Task 1** (roteamento aditivo em `build_processing_plan`): confirmada correta e completa — os 3 parâmetros de orçamento existiam na assinatura e eram repassados ao motor.
- **Task 2** (`_plan_once` com caminho único): o código de produção estava correto (sem ramo de streaming, `stock`/`criterion`/`tolerance`/`load_pedido_budget` incondicionais), mas o arquivo de teste correspondente (`test_pedidos_processing_service.py`) ainda continha só o teste antigo de paginação (falhando por `ImportError` depois da Task 1), sem o teste novo de ALOC-09 que a Task 2 deveria ter escrito.

Esta sessão completou a lacuna de teste da Task 2 e executou as Tasks 3 e 4 por completo.

## Performance

- **Tasks:** 4/4 (Tasks 1-2 já commitadas ao início desta sessão; Tasks 3-4 executadas agora, mais a lacuna de teste da Task 2 completada)
- **Files modified:** 8 (4 produção, 4 testes modificados, 1 teste deletado)

## Accomplishments

- **Caminho único de planejamento**: `build_processing_plan` chama `processar_pedidos` para os dois modos (`ADEQUAR`/`SEM_ADEQUAR`), com `deferred_count`/`blocked_credit_count` derivados de `result["preteridos"]`/`result["bloqueados_credito"]` reais — nunca mais constantes zeradas para `sem_adequar`.
- **`_plan_once` sem ramo de streaming**: hidratação global (`load_pending_items`) e `stock`/`criterion`/`tolerance`/`load_pedido_budget` carregados incondicionalmente nos dois modos; persistência sempre por `store_plan`.
- **ALOC-09 fechado e provado discriminante**: `test_orcamento_persiste_entre_execucoes_sucessivas` monta um pedido único (700) com dois produtos (40 peças, orçamento de corte `floor(40*0.05)=2`). Rodada 1 grava uma OR real via `SqlAlchemyProcessingWriter`, consumindo o orçamento de corte inteiro. O estado intermediário lido de volta do banco (`total_original=40`, `consumido_corte=2`, `consumido_adicao=0`) prova que o formato persistido em `ordens_reserva.itens` (`qt_solicitada`/`qt_liquida`) é exatamente o que `load_pedido_budget` agrega — o elo que o plano 15-01 não conseguiu provar sozinho. Rodada 2 chama `build_processing_plan` duas vezes sobre a MESMA foto pendente: com o orçamento real relido do banco, o corte de 1 peça é **recusado** (orçamento restante = 0); sem os mappings (placeholder de execução única), o mesmo corte é **aceito**, porque o denominador encolhido (20 peças da foto restante) concede um orçamento novo de `floor(20*0.05)=1`. `caplog` confirma o aviso `ALOC-09` ausente na chamada com orçamento real e presente na chamada com placeholder — nos dois sentidos (J6).
- Um teste equivalente e mais leve (sem banco) foi adicionado em `test_pedidos_processing_service.py` (`test_plan_once_feeds_real_budget_and_silences_the_aloc09_warning`), provando a mesma propriedade no nível de `_plan_once` com um planner fake.
- **Streaming completamente removido**: `StreamingPlanBuilder`, `PlanSummary`, `PLANNING_PAIR_PAGE_SIZE`, `build_pair_payload` (produção, `domain.py`); `prepare_pending_pair_stream`/`load_pending_pair_page` (`ports.py`/`adapters.py`, TEMP TABLE `orders_processing_pending_pairs` incluída); `append_plan_rows`/`finalize_streamed_plan` (`ports.py`/`repository.py`). `_payload_for_pair` (privada) e `store_plan` continuam sendo os caminhos vivos. `_assert_stock_capacity` (escopo do 15-02) permanece intocada — confirmado no `git diff` de cada commit.
- Arquivo `test_pedidos_processing_stream_repository.py` deletado inteiro (5 testes, exclusivos do caminho removido).
- Portão J7 (`grep -rn --include='*.py'` dos 6 símbolos de streaming em `app/modules app/tests`) retorna vazio, confirmado após cada commit de produção e ao final.

## Task Commits

1. **Completar lacuna de teste da Task 2** (ALOC-09 em `_plan_once`) - `af7aba3` (test)
2. **Task 3: apagar produção do streaming** - `49360ef` (refactor)
3. **Task 3: remover teste do `StreamingPlanBuilder`** - `e469126` (test)
4. **Rule 1 (bug): corrigir fixture de estoque quebrada pela Task 1** - `d9c39a3` (fix)
5. **Task 3 (remoção do teste de paginação keyset) + Task 4 (teste discriminante ALOC-09)** - `faa21f9` (test)

**Nota sobre granularidade dos commits:** o `git rm` do arquivo `test_pedidos_processing_stream_repository.py` foi executado antes do primeiro `git add` explícito desta sessão e acabou ficando staged junto do primeiro commit (completar a lacuna da Task 2) em vez de junto do commit de remoção de produção (Task 3) — o conteúdo dos commits está correto e nenhum deles mistura arquivos de escopos completamente alheios, mas a fronteira exata entre "Task 2" e "Task 3" nos commits não é perfeitamente 1:1 com o plano. Documentado aqui por transparência; não afeta a correção do resultado.

## Files Created/Modified

- `app/modules/pedidos/processing/domain.py` - `StreamingPlanBuilder`, `PlanSummary`, `PLANNING_PAIR_PAGE_SIZE`, `build_pair_payload` removidos; comentários de memória atualizados (os dois modos hidratam a foto global; o teto real é o preflight, não mais streaming)
- `app/modules/pedidos/processing/application/ports.py` - `prepare_pending_pair_stream`/`load_pending_pair_page`/`append_plan_rows`/`finalize_streamed_plan` removidos dos dois Protocols; import de `PlanSummary` removido
- `app/modules/pedidos/processing/infrastructure/adapters.py` - os dois métodos de paginação keyset removidos (a TEMP TABLE e o índice único saem junto)
- `app/modules/pedidos/processing/infrastructure/repository.py` - `append_plan_rows`/`finalize_streamed_plan` removidos; import `TYPE_CHECKING` de `PlanSummary` e import não utilizado de `func` removidos; `_assert_stock_capacity` intocada
- `app/tests/test_pedidos_processing_domain.py` - import e teste do `StreamingPlanBuilder` removidos
- `app/tests/test_pedidos_processing_service.py` - teste de paginação reescrito como prova de hidratação global; teste novo de ALOC-09 com `caplog`; `_pending_spec` ganha parâmetro nomeado opcional `mode`
- `app/tests/test_pedidos_processing_repository.py` - teste de paginação keyset removido; teste novo `test_orcamento_persiste_entre_execucoes_sucessivas` (discriminante, banco real); helper novo `_create_job_and_store_draft` (irmão de `_create_job_and_plan`, não alterado)
- `app/tests/test_pedidos_processing_cancellation_024.py` - fixture de estoque adicionada (Rule 1)
- `app/tests/test_pedidos_processing_stream_repository.py` - **deletado** (313 linhas, 5 testes)

## Decisions Made

- **`PlanSummary` sai junto com o streaming** (não estava na lista literal do CONTEXT.md, é consequência direta): existia só para servir `StreamingPlanBuilder.finish()`/`finalize_streamed_plan`. Confirmado por `grep -rn --include='*.py' "PlanSummary" app/` que, após remover os dois consumidores, não sobrava nada além da própria definição/`__all__`/imports — removida.
- **Reescrever, não apagar**, o teste de paginação keyset: ele provava uma propriedade real (visão global do canal), só que invertida depois desta entrega. `test_sem_adequar_planning_uses_keyset_pages_without_global_hydration` virou `test_sem_adequar_planning_hydrates_globally_and_reports_real_counts`.
- **Prova discriminante do ALOC-09 em dois níveis**: um teste rápido sem banco (`test_plan_once_feeds_real_budget_and_silences_the_aloc09_warning`, planner fake) e um teste de integração completo contra Postgres real (`test_orcamento_persiste_entre_execucoes_sucessivas`, grava uma OR de verdade e relê o orçamento do banco). Os dois seguem a mesma forma: duas chamadas sobre os MESMOS itens, uma com orçamento real e outra sem, comparando os resultados — nunca uma chamada isolada.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixture de estoque quebrada em `test_pedidos_processing_cancellation_024.py`**
- **Found during:** Task 3, passo 3 (rodar a suíte completa antes de tocar os testes)
- **Issue:** `test_callable_cancel_token_is_checked_between_payload_rows` chamava `build_processing_plan(mode=SEM_ADEQUAR, ...)` sem `stock`. Este arquivo não fazia parte da lista de arquivos declarada pela Task 1, então a mudança de Task 1 (a guarda `adequation_requires_stock_snapshot` passou a valer para os dois modos) quebrou este teste sem que a Task 1 o tivesse tocado.
- **Fix:** adicionada fixture `stock={"Franquia": {"PROD-0001_36": 2, "PROD-0002_36": 2}}`, sem alterar a asserção de cancelamento cooperativo (o teste continua provando o mesmo comportamento: o token é checado entre linhas de payload).
- **Files modified:** `app/tests/test_pedidos_processing_cancellation_024.py`
- **Committed in:** `d9c39a3`

---

**Total deviations:** 1 auto-fixed (1 bug, Rule 1)
**Impact on plan:** Necessário para reaproximar a suíte completa de zero regressões antes de prosseguir para a Task 4. Sem escopo adicional além do estritamente necessário.

## Issues Encountered

**Contagem de testes da suíte completa diverge do número literal do `15-03-PLAN.md` ("821 passed"), mas o delta interno do plano bate exatamente.** O plano cita "825 passed, 16 skipped, 1 failed" como baseline de entrada em 15-03. Investigado: esse número é, na verdade, o de fechamento da **Phase 14** (nota de STATE.md de 2026-08-17), já defasado quando 15-03 começou — a baseline real de entrada (pós-15-01 e 15-02) era **829 passed** (ver nota de STATE.md de 15-02). Dentro do próprio 15-03 o delta bate exatamente com o previsto: **-7 removidos** (5 do arquivo deletado + 1 do `StreamingPlanBuilder` + 1 da paginação keyset) **+3 adicionados** (`test_sem_adequar_now_defers_by_stock_and_blocks_by_credit` na Task 1, `test_plan_once_feeds_real_budget_and_silences_the_aloc09_warning` completando a Task 2 nesta sessão, `test_orcamento_persiste_entre_execucoes_sucessivas` na Task 4) = net **-4**. `829 - 4 = 825`, exatamente o resultado medido — confirmado via `git diff --stat` entre o commit anterior a 15-03 (`f6190f7`) e cada commit desta sessão, contando `def test_`/`async def test_` em cada arquivo tocado.

**A falha conhecida (`test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql`) é reproduzível deterministicamente toda vez que a suíte completa roda, por um motivo diferente do documentado no plano.** O plano descreve a falha como `RuntimeError: Event loop is closed` (teardown asyncpg). Nesta sessão, rodando a suíte completa, a mesma falha se manifestou como `AssertionError` (alertas residuais de usuários não confirmados, criados por `test_auth_flows.py`, que roda alfabeticamente antes de `test_pedidos_read_projection.py` na mesma sessão pytest e não faz limpeza própria dos usuários de teste que cria). Confirmado por isolamento: rodando `test_pedidos_read_projection.py` sozinho, a suíte passa 14/14 — a falha só aparece quando a suíte completa roda, por causa da ordem de execução alfabética (`test_auth_flows.py` < `test_pedidos_read_projection.py`) e ausência de limpeza entre arquivos. É o MESMO teste falhando nos dois relatos (do plano e desta sessão) — pré-existente, fora do escopo de 15-03, nenhuma linha de `test_auth_flows.py`/`conftest.py` foi tocada. Foi feita uma limpeza pontual de dados de teste acumulados de sessões anteriores (~2.742 usuários `@teste.example.com` + 248 `parametro_change_requests` órfãos) diretamente no banco `system_automation_test` via SQL, para reduzir ruído residual — isso é higiene de dados de um ambiente de teste compartilhado, não uma correção de código, e não elimina a causa raiz (a suíte volta a acumular ~7-20 usuários de teste a cada execução completa, porque `test_auth_flows.py` continua sem limpeza própria).

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- **15-04** (medição de memória do pico de `sem_adequar`) pode começar: o roteamento pelo caminho global já está em produção, então o volume elegível de `sem_adequar` (que pode ser estruturalmente maior que o de `ADEQUAR`, porque até esta entrega aceitava todo par) já é o real a ser medido.
- **15-05** (docs) pode começar: os comentários de `domain.py` já foram atualizados para não afirmar mais que `sem_adequar` usa páginas keyset; `docs/adequacao.md` não foi tocado (escopo do 15-05).
- **Dependência de integração com 15-02 (FIX-01) confirmada satisfeita**: `_assert_stock_capacity` já revalida capacidade de estoque para `tipo in {"com", "sem"}` (sem o desvio `if tipo != "com": continue`), verificado no início desta sessão antes de prosseguir.
- **Verificação manual pendente (não automatizável), herdada do plano**: comparar, contra dado real, o volume de ORs de `sem_adequar` antes/depois desta entrega, categorizando cada par que sumiu em `sem_credito`/`sem_estoque_grade`/`outro` — o bucket `outro` tem de estar vazio. Não fazível nesta sessão (requer acesso a dado de produção fora do escopo de teste automatizado).
- **Bloqueio nenhum para 15-04.** A suíte completa fecha com exatamente 1 falha (a mesma conhecida, pré-existente, agora documentada com sua causa raiz real).

## Self-Check: PASSED

- `.planning/phases/15-roteamento-global-do-modo-sem-adequa-o/15-03-SUMMARY.md` - FOUND
- Commits `af7aba3`, `49360ef`, `e469126`, `d9c39a3`, `faa21f9` - FOUND in `git log --oneline --all`
- `app/tests/test_pedidos_processing_stream_repository.py` - CONFIRMED DELETED
- J7 gate (`grep -rn --include='*.py'` dos 6 símbolos de streaming) - CONFIRMED EMPTY

---
*Phase: 15-roteamento-global-do-modo-sem-adequa-o*
*Completed: 2026-08-20*
