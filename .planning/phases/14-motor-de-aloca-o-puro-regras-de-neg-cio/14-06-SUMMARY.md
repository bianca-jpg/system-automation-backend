---
phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio
plan: 06
subsystem: domain
tags: [python, dataclass, ddd, pedidos, motor-adequacao, orcamento, rateio-hamilton]

# Dependency graph
requires:
  - phase: 14-01
    provides: "ratear_hamilton em domain/rateio.py, pronta para ligar ao recálculo financeiro do motor"
  - phase: 14-04
    provides: "tem_furo_de_grade ligado dentro do laço de decisão por par, entre crédito e política de quantidade"
  - phase: 14-05
    provides: "ModoAdequacao (enum de domínio) e despacho por modo (SEM_ADEQUAR via aplicar_tudo_ou_nada) já ligados em processar_pedidos/_processar_pedidos_canal"
provides:
  - "OrcamentoPedido: ledger mutável do orçamento ±5% do pedido completo, com dois orçamentos independentes (adição/corte), floor inteiro, consumo tudo-ou-nada (corte) e parcial (adição)"
  - "Laço pedido-externo/produto-interno em _processar_pedidos_canal, com o ledger instanciado uma vez por pedido"
  - "Duas passadas determinísticas: passada 1 (mínimo viável, corte necessário crescente) via adequar_grade_produto(ledger); passada 2 (extras, preço unitário decrescente) via nova conceder_adicao_pedido(ledger)"
  - "Recálculo financeiro via ratear_hamilton (_recalcular_financeiro_produto), substituindo o round() item a item — FIX-02 fechado"
  - "3 parâmetros opcionais keyword-only em processar_pedidos/_processar_pedidos_canal (total_original_por_pedido, consumido_previo_adicao_por_pedido, consumido_previo_corte_por_pedido) com placeholder documentado e logger.warning citando ALOC-09 quando usado"
affects: ["14-07 (migração consciente dos 5 testes com quebra esperada)", "15 (liga os parâmetros opcionais de orçamento aos dados reais lidos do banco, fechando ALOC-09 ponta a ponta)", "18 (ponto de extensão ALOC-11 comentado explicitamente em conceder_adicao_pedido)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Ledger de domínio mutável (slots=True, não frozen) com 4 campos obrigatórios sem default, para tornar o esquecimento de dados de I/O um TypeError em vez de um double-spend silencioso"
    - "Laço externo por entidade-dona-do-invariante (pedido) em vez de por entidade-dona-do-recurso-compartilhado (produto/estoque) — os dois produzem a mesma alocação de estoque quando a partição do recurso já é independente por chave"
    - "Duas passadas com pré-computação de chave de ordenação fora do sorted() — evita recálculo O(n²) dentro da função-chave (T-14-06-01)"
    - "Fallback opcional keyword-only + logger.warning agregado (uma vez por execução, não por item) como técnica para tornar uma lacuna de I/O deliberadamente adiada observável em produção"

key-files:
  created:
    - app/modules/pedidos/domain/orcamento_pedido.py
    - app/tests/test_pedidos_orcamento_pedido.py
  modified:
    - app/modules/pedidos/domain/motor_adequacao.py
    - app/tests/test_pedidos_motor.py
    - app/tests/test_cenario_disputa_mantenedora.py

key-decisions:
  - "Loop invertido para pedido-externo/produto-interno: a partição de estoque por produto já era independente entre produtos, então a inversão não muda a alocação de estoque — só torna o ledger local à iteração do pedido, eliminando a necessidade de um dict externo indexado por nr_pedido"
  - "Passada 1 ordenada por corte necessário CRESCENTE (maximiza quantos produtos são atendidos dentro do orçamento fixo de corte); passada 2 por preço unitário DECRESCENTE (maximiza R$ por peça extra do orçamento de adição compartilhado); ambas com desempate por cd_prod_cor ascendente, pré-computadas antes do sorted"
  - "adequar_grade_produto passa a fazer SÓ a decisão de corte (passada 1) e não recalcula mais vl_liquido/diff_valor internamente — isso migrou para _recalcular_financeiro_produto (FIX-02), aplicado depois das duas passadas"
  - "Fallback de ALOC-09 emitido uma vez por chamada de _processar_pedidos_canal (por canal), não por pedido — evita inundar o log em cenários de 100.000 pares, mantendo a semântica de 'gap observável, não silencioso'"
  - "test_cenario_disputa_mantenedora.py atualizado (fora da lista de arquivos do plano, mas dentro do escopo real do trabalho): o produto disputado do cliente A passa a receber orçamento de adição do PEDIDO COMPLETO (1000 peças) em vez do produto isolado (10 peças) — resultado correto da regra nova (ALOC-07), não uma regressão. Documentado com comentário explicando a mudança de base do orçamento"
  - "test_adequar_grade_produto_estoque_suficiente_sem_ajuste (test_pedidos_motor.py) migrado para a nova assinatura (ledger em vez de tolerancia) — não era um dos 3 'regra antiga' identificados por 14-RESEARCH.md, mas quebrava mecanicamente pela mesma mudança de assinatura; como seu cenário (estoque suficiente, sem ajuste) é compatível com o comportamento novo, foi atualizado em vez de deixado quebrado"

patterns-established:
  - "Ledger de domínio com campos obrigatórios sem default como mecanismo de proteção contra esquecimento silencioso de dados de I/O em uma fronteira de camada"
  - "Placeholder de orquestração + logger.warning agregado como técnica para fronteiras de I/O deliberadamente adiadas entre fases"

requirements-completed: [ALOC-07, ALOC-08, ALOC-09, ALOC-10, FIX-02]

# Metrics
duration: ~100min
completed: 2026-08-17
---

# Phase 14 Plan 06: Ledger de orçamento ±5% por pedido completo, duas passadas e rateio Hamilton Summary

**`OrcamentoPedido` (ledger mutável, floor inteiro, dois orçamentos independentes) ligado ao motor via laço invertido pedido-externo/produto-interno, com passada 1 (mínimo viável, corte crescente) e passada 2 (extras, preço decrescente) determinísticas, e `ratear_hamilton` fechando o drift de centavos no recálculo financeiro.**

## Performance

- **Duration:** ~100 min
- **Started:** 2026-08-17T00:00:00Z (aprox.)
- **Completed:** 2026-08-17T00:00:00Z (aprox.)
- **Tasks:** 2 completed (Task 1 com ciclo RED/GREEN)
- **Files modified:** 5 (2 novos, 3 editados)

## Accomplishments

- `OrcamentoPedido` criado em `app/modules/pedidos/domain/orcamento_pedido.py`: ledger mutável (`@dataclass(slots=True)`, não `frozen`) com 4 campos obrigatórios sem default (`nr_pedido`, `total_original`, `consumido_previo_adicao`, `consumido_previo_corte`), `limite_adicao`/`limite_corte` via `(total * 5) // 100` (floor inteiro, nunca `ceil`/`float`), `consumir_corte` tudo-ou-nada, `consumir_adicao` parcial saturando em 0, e `consumido_*_total` para encadear execuções sucessivas.
- `_processar_pedidos_canal` reescrita com laço pedido-externo/produto-interno: um `OrcamentoPedido` é instanciado uma vez por pedido, no início da iteração — a partição de estoque por produto (`estoque_locais`) continua sendo uma única cópia mutável por produto, decrementada conforme os pedidos avançam em ordem de prioridade global, exatamente como no desenho produto-externo anterior.
- `adequar_grade_produto` reescrita para receber `ledger: OrcamentoPedido` em vez de `tolerancia: float`, delegando a decisão de corte a `ledger.consumir_corte` (tudo-ou-nada) — sem mais `math.ceil`/`math.floor` locais.
- Nova `conceder_adicao_pedido` (passada 2): concede peças extra aos tamanhos de fronteira (`is_ext`, com `get_tamanho_idx` filtrando 999) dos produtos com folga total da passada 1, consumindo `ledger.consumir_adicao` (parcial, nunca mais do que o espaço de estoque disponível). Ponto de extensão ALOC-11 (Phase 18) comentado explicitamente no código.
- `_recalcular_financeiro_produto` liga `ratear_hamilton` ao recálculo de `vl_liquido`, substituindo o `round()` item a item — a soma dos itens bate exatamente com o total recalculado (FIX-02).
- `processar_pedidos`/`_processar_pedidos_canal` ganham 3 parâmetros opcionais keyword-only (`total_original_por_pedido`, `consumido_previo_adicao_por_pedido`, `consumido_previo_corte_por_pedido`); ausentes (todo chamador hoje), caem no placeholder documentado (soma do snapshot desta execução, consumido prévio = 0) com **fallback ruidoso**: `logger.warning` citando `ALOC-09` uma vez por execução de canal, provado por teste com `caplog` (sai sem os parâmetros, não sai com eles fornecidos).
- Suíte completa via Docker: **820 passed, 16 skipped, 6 failed** — as 6 falhas são exatamente as antecipadas por `14-VALIDATION.md`/`14-RESEARCH.md` (ver seção "Falhas conhecidas/aceitas" abaixo). Nenhuma regressão fora dessa lista.

## Task Commits

1. **Task 1 (RED): esqueleto de `OrcamentoPedido` + suíte de testes** - `fb023d1` (test)
2. **Task 1 (GREEN): implementação de `OrcamentoPedido`** - `f6cabad` (feat)
3. **Task 2: duas passadas + rateio Hamilton ligados ao motor (inversão de loop)** - `b90695e` (feat)

_Task 1 seguiu o ciclo TDD RED/GREEN completo (frontmatter `tdd="true"`). Task 2 combinou a reescrita do motor com os testes novos/atualizados de `test_pedidos_motor.py` e `test_cenario_disputa_mantenedora.py` em um único commit, dado o acoplamento estreito entre a mudança de assinatura e os cenários que a provam — RED foi verificado manualmente executando a suíte contra o código antigo antes da reescrita (as falhas anticipadas de `test_pedidos_motor.py -k "orcamento or permutacao"` não existiam ainda como testes, e o cenário e2e/permutação foi escrito e confirmado falhar contra o motor antigo antes de reescrever `_processar_pedidos_canal`)._

## Files Created/Modified

- `app/modules/pedidos/domain/orcamento_pedido.py` - Novo. `OrcamentoPedido`, ledger mutável do orçamento ±5% do pedido completo.
- `app/tests/test_pedidos_orcamento_pedido.py` - Novo. 10 testes unitários do ledger, isolados do motor.
- `app/modules/pedidos/domain/motor_adequacao.py` - Reescrita: laço pedido-externo/produto-interno, `adequar_grade_produto` ligada ao ledger, nova `conceder_adicao_pedido`, `_recalcular_financeiro_produto` (Hamilton), 3 parâmetros opcionais de orçamento + fallback ruidoso.
- `app/tests/test_pedidos_motor.py` - `test_adequar_grade_produto_falta_alem_da_tolerancia_vira_stand_by` substituído por cenário ponta a ponta "orçamento atravessa 3 produtos"; novo teste de permutação; 2 testes do fallback ALOC-09 (`caplog`); `test_adequar_grade_produto_estoque_suficiente_sem_ajuste` migrado para a nova assinatura.
- `app/tests/test_cenario_disputa_mantenedora.py` - Assertiva do cenário ADEQUAR atualizada (qty do produto disputado do cliente A: 10 -> 20) para refletir a regra nova de ALOC-07 (orçamento de adição do pedido completo, não do produto isolado).

## Decisions Made

Ver `key-decisions` no frontmatter. Resumo:

1. **Inversão de loop pedido-externo/produto-interno**, confirmada segura porque a partição de estoque por produto já era independente e a ordem de prioridade é calculada uma única vez a partir do snapshot completo.
2. **Ordem das duas passadas**: corte necessário crescente (passada 1, mínimo viável) e preço unitário decrescente (passada 2, extras), ambas pré-computadas fora da chave do `sorted` (evita o DoS quadrático do threat register T-14-06-01) e com desempate por `cd_prod_cor` ascendente (garante a invariante de permutação).
3. **`adequar_grade_produto` não recalcula mais finanças** — separação limpa entre "decidir quantidade" (passada 1/2) e "recalcular valor" (Hamilton), evitando duplicar a lógica de rateio em dois lugares.
4. **Fallback de ALOC-09 por execução de canal**, não por pedido — mantém o log utilizável em volume alto sem perder a garantia de observabilidade.
5. **`test_cenario_disputa_mantenedora.py` atualizado** apesar de não estar no `files_modified` do plano — a mudança de base do orçamento (ALOC-07: pedido completo, não produto isolado) altera corretamente o resultado esperado desse cenário específico (produto com folga recebe extra do orçamento do pedido inteiro, não mais do produto isolado). Deixar o teste quebrado seria documentar uma falsa regressão; atualizá-lo com o comentário explicativo documenta a regra nova corretamente.
6. **`test_adequar_grade_produto_estoque_suficiente_sem_ajuste` migrado**, não deixado quebrado — apesar de não estar nos "3 testes de regra antiga" nomeados por `14-RESEARCH.md`, ele quebrava pela mesma causa raiz (assinatura de `adequar_grade_produto`) e seu cenário (sem ajuste necessário) continua válido sob a regra nova; migrá-lo evita um 7º falso-positivo na lista de exceções da wave.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug de expectativa, não de código] `test_cenario_disputa_mantenedora.py` — resultado esperado desatualizado pela mudança de base do orçamento (ALOC-07)**
- **Found during:** Task 2, ao rodar a suíte completa após a reescrita do motor.
- **Issue:** O teste `test_cenario_disputa_mantenedora_cliente_a_atendido_cliente_b_stand_by` assumia que o orçamento de adição do produto disputado do cliente A era calculado sobre a grade DAQUELE produto isolado (10 peças, orçamento efetivo = 0 sob a regra antiga) — sob a regra nova (ALOC-07: base = pedido completo, 1000 peças), o mesmo produto tem folga e é elegível à passada 2, recebendo as 10 peças de espaço extra do orçamento de adição do pedido inteiro (`floor(1000 * 5%) = 50`).
- **Fix:** Assertiva atualizada de `qt_liquida == 10` para `qt_liquida == 20` (mais `qt_solicitada == 10` para preservar a quantidade originalmente pedida), com comentário explicando a mudança de regra.
- **Files modified:** `app/tests/test_cenario_disputa_mantenedora.py`
- **Verification:** `docker compose exec api uv run pytest app/tests/test_cenario_disputa_mantenedora.py -q` — 2 passed.
- **Commit:** `b90695e` (parte do commit da Task 2)

**2. [Rule 3 - Blocking, quebra mecânica não antecipada] `test_adequar_grade_produto_estoque_suficiente_sem_ajuste` migrado para a nova assinatura**
- **Found during:** Task 2, ao rodar a suíte completa.
- **Issue:** Este teste chama `adequar_grade_produto` diretamente com `tolerancia=0.05` (assinatura antiga) — quebra com `TypeError` pela mudança de assinatura mandatada pelo plano (`ledger` em vez de `tolerancia`), mas não era um dos 3 "testes de regra antiga" nomeados por `14-RESEARCH.md`/`14-VALIDATION.md` (seu cenário — estoque suficiente, sem corte nem extra — continua válido sob a regra nova).
- **Fix:** Teste atualizado para construir um `OrcamentoPedido` e chamar a nova assinatura; assertivas ajustadas para o novo contrato de `adequar_grade_produto` (que não recalcula mais `vl_liquido`/`diff_valor` — isso migrou para `_recalcular_financeiro_produto`).
- **Files modified:** `app/tests/test_pedidos_motor.py`
- **Verification:** `docker compose exec api uv run pytest app/tests/test_pedidos_motor.py::test_adequar_grade_produto_estoque_suficiente_sem_ajuste -q` — passed.
- **Commit:** `b90695e` (parte do commit da Task 2)

## Falhas conhecidas/aceitas (suíte completa: 6 failed)

Exatamente as 6 falhas antecipadas por `14-VALIDATION.md`/`14-PLAN.md` para esta wave (a única da fase autorizada a terminar vermelha):

| # | Teste | Categoria | Motivo |
|---|---|---|---|
| 1 | `test_pedidos_motor.py::test_adequar_grade_produto_aumenta_extremos_dentro_do_orcamento` | Regra antiga | Provava tolerância `ceil`/`floor` por PRODUTO ISOLADO com `tolerancia` custom (0.1) — regra que deixou de existir (ALOC-07/08: base é o pedido completo, orçamento fixo em 5%, floor nos dois lados). Migração consciente é escopo do 14-07. |
| 2 | `test_pedidos_motor.py::test_adequar_grade_produto_falta_dentro_da_tolerancia_reduz_parcial` | Regra antiga | Mesma causa — `tolerancia` custom (0.3) por produto isolado. Migração no 14-07. |
| 3 | `test_pedidos_motor.py::test_processar_pedidos_prioriza_maior_valor_e_isola_sem_credito` | Regra antiga | Assertiva `"motivo_stand_by" not in item2` — sob a regra nova, o stand-by por orçamento de corte esgotado passa por `marcar_stand_by` (que sempre inclui `motivo_stand_by`, consistente com furo de grade e crédito), não mais por uma ramificação inline sem esse campo. Migração no 14-07. |
| 4 | `test_pedidos_motor_performance_024.py::test_indice_visita_estoque_uma_vez_e_copia_apenas_subset_do_produto[10]` | Assinatura fixa (monkeypatch) | Substituto de `adequar_grade_produto` sem parâmetro `cancel_token`, recebe `ledger` na posição de `tolerancia`. Conserto no 14-07. |
| 5 | `test_pedidos_motor_performance_024.py::test_indice_visita_estoque_uma_vez_e_copia_apenas_subset_do_produto[100]` | Assinatura fixa (monkeypatch) | Mesma causa, segunda parametrização. Conserto no 14-07. |
| 6 | `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` | Pré-existente e conhecida | `RuntimeError: Event loop is closed` no teardown de conexão — presente desde a linha de base da fase (14-01), sem relação com o motor de adequação. |

**Nota:** `test_pedidos_processing_cancellation_024.py::test_cpu_planning_cancel_stops_thread_before_remaining_products` (o segundo arquivo de "assinatura fixa" citado no plano) **NÃO quebrou** — seu substituto de `adequar_grade_produto` aceita `cancel_token` e repassa o 4º argumento posicionalmente ao `original_grade` capturado antes do monkeypatch; como Python não valida o nome do parâmetro na chamada posicional, o `ledger` chega corretamente à implementação real. Confirmado verde na suíte completa.

**Não consertei** nenhuma das 6 falhas acima (fora de escopo, per `<quebra_esperada_e_declarada>` do plano) — apenas as 2 falhas adicionais fora dessa lista original (`test_cenario_disputa_mantenedora` e `test_adequar_grade_produto_estoque_suficiente_sem_ajuste`), que eram consequências mecânicas diretas e não regressões de lógica, foram corrigidas (ver "Deviations from Plan" acima).

## Known Stubs

Nenhum. O ponto de extensão da Phase 18 (ALOC-11 — distribuir peças extras nos tamanhos com mais sobra) está documentado como comentário explícito em `conceder_adicao_pedido`, não como stub de dados (o mecanismo de fronteira `is_ext` é funcional e testado, apenas não é a heurística final).

## Threat Flags

Nenhum novo — o threat register do plano (T-14-06-01 a T-14-06-05) já cobre a superfície introduzida (DoS via sort quadrático, integridade do ledger, integridade de estoque, drift de rateio, e o risco residual documentado de ALOC-09 sem prova ponta a ponta nesta fase).

## Issues Encountered

`ruff check`/`ruff format` rodados localmente (fora do container Docker, que não tem o binário disponível — mesma limitação documentada em 14-04) sobre os 5 arquivos tocados; 1 import mal-ordenado corrigido automaticamente (`--fix`), sem alteração de comportamento.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Núcleo da Fase 14 fechado: ALOC-07, ALOC-08, ALOC-10 e FIX-02 completos; ALOC-09 tem o MECANISMO provado por teste (o ledger nunca reseta nem excede o limite entre construções sucessivas) mas a prova ponta a ponta via `processar_pedidos` com dados reais fica para a Phase 15 (que já carrega essa herança documentada em `ROADMAP.md`).
- Próximo plano: `14-07` — migração consciente dos 2 arquivos de assinatura fixa (`test_pedidos_motor_performance_024.py`, `test_pedidos_processing_cancellation_024.py`, este último possivelmente sem mudança necessária) e dos 3 testes de regra antiga em `test_pedidos_motor.py`.
- `processar_pedidos`/`_processar_pedidos_canal` mantêm as 5 chaves de retorno exatas (`resultados`, `selecionados`, `preteridos`, `bloqueados_credito`, `pares_processados`) — nenhum consumidor downstream (`processing/domain.py`) precisa de alteração.

---
*Phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio*
*Completed: 2026-08-17*

## Self-Check: PASSED

- FOUND: app/modules/pedidos/domain/orcamento_pedido.py
- FOUND: app/tests/test_pedidos_orcamento_pedido.py
- FOUND: app/modules/pedidos/domain/motor_adequacao.py
- FOUND: app/tests/test_pedidos_motor.py
- FOUND: app/tests/test_cenario_disputa_mantenedora.py
- FOUND: .planning/phases/14-motor-de-aloca-o-puro-regras-de-neg-cio/14-06-SUMMARY.md
- FOUND commit: fb023d1
- FOUND commit: f6cabad
- FOUND commit: b90695e
