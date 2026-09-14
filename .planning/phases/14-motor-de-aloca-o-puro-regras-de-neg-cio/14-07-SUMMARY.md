---
phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio
plan: 07
subsystem: testing
tags: [pytest, monkeypatch, orcamento-pedido, regressao, motor-adequacao]

# Dependency graph
requires:
  - phase: 14-06
    provides: "OrcamentoPedido ligado ao motor via ledger por pedido completo (ledger em vez de tolerancia em adequar_grade_produto), 6 falhas antecipadas e documentadas"
provides:
  - "test_pedidos_motor.py migrado por completo para a semântica pós-14-06: nenhum teste afirma mais a tolerância ceil/floor por produto isolado"
  - "test_pedidos_motor_performance_024.py com o monkeypatch de adequar_grade_produto sincronizado (ledger: OrcamentoPedido em vez de tolerancia: float)"
  - "Suíte completa do repositório verde: 825 passed, 16 skipped, 1 failed (a única falha é a pré-existente e conhecida de test_pedidos_read_projection.py, sem relação com o motor) — critério de saída da Phase 14"
affects: ["15 (Phase 14 fechada; próxima é o roteamento global do modo sem adequação)", "19 (helper de invariantes I1/I5 já reusado por testes de negócio, não só pelo self-teste do helper)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Reuso direto dos helpers de invariante (assert_estoque_nunca_excedido, assert_orcamento_nao_excedido) dentro de testes de cenário de negócio, não só no self-teste do próprio helper — evidência de que o domínio está hypothesis-ready para a Phase 19"

key-files:
  created: []
  modified:
    - app/tests/test_pedidos_motor.py
    - app/tests/test_pedidos_motor_performance_024.py

key-decisions:
  - "Os 11 cenários nomeados de 14-VALIDATION.md já estavam cobertos ao final do 14-06 (disputa da mantenedora, furo de grade em suas 5 variações, orçamento atravessa produtos, permutação de produtos, empate de prioridade) ou cobertos a nível de ledger isolado em test_pedidos_orcamento_pedido.py (não se compensam, double-spend/acumulado entre execuções) — este plano NÃO recriou cobertura já existente; em vez disso, acrescentou 2 testes NOVOS via processar_pedidos (nível de integração, não só do ledger isolado) para 'não se compensam' e 'acumulado entre execuções', reforçando a prova end-to-end além do nível unitário do ledger"
  - "Renomeado test_processar_pedidos_orcamento_do_pedido_completo_atravessa_produtos_terceiro_em_standby (criado no 14-06) para test_processar_pedidos_orcamento_pedido_corte_atravessa_produtos_respeita_teto, alinhando ao Test Map do 14-RESEARCH.md (substring orcamento_pedido_) sem alterar comportamento nem asserção"
  - "test_pedidos_processing_cancellation_024.py NÃO foi modificado: seu substituto _slow_grade já repassava o 4º argumento posicionalmente ao original_grade capturado antes do monkeypatch, então o ledger chegava corretamente à implementação real mesmo com o parâmetro local ainda chamado 'tolerance' — Python não valida nome de parâmetro em chamada posicional. Confirmado verde na suíte completa sem qualquer edição."

requirements-completed: []

# Metrics
duration: ~45min
completed: 2026-08-17
---

# Phase 14 Plan 07: Migração consciente da suíte de testes para a semântica pós-14-06 Summary

**Suíte completa do motor de adequação migrada para o orçamento ±5% por pedido completo (floor, dois orçamentos independentes) — dos 6 testes com quebra antecipada por 14-06, 3 foram reescritos conscientemente como asserção da regra nova e 2 foram sincronizados mecanicamente com a assinatura real de `adequar_grade_produto`; suíte fecha com exatamente 1 falha (pré-existente, sem relação), fechando a Phase 14.**

## Performance

- **Duration:** ~45 min
- **Tasks:** 2 completed (Task 3 do PLAN.md era só verificação/commit — sem edição adicional de arquivo)
- **Files modified:** 2

## Accomplishments

- `test_pedidos_motor.py`: removidos os 2 testes finais que provavam a tolerância `ceil`/`floor` por PRODUTO ISOLADO (`test_adequar_grade_produto_aumenta_extremos_dentro_do_orcamento`, `test_adequar_grade_produto_falta_dentro_da_tolerancia_reduz_parcial`) — regra que deixou de existir com ALOC-07/08 (a unidade de orçamento passou de produto para pedido completo).
- Renomeado o teste de corte-atravessa-produtos (herdado do 14-06) para `test_processar_pedidos_orcamento_pedido_corte_atravessa_produtos_respeita_teto`, alinhado ao Test Map da fase, e adicionados 2 testes novos via `processar_pedidos` (nível de integração, não só do ledger isolado):
  - `test_processar_pedidos_orcamento_pedido_corte_e_adicao_nao_se_compensam` — prova que o corte de um produto do pedido esgota o orçamento de CORTE sem afetar o orçamento de ADIÇÃO de outro produto do mesmo pedido (ALOC-08).
  - `test_processar_pedidos_orcamento_acumulado_execucoes_nao_reseta` — prova que `consumido_previo_corte_por_pedido` threadado entre duas chamadas de `processar_pedidos` impede o orçamento de "resetar" a 5% novos na segunda execução (ALOC-09/double-spend).
  - Ambos reusam os helpers `assert_estoque_nunca_excedido` (I1) e `assert_orcamento_nao_excedido` (I5) de `invariantes_motor_adequacao.py` (14-02) — a primeira vez que o helper é chamado por um teste de negócio, não só pelo self-teste do próprio helper.
- Ajustada a asserção de `test_processar_pedidos_prioriza_maior_valor_e_isola_sem_credito`: sob a regra nova, o stand-by por orçamento de corte esgotado sempre passa por `marcar_stand_by` (que inclui `motivo_stand_by`), consistente com furo de grade e crédito — a asserção antiga (`"motivo_stand_by" not in item2`) testava um detalhe de implementação que deixou de valer, não a regra de negócio em si (preservação da quantidade, prioridade e isolamento de crédito continuam intactos e não foram tocados).
- `test_pedidos_motor_performance_024.py`: `_adequar_observando_subset` recebia `tolerancia: float` como 4º parâmetro posicional; sincronizado para `ledger: OrcamentoPedido`, repassando `cancel_token` como keyword-only ao `adequar_original` capturado antes do monkeypatch.
- Confirmado por leitura (não só grep) que os 11 cenários nomeados de `14-VALIDATION.md` têm cobertura automatizada: Disputa da mantenedora (`test_cenario_disputa_mantenedora.py`, 2 modos), Furo no meio/Não é furo/Tamanho único/Tamanho desconhecido (`test_pedidos_motor_furo_grade.py`, `test_invariantes_motor_adequacao.py`), Orçamento atravessa produtos e Permutação de produtos (`test_pedidos_motor.py`), Não se compensam e Double-spend (a nível de ledger isolado em `test_pedidos_orcamento_pedido.py`, reforçado agora a nível de integração pelos 2 testes novos deste plano), Furo sensível à ordem e Empate de prioridade (`test_pedidos_motor_furo_grade.py`/`test_pedidos_motor.py`).
- Suíte completa via Docker: **825 passed, 16 skipped, 1 failed** — a única falha é `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (pré-existente, conhecida, `RuntimeError: Event loop is closed` no teardown de conexão, sem relação com o motor de adequação). Este é exatamente o critério de saída da Phase 14: nenhum `--deselect`, nenhum `-k`.

## Task Commits

1. **Task 1: Reescrever conscientemente os testes cuja regra de orçamento mudou** - `34dffa0` (test)
2. **Task 2: Atualizar o arquivo de assinatura fixa restante** - `b5a0ed7` (test)

Task 3 do `14-07-PLAN.md` era só verificação (suíte completa, `git status` restrito a diretórios de produção, ausência de `hypothesis`, reuso do helper de invariantes) e o commit final de metadados — não exigiu edição de código além do já feito nas Tasks 1 e 2.

## Files Created/Modified

- `app/tests/test_pedidos_motor.py` - 2 testes de regra antiga removidos; 1 teste renomeado; 2 testes novos de orçamento por pedido (nível `processar_pedidos`); 1 asserção pontual corrigida (motivo_stand_by).
- `app/tests/test_pedidos_motor_performance_024.py` - `_adequar_observando_subset` sincronizada com a assinatura real de `adequar_grade_produto` (`ledger: OrcamentoPedido` em vez de `tolerancia: float`).

## Decisions Made

Ver `key-decisions` no frontmatter. Resumo:

1. Não recriar cobertura de cenário nomeado já existente (a maioria já foi coberta pelos planos 14-02/14-04/14-06) — só reforçar "Não se compensam" e "Double-spend" a nível de integração (`processar_pedidos`), já que a cobertura existente desses dois era só a nível do ledger isolado (`OrcamentoPedido`).
2. Renomear (não recriar) o teste de corte-atravessa-produtos para alinhar ao Test Map da fase.
3. Não tocar `test_pedidos_processing_cancellation_024.py` — confirmado verde sem edição, evitando uma mudança cosmética desnecessária em um arquivo que já funciona corretamente (mesmo que o nome do parâmetro local `tolerance` seja hoje uma etiqueta enganosa para o que na prática é um `OrcamentoPedido`).

## Deviations from Plan

None - plan executado exatamente como escrito. As acceptance criteria específicas do `14-07-PLAN.md` (nomes exatos de teste via grep) foram seguidas à risca; a única adaptação foi reconhecer que "Não se compensam" e "Double-spend" já tinham prova a nível de ledger isolado (herdada do 14-06, não deste plano) e, em vez de as tratar como ausentes, reforçá-las com um teste de integração adicional — não uma divergência do plano, apenas uma constatação de que o estado real já cobria parte do que o plano presumia estar faltando.

## Issues Encountered

`ruff format`/`ruff check --fix` rodados localmente (fora do container Docker, que não tem o binário disponível — mesma limitação documentada em planos anteriores da fase) sobre os 2 arquivos tocados; sem alteração de comportamento, suíte reconfirmada verde após a formatação.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- **Phase 14 fechada**: todos os 7 planos (14-01 a 14-07) completos. ALOC-01 a ALOC-04, ALOC-06 a ALOC-10 e FIX-02 provados por teste automatizado; ALOC-09 tem o MECANISMO provado (ledger nunca reseta nem excede o limite entre execuções), mas a prova ponta a ponta com dados reais do banco fica para a Phase 15, que herda `total_original_por_pedido`/`consumido_previo_*_por_pedido` como parâmetros opcionais já prontos para receber dados reais.
- Suíte completa do repositório: 825 passed, 16 skipped, 1 failed (pré-existente, sem relação, documentado desde o 14-02). Nenhuma regressão introduzida por esta fase.
- Nenhum arquivo de produção foi tocado por este plano — `git status --short -- app/modules app/shared alembic` mostra só o trabalho pré-existente e não relacionado de SSO Microsoft (não commitado por este plano).
- `hypothesis` continua ausente de `pyproject.toml` e de `app/tests/` — reservada para a Phase 19, conforme decisão da mantenedora.
- Próximo passo: `/gsd-plan-phase 15` (roteamento global do modo sem adequação), que depende da Phase 14 fechada.

---
*Phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio*
*Completed: 2026-08-17*

## Self-Check: PASSED

- FOUND: app/tests/test_pedidos_motor.py
- FOUND: app/tests/test_pedidos_motor_performance_024.py
- FOUND: .planning/phases/14-motor-de-aloca-o-puro-regras-de-neg-cio/14-07-SUMMARY.md
- FOUND commit: 34dffa0
- FOUND commit: b5a0ed7
