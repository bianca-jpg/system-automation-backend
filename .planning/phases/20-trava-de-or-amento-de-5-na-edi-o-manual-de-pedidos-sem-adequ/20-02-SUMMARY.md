---
phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ
plan: 02
subsystem: pedidos-domain
tags: [python, decimal, rateio, domain, edicao-grade, pitfall-2]

# Dependency graph
requires:
  - phase: 20-01
    provides: "domínio puro de orçamento de edição manual (rewind+cobrança), consumido futuramente por 20-03 junto com este plano"
provides:
  - "ratear_inteiro em rateio.py — rateio de maior resto em peças, aritmética Decimal, soma exata garantida"
  - "montar_grade_atualizada com parâmetro opt-in permitir_variacao_total e invariante de linha de base do par"
affects: [20-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Linha de base do PAR (não do tamanho) preservada por rateio de maior resto ao reconstruir grade"
    - "Fallback ausente/vazia -> qt_liquida ao ler qt_solicitada de itens antigos, preservando 0 explícito"

key-files:
  created:
    - app/tests/test_pedidos_edicao_grade_variacao_total.py
  modified:
    - app/modules/pedidos/domain/rateio.py
    - app/modules/pedidos/domain/edicao_grade.py

key-decisions:
  - "PD-03 (da pesquisa): linha de base preservada é do PAR, não do tamanho — rejeitada a recomendação de herdar por tamanho porque zera a base ao zerar um tamanho"
  - "PD-04: caminho 'com adequação' fica byte-idêntico, inclusive no reset de qt_solicitada — dívida deliberada, não corrigida nesta fase"
  - "Fallback de qt_solicitada ausente/vazia usa a mesma ideia de listar_ordens_reserva.py, mas preserva um 0 explicitamente gravado (não trata 0 como 'vazio'), para que o caso de borda de linha de base zero seja alcançável e não reinfle silenciosamente para qt_liquida"

patterns-established:
  - "ratear_inteiro: irmão inteiro de ratear_hamilton, para grandezas em peças em vez de centavos"

requirements-completed: [GRADE-03]

# Metrics
duration: ~25min
completed: 2026-09-08
status: complete
---

# Phase 20 Plan 02: ratear_inteiro + linha de base do par preservada em montar_grade_atualizada Summary

**`montar_grade_atualizada` ganha um parâmetro opt-in que permite total variável para ORs "sem adequação" e para de resetar `qt_solicitada` a cada edição — passa a preservar a linha de base do par via um novo rateio de maior resto em peças inteiras (`ratear_inteiro`), fechando o Pitfall 2 (double-spend silencioso só visível na segunda edição).**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-09-08T12:58:40Z
- **Completed:** 2026-09-08T13:09:36Z
- **Tasks:** 2
- **Files modified:** 3 (2 produção, 1 teste)

## Accomplishments

- `ratear_inteiro(total, sizes)` em `rateio.py`: rateio de maior resto em peças, aritmética `Decimal`, soma exata garantida, desempate alfabético determinístico — irmão inteiro de `ratear_hamilton`.
- `montar_grade_atualizada` ganha `permitir_variacao_total: bool = False` (opt-in, default preserva comportamento de hoje). Quando ligado, a guarda de total preservado não roda.
- Quando `permitir_variacao_total=True`, `qt_solicitada` de cada item deixa de ser resetado para a quantidade nova e passa a ser a **linha de base do par** (soma de `qt_solicitada` dos itens originais ativos, com fallback para `qt_liquida` quando ausente/vazia) rateada pelos tamanhos novos via `ratear_inteiro`.
- Provado com um teste de duas edições sucessivas (100 → 104 → 102): depois da segunda edição, `qt_solicitada` continua somando 100 — é exatamente o cenário que uma edição isolada não expõe (D-06).
- Tamanho novo introduzido não infla a linha de base; tamanho zerado não a encolhe — ambos provados por testes dedicados.
- Caminho `permitir_variacao_total=False` (ORs "com adequação") fica byte-idêntico ao de hoje, inclusive no reset de `qt_solicitada`, mantendo D-01 e regredindo `test_pedidos_product_writes.py` sem tocá-lo.
- Nenhum rateio monetário alterado; soma financeira em centavos continua exata (provado por teste dedicado).

## Task Commits

Each task was committed atomically:

1. **Task 1: ratear_inteiro — rateio de maior resto em peças, com soma exata** - `f1cf9dc` (feat)
2. **Task 2: montar_grade_atualizada com total variável opcional e linha de base do par preservada** - `ef82935` (feat)

_Nota: os testes da Task 1 (`ratear_inteiro`) foram commitados junto com a Task 2, pois o plano determinou explicitamente que ambos os conjuntos de teste vivem no mesmo arquivo (`test_pedidos_edicao_grade_variacao_total.py`, criado formalmente na Task 2). A Task 1 foi verificada rodando `pytest -k ratear_inteiro` sobre o arquivo antes de ele ser commitado, sem incluir o arquivo de teste no commit da Task 1 — ver Deviations._

**Plan metadata:** (este commit — docs)

## Files Created/Modified

- `app/modules/pedidos/domain/rateio.py` - adiciona `ratear_inteiro`; `ratear_hamilton` intocado
- `app/modules/pedidos/domain/edicao_grade.py` - novo parâmetro `permitir_variacao_total`, helper `_qt_solicitada_ou_fallback`, rateio da linha de base via `ratear_inteiro`
- `app/tests/test_pedidos_edicao_grade_variacao_total.py` - 29 testes (9 de `ratear_inteiro`/propriedade + 20 de `montar_grade_atualizada`, incluindo o cenário de duas edições sucessivas)

## Decisions Made

- **PD-03** (já registrada no plano): a linha de base preservada é do **par**, não do tamanho — rateada pelos tamanhos novos a cada edição, em vez de herdada tamanho a tamanho. Verificado que o único consumidor de `qt_solicitada` por item fora do ledger (`listar_ordens_reserva.py`) o soma por par, logo é indiferente à distribuição interna.
- **PD-04** (já registrada no plano): o reset de `qt_solicitada` em ORs "com adequação" não foi corrigido nesta fase, de propósito — dívida conhecida registrada aqui para uma fase futura decidir.
- **Semântica do fallback de `qt_solicitada`** (decisão local desta execução, não estava no plano): implementei o fallback "ausente ou vazia → `qt_liquida`" tratando um `qt_solicitada` explicitamente gravado como `0` como um valor válido preservado (não "vazio"), diferente do idioma Python `or` usado literalmente em `listar_ordens_reserva.py` (que também trata `0` como falsy). Escolhi essa leitura porque (a) o texto do plano diz "ausente ou vazia", que descreve estado de chave ausente/string vazia, não valor numérico zero; e (b) só essa leitura torna alcançável o caso de borda que o plano pede para tratar explicitamente ("linha de base zero... não invente um fallback que copie a quantidade nova") — com o idioma `or` puro, esse caso é matematicamente inatingível sempre que `current_qty > 0` (a mesma guarda que já impede grade sem quantidade líquida). Documentado no docstring de `_qt_solicitada_ou_fallback`.

## Deviations from Plan

### Auto-fixed Issues

Nenhuma das três regras de auto-fix (bug, funcionalidade crítica ausente, bloqueio) foi acionada — a implementação seguiu o plano.

### Ajustes de execução (não são deviations de regra, mas registrados por transparência)

**1. Divisão do arquivo de teste entre os commits das Tasks 1 e 2**

- **Contexto:** o plano instrui explicitamente que os testes da Task 1 (`ratear_inteiro`) vivem no arquivo criado pela Task 2 (`test_pedidos_edicao_grade_variacao_total.py`), "em uma seção própria". Isso cria uma tensão entre "cada task committed atomicamente" e "arquivo só existe formalmente a partir da Task 2".
- **Resolução:** escrevi o arquivo completo (as duas seções) uma vez, rodei a verificação da Task 1 (`pytest -k ratear_inteiro`) sobre ele, mas só incluí `rateio.py` no commit da Task 1. O arquivo de teste completo (as duas seções) foi commitado junto da Task 2, que é onde o plano diz que ele nasce formalmente. Nenhum código de produção da Task 2 foi antecipado para a Task 1.
- **Verificação:** `pytest -k ratear_inteiro` verde antes do commit da Task 1; suíte completa do arquivo (29 testes) verde no commit da Task 2.

**2. Acceptance criterion `git diff | grep -c "^-"` da Task 1 devolve 2, não "no máximo 1"**

- **Contexto:** o critério de aceite da Task 1 esperava no máximo 1 linha removida no diff de `rateio.py`.
- **Achado:** `git diff app/modules/pedidos/domain/rateio.py | grep -c "^-"` devolve `2`: uma é o cabeçalho do diff (`--- a/app/...`, que sempre começa com `-` e é contado por qualquer diff não vazio), a outra é a única linha de conteúdo removida (`__all__ = ["ratear_hamilton"]`, substituída por `__all__ = ["ratear_hamilton", "ratear_inteiro"]`, exatamente como o plano pedia). Nenhuma linha de `ratear_hamilton` foi tocada — confirmado por inspeção visual do diff completo.
- **Conclusão:** o critério de aceite tem um off-by-one no próprio comando (não conta com o cabeçalho do diff); a substância do critério ("`ratear_hamilton` intocado") está satisfeita. Não é um bug de código, é uma imprecisão da string de verificação no PLAN.md.

---

**Total deviations:** 0 de regra (1-4). 2 ajustes de execução documentados acima, nenhum com impacto em comportamento ou escopo.
**Impact on plan:** Nenhum. Plano executado conforme especificado, com duas notas de transparência sobre como o arquivo de teste compartilhado foi dividido entre commits e sobre uma imprecisão no comando de verificação da Task 1.

## Issues Encountered

Nenhum. Suíte completa do repositório (exceto `test_pedidos_processing_sem_adequar_memory_024.py`, que falha na coleta em qualquer branch neste Windows por depender do módulo `resource`, exclusivo de Unix — fora de escopo desta task, não tocado) rodou com **949 passed, 18 skipped, 0 failed** — sem nenhuma falha nova em relação à baseline, e sem a falha isolada de `test_pedidos_read_projection.py` que o STATE.md registrava como conhecida (não investigada nesta sessão; pode ter sido corrigida por 20-01 ou ser sensível a ordem/estado de execução — mencionar caso reapareça).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `montar_grade_atualizada(..., permitir_variacao_total=True)` está pronto para o plano 20-03 (`executar_alteracao_grades_produto`) chamar quando `state.tipo == "sem"`, casando com o ramo de orçamento por total de par que o plano 20-01 já entregou na SQL.
- Dívida conhecida (PD-04) documentada aqui para decisão futura: o reset de `qt_solicitada` em ORs "com adequação" continua existindo — se algum dia esse caminho for corrigido, os testes desta suíte que ancoram o comportamento "sem parâmetro" (`test_sem_parametro_novo_total_igual_resultado_identico_ao_de_hoje`, `test_sem_parametro_novo_total_divergente_continua_levantando_valueerror`) precisarão de revisão consciente.
- Nenhum bloqueio para 20-03.

---

_Phase: 20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ_
_Completed: 2026-09-08_

## Self-Check: PASSED

- FOUND: app/modules/pedidos/domain/rateio.py
- FOUND: app/modules/pedidos/domain/edicao_grade.py
- FOUND: app/tests/test_pedidos_edicao_grade_variacao_total.py
- FOUND commit: f1cf9dc
- FOUND commit: ef82935
