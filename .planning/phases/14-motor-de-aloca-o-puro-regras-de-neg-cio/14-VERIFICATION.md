---
phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio
verified: 2026-08-17T00:00:00Z
status: passed
score: 10/10 must-haves verified (5 roadmap success criteria + ALOC-09 partial-with-inheritance confirmed honest)
overrides_applied: 0
---

# Phase 14: Motor de alocação puro (regras de negócio) Verification Report

**Phase Goal:** O motor de adequação decide corretamente crédito, tudo-ou-nada por par, furo de grade, orçamento ±5% separado sobre o pedido completo (persistente entre execuções, com mínimo viável garantido antes de extras) e prioridade determinística — inteiramente em `domain/`, testável sem banco e sem tocar a orquestração.
**Verified:** 2026-08-17
**Status:** passed
**Re-verification:** No — initial verification

## Independent Fact-Check

Suíte completa executada de forma independente (não copiada do SUMMARY), via Docker:

```
docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q
→ 1 failed, 825 passed, 16 skipped in 155.29s
```

A única falha é `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (`RuntimeError: Event loop is closed`, teardown de conexão assíncrona), em `app/tests/test_pedidos_read_projection.py` — camada de leitura/infra fora do escopo desta fase (`processing`/leitura, não `domain/pedidos`). Nenhum `--deselect`/`-k` foi usado. Este número bate exatamente com o "fato medido" declarado na tarefa: **confirmado por execução própria, não por confiança no SUMMARY.**

Também confirmado, isoladamente, que os dois arquivos de assinatura fixa continuam verdes sem exclusão: `test_pedidos_motor_performance_024.py` + `test_pedidos_processing_cancellation_024.py` → `6 passed`.

## Goal Achievement

### Observable Truths (5 Success Criteria do ROADMAP)

| # | Truth (ROADMAP § Phase 14) | Status | Evidence |
|---|---|---|---|
| 1 | Cliente sem crédito nunca tem produto reservado, em nenhum modo | ✓ VERIFIED | `motor_adequacao.py:710-728` marca TODOS os produtos de `sem_credito_nrs` como stand-by antes do laço de prioridade, sem tocar `estoque_locais`/ledger. Testado nos dois modos por `test_processar_pedidos_credito_bloqueia_e_preserva_estoque_nos_dois_modos` (`test_pedidos_motor_politica_quantidade.py:76-112`) — prova que o estoque do bloqueado permanece disponível ao próximo pagante em ambos os modos. |
| 2 | SEM_ADEQUAR: só grade exata ou stand-by inteiro, nunca parcial | ✓ VERIFIED | `aplicar_tudo_ou_nada` (`motor_adequacao.py:306-341`): `falta_total > 0` → grade inteira em stand-by; senão grade inteira íntegra. Prova direta: `test_aplicar_tudo_ou_nada_falta_em_um_unico_tamanho_manda_grade_inteira_para_stand_by`, `test_processar_pedidos_sem_adequar_rejeita_falta_que_adequar_toleraria` (mesma entrada, resultado diferente por modo). |
| 3 | Prioridade por valor do pedido + desempate total e determinístico (mesmo hash entre execuções) | ✓ VERIFIED | `_prioridade` (`motor_adequacao.py:671-685`): `(score, len(prods_disp), -nr_pedido)` — três campos, nenhum empate residual possível (nr_pedido é único). Provado por `test_processar_pedidos_desempate_por_nr_pedido_crescente_independente_da_ordem_de_entrada` (ordem de entrada invertida produz plano item-a-item idêntico) e pela invariante de permutação (`test_processar_pedidos_permutacao_de_produtos_produz_resultado_identico`). Cenário numérico da mantenedora confirmado nos dois modos (`test_cenario_disputa_mantenedora.py`). |
| 4 | Furo de grade no meio da grade pedida → stand-by nos dois modos, mesmo com sobra nos demais tamanhos | ✓ VERIFIED | `tem_furo_de_grade` (`furo_de_grade.py`) roda **dentro do laço** antes da política de quantidade, nos dois ramos (`motor_adequacao.py:750-763` para SEM_ADEQUAR, `:821-834` para ADEQUAR). 8 testes unitários dos casos de borda + 2 testes de integração, incluindo sensibilidade à ordem (`test_processar_pedidos_furo_sensivel_a_ordem_prova_checagem_dentro_do_laco`, prova que o furo depende do estoque decrementado pelo prioritário processado antes, não de uma foto anterior ao laço). |
| 5 | ADEQUAR: mínimo viável antes de extras; orçamento ±5% separado, `floor`, persistente entre execuções (nunca reseta); rateio sem drift de centavo | ✓ VERIFIED (mecanismo) / ~ PARCIAL declarado (fio ponta a ponta) | `OrcamentoPedido` (`orcamento_pedido.py`): 4 campos obrigatórios sem default (`TypeError` se ausentes — `test_orcamento_pedido_construtor_exige_os_4_campos_obrigatorios`), `floor` inteiro (`// 100`), dois orçamentos independentes provados por `test_orcamento_pedido_dois_orcamentos_nao_se_compensam` e reforçados a nível de `processar_pedidos` por `test_processar_pedidos_orcamento_pedido_corte_e_adicao_nao_se_compensam`. Acumulado entre execuções provado a dois níveis: unitário (`test_orcamento_pedido_acumulado_entre_execucoes_sucessivas_nunca_reseta_nem_excede_o_limite`) e integração via `processar_pedidos` threading `consumido_previo_corte_por_pedido` entre duas chamadas (`test_processar_pedidos_orcamento_acumulado_execucoes_nao_reseta`). Duas passadas (mínimo viável antes de extra) garantidas **estruturalmente**: passada 1 resolve TODOS os produtos do pedido antes de passada 2 sequer começar a iterar (`motor_adequacao.py:841-891`). Rateio Hamilton sem drift provado no utilitário isolado (`test_pedidos_rateio.py`, inclusive casos com remainder). **Honesto na declaração de parcialidade:** ver seção ALOC-09 abaixo — o mecanismo está completo e provado, mas a fonte real de dados (ler ORs do banco) é, por desenho documentado, da Phase 15. |

**Score:** 5/5 critérios de sucesso do ROADMAP verificados no código (não apenas no SUMMARY).

### ALOC-09 — Verificação específica dos 3 itens pedidos

| Item | Verificação | Resultado |
|---|---|---|
| (a) `logger.warning` do fallback existe e cita ALOC-09 | `motor_adequacao.py:921-927`: `logger.warning("ALOC-09: total_original_por_pedido/consumido_previo_* não fornecidos para %d pedido(s); usando placeholder de execução única — o orçamento acumulado entre execuções NÃO está sendo respeitado.", ...)`, emitido uma vez por canal quando `pedidos_com_orcamento_placeholder > 0` | ✓ CONFIRMADO |
| (b) Teste com `caplog` prova que o aviso sai sem os parâmetros e não sai com eles | `test_processar_pedidos_aloc09_alerta_quando_orcamento_nao_fornecido` (linha 290) e `test_processar_pedidos_aloc09_sem_alerta_quando_orcamento_fornecido` (linha 308) em `test_pedidos_motor.py`, ambos usando `caplog.at_level(logging.WARNING, logger="app.modules.pedidos.domain.motor_adequacao")` | ✓ CONFIRMADO — os dois testes passam na suíte verificada |
| (c) Phase 15 no ROADMAP tem critério de sucesso verificável cobrindo o fechamento | ROADMAP.md § Phase 15, critério 5: *"O orçamento ±5% passa a usar dados reais: `processar_pedidos` é chamado com o total original de cada pedido e o consumido em ORs anteriores, lidos do banco... Verificação: processar o mesmo pedido em duas execuções sucessivas não concede 5% novos na segunda, e o log de aviso `ALOC-09` da Phase 14 deixa de ser emitido."* + nota explícita de herança logo abaixo da tabela de planos da Phase 15 | ✓ CONFIRMADO |

Os 3 itens estão presentes. **ALOC-09 permanece corretamente "Partial" em REQUIREMENTS.md** — a declaração de parcialidade é honesta e auditável, não um gap oculto: o mecanismo (ledger nunca reseta/excede) tem prova unitária e de integração; só a leitura real do banco (I/O, fora da fronteira desta fase por decisão do 14-CONTEXT.md) fica para a Phase 15, com critério de saída verificável já escrito.

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `app/modules/pedidos/domain/motor_adequacao.py` | Motor reescrito: laço pedido-externo, despacho por modo, furo de grade, ledger, rateio | ✓ VERIFIED | 949 linhas, todas as regras presentes e wired (lido integralmente) |
| `app/modules/pedidos/domain/orcamento_pedido.py` | Ledger ±5% do pedido completo | ✓ VERIFIED | `OrcamentoPedido`, 4 campos obrigatórios, `floor` inteiro, tudo-ou-nada (corte) e parcial (adição) |
| `app/modules/pedidos/domain/furo_de_grade.py` | Predicado puro `tem_furo_de_grade` | ✓ VERIFIED | Filtra idx 999 antes de calcular extremos; 8 testes de borda |
| `app/modules/pedidos/domain/politica_quantidade.py` | `ModoAdequacao` (enum de domínio) | ✓ VERIFIED | Enum próprio, paridade testada contra `ProcessingMode` sem import cruzado em produção |
| `app/modules/pedidos/domain/rateio.py` | `ratear_hamilton` extraído e promovido | ✓ VERIFIED | Extração byte-a-byte de `_allocate`; `edicao_grade.py` importa de volta via alias, sem duplicar aritmética |
| `app/tests/test_pedidos_motor.py` + 6 arquivos de teste novos | Cobertura das regras | ✓ VERIFIED | Ver tabela de cenários abaixo |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `_processar_pedidos_canal` | `tem_furo_de_grade` | import direto + chamada dentro do laço, antes da política de quantidade, nos 2 ramos | ✓ WIRED | Confirmado nas linhas 750/821 |
| `_processar_pedidos_canal` (modo ADEQUAR) | `OrcamentoPedido` | 1 instância por pedido, resolvida por `_resolver_orcamento_pedido` | ✓ WIRED | Linhas 786-806 |
| `adequar_grade_produto` | `ledger.consumir_corte` | passada 1, tudo-ou-nada | ✓ WIRED | Linha 182 |
| `conceder_adicao_pedido` | `ledger.consumir_adicao` | passada 2, parcial | ✓ WIRED | Linha 256 |
| `_recalcular_financeiro_produto` | `ratear_hamilton` | pós-passadas, por produto `Gerar OR` | ✓ WIRED | Linha 295 |
| `processar_pedidos` | `service.py` (re-export) | `aplicar_tudo_ou_nada` adicionado ao `__all__`/import | ✓ WIRED | Único ponto de contato com fora de `domain/` nesta fase — 2 linhas adicionadas, nenhuma outra mudança em `processing/`, `routes.py` ou `alembic/` (confirmado por `git diff --stat` das últimas 40 commits contra esses caminhos) |

### Data-Flow Trace (Level 4)

Não aplicável no sentido usual (não há UI/API nesta fase) — mas o equivalente é a fronteira de I/O do orçamento (ALOC-09), já tratada acima. `total_original_por_pedido`/`consumido_previo_*` são `None` por padrão em todo chamador hoje (verificado: nenhum caller em `processing/`/`routes.py` foi tocado nesta fase), então o placeholder documentado é sempre o caminho ativo em produção até a Phase 15 — exatamente como o fallback ruidoso anuncia.

### Behavioral Spot-Checks / Probe Execution

Suíte completa executada de forma independente pelo verificador (não script de probe dedicado nesta fase — domínio puro, cobertura via pytest):

| Behavior | Command | Result | Status |
|---|---|---|---|
| Suíte completa do repositório | `docker compose ... exec -T api uv run pytest -q` | `1 failed, 825 passed, 16 skipped` (mesma falha pré-existente e documentada) | ✓ PASS |
| Arquivos de assinatura fixa (monkeypatch) | `pytest test_pedidos_motor_performance_024.py test_pedidos_processing_cancellation_024.py -q` | `6 passed` | ✓ PASS |

### Cobertura das invariantes I1-I9 e dos 11 cenários nomeados (14-VALIDATION.md)

| # | Invariante/Cenário | Cobertura | Nível |
|---|---|---|---|
| I1 | Estoque nunca excedido | `assert_estoque_nunca_excedido` usada em 3 testes de negócio reais via `processar_pedidos` | Integração |
| I2 | Sem crédito nunca alocado | Testado diretamente em `test_processar_pedidos_credito_bloqueia_e_preserva_estoque_nos_dois_modos` e `test_processar_pedidos_prioriza_maior_valor_e_isola_sem_credito`; helper `assert_sem_credito_nunca_alocado` só a nível de self-teste | Integração (direta) + self-teste do helper |
| I3 | Tudo-ou-nada preserva quantidade | `test_aplicar_tudo_ou_nada_reserva_grade_completa_quando_estoque_suficiente`, cenário mantenedora modo SEM_ADEQUAR | Integração |
| I4 | Furo nunca selecionado | 8 testes unitários + 2 de integração em `test_pedidos_motor_furo_grade.py` | Unitário + integração |
| I5 | Orçamento nunca excedido (separado) | `assert_orcamento_nao_excedido` usada em 3 testes de negócio reais (`corte_atravessa_produtos`, `nao_se_compensam`, `acumulado_execucoes_nao_reseta`) | Integração |
| I6 | Execuções idênticas / desempate total | `test_processar_pedidos_desempate_por_nr_pedido_crescente_independente_da_ordem_de_entrada`, `test_processar_pedidos_permutacao_de_produtos_produz_resultado_identico` | Integração |
| I7 | Rateio sem drift | `test_pedidos_rateio.py` (unitário, com remainder); helper `assert_rateio_sem_drift` só a nível de self-teste | Unitário (mecanismo) — **ver observação abaixo** |
| I8 | Mínimo viável antes de extra | Garantido estruturalmente pelo código (passada 1 resolve TODOS antes de passada 2 iterar); helper `assert_minimo_viavel_antes_de_extra` só a nível de self-teste, não exercido contra saída real de `processar_pedidos` | Estrutural (código) + self-teste do helper — **ver observação abaixo** |
| I9 | Contrato de chaves preservado | Nenhuma mudança de forma nas 5 chaves (`resultados`, `selecionados`, `preteridos`, `bloqueados_credito`, `pares_processados`) — confirmado por leitura de todo o código e por todos os testes de integração que acessam essas chaves | Estrutural + self-teste do helper |
| Disputa da mantenedora | `test_cenario_disputa_mantenedora.py` (2 modos) | ✓ |
| Furo no meio / Não é furo / Tamanho único / Tamanho desconhecido | `test_pedidos_motor_furo_grade.py` | ✓ |
| Orçamento atravessa produtos | `test_processar_pedidos_orcamento_pedido_corte_atravessa_produtos_respeita_teto` | ✓ |
| Não se compensam | `test_processar_pedidos_orcamento_pedido_corte_e_adicao_nao_se_compensam` | ✓ |
| Double-spend | `test_processar_pedidos_orcamento_acumulado_execucoes_nao_reseta` | ✓ |
| Furo sensível à ordem | `test_processar_pedidos_furo_sensivel_a_ordem_prova_checagem_dentro_do_laco` | ✓ |
| Empate de prioridade | `test_processar_pedidos_desempate_por_nr_pedido_crescente_independente_da_ordem_de_entrada` | ✓ |
| Permutação de produtos | `test_processar_pedidos_permutacao_de_produtos_produz_resultado_identico` | ✓ |

**Todos os 11 cenários nomeados têm teste automatizado nomeado e executável.** Nenhum cenário nomeado ficou sem cobertura.

**Observações não-bloqueantes (I7/I8):** os helpers de invariante I7 e I8 só são exercidos, hoje, pelo self-teste do próprio helper (dados sintéticos) — não por uma chamada real a `processar_pedidos` com múltiplos tamanhos por produto (I7) ou com um produto zerado e outro recebendo extra dentro do mesmo pedido (I8). Isso é aceitável nesta fase por dois motivos verificados no código: (1) a garantia de I8 é estrutural — a passada 1 resolve `adequar_grade_produto` para TODOS os produtos elegíveis do pedido antes de a passada 2 sequer começar a iterar (`motor_adequacao.py:841-891`), então não há caminho de código onde um extra seria concedido antes do mínimo viável de outro produto do mesmo pedido; (2) `_recalcular_financeiro_produto` delega inteiramente a `ratear_hamilton`, que já está provado sem drift (inclusive com remainder) em `test_pedidos_rateio.py` — a wiring em si é uma soma+chamada direta, sem lógica adicional que pudesse introduzir drift. `14-02-SUMMARY.md`/`14-07-SUMMARY.md` documentam explicitamente que os helpers foram feitos para reuso pelos testes de propriedade da Phase 19 (que vão exercitá-los contra saída real do motor em dezenas de cenários gerados) — a ausência de um teste de integração dedicado para I7/I8 nesta fase é uma lacuna de cobertura de teste, não uma falha de comportamento observada, e não bloqueia o objetivo desta fase.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| ALOC-01 | 14-05 | Sem crédito nunca reservado, nenhum modo | ✓ SATISFIED | Ver Truth #1 |
| ALOC-02 | 14-05 | SEM_ADEQUAR: grade exata ou stand-by | ✓ SATISFIED | Ver Truth #2 |
| ALOC-03 | 14-05 | Prioridade consome estoque disputado | ✓ SATISFIED | `test_processar_pedidos_disputa_prioridade_mantenedora_atende_maior_valor_nos_dois_modos` |
| ALOC-04 | 14-04 | Furo de grade nos 2 modos | ✓ SATISFIED | Ver Truth #4 |
| ALOC-06 | 14-03 | Desempate total e explícito | ✓ SATISFIED | Ver Truth #3 |
| ALOC-07 | 14-06 | Tolerância sobre pedido completo | ✓ SATISFIED | `OrcamentoPedido.total_original` é o pedido inteiro, não o produto; provado por `test_cenario_disputa_mantenedora` (base 1000 pçs, não 10) |
| ALOC-08 | 14-06 | Orçamentos separados de 5% | ✓ SATISFIED | `test_processar_pedidos_orcamento_pedido_corte_e_adicao_nao_se_compensam` |
| ALOC-09 | 14-06 | Acumulado entre execuções não reseta | ~ PARTIAL (declarado, honesto) | Mecanismo provado; fio ponta a ponta com dados reais é Phase 15 (ver seção dedicada acima) |
| ALOC-10 | 14-06 | Mínimo viável antes de extras | ✓ SATISFIED | Garantia estrutural (duas passadas sequenciais) + `test_cenario_disputa_mantenedora` prova extra sendo concedido depois do mínimo |
| FIX-02 | 14-01 + 14-06 | Rateio sem drift | ✓ SATISFIED | `ratear_hamilton` provado sem drift (unitário); wiring simples e direto em `_recalcular_financeiro_produto` |

Nenhum requirement desta fase foi marcado "Complete" em REQUIREMENTS.md sem prova de teste correspondente — todos batem com commits e arquivos reais. Nenhum requirement órfão: ALOC-05, ALOC-11, FIX-01, ARCH-01 pertencem corretamente a Phases 13/15/18 (fora do escopo declarado em 14-CONTEXT.md) e não aparecem na tabela de `requirements-completed` de nenhum SUMMARY desta fase.

### Anti-Patterns Found

Nenhum bloqueador. Ocorrências da palavra "placeholder" em `motor_adequacao.py`/`orcamento_pedido.py` são todas o placeholder DOCUMENTADO e referenciado (ALOC-09 → Phase 15), não um placeholder de trabalho pendente sem rastreio — não se qualifica como debt marker (não é `TBD`/`FIXME`/`XXX`, e mesmo se fosse, referencia formalmente a Phase 15). Nenhum `TBD`/`FIXME`/`XXX` encontrado nos 5 arquivos de domínio tocados.

### Fronteira da fase (escopo respeitado)

Confirmado por `git diff --stat` das últimas ~40 commits contra `app/modules/pedidos/processing`, `app/modules/pedidos/routes.py`, `alembic/`: **apenas 2 linhas adicionadas em `app/modules/pedidos/service.py`** (re-export de `aplicar_tudo_ou_nada` para uso em testes) — nenhuma mudança em `processing/`, `routes.py` ou migrations. A fase ficou inteiramente em `domain/` + `app/tests/`, exatamente como o 14-CONTEXT.md exigia.

### Regressão silenciosa — inversão de loop (14-06)

**Verificado:** existe teste dedicado de permutação (`test_processar_pedidos_permutacao_de_produtos_produz_resultado_identico`, `test_pedidos_motor.py:240`) que roda o MESMO pedido com os 3 produtos em ordem de entrada normal e invertida, e compara resultado item a item (`status_item`, `qt_liquida`, `vl_liquido`) — resultado idêntico nos dois casos. Isso prova diretamente a alegação da 14-06-SUMMARY de que a inversão do laço (produto-externo → pedido-externo) não altera o resultado da alocação. Reforçado por `test_processar_pedidos_desempate_por_nr_pedido_crescente_independente_da_ordem_de_entrada`, que faz o mesmo teste de permutação para o desempate de prioridade entre pedidos.

### Human Verification Required

Nenhum item — domínio 100% puro, sem UI, sem I/O, toda a superfície é testável programaticamente e foi testada.

### Gaps Summary

Nenhum gap bloqueante encontrado. Duas observações não-bloqueantes registradas (cobertura de integração de I7/I8 pelos helpers de invariante, delegada por desenho à Phase 19) — não afetam o veredito porque as garantias correspondentes (ALOC-10 estrutural, FIX-02 via mecanismo isolado provado) já são verificáveis diretamente no código e em outros testes. ALOC-09 está corretamente documentado como "Partial" em REQUIREMENTS.md, com os 3 elementos de prova exigidos (logger.warning citando ALOC-09, teste `caplog` positivo/negativo, critério de saída verificável na Phase 15) todos confirmados presentes.

---

*Verified: 2026-08-17*
*Verifier: Claude (gsd-verifier)*
