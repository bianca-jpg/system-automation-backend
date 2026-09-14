---
phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ
verified: 2026-09-08T00:00:00Z
status: passed
score: 5/5 must-haves verified
behavior_unverified: 0
overrides_applied: 0
human_verification:
  - test: "Confirmar a política de negócio embutida no fix CR-01 (grandfathering de contribuição já detida pelo par no rewind+recobrança de validar_edicao_orcamento, app/modules/pedidos/domain/orcamento_edicao.py:125-202)"
    expected: "Que a mantenedora concorde que uma OR 'sem adequação' legada, cujo desvio ao vivo já ultrapassa ±5% (situação plausível para qualquer 'sem' anterior a esta fase, já que o bug do filtro SQL corrigido em 20-01 nunca validou essas ORs antes), deve poder ser resalva/redistribuída sem exigir redução imediata ao limite — apenas um NOVO incremento acima do que o par já detém é bloqueado."
    why_human: "É uma decisão de política de tolerância, não um bug de código — o próprio relatório de fix (20-REVIEW-FIX.md, achado CR-01) marcou a mudança como 'requires human verification' e pediu explicitamente para checar a semântica de grandfathering contra a política real de tolerância antes de mergear. O código está correto e testado (testes unitários + teste real de banco `test_orcamento_resave_par_legado_acima_do_limite_nao_bloqueia`), mas a pergunta é se esse é o comportamento de negócio desejado para OR legadas já fora do orçamento."
    result: "APROVADO pela mantenedora (Victoria Macedo Mollica) em 2026-09-08, na própria sessão que rodou esta verificação — confirmou que não piorar já é suficiente e que exigir redução imediata bloquearia toda OR legada afetada até alguém consertar na mão. Fase fechada como `passed`."
---

# Phase 20: Trava de orçamento de ±5% na edição manual de pedidos Sem Adequação Verification Report

**Phase Goal:** Ligar o ledger de orçamento ±5% (`OrcamentoPedido`) à edição manual de grade de pedidos "Sem Adequação" (`ModoAdequacao.SEM_ADEQUAR`) — hoje essa edição nunca consulta o orçamento e o domínio bloqueia qualquer mudança de total. Corrige antes 2 bugs pré-existentes no ledger que quebrariam a trava silenciosamente (filtro `WHERE op.tipo = 'com'` na SQL de orçamento escondendo consumo "sem adequação"; reset de `qt_solicitada` a cada edição apagando consumo registrado). Escopo desta fase é só backend — o indicador visual e a trava no modal (`product-grade-detail-modal.tsx`, requirement UI-02) ficam para uma quick-task separada no `frontend`.

**Verified:** 2026-09-08
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|---|---|---|
| 1 | O orçamento de um pedido reflete corretamente o consumo de ORs "sem adequação" (filtro `tipo='com'` corrigido), e uma segunda edição sucessiva não apaga o consumo registrado pela primeira (reset de `qt_solicitada` corrigido) — "Com Adequação" byte-idêntico | ✓ VERIFIED | `repositorio_orcamento_pedido.py` tem ramo novo `consumido_por_total_par` unido a `consumido_por_item` (ramo `'com'` intocado, comentado "byte-idêntico"); `edicao_grade.py` preserva linha de base do par via `ratear_inteiro`; `git diff` de `processing/application/service.py` (motor) vazio desde antes da fase; testes `test_duas_edicoes_sucessivas_soma_qt_solicitada_permanece_100`, `test_orcamento_sucessivo_edicoes_acumulam`, 8 testes `pedido_budget` novos — todos passam (rodado independentemente) |
| 2 | Editar a grade de uma OR "sem adequação" pode mudar o total do par; "Com Adequação" continua recusando qualquer mudança de total exatamente como hoje | ✓ VERIFIED | `montar_grade_atualizada(permitir_variacao_total=...)` em `edicao_grade.py`; `casos_uso.py:413` passa `permitir_variacao_total=(state.tipo == "sem")`; teste de regressão `ValueError` sem o parâmetro novo com total divergente passa |
| 3 | Salvar uma edição "sem adequação" que ultrapasse o orçamento de adição ou de corte do pedido é recusado com 409 e nenhuma escrita acontece — inclusive quando o lote mistura um pedido dentro e outro fora do limite | ✓ VERIFIED | `casos_uso.py:348-399` valida orçamento por `nr_pedido`, depois do lock (`_adquirir_lock_processamento` linha 283) e antes de qualquer escrita (primeira escrita em `atualizar_grades_em_lote` linha 478); `routes.py:204-215` mapeia `OrcamentoPedidoExcedidoError` para 409 estruturado; testes `-k orcamento` (17 em `test_pedidos_product_writes.py`, incluindo lote misto dentro/fora do limite) passam |
| 4 | Abrir a edição de um produto devolve o orçamento agregado do pedido inteiro (todos os produtos) para clientes "sem adequação"; linhas "com adequação" continuam com payload idêntico ao de antes desta fase | ✓ VERIFIED | `listar_clientes_produto.py` lê `carregar_orcamento_pedidos` em lote, fora do laço `for pair in page`; SQL agrega por `nr_pedido` (JOIN só por `nr_pedido`, não por `cd_prod_cor`) — cobre todos os produtos do pedido; teste `test_read_projection_orcamento_pedido_cobre_todos_os_produtos_do_pedido` semeia 2 produtos e confirma soma; linhas "com adequação" não recebem a chave (`orcamento_pedido` ausente no dict), campo opcional `default=None` em `PedidoCardOut` — nenhum outro campo do card foi alterado |
| 5 | Salvar uma edição "sem adequação" dentro do limite também aprova e finaliza a OR no mesmo clique, escopado só aos pares efetivamente editados — nenhuma OR "Com Adequação" é aprovada de carona | ✓ VERIFIED | `casos_uso.py:514-576` bloco de fusão entre a última escrita e o único `db.commit()` (confirmado só 1 commit na função); elegibilidade exige `state.tipo == "sem"` para TODOS os pares efetivamente alterados (`changed_nr_pedidos`); escopo restrito a `nr_pedido` do lote + `cd_prod_cor` do produto (nunca `aprovar_produto_canal`); 9 testes `salvar_e_aprovar_*` cobrindo escopo, mistura de tipos, atomicidade (falha na aprovação desfaz a grade) — todos passam |

**Score:** 5/5 truths verified (0 present, behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `app/modules/pedidos/domain/orcamento_edicao.py` | Domínio puro: snapshot, contribuição, validação rewind+cobrança (com fix CR-01: grandfathering) | ✓ VERIFIED | Existe, sem SQLAlchemy/async/I-O, exporta os 5 símbolos esperados; docstring documenta o fix CR-01 explicitamente |
| `app/modules/pedidos/infrastructure/repositorio_orcamento_pedido.py` | Fonte única da SQL de orçamento por pedido | ✓ VERIFIED | `PEDIDO_BUDGET_SQL` com ramo `'com'` (byte-idêntico, comentado) e ramo novo `'sem'` (por total do par), unidos sem duplicar linha; `missing_from_pedidos` (invariante J5) preservado |
| `app/modules/pedidos/domain/edicao_grade.py` | `montar_grade_atualizada` com total variável opcional | ✓ VERIFIED | Parâmetro `permitir_variacao_total` opt-in, default preserva comportamento; linha de base do par via `ratear_inteiro` |
| `app/modules/pedidos/domain/rateio.py` | `ratear_inteiro` | ✓ VERIFIED | Presente ao lado de `ratear_hamilton`, sem `float(` na aritmética |
| `app/modules/pedidos/application/casos_uso.py` | Validação de orçamento + fusão salvar/aprovar | ✓ VERIFIED | Ordem confirmada por leitura direta: lock → estoque → orçamento → reconstrução de grade → escritas → fusão de aprovação → commit único |
| `app/modules/pedidos/application/ports.py` / `write_adapter.py` | Acesso transacional a orçamento e tolerância | ✓ VERIFIED | `carregar_orcamento_pedidos`/`carregar_tolerancia_adequacao` no Protocol e na implementação, mesma `AsyncSession` |
| `app/modules/pedidos/infrastructure/http/routes.py` | Mapeamento 409 estruturado | ✓ VERIFIED | `except service.OrcamentoPedidoExcedidoError` antes das cláusulas genéricas, corpo com `code`/`nrPedido`/`orcamento`/`restanteAdicao`/`restanteCorte` |
| `app/modules/pedidos/application/schemas.py` | `OrcamentoPedidoOut`, campo em `PedidoCardOut`, `approvedCount` | ✓ VERIFIED | 7 campos camelCase, `orcamento_pedido: OrcamentoPedidoOut \| None = None`, `approved_count` com `default=0` |
| `app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py` | Projeção enriquecida com orçamento | ✓ VERIFIED | Leitura em lote fora do laço, `OrcamentoPedido(...)` instanciado (não recalcula fórmula), zero literal `0.05`/`1.05` |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `processing/infrastructure/adapters.py` | `repositorio_orcamento_pedido.py` | `load_pedido_budget` delega para `carregar_pedido_budget_tuplas` | ✓ WIRED | `grep -c "carregar_pedido_budget_tuplas" adapters.py` ≥ 1; `git diff` de `service.py` (motor) vazio |
| `orcamento_edicao.py` | `orcamento_pedido.py` | `validar_edicao_orcamento` constrói `OrcamentoPedido` | ✓ WIRED | Linha 162 do arquivo, `OrcamentoPedido(...)` construído com consumidos rebobinados |
| `casos_uso.py` | `orcamento_edicao.py` | `validar_edicao_orcamento` antes de qualquer write | ✓ WIRED | Confirmado por leitura direta: chamada na linha 394, primeira escrita (`atualizar_grades_em_lote`) na linha 478 |
| `write_adapter.py` | `repositorio_orcamento_pedido.py` | `carregar_orcamento_pedidos` na mesma `AsyncSession` da escrita | ✓ WIRED | Delegação de uma linha, mesmo padrão dos outros métodos do adapter |
| `routes.py` | `orcamento_edicao.py` | `OrcamentoPedidoExcedidoError` → 409 | ✓ WIRED | `except service.OrcamentoPedidoExcedidoError` antes das cláusulas de conflito genérico |
| `listar_clientes_produto.py` | `repositorio_orcamento_pedido.py` | leitura em lote por página | ✓ WIRED | Chamada fora do laço `for pair in page`, confirmado por leitura direta |
| `casos_uso.py` | `write_adapter.py` (aprovação) | `aprovar_ordem_reserva` por par antes do commit único | ✓ WIRED | Bloco de fusão (linhas 514-576), `db.commit()` único confirmado na linha 577 |

### Behavioral Spot-Checks (independent re-execution)

| Behavior | Command | Result | Status |
|---|---|---|---|
| Testes unitários de domínio (orçamento) | `pytest app/tests/test_pedidos_orcamento_edicao.py -q` | 16 passed | ✓ PASS |
| Testes de rateio/grade com total variável | `pytest app/tests/test_pedidos_edicao_grade_variacao_total.py -q` | 34 passed | ✓ PASS |
| Testes de SQL agregada (`pedido_budget`) | `pytest app/tests/test_pedidos_processing_repository.py -k pedido_budget -q` | 8 passed | ✓ PASS |
| Testes de escrita/orçamento/fusão | `pytest app/tests/test_pedidos_product_writes.py -k "orcamento or salvar_e_aprovar" -q` | 17 passed | ✓ PASS |
| Testes de projeção de leitura (orçamento) | `pytest app/tests/test_pedidos_read_projection.py -k orcamento -q` | 5 passed | ✓ PASS |
| Teste HTTP do contrato 409 | `pytest app/tests/test_pedidos_routes.py -k orcamento -q` | 1 passed | ✓ PASS |
| Teste dedicado do fix CR-01 (banco real) | `pytest app/tests/test_pedidos_product_writes.py -k legado -q` | 1 passed | ✓ PASS |
| Suíte completa do backend | `pytest -q` (exceto teste Unix-only conhecido) | **981 passed, 18 skipped, 0 failed** | ✓ PASS — bate exatamente com o número reportado no contexto da tarefa |
| Lint | `ruff check app/modules/pedidos/` | All checks passed | ✓ PASS |
| Migrations | `git diff --stat alembic/` desde antes da fase | vazio | ✓ PASS — nenhuma migration criada (D-05) |

### Code Review Findings (20-REVIEW.md / 20-REVIEW-FIX.md)

| ID | Severity | Status | Evidence |
|---|---|---|---|
| CR-01 | critical | ✓ FIXED, verified in code | `orcamento_edicao.py:125-202` implementa grandfathering (só cobra o incremento acima da contribuição atual); docstring do módulo documenta o fix explicitamente; 3 testes unitários (`resave_sem_mudanca`, `reducao`, `aumento_continua_estourando`) + 1 teste real de banco (`test_orcamento_resave_par_legado_acima_do_limite_nao_bloqueia`) — todos passam |
| WR-01 | warning | ✓ FIXED, verified in code | `test_batch_grade_orcamento_excedido_retorna_409_camelcase` em `test_pedidos_routes.py` (passa); `test_read_projection_orcamento_pedido_cobre_todos_os_produtos_do_pedido` estendido para serializar via `PedidoCardOut.model_validate(...).model_dump(mode="json", by_alias=True)` |
| WR-02 | warning | ✓ FIXED, verified in code | `test_linha_base_par_e_qt_solicitada_ou_fallback_concordam` em `test_pedidos_edicao_grade_variacao_total.py` (passa) — checa consistência cruzada entre `casos_uso._linha_base_par` e `edicao_grade._qt_solicitada_ou_fallback` |
| IN-01 | info | not fixed (`fix_scope: critical_warning`, explicitamente fora de escopo) | Cosmético — inconsistência de dict shape em um caminho de retorno antecipado, coberto pelo default do Pydantic |

Nota: `20-REVIEW-FIX.md` registra "commit: none yet" para os 3 fixes, alegando bloqueio de `commit_docs: false`. **Verificado no git log que isso está desatualizado** — os 3 commits existem (`8e751c0`, `fd6e367`, `2676b45`), seguidos por um commit de docs (`185103f`); `git status` está limpo. O documento não foi atualizado após o commit real acontecer, mas o código está commitado e presente na branch corrente.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| GRADE-03 | 20-01 a 20-05 | Trava de orçamento ±5% na edição manual "sem adequação" | ✓ SATISFIED (código) / ⚠️ documentação de requisito desatualizada | Ver nota abaixo |

**Nota sobre GRADE-03 em `.planning/REQUIREMENTS.md:37`:** o texto atual ("parcial... Não encontrada uma checagem explícita de tolerância ±5% separada da checagem de estoque; se esse limite específico for necessário, ainda é gap") descreve o estado **anterior** a esta fase, e está **desatualizado/impreciso agora**. Esta fase entrega exatamente a checagem de tolerância ±5% que essa frase descrevia como ausente — para ORs "sem adequação" editadas manualmente. A ferramenta automática de fechamento de requisito não conseguiu marcar isso porque `GRADE-03` vive como prosa narrativa dentro de `### Edição manual de grade` (seção `## Future Requirements`, fora da lista de checkbox de `## v1.1 Requirements` e fora da tabela de `## Traceability`) — um gap de formato pré-existente, não algo a corrigir nesta verificação, mas registrado aqui para que a mantenedora atualize o texto do requisito na próxima oportunidade. Note também que a frase original tratava a ausência da trava ±5% como algo a decidir "se necessário" — esta fase decidiu que sim, e implementou; a frase deveria ser reescrita para refletir isso, não apagada, porque a limitação "com adequação continua sem poder mudar total" (D-01) permanece verdadeira e é intencional.

`UI-02` (trava visual no modal) permanece explicitamente fora de escopo desta fase, por decisão registrada em `20-CONTEXT.md` (D-14) e no goal do ROADMAP — consistente, não é uma lacuna desta verificação.

### Anti-Patterns Found

Nenhum. Varredura em todos os 9 arquivos de produção tocados pela fase não encontrou `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` (os únicos hits de `grep -i TODO` foram falsos positivos da palavra "TODOS" em português). `ruff check` limpo.

### Frontend Scope Confirmation

`frontend` confirmado limpo (`git status --short` vazio) e sem commits novos relacionados a esta fase — consistente com a decisão explícita de escopo (goal do ROADMAP, D-12 a D-16 do `20-CONTEXT.md`) de deixar indicador visual/trava de modal para uma quick-task separada.

## Human Verification Required

### 1. Política de negócio do grandfathering (fix CR-01)

**Test:** Revisar `app/modules/pedidos/domain/orcamento_edicao.py:125-202` (função `validar_edicao_orcamento`) e o achado CR-01 em `20-REVIEW.md`/`20-REVIEW-FIX.md`. Confirmar com a mantenedora se o comportamento é o desejado: uma OR "sem adequação" legada cujo desvio ao vivo já ultrapassa ±5% (situação plausível para qualquer "sem" anterior a esta fase, dado o bug do filtro SQL corrigido em 20-01) pode ser resalva ou ter tamanhos redistribuídos sem exigir redução ao limite — apenas um NOVO incremento acima do que o par já detém seria bloqueado.

**Expected:** Confirmação explícita de que essa é a política de tolerância correta (grandfather do consumo legado, bloquear só crescimento adicional), ou uma decisão alternativa a implementar em fase futura.

**Why human:** Não é um bug de código — está corretamente implementado e coberto por testes (unitários + teste real de banco). É uma decisão de política de negócio que o próprio relatório de fix (`20-REVIEW-FIX.md`) marcou textualmente como "requires human verification... please double check the grandfathering semantics against the actual tolerance policy before merging".

## Gaps Summary

Nenhum gap bloqueante encontrado. Todas as 5 Success Criteria do ROADMAP e todos os `must_haves` (truths/artifacts/key_links) dos 5 planos foram verificados diretamente no código e confirmados por execução independente da suíte de testes (981 passed, 18 skipped, 0 failed — reproduzido nesta verificação, batendo exatamente com o número relatado). Os 2 bugs pré-existentes que a fase existia para corrigir (filtro SQL `tipo='com'` e reset de `qt_solicitada`) estão corrigidos e comprovados por teste dedicado de edições sucessivas. O achado crítico do code review (CR-01) foi corrigido no código, coberto por teste real de banco, e os commits existem na branch (apesar do `20-REVIEW-FIX.md` desatualizado dizendo o contrário). O único item pendente é uma confirmação de política de negócio (não um defeito), motivo do status `human_needed` em vez de `passed`. A documentação de `GRADE-03` em `REQUIREMENTS.md` está desatualizada e deveria ser reescrita numa próxima oportunidade, mas isso é dívida de documentação pré-existente, não uma lacuna desta fase.

---

_Verified: 2026-09-08_
_Verifier: Claude (gsd-verifier)_
