# Architecture Research — Motor de alocação de OR (v1.3)

**Domain:** Integração do novo motor de alocação (crédito + estoque compartilhado + prioridade global + furo de grade + orçamentos ±5% por pedido) na orquestração durável existente do processamento de Pedidos.
**Researched:** 2026-08-14
**Confidence:** HIGH (todas as afirmações abaixo foram conferidas linha a linha no código listado; nenhuma é dedução por nome de símbolo)

## Sumário executivo

O processamento durável (`app/modules/pedidos/processing/`) já é uma máquina de estados sólida: lock advisory de sessão → claim → **planeja uma vez** (`_plan_once`, congela `plan_hash`) → **aplica em checkpoints** (`_apply_chunks`, resumível). Essa camada não precisa mudar para acomodar o motor novo — ela só entende "recebo um `PlanDraft` determinístico e o aplico em chunks". O trabalho real está em três pontos:

1. **Convergir `SEM_ADEQUAR` para o caminho global** (`build_processing_plan`), porque prioridade global e estoque compartilhado são fundamentalmente incompatíveis com decisão página-a-página. Os números medidos (434 MiB reais / 1280–1536 MiB reservados) sustentam isso com folga.
2. **Remover o código de streaming** (`StreamingPlanBuilder` e o que só existe para alimentá-lo) na mesma entrega — ele fica inalcançável, e este projeto já pagou o preço de deixar código morto no repo.
3. **Adicionar um canal lateral pequeno e determinístico** (`PlanDraft.deferred_pairs` / `blocked_credit_pairs` → nova tabela `pedido_standby_motivo`) para expor o motivo do stand-by na tela sem tocar o contrato de 4 contadores do job nem o contrato de chaves do motor (`resultados/selecionados/preteridos/bloqueados_credito/pares_processados`) — ambos protegidos explicitamente em `.planning/PROJECT.md` § Constraints.

Um achado colateral relevante: o caminho de aplicação (`SqlAlchemyProcessingWriter._assert_stock_capacity`, `repository.py:489-513`) hoje **pula a revalidação de capacidade de estoque para `tipo != "com"`** — ou seja, para `sem_adequar`. Isso precisa ser corrigido junto, senão o novo `sem_adequar` planeja contra estoque compartilhado mas aplica sem o double-check que `adequar` já tem.

---

## 1. Caminho streaming vs. global — é seguro rotear `SEM_ADEQUAR` pelo `build_processing_plan`?

**Sim, e mais que seguro: é a única forma de implementar as regras novas.** Duas linhas de raciocínio, uma funcional e uma de memória.

### 1.1 Por que streaming e prioridade global são incompatíveis (não é só desempenho)

`_plan_once` (`service.py:189-354`) tem hoje dois ramos:

- `SEM_ADEQUAR` (`service.py:211-290`): pagina 250 pares por vez via `planner.load_pending_pair_page` (`adapters.py:200-267`), e para cada página monta o payload com `build_pair_payload` (`processing/domain.py:598-615`) **sem olhar nenhum outro par**. Cada página é uma decisão isolada.
- `ADEQUAR` (`service.py:292-354`): carrega a foto inteira (`planner.load_pending_items`, sem streaming — `adapters.py:138-173`) e chama `build_processing_plan` → `processar_pedidos` (`motor_adequacao.py:200-300`) uma única vez sobre o conjunto inteiro.

A Regra 4 (prioridade por valor) e o estoque compartilhado exigem que a decisão sobre o par de um cliente dependa do que **outro par, de outro cliente, em outra página**, também está pedindo do mesmo produto — é literalmente o que `_processar_pedidos_canal` faz (`motor_adequacao.py:387-407`: ordena `ordens_prioridade` por `(score, nº produtos com estoque)` **antes** de consumir estoque produto a produto). Uma página de 250 pares não sabe se existe, na página seguinte, um pedido de maior prioridade disputando o mesmo `cd_prod_cor`. Portanto, rotear `SEM_ADEQUAR` pelo streaming e ainda assim implementar Regra 4 é **impossível pela própria estrutura do código**, não apenas indesejável. A única forma de decidir corretamente é ter a foto inteira do canal em memória antes de decidir qualquer par — exatamente o que `ADEQUAR` já faz.

### 1.2 Memória: os números batem com folga

O preflight (`processing/domain.py:33-50`) já é **agnóstico de modo**: `PendingSnapshotStats.__post_init__` (linha 188-206) aplica os mesmos tetos — 100.000 pares (`PLAN_PAIR_LIMIT`), 200.000 itens (`PLAN_ITEM_LIMIT`), 100 itens/par (`PLAN_PAIR_ITEM_LIMIT`), 96 MiB de bytes estimados de entrada (`MAX_PLAN_TOTAL_BYTES`) — e `inspect_pending` (`adapters.py:99-136`) é chamado **antes** do `if spec.mode is ProcessingMode.SEM_ADEQUAR` (`service.py:210-211`), ou seja, o teto já protege os dois modos hoje; só não é aproveitado pelo `SEM_ADEQUAR` porque ele nunca materializa a foto inteira em Python.

O comentário em `processing/domain.py:34-37` já documenta o orçamento: "132.405 itens mediram 434 MiB RSS total; 200k mantém margem para a foto real e limita o pior caso extrapolado a cerca de 600 MiB antes da hidratação." `docs/adequacao.md:35-39` confirma o mesmo número e a config real do worker dedicado: fila `orders_processing`, `concurrency=1`, `prefetch=1` (`.docker/docker-compose.prod.yml:62`), `mem_reservation: 1280m` / `mem_limit: 1536m` (`.docker/docker-compose.prod.yml:65-66`). Isso deixa **~900 MiB de headroom** acima do pico projetado de 600 MiB — e o worker é de fato exclusivo por causa do lock advisory de sessão global (`claim_processing_job`, `service.py:157-186`, exige `lock_guard.ensure_held()` antes do claim), então não existe cenário de dois planos pesados concorrentes no mesmo processo nem em processos diferentes.

Rotear `SEM_ADEQUAR` por `build_processing_plan` **não aumenta** o pico de memória de forma nova: o código-caminho (agrupar por produto, ordenar por prioridade, consumir estoque local por produto) é estruturalmente o mesmo que `ADEQUAR` já executa hoje sob os mesmos limites, e o tamanho do PAYLOAD DE SAÍDA (linhas do plano) só pode diminuir em relação ao streaming atual, porque hoje `sem_adequar` grava **todo** par elegível (`selected = eligible` incondicional, `processing/domain.py:668`) enquanto o motor novo grava só os pares selecionados (subconjunto).

**Único ajuste real de código** necessário no ponto de carregamento de estoque: hoje `stock` só é carregado se `spec.mode is ProcessingMode.ADEQUAR` (`service.py:311-313`); isso precisa passar a valer também para `SEM_ADEQUAR`, e `_eligible_pairs`/`build_processing_plan` (`processing/domain.py:618-716`) precisa de um branch "tudo-ou-nada com estoque compartilhado" — hoje o único branch que usa `stock` é o `ADEQUAR` (linha 641-666); o `else` (linha 667-669) é o streaming legado que será substituído.

### 1.3 Alternativa, caso o pico real supere o orçamento em produção

Não é necessária a esta escala, mas se a base de pedidos em aberto crescer muito além de 200k itens: a alternativa correta **não é voltar ao streaming** (que quebra a Regra 4), e sim (a) reduzir `PLAN_ITEM_LIMIT`/`PLAN_PAIR_LIMIT` e processar por canal em vez de "Todos" de uma vez (o motor já particiona por canal internamente, `motor_adequacao.py:235-244`, então rodar `Franquia` e `Multimarca` como dois jobs sequenciais já corta o pico pela metade sem mudar a lógica de negócio), ou (b) subir `mem_reservation`/`mem_limit` do worker dedicado (já são variáveis de ambiente, `.docker/docker-compose.prod.yml:65-66`). Nenhuma das duas exige tocar a orquestração durável.

---

## 2. Código de streaming órfão — remover na mesma entrega

**Recomendação: remover, não deixar como "morto".** O projeto já tem um incidente documentado de código morto virando confusão meses depois (trava de UI apagada como "feature morta" por ter ficado inalcançável — ver memória do usuário). Manter `StreamingPlanBuilder` funcionando "por via das dúvidas" só recria o mesmo risco: qualquer mudança futura no motor vai divergir silenciosamente entre os dois caminhos (o mantido e o órfão), e ninguém vai lembrar por que o órfão existe.

Levantamento do raio de impacto (busca por símbolo, não por nome):

| Símbolo | Arquivo:linha | Uso após a mudança |
|---|---|---|
| `StreamingPlanBuilder` (classe) | `processing/domain.py:325-412` | Nenhum consumidor além do ramo removido de `service.py` |
| `PLANNING_PAIR_PAGE_SIZE` | `processing/domain.py:45` | Só usado como `limit` em `load_pending_pair_page` |
| `build_pair_payload` (função pública) | `processing/domain.py:598-615` | Único chamador é o ramo streaming (`service.py:245`); `build_processing_plan` usa a privada `_payload_for_pair` diretamente |
| `prepare_pending_pair_stream` | `ports.py:28`, `adapters.py:175-197` | Método do `Protocol` e da implementação SQL — remover de ambos |
| `load_pending_pair_page` | `ports.py:30-35`, `adapters.py:200-267` | Idem |
| `append_plan_rows` | `ports.py:69-74`, `repository.py:110-157` | Só existe para alimentar o builder incremental |
| `finalize_streamed_plan` | `ports.py:76-82`, `repository.py:159-208` | Idem — `store_plan` (repository.py:210-262) já cobre o caso não-streaming e passa a ser o único caminho |
| `_plan_once`, ramo `SEM_ADEQUAR` | `service.py:211-290` | Substituído pelo corpo hoje usado só por `ADEQUAR` (`service.py:292-354`), parametrizado por modo |
| `test_pedidos_processing_stream_repository.py` | arquivo inteiro (313 linhas) | Testa exclusivamente `append_plan_rows`/`finalize_streamed_plan` — remove junto |

Outros arquivos que **referenciam** os símbolos acima e precisam de edição (não remoção total) ao tirar o streaming:

- `app/tests/test_pedidos_processing_domain.py:22,216` — importa e testa `StreamingPlanBuilder` isoladamente; remover o teste específico.
- `app/tests/test_pedidos_processing_repository.py:659,663` — exercita `prepare_pending_pair_stream`/`load_pending_pair_page` do adapter; remover.
- `app/tests/test_pedidos_processing_service.py:522,525,569,573` — fakes de porto implementam os 4 métodos de streaming para satisfazer o `Protocol`; removê-los dos fakes junto com a remoção do `Protocol`.

Depois da remoção, `ProcessingPlannerSource` fica só com `inspect_pending`, `load_pending_items`, `load_stock`, `load_adequation_config`, `load_size_reference` (`ports.py:21-45` menos as 2 linhas de streaming); `ProcessingRepository` fica só com `create_spec`, `get_spec`, `store_plan`, `load_next_chunk`, `checkpoint_chunk` (`ports.py:48-97` menos `append_plan_rows`/`finalize_streamed_plan`). Isso simplifica a interface real do subcontexto, não só remove linhas.

---

## 3. Persistência do motivo de stand by — grão, tabela e consulta

### 3.1 O que existe hoje (e por que não serve)

Duas descobertas importantes ao ler o código, não a documentação:

**(a) O job já descarta o motivo por par de propósito.** Em `build_processing_plan` (`processing/domain.py:618-716`), o modo `ADEQUAR` chama `processar_pedidos(..., resultados_apenas_selecionados=True, ...)` (linha 657). Isso significa que, em produção, o dicionário `resultados` do motor (que carregaria os itens com `status_item`/`motivo_stand_by`, ver `marcar_stand_by`, `motor_adequacao.py:180-197`) **nunca é populado para pares não selecionados** — só a contagem sobrevive (`deferred_count = len(result["preteridos"])`, `blocked_credit_count = len(set(result["bloqueados_credito"]))`, linhas 664-665). As LISTAS `preteridos` (grão par) e `bloqueados_credito` (grão `nr_pedido`) **continuam disponíveis no dicionário retornado por `processar_pedidos`** independente da flag — são só o `PlanDraft` que as reduz a contadores hoje.

**(b) A tela de "pedidos em aberto" (estágio `aguardando`) não classifica motivo nenhum hoje.** Em `repositorio_produtos.py`, o CTE `pairs` para `estagio == "aguardando"` (linhas 203-227) devolve **todo** par pendente com `status = 'Liberados para faturamento'` fixo e `motivo = ''` fixo — sem checar `credito_bloqueado` nem estoque. A classificação `'Bloqueado sem crédito'`/`'Bloqueado Estoque'` que existe no código (linhas 260-266) só roda no ramo `else` (estágio `historico`), e é uma **heurística pós-hoc, não a decisão real do motor**: compara `p.credito_bloqueado` (recalculado na ingestão a partir de `status_credito`, ver `ingestao/infrastructure/repositorio_snapshot.py:110-126` e `PedidoProdutoRead.credito_bloqueado`, `ingestao/infrastructure/models.py:153-155`) contra `p.qty > coalesce(ap.qty, 0)`, onde `ap.qty` é a disponibilidade **agregada por produto inteiro** (`_AVAILABLE_STOCK_CTE`, `repositorio_produtos.py:40-96`) — sem considerar furo de grade, tolerância ±5%, ou prioridade entre clientes do mesmo produto. Essa heurística vai piorar, não melhorar, com as regras novas (dois clientes podem ambos "parecer" liberados porque a soma total do produto alcança, mesmo que só um deles realmente ganhe o estoque na prioridade real).

Conclusão: a tela que a regra de negócio pede ("aba de pedidos em aberto") é exatamente a que **hoje não expõe motivo nenhum** — é preciso alimentá-la com a decisão real do job, não inventar uma heurística nova.

### 3.2 Onde persistir: tabela nova, dedicada, fora do contrato do job

**Não** em coluna de tabela existente, e **não** em `pedido_processamento_plan` (a tabela do plano do job): essa tabela é efêmera por desenho — `job_id` tem `ON DELETE CASCADE` a partir de `durable_jobs.id` (`processing/infrastructure/models.py:69-72` e `105-109`), e `durable_jobs` é candidato a expurgo/retention (mecanismo em `app/shared/jobs/`). Amarrar a visibilidade de stand-by ao ciclo de vida do job quebra assim que o job antigo for varrido, mesmo que o par continue pendente.

**Recomendação: tabela nova `pedido_standby_motivo`**, seguindo o mesmo padrão já estabelecido no projeto para projeções recalculáveis (`estoque_virtual`, `pedido_produto_read`): pequena, com grão par, sem amarrar ao job.

```sql
CREATE TABLE pedido_standby_motivo (
    nr_pedido    INTEGER     NOT NULL,
    cd_prod_cor  VARCHAR(64) NOT NULL,
    canal        VARCHAR(16) NOT NULL,
    motivo       VARCHAR(16) NOT NULL,  -- 'sem_credito' | 'sem_estoque'
    job_id       UUID        NOT NULL,  -- informativo/auditoria, sem FK (job pode ser expurgado)
    atualizado_em TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (nr_pedido, cd_prod_cor),
    CHECK (canal IN ('Franquia', 'Multimarca')),
    CHECK (motivo IN ('sem_credito', 'sem_estoque'))
);
```

Grão: **par** `(nr_pedido, cd_prod_cor)` para os dois motivos — isso já resolve a exigência de granularidade diferente por natureza do bloqueio:

- **Sem crédito** → ao persistir, o writer faz *fan-out*: para cada `nr_pedido` em `bloqueados_credito`, grava uma linha `motivo='sem_credito'` para **todos os pares daquele pedido** presentes no conjunto elegível da rodada (não só os que apareceriam no plano) — implementa literalmente "tag em todos os produtos que ele tentou reservar".
- **Sem estoque** → `preteridos` já é grão-par; cada entrada vira uma linha `motivo='sem_estoque'` **só** para aquele par — implementa "tag só nos produtos em que faltou".

### 3.3 Como popular sem tocar os dois contratos protegidos

O `PROJECT.md` protege duas coisas explicitamente: (1) as chaves do dicionário do motor (`resultados/selecionados/preteridos/bloqueados_credito/pares_processados`) e (2) o resultado do job limitado a 4 contadores. Nenhuma das duas precisa mudar. O ponto de extensão é o **DTO intermediário `PlanDraft`** (`processing/domain.py:264-296`), que é uma camada de `processing/domain.py`, não do motor — adicionar dois campos:

```python
deferred_pairs: tuple[tuple[int, str], ...]        # espelha result["preteridos"], já ordenado
blocked_credit_pairs: tuple[tuple[int, str], ...]  # fan-out de result["bloqueados_credito"] × eligible
```

Ambos cabem tranquilamente no orçamento de memória (tuplas de `int`+`str`, no máximo `PLAN_PAIR_LIMIT` = 100.000 entradas — ordens de grandeza menor que os itens completos que a flag `resultados_apenas_selecionados` já evita reter). `build_processing_plan` (linha 618-716) já tem `eligible` e o `result` bruto do motor em mãos no momento em que hoje descarta essa informação (linha 662-666) — é só não descartar.

Persistência: um novo método no port `ProcessingRepository` (por exemplo `record_standby_reasons`), chamado **dentro da mesma transação** de `store_plan` (repository.py:210-262) — upsert (`ON CONFLICT (nr_pedido, cd_prod_cor) DO UPDATE`) das linhas derivadas de `deferred_pairs`/`blocked_credit_pairs`. Upsert em vez de delete-then-insert: se o mesmo par continuar em stand-by em rodadas sucessivas, a linha só é atualizada (motivo pode até mudar, ex. estoque→crédito); não há necessidade de limpar o escopo inteiro do canal a cada rodada.

Duas limpezas complementares, ambas baratas e já com precedente no código:

1. **Na aplicação do chunk** (`SqlAlchemyProcessingWriter.apply_pairs`, `repository.py:515-571`): ao inserir a OR de um par que finalmente foi selecionado, apagar a linha correspondente de `pedido_standby_motivo` (`DELETE ... WHERE (nr_pedido, cd_prod_cor) IN (chunk)`) — simétrico ao que já acontece ali com `pedidos_processados`/`ordens_reserva`/`estoque_virtual`.
2. **No full refresh da ingestão** (`reconstruir_pedido_produto_read`, `ingestao/infrastructure/repositorio_snapshot.py:248-260`, já roda a cada 2h): apagar linhas de `pedido_standby_motivo` cujo par não exista mais em `pedido_produto_read` com `source='pending'` — evita acúmulo de lixo se um pedido sair de "em aberto" por outro caminho (ex. confirmado no ERP).

Mesmo sem essas limpezas rodarem no mesmo instante, a leitura nunca mostra uma tag errada: a consulta sempre faz `JOIN`/`LEFT JOIN` a partir de `pedido_produto_read`/`pedidos_processados` (a fonte autoritativa de "ainda pendente"), então uma linha órfã em `pedido_standby_motivo` simplesmente não casa com nada e fica invisível.

### 3.4 Mudança na consulta paginada

`repositorio_produtos.py`, CTE `pairs` do estágio `aguardando` (linhas 203-227) — hoje:

```sql
'Liberados para faturamento'::text AS status,
''::text AS motivo,
```

passa a fazer `LEFT JOIN pedido_standby_motivo sm ON sm.nr_pedido = p.nr_pedido AND sm.cd_prod_cor = p.cd_prod_cor` e usar:

```sql
CASE sm.motivo
    WHEN 'sem_credito' THEN 'Bloqueado sem crédito'
    WHEN 'sem_estoque' THEN 'Aguardando estoque'
    ELSE 'Liberados para faturamento'
END AS status,
CASE sm.motivo
    WHEN 'sem_credito' THEN 'Aguardando liberação de crédito'
    WHEN 'sem_estoque' THEN 'Estoque insuficiente para o produto'
    ELSE ''
END AS motivo,
```

O mesmo `LEFT JOIN` se aplica a `listar_clientes_produto_sql` (`repositorio_produtos.py:545-734`, CTE `scoped`) para a tela de clientes por produto — mesmo grão de junção (`nr_pedido`, `cd_prod_cor`), já filtrada por `codigo_produto`/`canal`. Nenhuma paginação muda de forma: é uma coluna a mais por linha já retornada, não uma lista nova no payload do job.

Se a busca por status (`StatusHistorico`/`_STATUS_SQL`, `consultas.py:23-29`, `repositorio_produtos.py:19-25`) precisar filtrar por essas duas tags também no estágio `aguardando` (hoje o filtro só se aplica ao `historico`), é uma extensão pequena e opcional — sinalizar como decisão de UX a confirmar com a mantenedora, não bloqueia a entrega do dado.

### 3.5 A heurística antiga do estágio `historico` deve ser substituída, não duplicada

Já que a tabela nova passa a ter o motivo real, o `all_history_pairs` (linhas 252-280) deve trocar seu `WHERE (p.credito_bloqueado OR p.qty > coalesce(ap.qty, 0))` por `WHERE EXISTS (SELECT 1 FROM pedido_standby_motivo sm WHERE ...)` — manter as duas fontes de verdade lado a lado seria recriar, para este código, exatamente o problema que motivou este milestone (regra de negócio divergente do que a tela mostra).

---

## 4. Ordem de execução e idempotência com motor de duas passadas

A camada durável não precisa de nenhuma mudança estrutural — ela só protege duas invariantes, e ambas continuam válidas com um motor de duas passadas **desde que ele permaneça uma função pura, de ponta a ponta, dentro de uma única chamada a `build_processing_plan`**:

1. **A foto é congelada por igualdade de contagem antes de processar.** `service.py:292-301`: `pending_items`/`hydrated_pairs` são comparados a `stats` (o resultado de `inspect_pending`, lido ANTES) — se algo mudou entre a inspeção e a leitura (novo pedido chegou, uma OR foi criada por outro caminho), `RuntimeError("processing_snapshot_changed_during_planning")` aborta antes de qualquer gravação. Um motor de duas passadas continua seguro porque as duas passadas rodam sobre a MESMA lista `pending_items` já validada — nada nele precisa reconsultar o banco no meio do cálculo.
2. **O plano só é congelado uma vez.** `_plan_once` retorna cedo se `spec.planning_state is PlanningState.PLANNED` (`service.py:204-206`) — recompute nunca acontece depois do commit de `store_plan`. Um crash **antes** do commit apenas descarta o trabalho da thread (nada foi persistido; a próxima tentativa recomeça do zero, determinística porque parte da mesma foto). Um crash **depois** do commit já está em `_apply_chunks`, que é resumível por `next_ordinal`/`checkpoint_chunk` (`repository.py:264-356`) — o motor não entra mais em cena depois de congelado.

O que precisa ser garantido ao introduzir estado por pedido (ex.: orçamento ±5% agregado por `nr_pedido` inteiro, não mais por par — Regra 2 pede a métrica sobre o **pedido completo do cliente**, cruzando múltiplos `cd_prod_cor`):

- **A pré-passada (agregação por pedido) deve ser um cálculo determinístico sobre os dados já carregados, sem I/O e sem depender de ordem de iteração de dict/set para o resultado final.** O código já tem o precedente certo: `_prioridade`/`todos_pedidos_flat` (`motor_adequacao.py:340-400`) já agregam por `nr_pedido` cruzando produtos antes de decidir prioridade — a pré-passada nova do orçamento ±5% por pedido é a mesma forma de agregação, só que alimentando `adequar_grade_produto` com um teto vindo de fora em vez de calculado por grade isolada.
- **Toda ordenação que afeta o hash deve continuar explícita.** `selecionados`/`preteridos` já são `.sort()`ados (`motor_adequacao.py:297-299,458-459`) e `build_processing_plan` re-ordena de novo por `sorted(eligible)`/`sorted(selected)` (`processing/domain.py:645,663,668,673`) antes de atribuir `ordinal`. Os dois novos campos propostos (`deferred_pairs`, `blocked_credit_pairs`, item 3.3) precisam do mesmo tratamento — ordenar antes de persistir, nunca confiar na ordem de inserção do dict/set interno do motor.
- **`fingerprint_pair_source`** (a proteção contra plano obsoleto no apply, `processing/domain.py:532-551`, checada em `_assert_plan_still_current`, `repository.py:387-487`) usa os **itens brutos de entrada** do par (`source_items=eligible[pair]`, linha 681), não o resultado do motor — continua correta e não muda com a lógica interna de alocação, porque hash de entrada ≠ hash de decisão.
- **`plan_hash`** (`processing/domain.py:698-708`) é calculado sobre `rows` (o plano final, já com `ordinal`/`payload_hash`) mais os 3 contadores agregados — se `PlanDraft` ganhar `deferred_pairs`/`blocked_credit_pairs`, decida explicitamente se eles entram no hash (recomendado: **sim**, incluí-los no dicionário hasheado do `plan_hash`, já ordenados) para que qualquer divergência nesses dados também invalide um replay — sem isso, um bug que mude só o motivo persistido (sem mudar `rows`) passaria despercebido pela proteção de idempotência.

Nada disso exige mudança na tabela `pedido_processamentos`/`pedido_processamento_plan` nem nos `CheckConstraint`s de `processing/infrastructure/models.py:28-171` — o motor de duas passadas é inteiramente interno a `motor_adequacao.py` e a extensão de `PlanDraft`.

---

## 5. Ponto de integração do estoque — sem dupla contagem

Confirmado lendo `domain/estoque_virtual.py` e `infrastructure/repositorio_estoque_virtual.py`: **não há risco de dupla contagem**, e o schema já antecipava este caso.

- `calcular_estoque_virtual` (`domain/estoque_virtual.py:31-81`) desconta **toda** OR existente, `tipo='com'` ou `tipo='sem'`, sem distinção — o docstring é explícito: "OR de tipo 'sem' desconta igual: também é reserva" (linha 42-43). O `_ESTOQUE_ALVOS_CTE` em `repositorio_estoque_virtual.py:46-119` (usado tanto por `carregar_estoque_disponivel_alvos` quanto por `recalcular_estoque_virtual_alvos`) não filtra por `tipo` na CTE `reservas` — qualquer OR não-stand-by de qualquer tipo entra na conta.
- `carregar_estoque` (`repositorio_estoque_virtual.py:308-354`) é a função que `load_stock`/`load_adequation_config` do planner (`adapters.py:269-318`) e o próprio `estoque` que `processar_pedidos` recebe (`motor_adequacao.py:216-219`) já usam — é a MESMA fonte que `ADEQUAR` usa hoje. Rotear `SEM_ADEQUAR` para o mesmo `load_stock`/`carregar_estoque_disponivel_alvos` não introduz uma segunda fonte de verdade: é literalmente reutilizar o pipeline existente.

O achado real não é dupla contagem na leitura — é uma **lacuna simétrica na escrita**, do lado da revalidação de capacidade: `SqlAlchemyProcessingWriter._assert_stock_capacity` (`repository.py:489-513`) filtra explicitamente `if tipo != "com": continue` (dentro do loop que soma `requested`) — ou seja, hoje a revalidação de capacidade de estoque no momento de aplicar o chunk **é pulada para `tipo == "sem"`**, porque o `sem_adequar` legado nunca reservava estoque nenhum (não fazia sentido revalidar o que nunca foi verificado). **Isso precisa ser corrigido junto com o roteamento do item 1**: assim que `sem_adequar` também reservar estoque compartilhado no planejamento, a mesma revalidação de capacidade no apply precisa cobrir `tipo == "sem"` — senão existe uma janela real (ainda que pequena, dado o lock de sessão) entre congelar o plano e aplicá-lo em que outro caminho de escrita (ex. edição manual de grade, `PUT /api/v1/pedidos/produtos/grades`) poderia consumir o mesmo estoque sem essa passada perceber.

Por outro lado, a **recontagem do estoque virtual pós-aplicação** (`recalcular_estoque_virtual_alvos`, chamada em `apply_pairs`, `repository.py:566-571`) já é incondicional por `tipo` — os `stockTargets` de qualquer payload (`processing/domain.py:576-584`) já entram no recálculo independente de `com`/`sem`. Só a checagem de capacidade PRÉ-aplicação tem o gap.

---

## Integration Points (arquivo:linha)

### Pontos que mudam de comportamento (modificados)

| Ponto | Arquivo:linha | Mudança |
|---|---|---|
| Branch por modo em `_plan_once` | `processing/application/service.py:210-354` | Colapsar os dois ramos num só, parametrizado por `mode`; carregar `stock` para os dois modos |
| Carregamento de estoque condicional | `processing/application/service.py:311-313` | Remover o `if spec.mode is ProcessingMode.ADEQUAR` — carregar sempre |
| `build_processing_plan` | `processing/domain.py:618-716` | Novo branch "tudo-ou-nada com estoque compartilhado" para `SEM_ADEQUAR`, substituindo o `else` de linha 667-669; capturar `preteridos`/`bloqueados_credito` em vez de só contá-los |
| `PlanDraft` | `processing/domain.py:264-296` | + `deferred_pairs`, + `blocked_credit_pairs` (não mexe nas chaves do motor) |
| `motor_adequacao.adequar_grade_produto` | `domain/motor_adequacao.py:119-177` | Novo modo "tudo-ou-nada" (nº hoje só existe o modo ±5% com corte); orçamento ±5% passa a vir de fora (agregado por pedido), não mais calculado por grade isolada |
| `motor_adequacao._processar_pedidos_canal` | `domain/motor_adequacao.py:303-474` | Pré-passada por `nr_pedido` cruzando produtos para o orçamento ±5% do pedido completo; furo de grade nos tamanhos do meio |
| `SqlAlchemyProcessingWriter._assert_stock_capacity` | `processing/infrastructure/repository.py:489-513` | Remover o `if tipo != "com": continue` — revalidar capacidade também para `tipo == "sem"` |
| `SqlAlchemyProcessingWriter.apply_pairs` | `processing/infrastructure/repository.py:515-571` | + `DELETE` de `pedido_standby_motivo` para os pares que acabaram de virar OR |
| `repositorio_produtos._pairs_cte` (estágio `aguardando`) | `infrastructure/repositorio_produtos.py:203-227` | `LEFT JOIN pedido_standby_motivo`; `status`/`motivo` deixam de ser constantes |
| `repositorio_produtos._pairs_cte` (estágio `historico`) | `infrastructure/repositorio_produtos.py:252-280` | Trocar heurística `credito_bloqueado OR qty > available` por `EXISTS (... pedido_standby_motivo ...)` |
| `listar_clientes_produto_sql` | `infrastructure/repositorio_produtos.py:545-734` | Mesmo `LEFT JOIN`, mesmo grão |
| `reconstruir_pedido_produto_read` | `ingestao/infrastructure/repositorio_snapshot.py:248-260` | + limpeza de `pedido_standby_motivo` órfão, na mesma transação do full refresh |

### Pontos novos

| Ponto | Onde | O que é |
|---|---|---|
| Tabela `pedido_standby_motivo` | migration Alembic nova | Grão `(nr_pedido, cd_prod_cor)`; ver DDL no item 3.2 |
| `ProcessingRepository.record_standby_reasons` (nome sugerido) | novo método em `ports.py` + `repository.py` | Upsert das linhas, chamado na mesma transação de `store_plan` |
| Modo "tudo-ou-nada" do motor | `domain/motor_adequacao.py` | Função nova (ou parâmetro de modo em `adequar_grade_produto`) — não reaproveita a lógica de corte por tamanho de `ADEQUAR`, reaproveita a estrutura de prioridade/estoque compartilhado de `_processar_pedidos_canal` |
| Checagem de furo de grade | `domain/motor_adequacao.py` | Nova, usando `get_tamanho_idx`/`RANKINGS` já existentes (`domain/value_objects.py:41-109`) para identificar tamanhos "do meio" |

### Pontos removidos

Ver tabela completa no item 2. Resumo: `StreamingPlanBuilder`, `build_pair_payload` (público), `prepare_pending_pair_stream`, `load_pending_pair_page`, `append_plan_rows`, `finalize_streamed_plan`, `PLANNING_PAIR_PAGE_SIZE`, e `test_pedidos_processing_stream_repository.py` inteiro, mais os trechos referentes em `test_pedidos_processing_domain.py`, `test_pedidos_processing_repository.py`, `test_pedidos_processing_service.py`.

---

## Ordem de construção sugerida (por dependência real, não por prioridade de produto)

1. **Motor puro primeiro, sem tocar orquestração.** Mudanças em `domain/motor_adequacao.py` (modo tudo-ou-nada, orçamento ±5% por pedido completo, furo de grade, alocação de extras sem posição fixa) e `domain/value_objects.py` se necessário. Testável isoladamente, sem banco, sem afetar `processing/`. **Motivo da ordem:** é a dependência de tudo que vem depois — `build_processing_plan` só pode ser roteado para os dois modos depois que o motor sabe fazer os dois tipos de decisão.
2. **Extensão do `PlanDraft` + captura de `preteridos`/`bloqueados_credito`** em `processing/domain.py`, ainda sem persistir em lugar nenhum (só passar a existir na estrutura em memória). **Motivo:** precisa do motor do passo 1 já devolvendo os dois tipos de decisão para ter o que capturar; é pré-requisito do passo 4 (persistência) mas independente do passo 3 (roteamento).
3. **Roteamento de `SEM_ADEQUAR` para o caminho global + remoção do streaming**, em `service.py`, `ports.py`, `adapters.py`, `repository.py` (`_assert_stock_capacity`), e os 4 arquivos de teste do item 2. **Motivo:** depende do motor (passo 1) já suportar tudo-ou-nada; é o commit certo para remover o streaming junto, evitando o código órfão ficar no repo entre um PR e outro.
4. **Migration + tabela `pedido_standby_motivo` + escrita** (`record_standby_reasons` na transação de `store_plan`, `DELETE` em `apply_pairs`, limpeza no full refresh da ingestão). **Motivo:** depende do passo 2 (dados já capturados) e do passo 3 (os dois modos agora produzem `preteridos`/`bloqueados_credito` de verdade — sem o passo 3, só `ADEQUAR` alimentaria a tabela).
5. **Leitura: `repositorio_produtos.py` (`aguardando` e `historico`) + `listar_clientes_produto_sql`.** **Motivo:** depende da tabela existir e estar sendo populada (passo 4) — ler antes disso só mostraria `NULL`/vazio.
6. **Guarda contra OR zerada (`ceil`→`floor` + guarda explícita no orquestrador, WR-05)** — pode ser feita em paralelo ao passo 1, já que é uma correção pontual e independente em `motor_adequacao.py`/`processing/domain.py` (a checagem de `qt_liquida > 0` já existe em `_payload_for_pair`, linha 580, mas a guarda "explícita no orquestrador" mencionada no PROJECT.md sugere um ponto adicional a confirmar durante o plano de fase).

Fases 1–3 tocam só `domain/` e `processing/` (sem schema novo) — podem fechar e ser testadas (inclusive em produção, atrás do mesmo contrato de API) antes de abrir a fase de schema (4–5), que é a única que precisa de migration Alembic e portanto de janela de deploy coordenada com `alembic upgrade head` no boot (`docs` do projeto já documentam esse padrão).

---

## Riscos e itens a validar durante o plano de fase

- **Preflight preciso, mas ainda vale reconferir com dado real de produção** antes de assumir os 96 MiB/200k itens como definitivos para o modo tudo-ou-nada — o comentário em `processing/domain.py:34-37` foi calibrado com uma foto medida em `ADEQUAR`; o volume elegível de `SEM_ADEQUAR` pode ser estruturalmente maior (hoje ele aceita todo par sem filtro de crédito), então o número de pares/itens que efetivamente batem no teto pode ser diferente na prática — recomenda-se medir o pico real após o passo 3, não só confiar na extrapolação.
- **`_assert_stock_capacity` (item 5) é fácil de esquecer** porque hoje está "certo" por acidente (pular `tipo != "com"` nunca causou problema porque `sem` nunca reservava estoque). Não aparece em nenhum teste que falharia sozinho se for esquecido — vale um teste explícito cobrindo race de capacidade para `tipo == "sem"`.
- **Filtro por status no estágio `aguardando`** (se a mantenedora quiser filtrar a lista por "sem crédito"/"sem estoque" além de só exibir a tag) exige estender `StatusHistorico`/`_STATUS_SQL` — não é bloqueante, mas é uma decisão de UX a confirmar antes de fechar o desenho da fase de leitura.
- **`docs/adequacao.md` e `.planning/PROJECT.md`** citam números e contratos que este documento também cita — depois da entrega, ambos precisam de atualização (o parágrafo sobre "sem_adequar materializa uma vez somente as chaves elegíveis..." fica obsoleto assim que o streaming for removido).

---

## Sources

Todas as afirmações vieram de leitura direta do código nesta sessão (2026-08-14), não de busca externa — este é um domínio interno (arquitetura do próprio repositório), sem ecossistema de terceiros a pesquisar.

- `.planning/PROJECT.md` — regras de negócio autoritativas do v1.3, constraints de contrato
- `.planning/codebase/ARCHITECTURE.md` — mapa geral (contexto, não fonte de verdade sobre este subcontexto)
- `app/modules/pedidos/processing/application/service.py`
- `app/modules/pedidos/processing/application/ports.py`
- `app/modules/pedidos/processing/domain.py`
- `app/modules/pedidos/processing/infrastructure/adapters.py`
- `app/modules/pedidos/processing/infrastructure/repository.py`
- `app/modules/pedidos/processing/infrastructure/models.py`
- `app/modules/pedidos/domain/motor_adequacao.py`
- `app/modules/pedidos/domain/value_objects.py`
- `app/modules/pedidos/domain/estoque_virtual.py`
- `app/modules/pedidos/domain/consultas.py`
- `app/modules/pedidos/infrastructure/repositorio_consultas.py`
- `app/modules/pedidos/infrastructure/repositorio_produtos.py`
- `app/modules/pedidos/infrastructure/repositorio_estoque_virtual.py`
- `app/modules/ingestao/infrastructure/models.py`
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py`
- `app/tests/test_pedidos_processing_stream_repository.py`
- `app/tests/test_pedidos_processing_domain.py`, `test_pedidos_processing_repository.py`, `test_pedidos_processing_service.py` (grep de raio de impacto)
- `docs/adequacao.md`
- `.docker/docker-compose.prod.yml`
- `.claude/CLAUDE.md` (placebo do estoque virtual)

---
*Architecture research for: motor de alocação de OR — integração com processamento durável (v1.3)*
*Researched: 2026-08-14*
