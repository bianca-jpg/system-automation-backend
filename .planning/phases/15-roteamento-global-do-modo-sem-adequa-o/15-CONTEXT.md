# Phase 15: Roteamento global do modo sem adequação - Context

**Gathered:** 2026-08-17
**Status:** Ready for planning
**Source:** Decisões da mantenedora (2026-08-14), pesquisa do milestone (`.planning/research/v1.3/ARCHITECTURE.md`), e o que a Phase 14 entregou e deixou explicitamente aberto.

<domain>
## Phase Boundary

Esta fase leva o modo **sem adequação** para o mesmo caminho global que já serve o modo com adequação, remove o código de streaming que fica órfão, corrige a revalidação de capacidade de estoque na escrita, e **fecha o ALOC-09** ligando os dados reais de orçamento.

**Entra:**
1. **ARCH-01** — `SEM_ADEQUAR` planejado por `build_processing_plan` (caminho global), com `deferred_count` e `blocked_credit_count` reais em vez de zerados; remoção do `StreamingPlanBuilder` e de tudo que só existia para alimentá-lo.
2. **FIX-01** — `_assert_stock_capacity` (`processing/infrastructure/repository.py:489-513`) passa a revalidar capacidade também para `tipo == "sem"`.
3. **Fechamento do ALOC-09** — o ponto de entrada passa a receber `total_original_por_pedido` e `consumido_previo_*_por_pedido` **reais, lidos do banco**, em vez do placeholder de execução única deixado pela Phase 14. É o critério de sucesso 5 do ROADMAP.

**Não entra:**
- Persistência do motivo de stand by e contador de starvation (STANDBY-01/06) → **Phase 16**
- Qualquer coisa de frontend (tags, filtro) → **Phase 17**
- Distribuição das peças extras nos tamanhos com mais sobra (ALOC-11) → **Phase 18**
- Testes de propriedade com `hypothesis` → **Phase 19**
- Guarda contra OR zerada (ALOC-05) → **Phase 13**, ainda sem planos

**Contrato a preservar:** as chaves de retorno de `processar_pedidos` não mudam de forma. O resultado do job continua limitado a contadores (`plannedCount`, `appliedCount`, `deferredCount`, `blockedCreditCount`) — sem listas de itens. Essa é decisão de design registrada; a visibilidade do stand by vem por consulta REST na Phase 16/17, não inflando o payload do job.

</domain>

<decisions>
## Implementation Decisions

### Roteamento (ARCH-01)
- `SEM_ADEQUAR` **precisa** do caminho global: prioridade por valor e estoque compartilhado são decisões que uma página de 250 pares não consegue tomar — ela não sabe se existe, na página seguinte, um pedido de maior prioridade disputando o mesmo produto. Não é preferência de estilo, é impossibilidade estrutural.
- `service.py::_plan_once` perde o ramo de streaming (hoje linhas ~211-290); `stock`, `criterion` e `tolerance` passam a ser carregados nos **dois** modos (hoje só em `ADEQUAR`, linha ~311).
- `processing/domain.py::build_processing_plan` chama `processar_pedidos(..., modo=SEM_ADEQUAR)` no lugar de `selected = {pair: items for pair, items in sorted(eligible.items())}`, e passa a preencher `deferred_count`/`blocked_credit_count` de verdade. `tipo` continua `"sem"`.

### Remoção do streaming — escopo exato já mapeado
`StreamingPlanBuilder`, `build_pair_payload` (público), `prepare_pending_pair_stream`, `load_pending_pair_page`, `append_plan_rows`, `finalize_streamed_plan`, `PLANNING_PAIR_PAGE_SIZE`, o arquivo `test_pedidos_processing_stream_repository.py` inteiro, e os trechos correspondentes em `test_pedidos_processing_domain.py`, `test_pedidos_processing_repository.py` e `test_pedidos_processing_service.py`.

**Remover na mesma entrega, não depois.** Este projeto já pagou o preço de deixar código órfão no repositório: uma trava de UI ficou inalcançável por um redesenho e foi apagada meses depois como "feature morta", sem que ninguém percebesse que a intenção de produto continuava válida.

### FIX-01
`_assert_stock_capacity` hoje pula a revalidação quando `tipo != "com"`. É inofensivo enquanto `sem_adequar` não reserva estoque — vira gap real no instante em que este plano o fizer reservar. **Nenhum teste existente falharia se isso for esquecido**, então precisa de teste próprio.

### Fechamento do ALOC-09
A Phase 14 deixou, deliberadamente, `total_original_por_pedido` e `consumido_previo_*_por_pedido` como parâmetros **opcionais** em `processar_pedidos`, com fallback para um placeholder de execução única que **não** respeita o orçamento acumulado. O fallback é ruidoso (`logger.warning` citando ALOC-09, provado por dois testes de `caplog`).

Esta fase precisa:
- Ler o **total original** de cada pedido — todos os produtos, **sem** o filtro de já-processados que o snapshot de pendentes aplica.
- Ler o **acumulado consumido** em ORs anteriores — derivável de `qt_solicitada` e `qt_liquida` já gravados em `ordens_reserva`.
- Passar os dois ao motor, de modo que o `logger.warning` de ALOC-09 **deixe de ser emitido**.

**A prova de que fechou:** processar o mesmo pedido em duas execuções sucessivas não concede 5% novos na segunda, e o aviso some do log. Enquanto isso não passar, ALOC-09 não está entregue — mesmo com a Phase 14 fechada.

#### ⚠ PREMISSA FALSA — verificada no banco em 2026-08-17, não é hipótese

A pesquisa da fase levantou como suposição que um par já convertido em OR continuaria presente na tabela `pedidos` até o pedido inteiro ser resolvido. **Isso é FALSO.** Consultado no banco de dev:

- De **4 pares com OR, 1 já sumiu** de `pedidos`.
- O caso: par `(1591091, ML.18.0315|001)` não existe mais em `pedidos`, **mas o pedido 1591091 segue aberto com outros 6 produtos lá**.
- Efeito numérico: `SUM(qt_entregar)` do pedido em `pedidos` devolve **6**; o par que sumiu tinha `qt_solicitada = 3`. O total original verdadeiro é **9**.

Faz sentido: `pedidos` é espelho full-refresh da view `system_automation_pedidos_em_aberto` — **pedidos em aberto**. Um par atendido deixa de estar em aberto e cai fora do recorte na origem, independentemente do que o app faz.

**Consequência para o desenho:** ler o total original **apenas** de `pedidos` produz denominador menor que o real. Com base menor, `floor(base × 0,05)` dá orçamento menor — o cliente **perde** tolerância a que tem direito. (É a direção oposta do double-spend, mas viola ALOC-07/09 do mesmo jeito.)

**Mitigação obrigatória, não opcional:** o total original tem de ser `pedidos` **mais** o `qt_solicitada` das ORs já geradas para pares daquele pedido que não estão mais em `pedidos`. A pesquisa recomendou isso como defesa preventiva; a verificação no banco mostra que é necessidade comprovada. Um teste precisa cobrir exatamente este caso: pedido com um par já convertido em OR e ausente de `pedidos`, provando que o denominador inclui a quantidade dele.

### Claude's Discretion
- Forma da leitura dos dados de orçamento (query única com CTE, duas queries, ou extensão do snapshot existente) — desde que respeite o padrão de ports/adapters de `processing/`.
- Se a remoção do streaming vira commit separado dentro da fase ou entra junto do roteamento.
- Onde exatamente instrumentar a medição de memória do critério 4.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pesquisa e decisões
- `.planning/research/v1.3/ARCHITECTURE.md` — pontos de integração com arquivo:linha, tabela novo/modificado/removido, ordem de construção
- `.planning/research/v1.3/PITFALLS.md` — armadilhas com invariantes testáveis
- `.planning/STATE.md` § Decisions → "v1.3 — Motor de alocação"
- `.planning/ROADMAP.md` § Phase 15 — os 5 critérios de sucesso e a nota de herança da Phase 14

### O que a Phase 14 entregou (base desta fase)
- `.planning/phases/14-motor-de-aloca-o-puro-regras-de-neg-cio/14-06-SUMMARY.md` — o ledger, as duas passadas, e o fallback ruidoso do ALOC-09
- `.planning/phases/14-motor-de-aloca-o-puro-regras-de-neg-cio/14-VERIFICATION.md` — o que foi provado e o que ficou reservado
- `app/modules/pedidos/domain/motor_adequacao.py` — assinatura atual de `processar_pedidos` (os 3 parâmetros opcionais de orçamento)
- `app/modules/pedidos/domain/orcamento_pedido.py` — o ledger

### Código a modificar
- `app/modules/pedidos/processing/application/service.py` — `_plan_once`
- `app/modules/pedidos/processing/application/ports.py` — porta de leitura
- `app/modules/pedidos/processing/domain.py` — `build_processing_plan`, `StreamingPlanBuilder`
- `app/modules/pedidos/processing/infrastructure/adapters.py` — `_PENDING_BASE_SQL`, novas leituras
- `app/modules/pedidos/processing/infrastructure/repository.py` — `_assert_stock_capacity`

### Convenções
- `.claude/CLAUDE.md` · `.planning/codebase/CONVENTIONS.md` · `.planning/codebase/TESTING.md`

</canonical_refs>

<specifics>
## Specific Ideas

### Memória — medir, não extrapolar
O preflight limita a 100.000 pares / 200.000 itens / 96 MiB, e o worker dedicado reserva 1280 MiB com teto de 1536 MiB. O pico medido de 434 MiB para 132.405 itens foi calibrado em `ADEQUAR`. O volume elegível de `SEM_ADEQUAR` pode ser **estruturalmente maior**, porque hoje ele aceita todo par sem filtro de crédito. O critério 4 pede medir o pico real depois do roteamento, não confiar na extrapolação.

### Ambiente e armadilhas conhecidas deste repositório
- `uv run pytest` local falha no Windows (pyicu). Testes **sempre** por Docker: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`
- **Verde = exatamente 1 falha**, e é `app/tests/test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (`RuntimeError: Event loop is closed`, teardown pré-existente). Referência ao fim da Phase 14: **825 passed, 16 skipped, 1 failed**.
- **NÃO usar `gsd-tools query state.advance-plan`** — corrompe a frontmatter YAML do STATE.md deste projeto mesmo retornando erro. Atualizar com Edit.
- Worktrees estão desligados (`workflow.use_worktrees: false`): os testes rodam num container que monta a árvore principal, então worktree isolado testaria o código errado.
- Working tree compartilhado com trabalho de SSO Microsoft não commitado de outra frente. `git add` sempre com caminhos explícitos.

</specifics>

<deferred>
## Deferred Ideas

- **Persistência do motivo de stand by** e contador de execuções consecutivas (STANDBY-01/06) → Phase 16. Esta fase faz `preteridos`/`bloqueados_credito` passarem a ter valor real; **quem os persiste** é a 16.
- **Tags e filtro na UI** (STANDBY-02..05) → Phase 17, com a regra do design-system como critério de aceite.
- **Peças extras por sobra de estoque** (ALOC-11) → Phase 18.
- **Testes de propriedade com `hypothesis`** → Phase 19.
- **Guarda contra OR zerada** (ALOC-05) → Phase 13, que segue sem planos e é paralelizável.
- **A falha conhecida da suíte** (`test_read_projection...`) — teardown de asyncpg, fora do escopo do v1.3. Não consertar aqui.

</deferred>

---

*Phase: 15-roteamento-global-do-modo-sem-adequa-o*
*Context gathered: 2026-08-17 a partir das decisões já tomadas na sessão e do que a Phase 14 deixou explicitamente aberto*
