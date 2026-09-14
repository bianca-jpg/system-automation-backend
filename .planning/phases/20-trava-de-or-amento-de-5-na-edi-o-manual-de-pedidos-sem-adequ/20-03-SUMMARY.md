---
phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ
plan: 03
subsystem: api
tags: [fastapi, sqlalchemy, postgres, domain-driven-design, orcamento, http-409]

# Dependency graph
requires:
  - phase: 20-01
    provides: "orcamento_edicao.py (calcular_contribuicao, validar_edicao_orcamento, OrcamentoPedidoExcedidoError, OrcamentoPedidoSnapshot) + repositorio_orcamento_pedido.py (carregar_orcamento_pedidos)"
  - phase: 20-02
    provides: "montar_grade_atualizada(permitir_variacao_total=...) preservando a linha de base do par via ratear_inteiro"
provides:
  - "PedidosWritePort.carregar_orcamento_pedidos / carregar_tolerancia_adequacao — acesso transacional ao orçamento e à tolerância de negócio, mesma AsyncSession da escrita"
  - "Validação de orçamento por nr_pedido dentro da seção crítica de executar_alteracao_grades_produto, depois do lock e antes de qualquer write (PD-05/PD-06/PD-07)"
  - "Branch por state.tipo em montar_grade_atualizada: permitir_variacao_total=True só para 'sem adequação'"
  - "OrcamentoPedidoExcedidoError mapeada para HTTP 409 com detail estruturado (contrato consumido pelo plano 20-06)"
affects: [20-04, 20-05, 20-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Trava de orçamento roda DEPOIS do lock global e DENTRO da mesma transação que a escrita (PD-07) — fecha a janela de corrida entre edições concorrentes de produtos diferentes do mesmo pedido"
    - "Ledger de orçamento é por nr_pedido, nunca agregado por chamada (PD-06) — um lote com N pedidos faz uma única leitura em lote, mas valida cada pedido isoladamente"
    - "Lote tudo-ou-nada (PD-05): a exceção de orçamento sobe naturalmente antes de qualquer atualizar_grades_em_lote/upsert_modificacoes_em_lote/salvar_linhas_linx/commit — sem caminho de escrita parcial"
    - "Pedido ausente da leitura de orçamento falha fechado (total_original=0), nunca 'sem restrição' (T-20-13)"
    - "Linha de base do par (fallback qt_solicitada->qt_liquida) duplicada intencionalmente em casos_uso.py em vez de importada de edicao_grade.py, porque este plano não altera aquele arquivo (acceptance criteria explícito do PLAN.md)"

key-files:
  created: []
  modified:
    - app/modules/pedidos/application/ports.py
    - app/modules/pedidos/infrastructure/write_adapter.py
    - app/modules/pedidos/application/casos_uso.py
    - app/modules/pedidos/service.py
    - app/modules/pedidos/infrastructure/http/routes.py
    - app/tests/test_pedidos_product_writes.py

key-decisions:
  - "Linha de base do par (_linha_base_par) duplicada localmente em casos_uso.py em vez de extraída para edicao_grade.py — a extração sugerida pela action do PLAN.md conflitava com o acceptance criteria explícito que exige diff vazio em domain/edicao_grade.py; escolhida a duplicação documentada com comentário cruzado, não a extração"
  - "OrcamentoPedidoExcedidoError importada por service.py diretamente de domain/orcamento_edicao.py (não via casos_uso.py) — evita import não utilizado (F401) em casos_uso.py, já que a exceção é levantada de dentro de validar_edicao_orcamento (função importada e usada), nunca por referência direta ao nome da classe"
  - "Testes de orçamento seedam somente OrdemReserva (sem linha em pedidos), deixando o total_original vir do ramo missing_from_pedidos de PEDIDO_BUDGET_SQL — o total_original passa a ser o próprio qt_solicitada do par, que o plano 20-02 mantém estável entre edições sucessivas; isso é o que permite ao teste de acúmulo (orcamento_sucessivo) provar D-06 sem precisar seedar a tabela pedidos"

patterns-established:
  - "Contrato HTTP 409 de orçamento excedido (ver seção 'Contrato do 409' abaixo), consumido pelo plano 20-06 (branch de erro no front)"

requirements-completed: [GRADE-03]

# Metrics
duration: ~45min
completed: 2026-09-08
status: complete
---

# Phase 20 Plan 03: Trava de orçamento ±5% ligada ao caminho de escrita Summary

**`executar_alteracao_grades_produto` passa a recusar, com 409 estruturado, qualquer edição manual de OR "sem adequação" que estoure o orçamento de adição ou corte do pedido — validado por `nr_pedido` dentro do lock/transação existentes, com acúmulo entre edições sucessivas e lote tudo-ou-nada; "com adequação" continua byte-idêntico.**

## Performance

- **Duration:** ~45 min
- **Tasks:** 3/3
- **Files modified:** 6 (nenhum arquivo criado)

## Accomplishments

- `PedidosWritePort` ganha `carregar_orcamento_pedidos` e `carregar_tolerancia_adequacao`; `SqlAlchemyPedidosWriteUnitOfWork` os implementa delegando ao repositório do plano 20-01 e ao `param_service` (mesmo padrão de `load_adequation_config`), sempre na mesma `AsyncSession` da unidade de trabalho — nenhum tipo de infraestrutura vaza para o `Protocol`.
- `executar_alteracao_grades_produto` ganha um bloco de validação entre a checagem de estoque e a reconstrução da grade: monta o conjunto de `nr_pedido` com `tipo="sem"`, pula o bloco inteiro se vazio (D-01: zero query nova no caminho "com adequação"), lê tolerância + orçamento uma única vez por lote, e valida cada pedido individualmente via `calcular_contribuicao`/`validar_edicao_orcamento` (rewind + recobrança por estado final, do plano 20-01).
- `montar_grade_atualizada` recebe `permitir_variacao_total=(state.tipo == "sem")` — só ORs "sem adequação" podem mudar o total; "com adequação" preserva a exceção `ValueError` de sempre.
- `OrcamentoPedidoExcedidoError` mapeada para HTTP 409 com `detail` estruturado (contrato documentado abaixo) em `routes.py`, re-exportada por `service.py`.
- 8 testes novos em `test_pedidos_product_writes.py` provando: sucesso no teto exato, estouro de adição, estouro de corte (D-08, sem compensação), acúmulo entre duas edições sucessivas do mesmo pedido (o teste crítico da fase — pega double-spend se a rewind falhar), lote com um pedido estourando recusa os dois (PD-05), independência de orçamento entre pedidos do mesmo lote (PD-06), corrida bloqueada no lock global (T-20-10/D-07), e regressão "com adequação" sem asserção de orçamento (D-01/D-11).

## Task Commits

1. **Task 1: Port e adapter ganham acesso transacional ao orçamento e à tolerância** - `f5db455` (feat)
2. **Task 2: Validação de orçamento por pedido na seção crítica, com branch por tipo e 409 estruturado** - `37aef0c` (feat)
3. **Task 3: Gate de cobertura — edições sucessivas, lote multi-pedido, concorrência e regressão "com adequação"** - `85c2f39` (test)

## Contrato do 409 (consumido pelo plano 20-06)

`PUT /produtos/grades` responde `409` com `detail` estruturado quando `OrcamentoPedidoExcedidoError` é levantada:

```json
{
  "code": "orcamento_pedido_excedido",
  "message": "Pedido 12345: orçamento de adição excedido — restam 2 peça(s) disponíveis para adicionar, mas a edição pede 6 peça(s).",
  "nrPedido": 12345,
  "orcamento": "adicao",
  "restanteAdicao": 2,
  "restanteCorte": 5
}
```

- `code` é o identificador de máquina estável — o único campo que o front deve usar para decidir a branch de erro (não parsear `message`).
- `orcamento` é `"adicao"` ou `"corte"` — qual dos dois tetos estourou.
- `restanteAdicao`/`restanteCorte` são os restantes ANTES da tentativa (peças), úteis para a UI sugerir o valor máximo aceitável.
- `nrPedido` identifica qual cliente do lote estourou (necessário porque um lote edita até 100 clientes de uma vez — PD-05).
- As outras cláusulas HTTP existentes (`404` para pedido não encontrado, `409` genérico para `ProcessamentoEmAndamentoError`/`ProdutoBatchConflitoError` com `detail` string) não mudaram.

## Files Created/Modified

- `app/modules/pedidos/application/ports.py` - dois métodos novos no `Protocol PedidosWritePort`
- `app/modules/pedidos/infrastructure/write_adapter.py` - implementações delegando ao repositório (20-01) e ao `param_service`
- `app/modules/pedidos/application/casos_uso.py` - bloco de validação de orçamento na seção crítica, `_linha_base_par` (duplicação intencional documentada), branch `permitir_variacao_total` por `state.tipo`, helpers `carregar_orcamento_pedidos`/`carregar_tolerancia_adequacao` (padrão thin-delegate)
- `app/modules/pedidos/service.py` - re-exporta `OrcamentoPedidoExcedidoError` (importada direto de `domain/orcamento_edicao.py`)
- `app/modules/pedidos/infrastructure/http/routes.py` - cláusula `except` nova, antes das existentes, mapeando para 409 com `detail` estruturado
- `app/tests/test_pedidos_product_writes.py` - `_state` ganha parâmetro `tipo` (default `"com"`, aditivo); 8 testes novos de orçamento + helpers `_patches_edicao_grade`/`_run_edicao_grade`/`_limpar_or`

## Decisions Made

Ver `key-decisions` no frontmatter. Resumo:

- Duplicação deliberada de `_linha_base_par` em vez de extração para `domain/edicao_grade.py`, para respeitar o acceptance criteria do PLAN.md que exige diff vazio naquele arquivo neste plano.
- `service.py` importa `OrcamentoPedidoExcedidoError` diretamente do domínio (não via `casos_uso.py`), evitando um import não utilizado — a exceção nunca é referenciada pelo nome dentro de `casos_uso.py`, só levantada de dentro de `validar_edicao_orcamento`.
- Testes de orçamento seedam só `OrdemReserva` (sem `pedidos`), usando o ramo `missing_from_pedidos` para derivar `total_original` do próprio `qt_solicitada` do par — mantém o teste simples e prova D-06/PD-02 sem precisar simular pedidos multi-produto.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Import ordering (`ruff I001`) em `write_adapter.py` e `service.py`**

- **Found during:** Verificação da Task 1 e da Task 2
- **Issue:** Novos imports adicionados fora da ordem alfabética exigida pelo `ruff` (isort embutido)
- **Fix:** `ruff check --fix` nos dois arquivos — reordenação automática, nenhuma mudança de comportamento
- **Files modified:** `app/modules/pedidos/infrastructure/write_adapter.py`, `app/modules/pedidos/service.py`
- **Verification:** `ruff check` limpo em ambos após o fix
- **Committed in:** `f5db455` (Task 1), `37aef0c` (Task 2)

---

**Total deviations:** 1 auto-fixed (1 blocking, puramente estilístico)
**Impact on plan:** Nenhum impacto de escopo ou comportamento — apenas formatação de imports.

## Issues Encountered

None.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- Contrato do 409 (código `orcamento_pedido_excedido`, shape do `detail`) documentado acima e pronto para o plano 20-06 consumir no front.
- `PedidosWritePort.carregar_orcamento_pedidos`/`carregar_tolerancia_adequacao` disponíveis para qualquer caso de uso futuro que precise ler o mesmo orçamento (ex.: plano 20-04, API de leitura sob demanda, se ainda depender deste port em vez de ler direto do repositório).
- Suíte completa do backend: 957 passed, 18 skipped, 0 failed (baseline pré-plano: 949 passed, 18 skipped — 8 testes novos, nenhuma falha nova; `test_pedidos_processing_sem_adequar_memory_024.py` segue ignorado por depender do módulo `resource`, Unix-only, pré-existente e não relacionado a este plano).
- `ruff check app/` limpo.
- Nenhuma migration criada — `alembic heads` inalterado (`035`).
- Nenhum campo novo em `AlterarGradesProdutoRequest` — `git diff app/modules/pedidos/application/schemas.py` vazio.
- Nenhuma alteração em `domain/edicao_grade.py` nem `infrastructure/repositorio_orcamento_pedido.py` — este plano só consumiu os artefatos dos planos 20-01/20-02.

---

_Phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ_
_Completed: 2026-09-08_

## Self-Check: PASSED

- FOUND: app/modules/pedidos/application/ports.py
- FOUND: app/modules/pedidos/infrastructure/write_adapter.py
- FOUND: app/modules/pedidos/application/casos_uso.py
- FOUND: app/modules/pedidos/service.py
- FOUND: app/modules/pedidos/infrastructure/http/routes.py
- FOUND: app/tests/test_pedidos_product_writes.py
- FOUND commit: f5db455
- FOUND commit: 37aef0c
- FOUND commit: 85c2f39
