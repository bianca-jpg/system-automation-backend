# Phase 15: Roteamento global do modo sem adequação - Research

**Researched:** 2026-08-17
**Domain:** Fechamento de lacunas de integração no processamento durável de Pedidos (roteamento de `SEM_ADEQUAR`, remoção de código órfão, revalidação de estoque, fechamento de ALOC-09 com dados reais de orçamento) — domínio interno deste repositório, sem ecossistema de terceiros.
**Confidence:** HIGH (todas as afirmações sobre código foram conferidas linha a linha nesta sessão; a única área MEDIUM/LOW é a garantia de que linhas de `pedidos` nunca somem do mirror do Databricks para um pedido parcialmente processado — ver Assumptions Log)

## Summary

Esta pesquisa **não repete** o mapeamento de pontos de integração já feito em `.planning/research/v1.3/ARCHITECTURE.md` (arquivo:linha do roteamento e da remoção de streaming já estão lá). O que faltava para planejar bem: (1) o desenho concreto da leitura de orçamento que fecha ALOC-09 — decidi por **duas queries agregadas novas, com CTE reaproveitando o padrão de `_PENDING_BASE_SQL`**, escopadas por `nr_pedido` já hidratado, custando O(1) round-trip por canal em vez de N+1; (2) a confirmação de que a remoção do streaming **não perde nenhuma propriedade de memória real**, porque a única razão de o streaming existir era permitir que `sem_adequar` decidisse por página — algo que a Regra 4 (prioridade global) já torna impossível, então o teto de memória do preflight (`inspect_pending`, agnóstico de modo) já era a proteção real, não o streaming; (3) o levantamento exato de quais dos 4 arquivos de teste afetados **morrem**, quais **mudam de assinatura mecanicamente**, e quais precisam de **dados de estoque novos** para continuar provando o que provavam antes (4 testes em `test_pedidos_processing_domain.py` chamam `build_processing_plan` em modo `SEM_ADEQUAR` sem `stock`, e vão quebrar de um jeito que não é "regra antiga" — é ausência de fixture); (4) uma divisão em waves que respeita a restrição real deste projeto (executor sequencial, sem worktrees) agrupando por arquivo tocado, não por "prioridade de produto".

**Primary recommendation:** trate esta fase como **um único plano com tasks sequenciais**, não múltiplos planos paralelos — os arquivos centrais (`ports.py`, `domain.py`, `adapters.py`, `repository.py`) são tocados por mais de uma preocupação (orçamento, roteamento, remoção de streaming, FIX-01) e o projeto já não tem paralelismo real (worktrees desligados). A única peça seguramente isolável em plano próprio é a leitura de orçamento (aditiva, sem tocar `_plan_once` ainda) e o FIX-01 (uma função, um teste, sem overlap).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Roteamento de modo (`SEM_ADEQUAR` → caminho global) | API/Backend — `processing/application/service.py` (`_plan_once`) | Domain — `processing/domain.py` (`build_processing_plan`) | Orquestração de qual pipeline usar é caso de uso; a decisão de negócio (tudo-ou-nada, prioridade) já vive no domínio puro (`motor_adequacao.py`) desde a Phase 14 |
| Leitura de dados de orçamento (total original, consumido prévio) | Database/Storage — SQL agregado sobre `pedidos`/`ordens_reserva` | API/Backend — novo método de port em `ProcessingPlannerSource` | É I/O puro (leitura agregada), deliberadamente adiado do domínio (`orcamento_pedido.py`/`motor_adequacao.py` já rejeitam receber isso implicitamente — exigem os 4 campos do ledger explícitos) |
| Revalidação de capacidade de estoque na escrita (FIX-01) | Database/Storage — `_assert_stock_capacity`, `processing/infrastructure/repository.py` | — | É um double-check contra mutação concorrente entre planejar e aplicar; pertence à camada de escrita, não ao domínio (que já decidiu com a foto congelada) |
| Remoção do código de streaming | API/Backend + Database/Storage (símbolos espalhados em `domain.py`, `ports.py`, `adapters.py`, `repository.py`) | — | Puramente estrutural — nenhuma responsabilidade de negócio nova, só eliminação de um caminho que a Regra 4 tornou inatingível |
| Medição de pico de memória (critério 4) | API/Backend — instrumentação do worker/teste de carga | — | Observabilidade operacional, não lógica de negócio; não pertence ao domínio puro |

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ARCH-01 | `SEM_ADEQUAR` planejado pelo caminho global (`build_processing_plan`), com `deferred_count`/`blocked_credit_count` reais; streaming removido na mesma entrega | Seção "Ordem de remoção do streaming", "Impacto na suíte", tabela de waves |
| FIX-01 | `_assert_stock_capacity` revalida capacidade também para `tipo == "sem"` | Seção "FIX-01 — o teste que falta", tabela de waves |
| Fechamento ALOC-09 (critério de sucesso 5 do ROADMAP, rastreado na Phase 14) | `processar_pedidos` recebe `total_original_por_pedido`/`consumido_previo_*_por_pedido` reais, lidos do banco | Seção "Fechamento do ALOC-09 — desenho da leitura de orçamento", Validation Architecture |
</phase_requirements>

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Roteamento (ARCH-01)**
- `SEM_ADEQUAR` **precisa** do caminho global: prioridade por valor e estoque compartilhado são decisões que uma página de 250 pares não consegue tomar — ela não sabe se existe, na página seguinte, um pedido de maior prioridade disputando o mesmo produto. Não é preferência de estilo, é impossibilidade estrutural.
- `service.py::_plan_once` perde o ramo de streaming (hoje linhas ~211-290); `stock`, `criterion` e `tolerance` passam a ser carregados nos **dois** modos (hoje só em `ADEQUAR`, linha ~311).
- `processing/domain.py::build_processing_plan` chama `processar_pedidos(..., modo=SEM_ADEQUAR)` no lugar de `selected = {pair: items for pair, items in sorted(eligible.items())}`, e passa a preencher `deferred_count`/`blocked_credit_count` de verdade. `tipo` continua `"sem"`.

**Remoção do streaming — escopo exato já mapeado**
`StreamingPlanBuilder`, `build_pair_payload` (público), `prepare_pending_pair_stream`, `load_pending_pair_page`, `append_plan_rows`, `finalize_streamed_plan`, `PLANNING_PAIR_PAGE_SIZE`, o arquivo `test_pedidos_processing_stream_repository.py` inteiro, e os trechos correspondentes em `test_pedidos_processing_domain.py`, `test_pedidos_processing_repository.py` e `test_pedidos_processing_service.py`.

**Remover na mesma entrega, não depois.** Este projeto já pagou o preço de deixar código órfão no repositório: uma trava de UI ficou inalcançável por um redesenho e foi apagada meses depois como "feature morta", sem que ninguém percebesse que a intenção de produto continuava válida.

**FIX-01**
`_assert_stock_capacity` hoje pula a revalidação quando `tipo != "com"`. É inofensivo enquanto `sem_adequar` não reserva estoque — vira gap real no instante em que este plano o fizer reservar. **Nenhum teste existente falharia se isso for esquecido**, então precisa de teste próprio.

**Fechamento do ALOC-09**
A Phase 14 deixou, deliberadamente, `total_original_por_pedido` e `consumido_previo_*_por_pedido` como parâmetros **opcionais** em `processar_pedidos`, com fallback para um placeholder de execução única que **não** respeita o orçamento acumulado. O fallback é ruidoso (`logger.warning` citando ALOC-09, provado por dois testes de `caplog`).

Esta fase precisa:
- Ler o **total original** de cada pedido — todos os produtos, **sem** o filtro de já-processados que o snapshot de pendentes aplica.
- Ler o **acumulado consumido** em ORs anteriores — derivável de `qt_solicitada` e `qt_liquida` já gravados em `ordens_reserva`.
- Passar os dois ao motor, de modo que o `logger.warning` de ALOC-09 **deixe de ser emitido**.

**A prova de que fechou:** processar o mesmo pedido em duas execuções sucessivas não concede 5% novos na segunda, e o aviso some do log. Enquanto isso não passar, ALOC-09 não está entregue — mesmo com a Phase 14 fechada.

**Contrato a preservar:** as chaves de retorno de `processar_pedidos` não mudam de forma. O resultado do job continua limitado a contadores (`plannedCount`, `appliedCount`, `deferredCount`, `blockedCreditCount`) — sem listas de itens. A visibilidade do stand by vem por consulta REST na Phase 16/17, não inflando o payload do job.

### Claude's Discretion
- Forma da leitura dos dados de orçamento (query única com CTE, duas queries, ou extensão do snapshot existente) — desde que respeite o padrão de ports/adapters de `processing/`.
- Se a remoção do streaming vira commit separado dentro da fase ou entra junto do roteamento.
- Onde exatamente instrumentar a medição de memória do critério 4.

### Deferred Ideas (OUT OF SCOPE)
- **Persistência do motivo de stand by** e contador de execuções consecutivas (STANDBY-01/06) → Phase 16. Esta fase faz `preteridos`/`bloqueados_credito` passarem a ter valor real; **quem os persiste** é a 16.
- **Tags e filtro na UI** (STANDBY-02..05) → Phase 17, com a regra do design-system como critério de aceite.
- **Peças extras por sobra de estoque** (ALOC-11) → Phase 18.
- **Testes de propriedade com `hypothesis`** → Phase 19.
- **Guarda contra OR zerada** (ALOC-05) → Phase 13, que segue sem planos e é paralelizável.
- **A falha conhecida da suíte** (`test_read_projection...`) — teardown de asyncpg, fora do escopo do v1.3. Não consertar aqui.
</user_constraints>

## Project Constraints (from CLAUDE.md)

- **Consultar `.planning/codebase/` antes de varredura ampla** — cumprido: este documento não repete o levantamento de arquivo:linha já feito em `ARCHITECTURE.md`/`PITFALLS.md`, só complementa.
- **Consultar coding standards via MCP Backstage** (`backstage_get_coding_standards`) antes de editar — não disponível nesta sessão de pesquisa (ferramenta MCP não exposta ao agente de pesquisa); o executor da fase deve rodar essa consulta antes de editar, per CLAUDE.md.
- **Full refresh sem incremental** para `pedidos`/`estoque` (DELETE+INSERT a cada sync) — implicação direta para a leitura de orçamento desta fase (ver Assumptions Log, A1).
- **Isolamento por canal** no matching — a query de orçamento não precisa isolar por canal porque opera sobre `nr_pedido` (identidade única do pedido, que já pertence a um único canal por construção do motor).
- **async/await de ponta a ponta** — os dois novos métodos de leitura devem seguir o padrão async já usado em `SqlAlchemyProcessingPlannerSource`.
- **Nunca "limpar" imports `# noqa: F401` de `alembic/env.py`** — não aplicável (esta fase não adiciona tabela nova).
- **Commits/branches:** Conventional Commits, a partir de `develop`, nunca commitar direto em `main`.
- **Nenhuma coautoria de Claude em commits** (memória do usuário) — aplica-se à execução da fase, não a este documento.

## Fechamento do ALOC-09 — desenho da leitura de orçamento

### 1. Total original por pedido — por que a query pode reaproveitar o padrão existente sem duplicar linhas

A pergunta era: "precisa de query nova, ou dá para estender o SQL existente com uma CTE sem duplicar linhas?" A resposta, depois de ler `_PENDING_BASE_SQL` (`adapters.py:29-92`) linha a linha: **não estenda `_PENDING_BASE_SQL` em si** (ela existe para produzir `eligible_items`, que é deliberadamente **filtrado** pelas 3 cláusulas `NOT EXISTS` — exatamente o filtro que o total original **não pode** ter). Em vez disso, adicione uma **CTE irmã, no mesmo `WITH`**, que lê `pedidos` diretamente por `nr_pedido`, sem os `NOT EXISTS`:

```sql
WITH {_PENDING_BASE_SQL}
, pedido_scope AS (
    SELECT DISTINCT nr_pedido FROM eligible_pairs
)
, total_original AS (
    SELECT p.nr_pedido, sum(p.qt_entregar)::bigint AS total_original
    FROM pedidos p
    JOIN pedido_scope USING (nr_pedido)
    GROUP BY p.nr_pedido
)
SELECT nr_pedido, total_original FROM total_original
```

Isso não duplica linhas porque `pedido_scope` já é `DISTINCT nr_pedido` (um pedido pode ter N pares elegíveis, mas o `JOIN` com `pedidos p` traz de volta TODAS as linhas de `p` para aquele `nr_pedido` — inclusive tamanhos/produtos que não estão em `eligible_pairs` porque já têm OR ou já estão em `pedidos_processados`/`pedidos_processados_erp`). É exatamente o que CONTEXT.md pede: "todos os produtos, sem o filtro de já-processados". `sum(p.qt_entregar)` sobre essas linhas é o total original do pedido inteiro.

**Por que isso é seguro dado o full refresh:** a tabela `pedidos` é um espelho 1:1 da view `system_automation_pedidos_em_aberto` do Databricks (`ingestao/infrastructure/models.py::Pedido`, sem coluna derivada por este app). A exclusão de pares já processados (`pedidos_processados`, `ordens_reserva`) é um filtro **só deste app**, aplicado em cima da tabela `pedidos` — a criação de uma OR pelo app **não remove** a linha correspondente de `pedidos` (são tabelas diferentes, sem trigger entre elas). Portanto, para um pedido ainda com pelo menos um par pendente (é o único caso em que esta query roda, porque só pedidos com pares em `eligible_pairs` entram em `pedido_scope`), todas as linhas do pedido — processadas pelo app ou não — **continuam fisicamente presentes em `pedidos`**, contanto que a view de origem no Databricks ainda as considere "em aberto" do ponto de vista do ERP/Linx.

### 2. Acumulado consumido em ORs anteriores — onde está e como agregar sem compensar adição×corte

Confirmado lendo `pedidos/infrastructure/models.py::OrdemReserva` (`nr_pedido`/`cd_prod_cor` como PK composta, `itens` JSONB, `tipo` `'com'|'sem'`) e `motor_adequacao.py::adequar_grade_produto`/`marcar_stand_by`: **cada linha de `ordens_reserva` só existe para pares que efetivamente geraram OR** (`_commitar_par` só chama `resultados[par].extend(...)` incondicionalmente para o ramo `gerou_or`; o ramo `preterido` só grava em `resultados` quando `resultados_apenas_selecionados=False`, e `build_processing_plan` chama o motor com essa flag `True` — logo `selected`/o que vira `PlannedPair`/o que é persistido em `ordens_reserva` **nunca inclui itens em stand-by**). Isso simplifica a agregação: todo item dentro de `ordens_reserva.itens` já tem `status_item == "Gerar OR"`, com `qt_solicitada` (quantidade original pedida, preservada por `adequar_grade_produto` antes de sobrescrever `qt_liquida`) e `qt_liquida` (quantidade final, após corte ou adição).

```sql
WITH pedido_scope AS (
    SELECT unnest(:nr_pedidos::int[]) AS nr_pedido
)
SELECT
    o.nr_pedido,
    coalesce(sum(
        GREATEST((item->>'qt_liquida')::int - (item->>'qt_solicitada')::int, 0)
    ), 0)::bigint AS consumido_adicao,
    coalesce(sum(
        GREATEST((item->>'qt_solicitada')::int - (item->>'qt_liquida')::int, 0)
    ), 0)::bigint AS consumido_corte
FROM ordens_reserva o
JOIN pedido_scope USING (nr_pedido)
CROSS JOIN LATERAL jsonb_array_elements(o.itens) AS item
WHERE o.tipo = 'com'
GROUP BY o.nr_pedido
```

`GREATEST(diff, 0)` em cada direção, nunca subtraindo um do outro, implementa literalmente "adições e cortes não se compensam" (Pitfall 3 do `PITFALLS.md`) na camada SQL, e `WHERE o.tipo = 'com'` é redundante-mas-explícito: itens de `tipo='sem'` sempre têm `qt_solicitada == qt_liquida` (`aplicar_tudo_ou_nada` faz `item["qt_solicitada"] = item["qt_liquida"]` sem nunca cortar/aumentar), então mesmo sem o filtro o resultado seria o mesmo — mas o filtro documenta a intenção e evita depender desse invariante silenciosamente.

**Só existe UMA linha de `ordens_reserva` por par `(nr_pedido, cd_prod_cor)` para sempre** (PK composta, insert-only via `on_conflict_do_nothing`) — não há necessidade de deduplicar por execução; a soma sobre todas as linhas do pedido já é o acumulado de todas as execuções passadas.

### 3. Onde plugar isso sem violar a fronteira de I/O que `motor_adequacao.py` já protege

`processar_pedidos`/`_processar_pedidos_canal` já aceitam os 3 parâmetros keyword-only opcionais (`total_original_por_pedido`, `consumido_previo_adicao_por_pedido`, `consumido_previo_corte_por_pedido`) desde a Phase 14 (`domain/motor_adequacao.py:462-467`). **Nada muda na assinatura do motor.** O trabalho desta fase é só: em `build_processing_plan` (`processing/domain.py:641-666`, ramo `ADEQUAR`), passar esses 3 mappings adiante, populados a partir do novo método de porta.

**Escopo (`Claude's Discretion` resolvido):** um único método novo no `ProcessingPlannerSource` — `load_pedido_budget(nr_pedidos: set[int]) -> tuple[dict[int, int], dict[int, int], dict[int, int]]` — que roda as DUAS queries acima (podem ser uma única viagem ao banco via `db.execute` sequencial na mesma transação, sem round-trip extra relevante — a conexão já está aberta). **Só chamado para `ProcessingMode.ADEQUAR`** (o modo `SEM_ADEQUAR`/`aplicar_tudo_ou_nada` nunca toca o ledger — `motor_adequacao.py:744-782` não chama `_resolver_orcamento_pedido` no ramo `SEM_ADEQUAR` — então gastar uma query de orçamento para esse modo seria I/O desperdiçado). Isso é uma decisão de escopo que o `service.py::_plan_once` unificado precisa respeitar explicitamente: `stock`/`criterion`/`tolerance` viram incondicionais (per CONTEXT.md), mas o orçamento continua condicional a `mode is ADEQUAR`.

`nr_pedidos` para a chamada vem de `{int(item["nr_pedido"]) for item in pending_items}` — já materializado no momento em que `_plan_once` faz a hidratação global (linha ~292-296 atual), sem custo adicional de agrupamento.

### 4. Custo no volume do preflight (100k pares / 200k itens)

Ambas as queries são **agregadas, uma viagem por canal**, não N+1: `pedido_scope`/`ANY(:nr_pedidos::int[])` seguem o mesmo padrão já usado em `load_stock` (`adapters.py:269-302`, `ANY(CAST(:codes AS text[]))`). Tanto `pedidos.nr_pedido` quanto a PK de `ordens_reserva` (que começa por `nr_pedido`) já têm índice (`ix_pedidos_...` implícito de `index=True` na coluna, `PRIMARY KEY (nr_pedido, cd_prod_cor)`) — um `nr_pedido = ANY(...)` com até algumas dezenas de milhares de valores usa esses índices via bitmap/index scan, o mesmo padrão de custo que `load_stock` já paga hoje para até 100.000 pares. Distintos `nr_pedido` tendem a ser bem menores que `PLAN_PAIR_LIMIT` (100.000) porque um pedido normalmente tem múltiplos produtos/tamanhos — mas mesmo no pior caso (1 produto por pedido), o array de `ANY()` de ~100k inteiros é uma operação padrão do Postgres, não um limite prático como uma lista de `IN` inline geraria. **Recomendação: dividir em chunks de ~5.000 `nr_pedido` por query** (o mesmo espírito defensivo do chunk de 200 códigos em `load_stock`, embora aqui o limite prático do Postgres para `ANY(int[])` seja bem mais alto) — mais por disciplina de consistência com o resto do módulo do que por necessidade medida.

O `jsonb_array_elements` sobre `ordens_reserva.itens` roda só sobre as linhas **já existentes** para os pedidos em `pedido_scope** (não sobre o snapshot pendente de 200k itens) — o volume aqui é histórico (ORs já persistidas), tipicamente muito menor que o volume do canal sendo processado agora, e cresce de forma limitada (o denominador do orçamento nunca ultrapassa o pedido original).

## Ordem de remoção do streaming — o que sobrevive e o que precisa ser reposto

### O que o streaming realmente garantia (e por que não é uma propriedade perdida)

`StreamingPlanBuilder` + `append_plan_rows` existiam para permitir que `sem_adequar` **nunca materializasse o snapshot inteiro em Python** — persistindo cada página de 250 pares na mesma transação e descartando o payload assim que o `INSERT` terminava (`processing/domain.py:325-412`, comentário "cada payload pode ser descartado após INSERT"). Essa é uma otimização de memória **do processo Python**, não do banco — o `INSERT` em si sempre foi transacional independentemente do streaming.

Depois do roteamento, `SEM_ADEQUAR` passa a usar exatamente o mesmo caminho que `ADEQUAR` já usa hoje: `planner.load_pending_items` materializa a foto inteira em Python (`adapters.py:138-173`, já usa `yield_per=2_000` no cursor do lado do banco, mas acumula tudo em `items: list[dict]` no lado do app) → `build_processing_plan` → `processing.store_plan` grava em chunks de `APPLY_CHUNK_SIZE=250` (`repository.py:236-251`, já existia para `ADEQUAR` e não muda). **Não há novo código de persistência a escrever**: `store_plan` já é chunkeado e já é o único caminho usado por `ADEQUAR` — a "propriedade de memória" que se perde é apenas a hidratação em Python, e essa perda já está **orçada e medida** desde a Phase 14 (comentário em `processing/domain.py:34-37`: 434 MiB reais para 132.405 itens, ~600 MiB projetados no teto de 200k) — o preflight (`inspect_pending`) já é agnóstico de modo e já protegia os dois caminhos; só não era exercido pelo lado Python de `sem_adequar` porque ele nunca hidratava a foto inteira.

**Conclusão prática:** a remoção do streaming não exige repor NADA em troca — `store_plan` (write) e o preflight (read-side memory cap) já cobrem o que o streaming cobria, e cobrem melhor (streaming só protegia a memória de escrita; o preflight protege a memória de leitura, que é onde o risco real está).

### Ordem de edição recomendada dentro do mesmo commit/plano

1. Adicionar o branch novo em `build_processing_plan` para `SEM_ADEQUAR` (chama `processar_pedidos(modo=SEM_ADEQUAR)`), **sem ainda apagar o branch antigo em `service.py`** — implementação aditiva, testável isoladamente com os testes de domínio já existentes adaptados.
2. Trocar o corpo de `_plan_once` para o branch único parametrizado por `mode` (o corpo hoje usado só por `ADEQUAR`, `service.py:292-354`), carregando `stock`/`criterion`/`tolerance` incondicionalmente.
3. **Só então** apagar os símbolos: `StreamingPlanBuilder`, `PLANNING_PAIR_PAGE_SIZE`, `build_pair_payload` (público — a privada `_payload_for_pair` permanece), `prepare_pending_pair_stream`, `load_pending_pair_page` (de `ports.py` e `adapters.py`), `append_plan_rows`, `finalize_streamed_plan` (de `ports.py` e `repository.py`), e deletar `test_pedidos_processing_stream_repository.py` inteiro.
4. Atualizar os 4 arquivos de teste restantes (ver próxima seção) — inclusive os que quebram por ausência de `stock`, não só os que testam símbolos removidos diretamente.

Fazer nesta ordem (implementar → trocar → apagar) garante que em nenhum momento a suíte fica sem cobertura para o caminho novo enquanto o antigo ainda existe, e que a remoção do passo 3 acontece com o comportamento equivalente já provado pelo passo 1-2 — reduz o risco de "apaguei e só descobri o que quebrou depois".

## FIX-01 — o teste que falta

`_assert_stock_capacity` (`processing/infrastructure/repository.py:489-513`) tem `if tipo != "com": continue` dentro do loop que soma `requested[(cd_prod_cor, size, channel)]`. A correção é **remover essa linha** (revalidar para os dois tipos). O teste que já existe, `test_adequation_revalidates_stock_before_writes` (`test_pedidos_processing_repository.py:382-395`), usa `_planned_pair(tipo="com", qty=2)` com `stock_qty=1` — prova exatamente o comportamento que precisa passar a valer também para `tipo="sem"`.

**Teste novo necessário** (nome sugerido: `test_sem_adequar_revalidates_stock_before_writes`), espelhando o existente mas com `_planned_pair(tipo="sem", qty=2)` (o **default** do helper já é `tipo="sem"` — `_planned_pair(*, tipo: str = "sem", qty: int = 2)`, linha 153) e `_seed_source(db, stock_qty=1)`: hoje esse cenário específico (stock insuficiente + tipo="sem") **passa silenciosamente sem erro** porque o `continue` pula a linha antes de somar `requested`; depois do FIX-01, deve levantar `ProcessingPlanConflict("processing_plan_stale")`, igual ao caso `"com"`.

**Risco de regressão em testes existentes que usam o default `tipo="sem"`:** todos os outros usos de `_planned_pair()` no arquivo (`test_writer_applies_pair_and_checkpoint_in_same_transaction`, etc.) usam `_seed_source()` com o default `stock_qty=20` e `_planned_pair()` com `qty=2` — como `20 >= 2`, a revalidação nova passa sem alterar o resultado desses testes. Conferido: nenhum teste existente combina `tipo="sem"` (implícito) com estoque insuficiente, então FIX-01 não quebra nada além do que precisa — mas **precisa do teste novo**, porque, como o CONTEXT.md já registra, nenhum teste hoje falharia se o `continue` fosse esquecido.

## Impacto na suíte — por arquivo e por teste

### (a) Roteamento (`SEM_ADEQUAR` → caminho global)

| Arquivo | Teste | O que quebra | Categoria |
|---|---|---|---|
| `test_pedidos_processing_domain.py` | `test_exact_channel_excludes_other_channel_without_counting_deferred` (linha 94) | Chama `build_processing_plan(mode=SEM_ADEQUAR, ...)` **sem `stock`**. Depois do roteamento, `SEM_ADEQUAR` passa por `processar_pedidos` que exige um dict de estoque (mesmo vazio funcionaria para `_indexar_estoque_por_produto`, mas o item não teria estoque disponível e o par cairia em `preteridos`/stand-by em vez de gerar OR) | **Muda de asserção** — precisa de `stock={"Franquia": {"PROD_36": 2}}` (ou equivalente) para o item continuar virando "Gerar OR"; sem isso o teste passa a falhar por `draft.rows` vir vazio |
| idem | `test_mixed_channel_pair_fails_closed_for_every_scope` (linha 114) | Nenhuma — o erro é levantado em `_eligible_pairs` (checagem de canal), **antes** do branch de modo rodar | **Não quebra** — validar que continua passando sem alteração |
| idem | `test_plan_preserves_numeric_sizes_and_freezes_linx_and_source_hash` (linha 134) | Mesma causa do primeiro: sem `stock`, os 2 itens do par (tamanhos 36/37) não teriam estoque e o par cairia em stand-by, então `draft.rows[0]` não existiria com o payload esperado | **Muda de asserção** — precisa de `stock` cobrindo `SKU|01_36` e `SKU|01_37` com quantidade suficiente (>= 2 e >= 3) para reproduzir "grade completa disponível" |
| idem | `test_streaming_builder_keeps_only_counts_hash_and_cumulative_cap` (linha 198) | Importa e testa `StreamingPlanBuilder` diretamente | **Morre** (remover — símbolo deixa de existir) |
| idem | `test_plan_aborts_during_build_when_cumulative_payload_exceeds_cap` (linha 231) | Chama `build_processing_plan(mode=SEM_ADEQUAR, ...)` duas vezes sem `stock`, monkeypatchando `MAX_PLAN_TOTAL_BYTES` para forçar estouro de bytes cumulativos durante a construção das rows | **Muda de asserção** — precisa de `stock` suficiente para os itens virarem "Gerar OR" (senão nem chegam a ser incluídos em `rows` para estourar o cap); a intenção do teste (estouro de payload cumulativo) continua válida, só precisa da fixture de estoque |
| `test_pedidos_processing_service.py` | `test_sem_adequar_planning_uses_keyset_pages_without_global_hydration` (linha 498) | Testa exatamente o comportamento que deixa de existir: paginação via `load_pending_pair_page`, `load_pending_items` nunca chamado. Depois do roteamento, `load_pending_items` **precisa** ser chamado para `SEM_ADEQUAR` também | **Morre e é substituído** por um teste equivalente ao `ADEQUAR` de hoje (hidratação global, `load_stock`/`load_adequation_config` chamados, plano via `store_plan`) — não é mera adaptação, é reescrita do cenário |
| `test_pedidos_processing_repository.py` | `test_planner_keyset_pages_cover_each_pair_once` (linha 637) | Testa `prepare_pending_pair_stream`/`load_pending_pair_page` do adapter real (contra banco) | **Morre** (símbolos removidos do adapter) |

### (b) Remoção do streaming

| Arquivo | O que remover | Categoria |
|---|---|---|
| `test_pedidos_processing_stream_repository.py` | Arquivo inteiro (313 linhas) — testa só `append_plan_rows`/`finalize_streamed_plan` | **Morre inteiro** |
| `test_pedidos_processing_domain.py` | Import de `StreamingPlanBuilder` (linha 22) + teste da linha 198 | **Morre** (import + teste) |
| `test_pedidos_processing_repository.py` | Qualquer teste exercitando `append_plan_rows`/`finalize_streamed_plan` do repository real — busca por esses símbolos no arquivo não encontrou testes dedicados além do arquivo (a) acima; **confirmar na hora da implementação** se `store_plan` já cobre 100% dos casos que `finalize_streamed_plan` cobria (contiguidade de ordinais, conflito de header) — parece que sim, lendo os dois métodos lado a lado (`repository.py:159-262`) | **Revisão, não remoção garantida** — sinalizado como item a confirmar, não um fato fechado |
| `test_pedidos_processing_service.py` | Fakes de `Planner`/`Processing` que implementam os 4 métodos de streaming para satisfazer o `Protocol` antigo (linhas ~504-589, e possivelmente outras classes fake no arquivo) | **Editar** — remover os métodos de streaming dos fakes; **conferir se algum outro teste do arquivo usa esses fakes e depende deles existirem** (proteção contra quebra por remoção de método que outro teste ainda chama) |

### (c) FIX-01

Ver seção dedicada acima — 1 teste novo, nenhum teste existente quebra (confirmado: nenhuma combinação de `tipo="sem"` + estoque insuficiente existe hoje na suíte).

### (d) Fechamento do ALOC-09

| Arquivo | Teste | Mudança |
|---|---|---|
| `app/tests/test_pedidos_motor.py` | Os 2 testes de `caplog` do ALOC-09 (criados na 14-06 — Phase 14 Summary cita "2 testes do fallback ALOC-09 (`caplog`)") | **Muda de asserção**, não morre: hoje um afirma que o warning **sai** quando os parâmetros não são passados, o outro que **não sai** quando são passados. Depois desta fase, o segundo cenário (parâmetros fornecidos) passa a ser o **caminho de produção real** — o teste que prova "não sai quando fornecido" continua válido como está (a fase não muda o comportamento de `processar_pedidos` em si, só quem chama). Nenhuma mudança de assinatura aqui: o motor já aceita os parâmetros desde a 14-06. **O que muda é a necessidade de um teste de integração NOVO** (fora de `test_pedidos_motor.py`, provavelmente em `test_pedidos_processing_service.py` ou um teste dedicado) provando que `_plan_once`/`build_processing_plan`, quando chamado via o fluxo real (banco), **de fato busca e passa** os dados reais — sem isso, o teste de `caplog` do motor prova que a função aceita os dados, mas não que alguém os está entregando de verdade |
| Novo — local sugerido: `test_pedidos_processing_repository.py` ou `test_pedidos_processing_service.py` | `test_orcamento_persiste_entre_execucoes_sucessivas` (nome sugerido) | Novo teste de integração: processa um pedido pequeno (ex. 20 peças, para o orçamento de 5% ser >0 e observável — lembrar do Pitfall 4, nunca usar `total_grade < 20` em teste de orçamento por causa do arredondamento), roda `_plan_once`+aplicação uma vez, cria uma 2ª rodada sobre o que sobrou do mesmo pedido, e assert que a soma de adição/corte ao longo das duas rodadas nunca excede `floor(total_original * 5%)` — **e** que `caplog` não contém `"ALOC-09"` em nenhuma das duas rodadas |

## Divisão em planos executáveis (waves e dependências)

**Restrição real deste projeto:** `workflow.use_worktrees: false` — o executor é sequencial, então "waves paralelas" não trazem ganho de tempo real; o valor de dividir em waves aqui é **ordem de dependência e isolamento de risco por commit**, não paralelismo. Arquivos centrais (`ports.py`, `domain.py`, `adapters.py`, `repository.py`, `service.py`) são tocados por mais de uma preocupação — **não crie planos "paralelos" que editam o mesmo arquivo em ordens diferentes**; sequencie.

| Wave | Escopo | Arquivos tocados | Depende de | Por que nesta ordem |
|---|---|---|---|---|
| 1 | Leitura de orçamento (aditiva, não ligada ainda) — novo método `load_pedido_budget` no port + implementação SQL + teste de integração isolado da query | `ports.py` (só adiciona ao `Protocol`), `adapters.py` (implementa) | Nada | Puramente aditiva — zero risco de regressão porque nada ainda chama o método novo; pode ser validada isoladamente contra o banco de teste antes de qualquer coisa tocar `_plan_once` |
| 2 | FIX-01 — remover `if tipo != "com": continue` + teste novo | `repository.py` (`_assert_stock_capacity`), `test_pedidos_processing_repository.py` | Nada | Isolado a uma função e um teste; nenhum overlap com wave 1 (arquivos diferentes) nem com wave 3 (não mexe em `_plan_once`/`domain.py`) — pode ser feito em qualquer ordem relativa à wave 1, mas sequencie por simplicidade de revisão |
| 3 | Roteamento + fechamento ALOC-09 + remoção de streaming (a peça grande, tasks sequenciais dentro do mesmo plano) | `service.py` (`_plan_once`), `domain.py` (`build_processing_plan`, remoção de `StreamingPlanBuilder`), `ports.py` (remoção dos 4 métodos de streaming), `adapters.py` (remoção de `prepare_pending_pair_stream`/`load_pending_pair_page`), `repository.py` (remoção de `append_plan_rows`/`finalize_streamed_plan`), os 4 arquivos de teste (`test_pedidos_processing_domain.py`, `_repository.py`, `_service.py`, deletar `_stream_repository.py`), `test_pedidos_motor.py` (novo teste de integração de orçamento persistente) | Wave 1 (precisa do `load_pedido_budget` existir para ligar ao `_plan_once`) | É a mudança que precisa acontecer atômica: implementar o branch novo, trocar `_plan_once`, e só então apagar o streaming — per CONTEXT.md, remover na mesma entrega. Fazer em 2 planos separados (rotear primeiro, remover depois) criaria uma janela real com dois caminhos concorrentes fazendo a mesma coisa, que é exatamente o anti-padrão que este projeto quer evitar |
| 4 | Medição de pico de memória (critério 5 do ROADMAP) + validação end-to-end | Novo arquivo de teste de performance (ex. `test_pedidos_processing_sem_adequar_memory_024.py`, seguindo a convenção `_024` já usada por `test_pedidos_motor_performance_024.py`) | Wave 3 (precisa do caminho novo existir para medir) | Só faz sentido medir depois que `SEM_ADEQUAR` de fato hidrata a foto global — medir antes mediria o caminho antigo (streaming), que não é o que o critério 4 do ROADMAP pede provar |
| 5 (opcional, baixo risco) | Atualização de `docs/adequacao.md` (linhas 55-62 e 91-105 já ficam desatualizadas: streaming, `ceil`/`float` na descrição de `adequar_grade_produto`) | `docs/adequacao.md` | Wave 3 | Documentação, não bloqueia entrega; pode ser o último commit da fase ou até adiado, mas sinalizado aqui para não esquecer (ARCHITECTURE.md já apontou esse débito) |

**Não paralelizável dentro da wave 3:** todas as tasks dessa wave tocam o mesmo conjunto pequeno de arquivos centrais — trate como uma sequência de commits dentro de UM plano, não como sub-planos distintos.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.3.0+ com `pytest-asyncio` (modo `auto`) |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_processing_domain.py app/tests/test_pedidos_processing_service.py app/tests/test_pedidos_processing_repository.py app/tests/test_pedidos_motor.py -q` |
| Full suite command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |

**Ambiente:** `uv run pytest` local falha no Windows (dependência `pyicu`). Testes **sempre** via Docker. Baseline herdada da Phase 14: **820 passed, 16 skipped, 6 failed** (6 falhas já antecipadas e não fechadas pela 14-06, mais a falha pré-existente de teardown asyncpg). O plano 14-07 (migração consciente dos 2 arquivos de assinatura fixa + 3 testes de regra antiga) pode ou não já ter rodado antes desta fase — **confirmar o baseline real rodando a suíte completa antes de tocar qualquer arquivo**, não assumir o número da Phase 14 como верdade atual.

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ARCH-01 (roteamento) | `SEM_ADEQUAR` usa `build_processing_plan`/hidratação global, nenhuma decisão depende de página | integration | `pytest app/tests/test_pedidos_processing_service.py -k "sem_adequar" -x` (teste reescrito da linha 498) | ❌ precisa reescrita — teste atual prova o oposto (paginação) |
| ARCH-01 (remoção do streaming) | Símbolos de streaming não existem mais no repositório | static/negative | `grep -rn "StreamingPlanBuilder\|PLANNING_PAIR_PAGE_SIZE\|prepare_pending_pair_stream\|load_pending_pair_page\|append_plan_rows\|finalize_streamed_plan" app/modules app/tests` (deve retornar vazio) | ❌ verificação manual/CI, não é teste pytest — recomenda-se rodar este grep como último passo de verificação da wave 3 |
| FIX-01 | Revalidação de capacidade cobre `tipo == "sem"` | integration | `pytest app/tests/test_pedidos_processing_repository.py::test_sem_adequar_revalidates_stock_before_writes -x` | ❌ Wave 0 — teste novo (nome sugerido) |
| ALOC-09 (fechamento) | Mesmo pedido em 2 execuções não ganha 5% novos; warning some do log | integration | `pytest app/tests/test_pedidos_processing_repository.py::test_orcamento_persiste_entre_execucoes_sucessivas -x` (ou local equivalente em `test_pedidos_processing_service.py`) | ❌ Wave 0 — teste novo (nome sugerido) |
| Critério 4 do ROADMAP (memória) | Pico de RSS do canal inteiro em `SEM_ADEQUAR` fica dentro do orçamento do worker (~600 MiB projetado, 1280/1536 MiB reservados) | manual-only + smoke automatizado | `pytest app/tests/test_pedidos_processing_sem_adequar_memory_024.py -x` (novo, ver abaixo) | ❌ Wave 0 — arquivo novo |

### Sampling Rate
- **Por commit de task:** rodar o arquivo de teste específico tocado (comando "quick run" acima, filtrado por arquivo)
- **Por merge de wave:** suíte completa via Docker
- **Gate da fase:** suíte completa verde (mesmo baseline conhecido: só a falha pré-existente de `test_read_projection_empty_contracts_execute_real_sql`) antes de `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `test_pedidos_processing_repository.py::test_sem_adequar_revalidates_stock_before_writes` — cobre FIX-01
- [ ] `test_pedidos_processing_repository.py::test_orcamento_persiste_entre_execucoes_sucessivas` (ou equivalente) — cobre o fechamento do ALOC-09 ponta a ponta
- [ ] `test_pedidos_processing_sem_adequar_memory_024.py` (novo, ver seção "Medição de memória" abaixo) — cobre o critério 4
- [ ] Reescrita de `test_sem_adequar_planning_uses_keyset_pages_without_global_hydration` para provar o oposto do que prova hoje (hidratação global, não paginação)
- [ ] Framework: nenhum novo — pytest/pytest-asyncio já cobrem tudo; `resource` (stdlib) para medição de memória, sem dependência nova

### Medição de pico de memória (critério 4) — desenho recomendado

Não existe hoje nenhuma instrumentação de memória automatizada no repositório (os números "434 MiB"/"8,9 MiB" em `docs/adequacao.md`/`processing/domain.py` são comentários de medição manual anterior, não testes reprodutíveis — busca por `psutil`/`tracemalloc`/`RSS`/`resource.` no código-fonte não encontrou nenhum uso). **Recomendação:** um teste de performance novo, seguindo a convenção `_024` já estabelecida (`test_pedidos_motor_performance_024.py`), usando `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` (stdlib, disponível em Linux — o container Docker onde os testes rodam) antes e depois de uma chamada a `build_processing_plan` com um dataset sintético grande (ex. 40-50k pares/100k+ itens, gerado programaticamente, não lido do banco) em modo `SEM_ADEQUAR`, comparando o delta contra um teto (ex. 700 MiB, com margem sobre os ~600 MiB projetados). **Isto é uma recomendação de desenho, não uma medição real** — nenhum número de pico real para `SEM_ADEQUAR` foi medido nesta pesquisa; o teste deve ser tratado como instrumentação nova a ser calibrada na primeira execução, não como um valor já validado.

## Security Domain

> `security_enforcement` não está definido em `.planning/config.json` — tratado como habilitado (default).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Não (fase não toca rotas HTTP nem autenticação) | — |
| V3 Session Management | Não | — |
| V4 Access Control | Não (RBAC já protege as rotas que disparam o job; esta fase é interna ao worker) | — |
| V5 Input Validation | Sim, já coberto | `PlannedPair.__post_init__`/`PlanDraft.__post_init__` (`processing/domain.py`) já validam ranges de `nr_pedido`, tamanho de `cd_prod_cor`, tamanho de payload — nenhuma validação nova necessária para os 2 métodos de leitura de orçamento além de bind params tipados (`int[]`), que já previnem injection por construção (SQLAlchemy `text()` com parâmetros nomeados, mesmo padrão de `_PENDING_BASE_SQL`) |
| V6 Cryptography | Não aplicável | — |

### Known Threat Patterns for este domínio

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Query de orçamento sem escopo (`nr_pedido` vindo de fora sem validação) permitindo leitura além do canal/pedidos elegíveis | Information Disclosure (baixo risco — dados já pertencem ao mesmo tenant/app, sem multi-tenancy neste sistema) | Escopar sempre por `{int(item["nr_pedido"]) for item in pending_items}` — nunca aceitar `nr_pedido` arbitrário vindo de fora da foto já validada pelo preflight |
| Reintrodução do double-spend do orçamento por bug de wiring (esquecer de passar os parâmetros reais) | Tampering (integridade do orçamento financeiro) | Já mitigado pelo desenho da Phase 14: `OrcamentoPedido` exige os 4 campos sem default (`TypeError` se esquecido) + `logger.warning` agregado citando ALOC-09 enquanto o placeholder for usado — o teste de `caplog` já prova a ausência do warning quando os dados reais são fornecidos |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Uma linha de `pedidos` para um par `(nr_pedido, cd_prod_cor, sg_tamanho)` que já gerou OR pelo app **continua fisicamente presente** na tabela `pedidos` até que o ERP/Linx confirme o fulfillment pelo lado de origem (Databricks), e não desaparece só porque o app já processou o pedido. Esta é a premissa central que torna "somar `qt_entregar` de `pedidos` sem o filtro de já-processados" equivalente a "o total original do pedido inteiro". Se rows puderem desaparecer do mirror `pedidos` (full refresh) ANTES de todos os pares do mesmo pedido serem processados pelo app — por exemplo, se o Linx confirmar o fulfillment de um produto específico independentemente do resto do pedido — o `total_original` calculado por esta query **encolheria** ao longo de execuções sucessivas, reproduzindo exatamente o Pitfall 3 (double-spend por denominador que encolhe) que a Phase 14/15 existem para fechar. | "Fechamento do ALOC-09 — desenho da leitura de orçamento", item 1 | ALTO se errado: double-spend silencioso do orçamento ±5%, o próprio bug que a Phase 15 existe para fechar, mascarado atrás de uma query que "parece" resolver ALOC-09 mas reintroduz o problema por outro caminho. **Mitigação recomendada independente de confirmar A1:** somar, como fonte suplementar de baixo custo, `qt_solicitada` de `ordens_reserva.itens` para qualquer par do mesmo `nr_pedido` que tenha OR mas **não** apareça mais na leitura direta de `pedidos` (`LEFT JOIN`/`UNION` complementar) — isso fecha o buraco custando pouco, e deveria ser incluído na query de `total_original` independentemente da resposta a A1, como defesa em profundidade. |
| A2 | O teto de memória (~600 MiB projetado) medido para `ADEQUAR` com 200k itens se aplica igualmente ao volume elegível de `SEM_ADEQUAR`, mesmo que este último hoje aceite todo par sem filtro de crédito (potencialmente mais pares elegíveis por execução). `ARCHITECTURE.md` já sinaliza isso como risco a validar, não como fato confirmado. | Validation Architecture, "Medição de pico de memória" | MÉDIO: se o volume elegível de `SEM_ADEQUAR` for estruturalmente maior que o de `ADEQUAR` no canal "Todos", o pico real pode se aproximar ou ultrapassar os 1280-1536 MiB reservados ao worker — só a medição real (Wave 4) resolve esta suposição. |
| A3 | Nenhum outro teste na suíte, além dos listados na seção "Impacto na suíte", depende de comportamento do streaming ou do branch condicional de `stock` em `service.py` — a busca de raio de impacto foi feita por grep de símbolo nos 4 arquivos que `ARCHITECTURE.md`/CONTEXT.md já apontaram, não uma varredura de toda a suíte. | "Impacto na suíte" | BAIXO: se houver um teste esquecido (ex. em `test_pedidos_routes.py`, que testa endpoints HTTP que disparam o job), ele quebraria de forma barulhenta (falha de import/asserção), não silenciosamente — o risco é de retrabalho, não de regressão não detectada. |

**Se esta tabela estivesse vazia:** não está — A1 em particular precisa de confirmação ativa ou da mitigação defensiva sugerida antes de considerar o fechamento do ALOC-09 robusto para múltiplas semanas de operação (não só para o teste de "2 execuções sucessivas" que o critério de sucesso 5 pede — esse teste PASSARIA mesmo com A1 errado, porque o cenário de "produto já removido do mirror `pedidos`" só se manifesta depois de um ciclo real de fulfillment no Linx, não em 2 execuções de teste consecutivas no mesmo dia).

## Open Questions

1. **A tabela `pedidos` (mirror do Databricks) alguma vez remove um par que já tem OR do app, antes do pedido inteiro estar processado?**
   - What we know: full refresh (DELETE+INSERT) a cada sync; o app não tem conexão com o ERP (placebo `estoque_virtual` documenta isso); a view de origem `system_automation_pedidos_em_aberto` corta "em aberto" por algum critério do lado do Databricks/ERP/Linx que este código não define.
   - What's unclear: se esse corte reage à existência de uma OR do app (não deveria, já que não há integração) ou a um evento independente no Linx (provável, dado o milestone "Saída Linx" já documentado na memória do usuário).
   - Recommendation: confirmar com a mantenedora ou com quem administra a view Databricks antes de finalizar o desenho da query de `total_original`; até lá, implementar a mitigação defensiva de A1 (somar `qt_solicitada` de ORs cujo par não aparece mais em `pedidos`) independentemente da resposta, por ser barata e por fechar o buraco nos dois cenários.

2. **O pico de memória real de `SEM_ADEQUAR` depois do roteamento — quanto excede (ou não) o de `ADEQUAR`?**
   - What we know: a estrutura de código é a mesma (agrupar por produto, ordenar por prioridade, consumir estoque local); o preflight já limita a 200k itens para os dois modos.
   - What's unclear: se o volume elegível de `SEM_ADEQUAR` no canal "Todos" hoje (sem filtro de crédito) é sistematicamente maior que o de `ADEQUAR` a ponto de aproximar o teto de 1280-1536 MiB do worker.
   - Recommendation: medir com o teste da Wave 4 antes de declarar o critério 4 do ROADMAP satisfeito; se o pico ultrapassar a margem seguramente, a alternativa já documentada em `ARCHITECTURE.md` (processar por canal em vez de "Todos" de uma vez) resolve sem tocar orquestração.

3. **`test_pedidos_processing_repository.py` tem algum teste dedicado a `append_plan_rows`/`finalize_streamed_plan` além do arquivo `_stream_repository.py`?**
   - What we know: a busca de raio de impacto do `ARCHITECTURE.md` não cita nenhum, e a leitura desta sessão de `test_pedidos_processing_repository.py` (todos os nomes de teste do arquivo) não achou nenhum candidato óbvio.
   - What's unclear: 100% de certeza sem rodar a suíte completa com os símbolos já removidos (o jeito mais confiável de descobrir uso residual é deixar o import quebrar).
   - Recommendation: ao remover, rodar a suíte completa uma vez ANTES de editar os testes (para capturar o baseline) e uma vez DEPOIS de remover os símbolos de produção mas ANTES de editar os testes (para ver exatamente quais falham por `ImportError`/`AttributeError`) — é o sinal mais confiável de raio de impacto real, mais barato que grep exaustivo.

## Riscos de execução — onde esta fase pode mudar comportamento silenciosamente

**O risco central, já nomeado no CONTEXT (seção Specifics) e não repetido aqui em detalhe:** `SEM_ADEQUAR` hoje aceita **todo par elegível incondicionalmente** (`selected = eligible`, sem checar crédito, estoque ou furo de grade — o motor nunca é chamado). Depois desta fase, o mesmo modo passa a recusar por crédito (`is_sem_credito`), por estoque insuficiente (tudo-ou-nada, `aplicar_tudo_ou_nada`) e por furo de grade (`tem_furo_de_grade`). **O volume de ORs geradas por execução vai cair — isso é o comportamento CORRETO, não uma regressão.**

**Como distinguir "caiu porque a regra está certa agora" de "caiu demais porque tem bug", concretamente:**

1. **Comparação categorizada antes/depois, não visual.** Rodar `SEM_ADEQUAR` sobre o MESMO snapshot (uma foto real ou sintética, sanitizada) com o código antigo (`selected = eligible`) e com o código novo (`processar_pedidos(modo=SEM_ADEQUAR)`), e categorizar cada par que estava em `selected` antes mas não está depois em exatamente um destes 3 buckets: `sem_credito` (o pedido do par está em `bloqueados_credito` no resultado novo), `sem_estoque_grade` (o par está em `preteridos`, motivo == furo de grade OU tudo-ou-nada indisponível), `outro` (qualquer par que caiu por razão fora dessas duas — **esse bucket deveria estar vazio**; se não estiver, há bug). Isso é exatamente o "Diff categorizado" que `PITFALLS.md` (Pitfall 6) já recomenda para a troca do motor, aplicado aqui à troca de roteamento — reaproveitar a mesma técnica, não reinventar.
2. **Contagem por motivo, não só contagem total.** O ROADMAP/CONTEXT já preveem que `deferred_count`/`blocked_credit_count` passam a ter valor real (hoje sempre 0 para `sem_adequar`). Depois do roteamento, comparar a PROPORÇÃO entre os dois contadores contra a proporção observada historicamente em `ADEQUAR` para o mesmo canal — uma proporção muito diferente (ex. 90% caindo por "sem estoque" quando `ADEQUAR` historicamente via 30%) é sinal de alerta para investigar antes de assumir que é só a regra nova fazendo efeito.
3. **Teste de regressão dedicado, não confiar só na suíte unitária.** Nenhum teste unitário isolado consegue provar "o volume caiu pela razão certa" — isso só aparece comparando execuções sobre o MESMO dataset. Recomenda-se que a wave 3 inclua, como parte da verificação manual (não necessariamente um `assert` de CI), rodar o comando de diff categorizado acima contra um snapshot de homologação antes de considerar a fase pronta para produção — alinhado com o Pitfall 6 do `PITFALLS.md`, que já pede exatamente isso para a troca do motor e se aplica igualmente aqui.

## Sources

Todas as afirmações sobre código vieram de leitura direta nesta sessão (2026-08-17); não é um domínio com ecossistema de terceiros a pesquisar.

- `.planning/phases/15-roteamento-global-do-modo-sem-adequa-o/15-CONTEXT.md` — decisões travadas, discrição, escopo out
- `.planning/research/v1.3/ARCHITECTURE.md` — pontos de integração arquivo:linha (não repetidos aqui)
- `.planning/research/v1.3/PITFALLS.md` — pitfalls 3, 4, 6 diretamente aplicáveis a esta fase
- `.planning/phases/14-motor-de-aloca-o-puro-regras-de-neg-cio/14-06-SUMMARY.md` — o que a Phase 14 entregou, o placeholder e o fallback ruidoso
- `.planning/ROADMAP.md` — critérios de sucesso da Phase 15, herança da Phase 14
- `.planning/REQUIREMENTS.md` — ARCH-01, FIX-01, status de ALOC-09
- `app/modules/pedidos/processing/application/service.py` — `_plan_once`, os dois ramos atuais
- `app/modules/pedidos/processing/domain.py` — `build_processing_plan`, `StreamingPlanBuilder`, `PlanDraft`
- `app/modules/pedidos/processing/application/ports.py` — `ProcessingPlannerSource`/`ProcessingRepository` Protocols
- `app/modules/pedidos/processing/infrastructure/adapters.py` — `_PENDING_BASE_SQL`, `load_stock` (padrão de chunk/ANY reaproveitado)
- `app/modules/pedidos/processing/infrastructure/repository.py` — `_assert_stock_capacity`, `store_plan`, `append_plan_rows`/`finalize_streamed_plan`
- `app/modules/pedidos/domain/motor_adequacao.py` — assinatura de `processar_pedidos`, `_resolver_orcamento_pedido`, `aplicar_tudo_ou_nada`
- `app/modules/pedidos/domain/orcamento_pedido.py` — `OrcamentoPedido`, os 4 campos sem default
- `app/modules/pedidos/infrastructure/models.py` — `OrdemReserva` (JSONB `itens`, PK composta)
- `app/modules/ingestao/infrastructure/models.py` — `Pedido` (tabela `pedidos`, índices)
- `app/modules/pedidos/infrastructure/repositorio_produtos.py` — precedente de agregação sobre `ordens_reserva.itens` JSONB (`qt_solicitada`/`qt_liquida`)
- `app/tests/test_pedidos_processing_domain.py`, `test_pedidos_processing_service.py`, `test_pedidos_processing_repository.py`, `test_pedidos_processing_stream_repository.py` — leitura completa dos nomes de teste e das seções afetadas
- `docs/adequacao.md` — números de memória já documentados (434 MiB / 8,9 MiB), confirmando ausência de instrumentação automatizada
- `.planning/codebase/TESTING.md` — convenções de teste do repositório
- `.claude/CLAUDE.md` — stack, placebos, regras de arquitetura

## Metadata

**Confidence breakdown:**
- Leitura de orçamento (queries, escopo, custo): MEDIUM-HIGH — o desenho SQL é sólido e verificado contra o schema real, mas a premissa central (A1: rows de `pedidos` não somem antes do pedido inteiro ser processado) não pôde ser confirmada nesta sessão, só inferida de documentação de placebo existente.
- Remoção do streaming e impacto na suíte: HIGH — cada teste citado foi lido diretamente nesta sessão, não inferido por nome.
- FIX-01: HIGH — comportamento atual e teste faltante confirmados por leitura direta.
- Medição de memória: LOW — nenhuma instrumentação existente no repositório para basear a recomendação; é um desenho novo, não uma prática validada.

**Research date:** 2026-08-17
**Valid until:** válido enquanto `processing/domain.py`, `motor_adequacao.py` e o schema de `pedidos`/`ordens_reserva` não mudarem de estrutura — recomenda-se reconfirmar se a Phase 14-07 (migração dos testes de assinatura fixa) rodar entre esta pesquisa e o início do planejamento.
