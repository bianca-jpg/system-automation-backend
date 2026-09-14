---
phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio
plan: 02
subsystem: testing
tags: [pytest, domain-testing, motor-adequacao, fixtures, invariantes]

# Dependency graph
requires:
  - phase: 14-01
    provides: "ratear_hamilton promovido a utilitário público de domínio (app/modules/pedidos/domain/rateio.py)"
provides:
  - "item_pedido / estoque_produto / estoque_por_canal — factories compartilhadas de item de pedido e estoque por canal (app/tests/factories_pedidos_motor.py)"
  - "cenario_disputa_mantenedora — fixture do cenário numérico obrigatório (cliente A atendido / cliente B em stand by, estoque=20)"
  - "10 helpers de asserção das invariantes I1-I9 + oráculo de furo de grade (app/tests/invariantes_motor_adequacao.py)"
affects: ["14-03", "14-04", "14-05", "14-06", "14-07", "19 (testes de propriedade)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Factories de teste de domínio compartilhadas entre planos de uma fase, sem duplicar montagem de dict à mão"
    - "Helpers de invariante como AssertionError com mensagem prefixada 'I{n} violada: ', importáveis por testes de propriedade futuros (Phase 19)"

key-files:
  created:
    - app/tests/factories_pedidos_motor.py
    - app/tests/test_factories_pedidos_motor.py
    - app/tests/cenarios_motor_adequacao.py
    - app/tests/test_cenario_disputa_mantenedora.py
    - app/tests/invariantes_motor_adequacao.py
    - app/tests/test_invariantes_motor_adequacao.py
  modified: []

key-decisions:
  - "assert_sem_credito_nunca_alocado (I2) verifica o par (nr_pedido, cd_prod_cor) contra o conjunto de nr_pedido sem crédito, já que selecionados/pares_processados são pares e não nr_pedido isolado"
  - "calcular_furo_de_grade descarta índices 999 (tamanho desconhecido) ANTES de calcular extremos e interior, para não deixar um tamanho desconhecido definir falsamente um extremo ou um tamanho real ser tratado como 'interior' por engano"
  - "assert_rateio_sem_drift converte cada vl_liquido via Decimal(str(...)) item a item, nunca Decimal(float) direto, para não herdar imprecisão binária"

patterns-established:
  - "Módulos de apoio em app/tests/ que não começam com test_ (não coletados pelo pytest), com __all__ explícito, seguindo o precedente de rateio.py/edicao_grade.py"

requirements-completed: []

# Metrics
duration: ~35min (retomada; execução original interrompida)
completed: 2026-08-17
---

# Phase 14 Plan 02: Infraestrutura de teste do motor de adequação Summary

**Factories compartilhadas de item/estoque, fixture do cenário "disputa da mantenedora" e 10 helpers de asserção das invariantes I1-I9 (com self-teste passa/falha cada), prontos para os planos 14-03 a 14-07 e para os testes de propriedade da Phase 19.**

## Performance

- **Tasks:** 3/3 concluídas
- **Files created:** 6

## Accomplishments
- `item_pedido`, `estoque_produto`, `estoque_por_canal` — factory pública e compartilhada, substituindo a necessidade de montar dicts à mão em cada plano seguinte
- Fixture `cenario_disputa_mantenedora` codifica e comprova, contra o motor ATUAL (modo adequação), o cenário numérico exato ditado pela mantenedora: cliente A (1.000 pçs / R$ 50.000, disputa 10 peças) atendido; cliente B (50 pçs / R$ 1.000, disputa 15 peças) em stand by; estoque do produto disputado = 20
- 10 helpers de asserção (`assert_estoque_nunca_excedido`, `assert_sem_credito_nunca_alocado`, `assert_tudo_ou_nada_preserva_quantidade`, `calcular_furo_de_grade`, `assert_furo_de_grade_nao_selecionado`, `assert_orcamento_nao_excedido`, `assert_execucoes_identicas`, `assert_rateio_sem_drift`, `assert_minimo_viavel_antes_de_extra`, `assert_contrato_chaves_resultado`) cobrindo I1-I9, cada um com pelo menos um caso que passa e um que falha (`pytest.raises(AssertionError)`)
- Oráculo `calcular_furo_de_grade` comprovado nos 4 casos de borda do 14-CONTEXT.md (tamanho único, dois adjacentes, PP+GG sem M, tamanho desconhecido idx 999) mais o caso positivo (tamanho do meio zerado)

## Task Commits

Cada task foi commitada atomicamente. Task 1 já estava concluída e commitada por um executor anterior antes desta retomada; Tasks 2 e 3 foram fechadas nesta sessão:

1. **Task 1: Factory de item de pedido e de estoque por canal** - `bd931af` (feat) — commitado por execução anterior
2. **Task 2: Fixture do cenário nomeado — disputa da mantenedora** - `ce4a3d5` (feat) — trabalho já estava pronto (não commitado) ao retomar; revisado contra o plano nesta sessão e commitado sem alterações
3. **Task 3: Helpers de asserção para as invariantes I1-I9** - `78a2f92` (feat) — implementado do zero nesta sessão

**Plan metadata:** (este commit)

## Files Created/Modified
- `app/tests/factories_pedidos_motor.py` - `item_pedido`, `estoque_produto`, `estoque_por_canal`
- `app/tests/test_factories_pedidos_motor.py` - self-teste das três factories
- `app/tests/cenarios_motor_adequacao.py` - `cenario_disputa_mantenedora` + constantes (`NR_PEDIDO_CLIENTE_A`, `NR_PEDIDO_CLIENTE_B`, `PRODUTO_DISPUTADO`, `TAMANHO_DISPUTADO`, `CANAL_CENARIO`, `ESTOQUE_DISPUTADO`)
- `app/tests/test_cenario_disputa_mantenedora.py` - prova contra o motor atual (A atendido, B em stand by)
- `app/tests/invariantes_motor_adequacao.py` - 10 helpers de asserção (I1-I9 + oráculo de furo de grade)
- `app/tests/test_invariantes_motor_adequacao.py` - 26 testes, self-teste passa/falha de cada invariante e dos 4 casos de borda + 1 positivo do oráculo

## Decisions Made
- `assert_sem_credito_nunca_alocado` (I2): como `selecionados`/`pares_processados` guardam pares `(nr_pedido, cd_prod_cor)` e não `nr_pedido` isolado, o helper checa `par[0]` contra o conjunto de pedidos sem crédito (calculado via `is_sem_credito` sobre `status_credito` de cada item de `dados`)
- `calcular_furo_de_grade` descarta índices `999` (tamanho desconhecido, via `get_tamanho_idx`) **antes** de calcular o menor/maior índice — evita que um tamanho desconhecido defina um extremo falso ou que um tamanho real seja tratado erroneamente como "interior" só por estar entre um extremo real e um extremo espúrio de 999 (caso coberto pelo teste `test_calcular_furo_de_grade_tamanho_desconhecido_nao_conta_como_interior`)
- `assert_rateio_sem_drift` soma via `Decimal(str(item["vl_liquido"]))` item a item, nunca `Decimal(float)` direto, para não herdar imprecisão binária de ponto flutuante — conforme explicitado no plano

## Deviations from Plan

None - plan executado exatamente como escrito. Task 2 já estava implementada por um executor interrompido anteriormente; foi revisada linha a linha contra os critérios de aceite do `14-02-PLAN.md` (constantes, matemática do cenário 1000pçs/R$50.000 vs 50pçs/R$1.000, formato de retorno) e nenhuma lacuna foi encontrada — apenas commitada nesta sessão.

## Issues Encountered

Execução anterior foi interrompida entre a Task 2 (trabalho pronto, mas não commitado) e a Task 3 (não iniciada). Esta sessão retomou lendo o estado real do working tree e do git, confirmou que os testes da Task 2 já passavam, commitou-a sem modificação, e implementou a Task 3 (helpers de invariante + self-teste) do zero.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `app/tests/factories_pedidos_motor.py`, `app/tests/cenarios_motor_adequacao.py` e `app/tests/invariantes_motor_adequacao.py` são importáveis por qualquer plano seguinte (`14-03` a `14-07`) via `app.tests.<modulo>`, sem precisar recriar nenhum helper.
- Suíte completa (Docker): `1 failed, 792 passed, 16 skipped` — o único failed é o conhecido e pré-existente `app/tests/test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (falha de teardown de conexão / pool assíncrono, sem relação com esta fase). Nenhuma regressão introduzida.
- Nenhum arquivo de produção foi tocado (`git status --short app/modules` mostra apenas o trabalho pré-existente e não relacionado de SSO Microsoft); `app/tests/test_pedidos_motor.py` não foi editado.
- Bloqueio conhecido para os próximos planos: nenhum. `14-03` pode consumir diretamente as factories e os helpers de invariante entregues aqui.

---
*Phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio*
*Completed: 2026-08-17*
