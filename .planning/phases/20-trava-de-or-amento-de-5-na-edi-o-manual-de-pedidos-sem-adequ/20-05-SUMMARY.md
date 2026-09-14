---
phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ
plan: 05
subsystem: api
tags: [fastapi, sqlalchemy, postgres, domain-driven-design, orcamento, aprovacao]

# Dependency graph
requires:
  - phase: 20-03
    provides: "Trava de orçamento ±5% ligada ao caminho de escrita (executar_alteracao_grades_produto), branch por state.tipo, bloco de validação por nr_pedido antes das escritas"
  - phase: 20-04
    provides: "OrcamentoPedidoOut embutido em PedidoCardOut (não consumido diretamente por este plano, mas mesmo domínio de orçamento)"
provides:
  - "executar_alteracao_grades_produto funde salvar+aprovar num único commit: quando TODOS os pares efetivamente alterados do lote são tipo 'sem adequação', a mesma chamada que grava a grade também finaliza (aprova) aquele(s) par(es) — sem clique separado de 'Aprovar OR'"
  - "AlterarGradesProdutoResponse.approved_count/approvedCount (default 0) — contagem de ORs aprovadas junto ao salvamento"
  - "Escopo da aprovação fundida restrito aos nr_pedido do lote e ao cd_prod_cor do produto editado (PD-10) — nunca ao produto/canal inteiro"
affects: [20-06, 20-07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Fusão salvar+aprovar chama o port de aprovação (carregar_pares_aprovaveis_pedido_para_update + aprovar_ordem_reserva) inline, dentro da mesma seção crítica, nunca os casos de uso executar_aprovacao/executar_aprovacao_produto — esses fariam commit próprio e quebrariam o tudo-ou-nada da função (PD-12)"
    - "Elegibilidade da fusão é decidida por state.tipo dos pares EFETIVAMENTE alterados (changed_pairs, pós-verificação de concorrência), nunca pela lista de changes recebida — um change que não muda nada não deve influenciar a decisão"
    - "Eventos de aprovação fundida marcam a origem no payload (source=grade_save / reason=grade_save_approved), distinto da aprovação manual (order_approved/product_approved), para auditoria (T-20-25)"

key-files:
  created: []
  modified:
    - app/modules/pedidos/application/casos_uso.py
    - app/modules/pedidos/application/schemas.py
    - app/tests/test_pedidos_product_writes.py
    - app/tests/test_pedidos_routes.py

key-decisions:
  - "PD-10 (do plano): aprovação automática escopada APENAS aos nr_pedido do lote editado + cd_prod_cor do produto, rejeitando explicitamente a recomendação da pesquisa de reusar o escopo de aprovar_produto_canal (que aprovaria qualquer OR aberta do produto/canal, inclusive 'com adequação' misturadas) — isso violaria D-01/D-11"
  - "PD-11 (do plano): fusão só ocorre quando TODO o lote é 'sem adequação'; lotes mistos gravam tudo e não aprovam nada"
  - "PD-12 (do plano): chama o repositório de aprovação inline via port, nunca os casos de uso de aprovação (que têm commit próprio) — garante exatamente um db.commit() na função"
  - "approved_count sempre presente no dict de retorno do caminho de sucesso (mesmo quando 0), mas AUSENTE no retorno antecipado de zero-alterações — preserva o dict exato que testes pré-existentes desse caminho já verificavam, e o schema tem default=0 para quem só ler o JSON HTTP"
  - "_sem_fusao() (deviation): testes de acúmulo sucessivo do plano 20-03 (test_orcamento_sucessivo_edicoes_acumulam) reeditam a MESMA OR 'sem adequação' ainda aberta em 3 passos sequenciais — com a fusão ativa, o passo 1 (dentro do orçamento) finalizaria a OR e fecharia a janela de edição, quebrando os passos 2/3. Neutralizado localmente nesse teste via patch dos dois pontos de entrada da aprovação, preservando a semântica pré-fusão que o teste crítico de double-spend precisa; a fusão em si tem cobertura própria nos testes salvar_e_aprovar_*"

patterns-established:
  - "Testes salvar_e_aprovar_* em test_pedidos_product_writes.py rodam contra o banco de teste real via SqlAlchemyPedidosWriteUnitOfWork (não mockam o port de aprovação), reaproveitando _patches_edicao_grade/_run_edicao_grade/_limpar_or do plano 20-03"

requirements-completed: [GRADE-03]

# Metrics
duration: ~50min
completed: 2026-09-08
status: complete
---

# Phase 20 Plan 05: Fusão salvar+aprovar para OR "sem adequação" dentro do orçamento Summary

**`executar_alteracao_grades_produto` passa a aprovar, no mesmo commit da gravação da grade, apenas os pares `nr_pedido`+`cd_prod_cor` efetivamente alterados quando TODO o lote editado é "sem adequação" — sem clique separado de "Aprovar OR"; lotes com qualquer par "com adequação" gravam tudo e não aprovam nada.**

## Performance

- **Duration:** ~50 min
- **Tasks:** 2/2
- **Files modified:** 4

## Accomplishments

- `AlterarGradesProdutoResponse` ganha `approved_count`/`approvedCount` (`default=0`), informando quantas ORs foram aprovadas junto com o salvamento.
- `executar_alteracao_grades_produto` ganha um bloco de fusão logo antes do único `db.commit()` da função: decide elegibilidade pelo `state.tipo` dos pares **efetivamente** alterados (`changed_pairs`, já validado contra `expected_pairs`), e só aprova quando 100% desses pares são `tipo="sem"` (PD-11).
- A aprovação, quando elegível, roda par a par (`nr_pedido` do lote × `cd_prod_cor` do produto), chamando `carregar_pares_aprovaveis_pedido_para_update` + `aprovar_ordem_reserva` diretamente pelo port — nunca `executar_aprovacao`/`executar_aprovacao_produto`, que fariam commit próprio e quebrariam a atomicidade tudo-ou-nada da função (PD-12). Gate estático confirma exatamente 1 `db.commit()` no corpo da função.
- Efeitos colaterais da aprovação manual são replicados apenas quando algo foi de fato aprovado: `observe_entity_keys` no tópico `history` e os eventos `orders.approved.v1`/`history.changed.v1`, com a origem marcada no payload (`source="grade_save"` / `reason="grade_save_approved"`) para distinguir da aprovação manual na auditoria (T-20-25).
- 9 testes novos `test_salvar_e_aprovar_*` provando: caminho feliz com eventos e `aprovado_em` preenchido; escopo não vaza para outro pedido do mesmo produto/canal (PD-10); escopo não vaza para outro produto do mesmo pedido (PD-10, restrição por `cd_prod_cor`); "com adequação" nunca é aprovada (D-11); lote misto não aprova nada (PD-11); dois pares "sem" aprovam os dois; falha na etapa de aprovação desfaz também a gravação da grade (atomicidade); edição sem mudança não aprova; estouro de orçamento não aprova.

## Task Commits

1. **Task 1: Fundir a aprovação ao salvamento, escopada aos pares editados, num único commit** - `f897048` (feat)
2. **Task 2: Gate de cobertura da fusão — escopo, mistura de tipos, atomicidade e regressão dos dois endpoints de aprovação** - `9eba995` (test)

## Files Created/Modified

- `app/modules/pedidos/application/casos_uso.py` - bloco de fusão salvar+aprovar em `executar_alteracao_grades_produto`, entre `_registrar_novos_alertas` e o `db.commit()` final
- `app/modules/pedidos/application/schemas.py` - campo `approved_count`/`approvedCount` (default 0) em `AlterarGradesProdutoResponse`
- `app/tests/test_pedidos_product_writes.py` - 9 testes `salvar_e_aprovar_*` novos; `_patches_edicao_grade` passa a ceder o mock de `record_event`; helper `_sem_fusao()` novo; `test_orcamento_sucessivo_edicoes_acumulam` (plano 20-03) usa `_sem_fusao()` nos 3 passos para preservar a semântica de edições sucessivas da mesma OR ainda aberta; `test_batch_redistribui_dentro_da_capacidade_em_uma_transacao` (plano 20-03) ganha `"approved_count": 0` na asserção de igualdade do dict de retorno
- `app/tests/test_pedidos_routes.py` - `test_batch_grade_http_camelcase_limites_e_channel_exato` ganha `"approvedCount": 0` na asserção do JSON HTTP (regressão mecânica do campo novo no contrato)

## Decisions Made

Ver `key-decisions` no frontmatter. Resumo: PD-10/PD-11/PD-12 do PLAN.md seguidas à risca (rejeitando a recomendação original da pesquisa de escopo por produto/canal, por conflitar com D-01/D-11); a decisão nova desta execução foi o design de `_sem_fusao()` para resolver o conflito entre a nova fusão e o teste crítico de acúmulo sucessivo do plano 20-03 (ver Deviations abaixo).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `test_orcamento_sucessivo_edicoes_acumulam` (plano 20-03) quebrava com a fusão ativa**

- **Found during:** Task 1, ao rodar a suíte completa do arquivo após implementar a fusão
- **Issue:** Esse teste reedita a MESMA OR "sem adequação" ainda aberta em 3 passos sequenciais para provar acúmulo de orçamento (D-06). Com a fusão ativa, o passo 1 (edição válida dentro do orçamento) passou a finalizar automaticamente a OR — fechando a janela de edição. O passo 2 então falhava com `ProdutoBatchConflitoError` ("não está mais aberta para edição") em vez do `OrcamentoPedidoExcedidoError` esperado, e o passo 3 quebrava pelo mesmo motivo.
- **Fix:** Criado `_sem_fusao()`, um context manager que mocka os dois pontos de entrada da aprovação (`carregar_pares_aprovaveis_pedido_para_update`, `aprovar_ordem_reserva`) para retornar "nada encontrado/nada alterado", sem tocar o resto do caminho de escrita real. Aplicado nos 3 blocos `with` do teste. A cobertura da fusão em si (incluindo o caso "dentro do orçamento aprova") vive nos testes `salvar_e_aprovar_*` da Task 2, então nenhuma cobertura foi perdida.
- **Files modified:** `app/tests/test_pedidos_product_writes.py`
- **Verification:** `test_orcamento_sucessivo_edicoes_acumulam` volta a passar; os 9 testes `salvar_e_aprovar_*` provam a fusão de verdade sem essa neutralização.
- **Committed in:** `f897048` (Task 1)

**2. [Rule 1 - Bug] Dois testes de asserção exata de dict/JSON quebravam com o campo `approved_count`/`approvedCount` novo**

- **Found during:** Task 1 (arquivo do plano) e verificação da suíte completa (arquivo fora do escopo declarado)
- **Issue:** `test_batch_redistribui_dentro_da_capacidade_em_uma_transacao` (`test_pedidos_product_writes.py`) e `test_batch_grade_http_camelcase_limites_e_channel_exato` (`test_pedidos_routes.py`, fora de `files_modified` do PLAN.md) comparam o retorno/JSON por igualdade exata de dict — o campo novo (presente em todo caminho de sucesso, mesmo com valor 0) quebrava as duas asserções.
- **Fix:** Adicionado `"approved_count": 0` / `"approvedCount": 0` às duas asserções, refletindo o contrato de resposta atualizado (tipo "com adequação" nesses dois cenários, então o valor correto é sempre 0).
- **Files modified:** `app/tests/test_pedidos_product_writes.py`, `app/tests/test_pedidos_routes.py`
- **Verification:** Ambos os testes voltam a passar; suíte completa do backend sem falhas novas em relação à baseline.
- **Committed in:** `f897048` (Task 1), `9eba995` (Task 2)

---

**Total deviations:** 2 auto-fixed (2 bugs mecânicos causados diretamente pela mudança de contrato/comportamento desta task, nenhum de escopo novo)
**Impact on plan:** Nenhum impacto de escopo. Ambos os ajustes eram necessários para manter a suíte verde após o comportamento novo descrito no PLAN.md; nenhuma asserção de negócio foi enfraquecida.

## Issues Encountered

None.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- **Nome camelCase exato do campo novo (para o plano 20-06):** `approvedCount`, inteiro `>= 0`, `default 0`, dentro de `AlterarGradesProdutoResponse` (resposta de `PUT /produtos/grades`).
- Os dois endpoints de aprovação manual (`POST /produtos/aprovar`, `executar_aprovacao`) seguem existindo e byte-idênticos — `git diff` de `test_aprovacao_produto_e_idempotente_e_nao_cruza_canal` e `test_aprovacao_use_case_retry_e_expirado_nao_reemitem_eventos` vazio, ambos verdes.
- Asserção estática de autorização (T-20-23): `grep -c "Depends(require_actor)" app/modules/pedidos/infrastructure/http/routes.py` = 5, `routes.py` não foi tocado por este plano.
- Suíte completa do backend: 971 passed, 18 skipped (baseline do plano 20-04 era 962 passed, 18 skipped — diferença de exatamente 9, os testes novos deste plano; `test_pedidos_processing_sem_adequar_memory_024.py` segue ignorado, pré-existente, módulo `resource` Unix-only em ambiente Windows, não relacionado a este plano).
- `ruff check` limpo em todos os arquivos tocados (`casos_uso.py`, `schemas.py`, `test_pedidos_product_writes.py`, `test_pedidos_routes.py`).
- Nenhuma migration criada — fase não altera schema.
- Reexecutar a mesma edição já aprovada continua recusado pela checagem de janela/aprovação já existente no início da função (a OR aprovada deixa de ser editável) — sem efeito colateral novo, comportamento herdado.
- Este é o último plano da Phase 20 (Wave 3) — fase pronta para verificação.

---

_Phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ_
_Completed: 2026-09-08_

## Self-Check: PASSED

- FOUND: app/modules/pedidos/application/casos_uso.py
- FOUND: app/modules/pedidos/application/schemas.py
- FOUND: app/tests/test_pedidos_product_writes.py
- FOUND: app/tests/test_pedidos_routes.py
- FOUND commit: f897048
- FOUND commit: 9eba995
