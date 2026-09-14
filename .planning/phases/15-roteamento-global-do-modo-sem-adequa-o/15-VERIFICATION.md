---
phase: 15-roteamento-global-do-modo-sem-adequa-o
verified: 2026-08-20T00:00:00Z
status: passed
score: 5/5 must-haves verified (ROADMAP success criteria) + 2/2 requirements (ARCH-01, FIX-01)
overrides_applied: 0
---

# Phase 15: Roteamento global do modo sem adequação — Verification Report

**Phase Goal:** O modo sem adequação passa a ser planejado pelo mesmo caminho global que já serve o com adequação; o código de streaming órfão é removido na mesma entrega; a revalidação de capacidade de estoque volta a cobrir o tipo "sem"; e o orçamento ±5% usa dados reais lidos do banco (fecha ALOC-09).
**Requirements:** ARCH-01, FIX-01
**Verified:** 2026-08-20
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (5 critérios de sucesso do ROADMAP)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `SEM_ADEQUAR` usa a mesma pipeline global que `ADEQUAR` — nenhuma decisão de prioridade/estoque depende de paginação | ✓ VERIFIED | `app/modules/pedidos/processing/domain.py:486-601` (`build_processing_plan`) chama `processar_pedidos(..., modo=modo)` incondicionalmente para os dois modos, sem ramo condicional de streaming. `app/modules/pedidos/processing/application/service.py:184-281` (`_plan_once`) hidrata `pending_items` (foto global) e carrega `stock`/`criterion`/`tolerance`/`load_pedido_budget` incondicionalmente nos dois modos (comentário explícito nas linhas 223-231). Teste `test_sem_adequar_planning_hydrates_globally_and_reports_real_counts` (`test_pedidos_processing_service.py:501`) — reescrita deliberada do teste que antes provava paginação keyset — roda e passa (confirmado nesta verificação), provando hidratação única (`events.count("hydrate") == 1`) e contadores reais (`deferred_count=1`, `blocked_credit_count=1`, não constantes zeradas). Teste de domínio `test_sem_adequar_now_defers_by_stock_and_blocks_by_credit` prova J1/J2/J3 diretamente no motor puro. |
| 2 | O construtor de plano por streaming não existe mais no repositório | ✓ VERIFIED | Grep independente de `StreamingPlanBuilder\|PLANNING_PAIR_PAGE_SIZE\|prepare_pending_pair_stream\|load_pending_pair_page\|append_plan_rows\|finalize_streamed_plan\|PlanSummary` em `app/` retorna vazio (rodado nesta verificação). Arquivo `app/tests/test_pedidos_processing_stream_repository.py` confirmado ausente do diretório de testes. `git show --stat` de cada commit de 15-03 confirma a remoção real: `49360ef` (-371 linhas líquidas em produção), `af7aba3`/`e469126` (deleção do arquivo de teste, -382 linhas). |
| 3 | Plano `tipo="sem"` que exceda estoque na escrita é rejeitado, igual ao `adequar` | ✓ VERIFIED | `app/modules/pedidos/processing/infrastructure/repository.py:385-407` (`_assert_stock_capacity`) não tem mais nenhum filtro por `tipo` — itera `rows` incondicionalmente para os dois tipos válidos (`{"com","sem"}`, validados em `_payload`). Teste `test_sem_adequar_revalidates_stock_before_writes` (repository.py:438) roda e passa nesta verificação, provando `ProcessingPlanConflict("processing_plan_stale")` para `tipo="sem", qty=2` contra `stock_qty=1`, sem gravar `OrdemReserva`. |
| 4 | Pico de memória do canal inteiro em `sem_adequar` cabe no orçamento do worker | ✓ VERIFIED | `app/tests/test_pedidos_processing_sem_adequar_memory_024.py` — primeira instrumentação de memória do repositório (`resource.getrusage(RUSAGE_SELF).ru_maxrss`), dataset sintético de 45.000 pares/112.500 itens. Executado isoladamente nesta verificação (fora da suíte completa, para não herdar pico de outros testes): `delta_total_mib=631.0`, `delta_chamada_mib=540.8`, dentro do teto `_MEMORY_CEILING_MIB=700.0` (margem ~9.9%, consistente com o número documentado de 630.9 MiB no SUMMARY). Worker dedicado reserva 1280-1536 MiB — folga ampla confirmada. |
| 5 | Orçamento ±5% usa dados reais (`processar_pedidos` recebe `total_original_por_pedido`/`consumido_previo_*`); 2 execuções sucessivas não concedem 5% novos; aviso ALOC-09 some do log quando dados reais são fornecidos | ✓ VERIFIED | `service.py:232-237` lê `load_pedido_budget(order_ids)` no escopo da foto pendente e repassa a `build_processing_plan` (linhas 250-252), que repassa a `processar_pedidos` (`domain.py:544-546`). Teste discriminante `test_orcamento_persiste_entre_execucoes_sucessivas` (repository.py:872) — executado e passou nesta verificação — grava uma OR real via `SqlAlchemyProcessingWriter`, relê o orçamento do banco (`consumido_corte=2` confirmado no estado intermediário), e faz DUAS chamadas de `build_processing_plan` sobre a MESMA foto pendente da rodada 2: com orçamento real, o corte de 1 peça é **recusado** (`rows == ()`, `deferred_count=1`) e o log não contém "ALOC-09"; sem os mappings (placeholder), o mesmo corte é **aceito** (`planned_count=1`) e o log CONTÉM "ALOC-09". Comparação é genuinamente discriminante (dois resultados diferentes sobre os mesmos itens), não uma recusa isolada — exatamente o padrão exigido pela tarefa de verificação. Teste espelho sem banco (`test_plan_once_feeds_real_budget_and_silences_the_aloc09_warning`, service.py:654) prova a mesma propriedade no nível de `_plan_once`. |

**Score:** 5/5 truths verified

### Caso obrigatório do denominador que encolhe (invariante J5)

**Verificado com os números reais de produção.** `test_load_pedido_budget_includes_or_quantity_for_pair_missing_from_pedidos` (`app/tests/test_pedidos_processing_repository.py:796-843`) reproduz exatamente o caso descrito no CONTEXT.md e VALIDATION.md: pedido `1591091`, par `(1591091, "ML.18.0315|001")` já convertido em OR (`qt_solicitada=3`) e ausente de `pedidos`, com os outros 6 produtos do pedido somando `qt_entregar=6` em `pedidos`. O teste afirma `total_original == {1591091: 9}` — a soma correta (6+3), não os 6 que a leitura ingênua devolveria. Executado e passou de forma isolada nesta verificação. A implementação em `adapters.py:95-161` (`_PEDIDO_BUDGET_SQL`) usa a CTE `missing_from_pedidos` (sem filtro de `tipo`, somando qualquer OR cujo par sumiu de `pedidos`) somada a `from_pedidos` — exatamente a mitigação obrigatória descrita no CONTEXT.md.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/modules/pedidos/processing/domain.py::build_processing_plan` | Caminho único (sem ramo streaming) para os dois modos, orçamento repassado nos dois modos | ✓ VERIFIED | Lido integralmente; chama `processar_pedidos` incondicionalmente; `StreamingPlanBuilder`/`PlanSummary`/`PLANNING_PAIR_PAGE_SIZE`/`build_pair_payload` ausentes |
| `app/modules/pedidos/processing/application/service.py::_plan_once` | Sem ramo condicional; `stock`/`criterion`/`tolerance`/`load_pedido_budget` incondicionais | ✓ VERIFIED | Lido integralmente; nenhum `if mode is ADEQUAR` remanescente antes da hidratação de estoque/orçamento |
| `app/modules/pedidos/processing/infrastructure/repository.py::_assert_stock_capacity` | Revalida para `tipo in {"com","sem"}` | ✓ VERIFIED | Lido integralmente; sem filtro de tipo no loop |
| `app/modules/pedidos/processing/infrastructure/adapters.py::load_pedido_budget` | Query agregada com correção J5 | ✓ VERIFIED | Lido integralmente; CTE `missing_from_pedidos` implementa a correção |
| `app/tests/test_pedidos_processing_stream_repository.py` | Não existe mais | ✓ VERIFIED | Confirmado ausente do diretório |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `_plan_once` | `build_processing_plan` | chamada direta com `total_original_por_pedido` etc. | WIRED | Confirmado nas linhas 241-253 de `service.py` |
| `build_processing_plan` | `processar_pedidos` (motor puro) | chamada direta, `modo=modo` | WIRED | Confirmado nas linhas 535-547 de `domain.py`, sem condicional por modo |
| `planner.load_pedido_budget` | `_PEDIDO_BUDGET_SQL` | query SQL agregada | WIRED | Confirmado em `adapters.py:303-333` |
| `_assert_stock_capacity` | `carregar_estoque_disponivel_alvos` | chamada incondicional por tipo | WIRED | Confirmado em `repository.py:385-407` |

### Behavioral Spot-Checks / Probe Execution

| Check | Command | Result | Status |
|-------|---------|--------|--------|
| Suíte completa via Docker | `docker compose ... exec -T api uv run pytest -q` | `826 passed, 16 skipped, 1 failed` (mesma falha conhecida pré-existente, `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql`) | ✓ PASS (reproduzido independentemente nesta verificação) |
| Testes-chave da Fase 15 isolados | `pytest` nos 6 testes discriminantes/J5/FIX-01/hidratação global | `6 passed` | ✓ PASS (reproduzido independentemente) |
| Teste de memória isolado | `pytest test_pedidos_processing_sem_adequar_memory_024.py -q -s` | `delta_total_mib=631.0`, teto 700.0 — `1 passed` | ✓ PASS (reproduzido independentemente) |
| Grep de símbolos de streaming (J7) | `grep -rn` dos 6 símbolos + `PlanSummary` em `app/` | vazio | ✓ PASS (reproduzido independentemente) |
| Commits declarados existem | `git log --oneline --all \| grep <hash>` | todos os 13 hashes citados nos 5 SUMMARYs encontrados | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| ARCH-01 | 15-03 | Modo sem adequação planejado pelo caminho global; streaming removido | ✓ SATISFIED | `build_processing_plan`/`_plan_once` sem ramo de streaming; grep confirmou remoção total |
| FIX-01 | 15-02 | Revalidação de capacidade de estoque cobre também `tipo="sem"` | ✓ SATISFIED | `_assert_stock_capacity` sem filtro de tipo; teste `test_sem_adequar_revalidates_stock_before_writes` passa |

Nenhum requirement órfão: ROADMAP.md e REQUIREMENTS.md mapeiam exatamente ARCH-01 e FIX-01 para a Phase 15, e ambos têm evidência direta em código + teste.

### Anti-Patterns Found

Nenhum `TBD`/`FIXME`/`XXX`/`HACK`/`PLACEHOLDER` encontrado nos arquivos de produção modificados (`domain.py`, `service.py`, `ports.py`, `adapters.py`, `repository.py`). Nenhuma implementação vazia (`return null`/`return {}`/handler vazio) nos caminhos tocados. Comentários no código (`domain.py:35-44`) documentam honestamente as limitações conhecidas (teto de memória ainda não medido antes de 15-04, agora resolvido) em vez de esconder gaps.

### Boundary Check (fronteira da fase)

Confirmado por `git show --stat` de cada um dos 13 commits específicos de 15-01 a 15-05: **somente** arquivos dentro de `app/modules/pedidos/processing/`, seus testes correspondentes (`app/tests/test_pedidos_processing_*`) e `docs/adequacao.md` foram tocados. Nenhuma migration, nenhum arquivo de frontend, nenhuma antecipação de Phase 16/17/18/19. O commit `fdc4538` ("feat(auth): adicionar login via Microsoft Entra ID (SSO)") aparece intercalado no histórico do branch por ser trabalho concorrente de outra sessão no mesmo working tree compartilhado (conforme documentado em `15-CONTEXT.md` e nos SUMMARYs) — não pertence ao escopo da Phase 15 e não foi tocado por nenhum commit desta fase.

### Regressão silenciosa (item 6 da tarefa)

A queda esperada no volume de ORs de `sem_adequar` (porque o modo agora recusa por crédito/estoque/furo, em vez de aceitar todo par) está **documentada explicitamente** como comportamento esperado, não como bug: `15-VALIDATION.md` § "Manual-Only Verifications" e `15-03-SUMMARY.md` § "Next Phase Readiness" registram a verificação manual pendente (comparar volume antes/depois contra dado real de produção, usando `deferredCount`/`blockedCreditCount` para explicar a diferença). Este item é rotineiramente fora do alcance de verificação automatizada (dado de produção), corretamente classificado como verificação manual, não como gap da fase.

### Achado da execução paralela (item 7 da tarefa)

Confirmado por análise do git log e dos SUMMARYs: os planos 15-04 e 15-05 rodaram em sessões concorrentes na mesma working tree (sem worktrees, conforme `workflow.use_worktrees: false`). `15-05-SUMMARY.md` documenta que a suíte retornou `826 passed` em vez dos `825` esperados, investigou e atribuiu corretamente a diferença a um arquivo de teste não commitado pertencente a 15-04 (`test_pedidos_processing_sem_adequar_memory_024.py`), sem tratar isso como regressão. `15-04-SUMMARY.md` documenta o mesmo cenário do lado oposto (percebeu `STATE.md`/`15-05-SUMMARY.md` não commitados de outra sessão) e confirma que nenhuma reconciliação manual de conflito foi necessária porque os dois planos tocam arquivos disjuntos. O estado final do `git log` (commits `8e73b90`, `1230212`, `e355712` de 15-05; `5e0f553`, `add37c8` de 15-04) confirma que ambos os planos foram commitados corretamente, sem conflito real, e a contagem final da suíte (826 passed) bate com a soma esperada (825 baseline + 1 teste novo de memória).

### Human Verification Required

Nenhum item pendente de verificação humana que bloqueie o fechamento da fase. O único item manual identificado (comparação de volume de ORs `sem_adequar` antes/depois, contra dado real de produção) já está corretamente classificado no VALIDATION.md como "Manual-Only Verification" pós-deploy, fora do escopo de uma verificação de código estática, e não é uma condição de aceite desta fase (o critério 5 do ROADMAP não exige essa comparação — só exige que o mecanismo do orçamento use dados reais, o que foi provado por teste automatizado).

### Gaps Summary

Nenhum gap encontrado. Os 5 critérios de sucesso do ROADMAP, os 2 requirements (ARCH-01, FIX-01), as 7 invariantes de VALIDATION.md (J1-J7) e os 7 pontos específicos solicitados nesta verificação foram todos confirmados com evidência direta de código lido, testes executados de forma independente (não apenas citação do SUMMARY) e grep próprio. A suíte completa foi reproduzida via Docker nesta sessão de verificação e retornou exatamente `826 passed, 16 skipped, 1 failed`, com a única falha sendo a mesma pré-existente e documentada, fora do escopo da Phase 15.

---

_Verified: 2026-08-20_
_Verifier: Claude (gsd-verifier)_
