# Phase 20: Trava de orçamento de ±5% na edição manual de pedidos Sem Adequação - Research

**Researched:** 2026-09-04
**Domain:** Backend (FastAPI/SQLAlchemy async, domínio Python puro) + Frontend (Next.js/React, React Query) — cross-repo
**Confidence:** HIGH (achados de código, com trechos exatos lidos e citados) / MEDIUM apenas no desenho exato do payload novo (discretion do CONTEXT.md)

## Summary

Esta pesquisa foi feita inteiramente por leitura direta do código (backend e frontend), sem
dependências externas — não há biblioteca nova a avaliar. O trabalho central da fase é: (1)
relaxar DUAS travas de "só redistribui, nunca muda total" que hoje existem em **duas camadas
diferentes** (uma no frontend, `hasInvalidClientTotals`; outra, menos óbvia, dentro do próprio
domínio backend, `montar_grade_atualizada`), (2) introduzir uma validação de orçamento ±5% por
`nr_pedido` dentro da mesma transação/lock de `executar_alteracao_grades_produto`, e (3) unificar
salvar+aprovar reaproveitando a lógica que já existe em `aprovar_produto_canal`.

A pesquisa confirma que `service.py:230` é um wrapper fino e direto de `casos_uso.py:242` (mesma
função, camadas de composição, não duas implementações) — resolvendo a dúvida #2 do briefing. O
`_adquirir_lock_processamento` usa `pg_try_advisory_xact_lock`, um lock **global e
transaction-scoped**, reentrante dentro da mesma transação — o que significa que fundir
salvar+aprovar numa única função, chamando a lógica de aprovação inline (sem reabrir uma segunda
transação/wrapper), é seguro e não introduz risco de deadlock.

O achado mais importante e **não previsto no CONTEXT.md**: a query agregada que calcula "quanto
o pedido já consumiu de ±5%" (`_PEDIDO_BUDGET_SQL`, dentro do CTE `consumido`) filtra
explicitamente `WHERE op.tipo = 'com'`. Isso foi deliberado — hoje pedidos "sem adequação" são
tudo-ou-nada, então `qt_liquida` sempre igual `qt_solicitada` para eles, e o filtro é só uma
defesa redundante. Mas a partir do momento em que esta fase permite editar o total de um pedido
"sem", esse filtro vai **silenciosamente ignorar exatamente a edição que essa fase cria** — o
"restante" calculado sempre vai aparentar 100% disponível, mesmo depois de várias edições já
terem consumido o orçamento. Um segundo achado agrava isso: `montar_grade_atualizada`
(`edicao_grade.py:93`) reescreve `qt_solicitada` para o valor NOVO editado a cada chamada — então,
mesmo corrigindo o filtro de tipo, se `qt_solicitada` for resetado a cada edição, o ledger nunca
enxerga o consumo acumulado. Os dois pontos precisam ser corrigidos juntos, ou a trava de ±5%
desta fase é inofensiva na aparência mas ineficaz na prática (permite estourar o orçamento em
edições sucessivas sem nunca acusar).

**Primary recommendation:** Implementar a validação de orçamento como um passo adicional dentro
do loop existente de `executar_alteracao_grades_produto` (um `OrcamentoPedido` por `nr_pedido`
tocado no lote, construído a partir de uma versão de `load_pedido_budget` que TAMBÉM soma
consumo de OR `tipo='sem'`), branchando o hard-check de `montar_grade_atualizada` por `state.tipo`,
preservando o `qt_solicitada` original entre edições, e fundindo a chamada de aprovação
(`aprovar_produto_canal`) na mesma transação já travada — sem criar um segundo endpoint ou uma
segunda aquisição de lock.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Validação atômica do orçamento ±5% (fonte de verdade) | API/Backend | Database (SQL agregada) | D-07/D-16: backend é a única fonte de verdade; front só dá feedback |
| Cálculo "ao vivo" de quanto já foi consumido do pedido | Database (SQL) | API/Backend | `_PEDIDO_BUDGET_SQL` já lê `ordens_reserva.itens` diretamente — não precisa de coluna nova |
| Redistribuição/relaxamento da trava de total fixo | API/Backend (domínio puro) | — | `montar_grade_atualizada` é função pura de domínio, sem I/O — a decisão de permitir mudança de total é regra de negócio, não de UI |
| Unificação salvar+aprovar (transação) | API/Backend | — | Ambos os casos de uso já competem pelo mesmo lock global; fundir é problema de orquestração de transação, não de UI |
| Indicador visual de orçamento / aviso de trava | Browser/Client | — | D-13/D-14/D-15: UX pura, não decide nada — só reflete o que o backend calculou |
| Leitura do orçamento do pedido ao abrir o modal | API/Backend | Browser/Client (fetch/cache) | Fonte é SQL agregada por pedido; frontend só consome e cacheia com staleTime baixo |

## User Constraints (from CONTEXT.md)

<user_constraints>

### Locked Decisions

- **D-01:** Pedidos "Com Adequação" — zero mudança de comportamento (tela e endpoint intocados).
- **D-02:** Pedidos "Sem Adequação" — edição manual passa a permitir mudar o total do pedido, dentro de ±5% do total original do pedido inteiro (`nr_pedido`, somando todos os produtos/clientes daquele pedido — não por produto isolado).
- **D-03:** O tipo da OR ("com"/"sem" adequação) já é persistido no registro (`tipo` gravado em `processing/domain.py`) — o backend deve ler esse campo para decidir se a trava nova se aplica, não inferir por heurística.
- **D-04:** Reaproveitar `OrcamentoPedido` (`app/modules/pedidos/domain/orcamento_pedido.py`) — já testado (`test_pedidos_orcamento_pedido.py`, 13 casos). Não recriar a regra `floor(total_original × tolerancia)`, `consumir_adicao`/`consumir_corte`.
- **D-05:** Reaproveitar a lógica de `_PEDIDO_BUDGET_SQL` / `load_pedido_budget` (`processing/infrastructure/adapters.py:307`) — hoje só chamada em lote pelo motor; precisa virar consultável sob demanda para um `nr_pedido` específico. Essa SQL já calcula o consumido **ao vivo** (compara `qt_liquida` vs `qt_solicitada` nos itens de `ordens_reserva`), então não precisa de coluna nova no banco nem de estado persistido à parte — o restante já reflete automaticamente qualquer edição anterior já salva no mesmo pedido.
- **D-06:** Para "sem adequação", o `consumido_previo_adicao`/`consumido_previo_corte` inicial é sempre 0 (confirmado: `SEM_ADEQUAR` não toca o ledger) — mas isso só vale até a primeira edição salva; edições subsequentes do mesmo pedido devem ler o estado já gasto via D-05, não assumir 0 sempre.
- **D-07:** A validação do orçamento deve rodar **dentro da mesma transação/lock** que já existe em `executar_alteracao_grades_produto` (via `_adquirir_lock_processamento`, `app/modules/pedidos/application/casos_uso.py:242`) — não como um `if` solto antes de salvar. Motivo: evitar corrida entre edições concorrentes de produtos diferentes do mesmo pedido que, cada uma isoladamente, pareceria caber no orçamento mas juntas estourariam.
- **D-08:** Adição e corte são orçamentos independentes (regra já existente no `OrcamentoPedido` — não se compensam).
- **D-09:** Para "sem adequação" dentro do limite: o botão "Salvar Alterações" passa a também aprovar/finalizar a OR num único clique — a pessoa não precisa mais do passo separado "Aprovar OR" depois.
- **D-10:** Pontos de entrada existentes no backend para aprovação, a mapear durante pesquisa/planejamento: `aprovar_produto_canal` e `aprovar_ordem_reserva` (`app/modules/pedidos/application/casos_uso.py:100` e `:128`, delegando a `PedidosWritePort`). A unificação com o salvamento (`executar_alteracao_grades_produto`) precisa decidir se chama esses casos de uso na mesma transação ou se funde a lógica.
- **D-11:** "Com adequação" mantém os dois botões separados, sem mudança.
- **D-12:** Buscar e exibir o orçamento do **pedido inteiro** ao abrir a edição de qualquer produto dele — mesmo estando na tela de um produto isolado, o indicador mostra o gasto/restante agregado do `nr_pedido`.
- **D-13:** Indicador visual permanente do orçamento (gasto/restante), visível antes de qualquer edição.
- **D-14:** Aviso explícito antes de confirmar, avisando que salvar dentro do limite finaliza a OR (não é mais um rascunho intermediário).
- **D-15:** Trava visual + alerta (`role="alert"`) quando a edição pretendida ultrapassa o restante do pedido.
- **D-16:** O cálculo no frontend é só para feedback imediato (UX) — o backend (D-07) continua sendo a fonte de verdade que pode rejeitar mesmo que o front achasse que cabia.

### Claude's Discretion

- Texto exato do aviso/rótulo do botão unificado (ex.: "Salvar e Aprovar OR") — copy final é decisão de implementação, não de negócio.
- Desenho exato do payload/shape do endpoint de leitura do orçamento (novo endpoint dedicado vs. campo embutido na resposta que já alimenta o modal) — decisão do planejamento, orientada por D-04/D-05.
- Como exatamente unificar o fluxo de `executar_alteracao_grades_produto` com `aprovar_produto_canal`/`aprovar_ordem_reserva` (D-10) — requer leitura mais profunda desses casos de uso durante a pesquisa/planejamento; não é uma decisão de visão do usuário, é implementação.

### Deferred Ideas (OUT OF SCOPE)

Nenhuma — a discussão ficou inteiramente dentro do escopo da fase (trava de orçamento na edição
manual de "sem adequação"). Nenhum pedido de capacidade nova fora desse escopo surgiu durante a
conversa.

</user_constraints>

## Project Constraints (from CLAUDE.md)

- **Grão de negócio**: uma OR é sempre um produto (`nr_pedido, cd_prod_cor`) com N clientes dentro
  — atenção: dentro deste grão, "cliente" na tela de edição de grade corresponde a um `order_id`
  que **é o `nr_pedido`** (confirmado no código: `executar_alteracao_grades_produto` edita até 100
  `order_id`s de UM produto, e cada `order_id` mapeia 1:1 para `state.nr_pedido`). Ou seja, uma
  única chamada de edição pode tocar **múltiplos `nr_pedido`s diferentes** (um por cliente), todos
  do mesmo produto/canal — o orçamento ±5% precisa ser validado **por `nr_pedido` individualmente**
  dentro do lote, nunca agregado entre pedidos diferentes.
- **Compatibilidade ERP (Linx)**: qualquer novo total de grade editado precisa continuar produzindo
  uma linha Linx válida — `executar_alteracao_grades_produto` já chama `montar_linha_linx` e valida
  `linx["qtde_embalada"] == requested_qty`; isso não muda com esta fase, mas o novo caminho de
  total-variável deve continuar passando por essa mesma validação.
- **Regra permanente de processo (STATE.md):** nenhuma alteração de front-end sem antes consultar
  os arquivos do design-system e seguir à risca o que está prescrito — vale integralmente para D-13/
  D-14/D-15 (indicador de orçamento, aviso, alerta).
- **GSD Workflow Enforcement:** implementação deve passar por `/gsd-execute-phase` (ou `/gsd-quick`
  para ajustes triviais) — sem edição direta fora do fluxo GSD.

## Standard Stack

Não há biblioteca nova a instalar nesta fase — é 100% reaproveitamento de código de domínio já
existente e testado (backend) mais composição de hooks React Query já padronizados (frontend).

### Core (reaproveitado, não instalado)

| Componente | Localização | Papel nesta fase |
|---|---|---|
| `OrcamentoPedido` | `app/modules/pedidos/domain/orcamento_pedido.py` | Ledger ±5% por pedido — instanciar 1x por `nr_pedido` tocado no lote de edição |
| `load_pedido_budget` / `_PEDIDO_BUDGET_SQL` | `app/modules/pedidos/processing/infrastructure/adapters.py:98,307` | Fonte do `total_original`/`consumido_previo_*` — precisa de fix (ver Pitfall 1) antes de ser reaproveitada aqui |
| `montar_grade_atualizada` | `app/modules/pedidos/domain/edicao_grade.py` | Função pura que hoje FORÇA total preservado — precisa de branch por `tipo` (ver Pitfall 2) |
| `ratear_hamilton` | `app/modules/pedidos/domain/rateio.py` (usado dentro de `edicao_grade.py`) | Rateio de valor financeiro por tamanho quando a grade muda — já trata arredondamento exato, não recriar |
| `montar_linha_linx` | `app/modules/pedidos/domain/ordem_reserva_linx.py` | Validação/gravação da linha Linx — já chamada em `executar_alteracao_grades_produto`, sem mudança de contrato necessária |
| `useToleranciaAdequacaoFator` (padrão) | `frontend/features/pedidos/model/use-tolerancia-adequacao.ts` | Padrão de hook React Query (staleTime, fallback local) a replicar para o novo hook de orçamento do pedido |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|---|---|---|
| Estender `_PEDIDO_BUDGET_SQL` existente (remover/ajustar `WHERE op.tipo = 'com'`) | Criar uma segunda query paralela só para "sem" | Duplicaria a lógica de agregação e criaria drift entre as duas fórmulas — CONTEXT.md (D-05) já pede reaproveitar a existente; ajustar é mais seguro que duplicar |
| Branch de `tipo` dentro de `montar_grade_atualizada` | Nova função `montar_grade_atualizada_livre` para "sem" | Uma função nova evita mexer no caminho "com" (D-01, zero mudança), mas duplica ~80% da lógica de rateio/validação. Decisão de planejamento — ver Pitfall 2 |
| Fundir aprovação chamando o repositório de `aprovar_produto_canal` diretamente dentro da transação já aberta | Chamar `executar_aprovacao_produto` (o caso de uso completo) como sub-rotina | `executar_aprovacao_produto` reabre `_adquirir_lock_processamento` (seguro, é reentrante) e faz seu próprio commit — chamar sua versão "crua" (sem lock/commit duplicado) evita dois `db.commit()` na mesma função |

**Installation:** N/A — nenhum pacote novo.

## Package Legitimacy Audit

Não aplicável — esta fase não introduz nenhuma dependência de terceiros (backend ou frontend). Nenhuma verificação de registry é necessária.

## Architecture Patterns

### System Architecture Diagram

```
[Frontend: product-grade-detail-modal.tsx]
   │
   │ 1. Ao abrir modal de edição de um produto
   │    (useProductClients -> GET /produtos/clientes)
   ▼
[Backend: listar_clientes_produto] ──► retorna rows já com `order.adequacaoAplicada`
   │                                    (= tipo == "com", JÁ EXISTE, sem mudança de schema)
   │
   │ 2. [NOVO] response passa a incluir orçamento agregado por nr_pedido
   │    (via load_pedido_budget nos nr_pedidos da página, só quando !adequacaoAplicada)
   ▼
[Frontend] exibe indicador de orçamento (D-13) por linha "sem adequação"
   │
   │ 3. Operador edita quantidade de um ou mais clientes (linhas com adequacaoAplicada=false
   │    agora podem mudar o total da linha, não só redistribuir)
   ▼
[Frontend] valida localmente (D-16, feedback só) contra o orçamento já buscado
   │
   │ 4. Clique único "Salvar e Aprovar" (D-09) quando tipo=sem e dentro do limite
   │    -> PUT /produtos/grades  (mesmo endpoint de hoje, sem endpoint novo)
   ▼
[Backend: executar_alteracao_grades_produto]
   │  a. _adquirir_lock_processamento (pg_try_advisory_xact_lock, global, reentrante)
   │  b. carrega states (já inclui state.tipo)
   │  c. [NOVO] para cada nr_pedido distinto no lote:
   │       - load_pedido_budget({nr_pedidos}) (CORRIGIDO — ver Pitfall 1)
   │       - OrcamentoPedido(...).consumir_adicao/consumir_corte(delta)
   │       - falha -> ProdutoBatchConflitoError (409, all-or-nothing, nenhum write)
   │  d. branch por state.tipo em montar_grade_atualizada (com=igual hoje; sem=total livre
   │     dentro do que já passou na validação do passo c)
   │  e. [NOVO] preserva qt_solicitada original entre edições (ver Pitfall 2)
   │  f. grava updates/modifications/linx_rows (inalterado)
   │  g. [NOVO] se tipo=sem e dentro do limite: chama a lógica de aprovação
   │     (aprovar_produto_canal) DENTRO da mesma transação, antes do único db.commit()
   │  h. record_event / alerts (padrão já existente)
   ▼
[Postgres] ordens_reserva.itens atualizado + aprovado_em setado no mesmo commit
```

### Recommended Project Structure

Nenhuma pasta nova — todos os arquivos a tocar já existem:

```
app/modules/pedidos/
├── domain/
│   ├── orcamento_pedido.py        # reaproveitar, sem mudança de contrato
│   └── edicao_grade.py            # branch por tipo (Pitfall 2)
├── application/
│   └── casos_uso.py               # executar_alteracao_grades_produto: novo passo de
│                                   # validação + fusão de aprovação
├── processing/infrastructure/
│   └── adapters.py                # _PEDIDO_BUDGET_SQL: remover/ajustar filtro tipo='com'
├── infrastructure/
│   ├── produtos/listar_clientes_produto.py   # embutir orçamento na resposta (D-12)
│   └── sql/estoque_cte.py         # já expõe tipo via `adequacao` — NENHUMA mudança aqui
└── application/schemas.py         # novo(s) campo(s) em ProdutoClienteRowOut/Summary

frontend/
├── features/pedidos/
│   ├── model/
│   │   └── use-orcamento-pedido.ts        # NOVO — replica padrão de use-tolerancia-adequacao.ts
│   ├── ui/modals/
│   │   └── product-grade-detail-modal.tsx # relaxar hasInvalidClientTotals por linha,
│   │                                       # unificar save+approve, indicador/aviso/alerta
│   └── api/pedidos.api.ts                 # tipos extendidos (orçamento no summary/row)
```

### Pattern 1: Resolver o ledger de orçamento por pedido (backend, padrão já usado no motor)

**What:** Construir um `OrcamentoPedido` por `nr_pedido`, lendo `total_original`/`consumido_previo_*`
de uma fonte agregada — exatamente o padrão que `motor_adequacao.py` já usa via
`_resolver_orcamento_pedido` + `load_pedido_budget`.

**When to use:** Dentro de `executar_alteracao_grades_produto`, para cada `nr_pedido` distinto
tocado no lote, antes de aplicar qualquer `montar_grade_atualizada` de tipo "sem".

**Example (padrão real, `motor_adequacao.py:851-872`):**
```python
# Source: app/modules/pedidos/domain/motor_adequacao.py:851-872 (padrão a replicar)
(
    total_original,
    consumido_previo_adicao,
    consumido_previo_corte,
    usou_placeholder,
) = _resolver_orcamento_pedido(
    nr,
    itens_do_pedido,
    total_original_por_pedido,
    consumido_previo_adicao_por_pedido,
    consumido_previo_corte_por_pedido,
)

ledger = OrcamentoPedido(
    nr_pedido=nr,
    total_original=total_original,
    consumido_previo_adicao=consumido_previo_adicao,
    consumido_previo_corte=consumido_previo_corte,
    tolerancia=tolerancia,
)
```
Na edição manual, `total_original_por_pedido`/`consumido_previo_*_por_pedido` vêm de
`load_pedido_budget({nr_pedidos_do_lote})` (chamada única para todos os pedidos do lote, já
suporta `set[int]` e faz chunking de 5.000 — nenhuma mudança de assinatura necessária). O delta a
consumir (`consumir_adicao`/`consumir_corte`) é `new_qty - current_qty` por `state` (ambos já
calculados dentro do loop existente de `executar_alteracao_grades_produto`).

### Pattern 2: Branch por `tipo` na validação de total fixo (domínio)

**What:** `montar_grade_atualizada` hoje levanta `ValueError` sempre que `new_qty != current_qty`
(edicao_grade.py:60-63). Essa validação precisa ficar condicional a `tipo == "com"` — para
`tipo == "sem"`, o total pode mudar, mas só depois que o chamador (`executar_alteracao_grades_produto`)
já validou que o delta cabe no `OrcamentoPedido` daquele `nr_pedido` (Pattern 1).

**When to use:** Toda vez que `executar_alteracao_grades_produto` chama a função de domínio que
reconstrói a grade, para qualquer `state` cujo `state.tipo == "sem"`.

**Example (estado atual, o que precisa ser tornado condicional):**
```python
# Source: app/modules/pedidos/domain/edicao_grade.py:59-63 (comportamento atual, "com" mantém)
new_qty = sum(positive_sizes.values())
if new_qty != current_qty:
    raise ValueError(
        "edição de grade só redistribui tamanhos e deve preservar a quantidade total"
    )
```
A forma exata de tornar isso condicional (parâmetro novo `tipo`/`permitir_variacao_total: bool` na
assinatura de `montar_grade_atualizada`, vs. uma segunda função dedicada) é decisão de
planejamento — ver Alternatives Considered acima. Qualquer que seja a forma escolhida, o caminho
"com" (D-01) precisa continuar levantando `ValueError` exatamente como hoje — não relaxar sem
querer os dois tipos.

### Anti-Patterns to Avoid

- **Validar o orçamento fora da transação/lock (`if` solto antes de chamar
  `executar_alteracao_grades_produto`):** quebra D-07 diretamente — duas edições concorrentes de
  produtos diferentes do mesmo pedido, cada uma OK isoladamente, podem estourar juntas se a checagem
  não estiver dentro do lock global já existente.
- **Reescrever `qt_solicitada` para o valor pós-edição em toda chamada de `montar_grade_atualizada`
  sem preservar o original:** zera silenciosamente o histórico de consumo do pedido — ver Pitfall 2.
  Isso já é uma característica presente no código hoje (mesmo para "com"), mas só se torna um bug
  visível quando o total pode variar — o que esta fase introduz.
- **Confiar só na validação do frontend (D-16 é explícito: front é só UX).** Qualquer checagem de
  orçamento no cliente deve ser tratada como conveniência, nunca como gate real.
- **Duplicar a query de orçamento** (uma nova query "só para manual", outra para o motor
  automático) em vez de ajustar `_PEDIDO_BUDGET_SQL` uma única vez — cria duas fontes de verdade
  que podem divergir silenciosamente.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| Cálculo de limite/restante ±5% | Nova classe de ledger | `OrcamentoPedido` (`orcamento_pedido.py`) | Já testado em 13 casos (`test_pedidos_orcamento_pedido.py`), aritmética `Decimal`/`floor` cuidadosamente calibrada para não desviar por `float` |
| Agregação "quanto o pedido já gastou" | Query SQL nova do zero | `_PEDIDO_BUDGET_SQL` / `load_pedido_budget` (ajustada) | Já resolve o caso "pares que saíram do snapshot de pendentes" (invariante J5) — reescrever do zero reintroduz esse bug já corrigido na Phase 15 |
| Rateio de valor financeiro entre tamanhos após mudar quantidade | Divisão proporcional própria | `ratear_hamilton` (via `edicao_grade.py`) | Já garante soma exata em centavos (Hamilton apportionment); divisão ingênua reintroduz drift de centavos (o mesmo bug que FIX-02 corrigiu na Phase 14) |
| Indicador de "quanto falta buscar do parâmetro real" no front | Novo padrão de fetch ad-hoc | Replicar `useToleranciaAdequacaoFator` (`use-tolerancia-adequacao.ts`) | Padrão já estabelecido: React Query + `staleTime` + fallback local sem tela nunca ficar sem número |

**Key insight:** Toda a "física" do orçamento ±5% (limite, consumo, aritmética inteira exata) já
existe e está testada; o trabalho desta fase é 90% fiação (plumbing) — conectar uma fonte de dados
que já existe a um caminho de escrita que ainda não a consulta — e 10% ajuste cirúrgico em duas
funções que hoje assumem, implicitamente, que o total nunca muda.

## Runtime State Inventory

Não aplicável — esta fase não é rename/refactor/migration. Não há strings, chaves de
armazenamento, configuração de serviço externo, tarefas de OS ou variáveis de ambiente sendo
renomeadas ou migradas.

## Common Pitfalls

### Pitfall 1: `_PEDIDO_BUDGET_SQL` ignora consumo de OR "sem adequação" por desenho — e isso deixa de ser inofensivo nesta fase

**What goes wrong:** O CTE `consumido` de `_PEDIDO_BUDGET_SQL`
(`app/modules/pedidos/processing/infrastructure/adapters.py:132-153`) tem `WHERE op.tipo = 'com'`
explícito. Depois que esta fase permitir editar o total de pedidos "sem adequação", qualquer
edição salva vai gravar `qt_liquida != qt_solicitada` num item cujo `tipo` é `'sem'` — e essa
diferença **nunca vai aparecer no `consumido_adicao`/`consumido_corte`** calculado pela query,
porque o `WHERE` a exclui. O `restante_adicao`/`restante_corte` retornado por uma segunda chamada
de `load_pedido_budget` para o mesmo pedido vai continuar mostrando o orçamento cheio, mesmo que a
primeira edição já tenha consumido parte dele.

**Why it happens:** A query foi escrita antes desta fase existir, para um mundo onde "sem
adequação" é tudo-ou-nada e portanto nunca produz `qt_liquida != qt_solicitada`
[VERIFIED: `app/tests/test_pedidos_processing_repository.py:1152` fixa `tipo="com"` explicitamente
no teste que exercita esse CTE]. O filtro nunca precisou cobrir `'sem'` até agora.

**How to avoid:** Ajustar o `WHERE` do CTE `consumido` para incluir também `tipo = 'sem'` (ou
remover o filtro de tipo inteiramente, já que a lógica de `GREATEST(qt_liquida - qt_solicitada, 0)`
é neutra em relação a tipo — o que muda é só QUEM hoje pode gerar essa diferença). Cobrir com um
teste de integração análogo aos já existentes em `test_pedidos_processing_repository.py`
(linhas ~1090-1230), mas com `tipo="sem"` e itens com `qt_liquida != qt_solicitada`.

**Warning signs:** Um teste que salva duas edições sucessivas no mesmo pedido "sem" e espera que
a segunda tentativa de ultrapassar o limite seja rejeitada, mas ela passa — sinal de que o
`consumido` está sempre voltando 0 para aquele pedido.

### Pitfall 2: `montar_grade_atualizada` reseta `qt_solicitada` para o valor pós-edição — corrompe o ledger em edições sucessivas

**What goes wrong:** Em `edicao_grade.py:93`, cada item novo grava
`"qt_solicitada": positive_sizes[size]` — ou seja, o MESMO valor que acabou de virar
`qt_liquida`. Combinado com o Pitfall 1 corrigido, isso ainda quebra o ledger: depois de UMA
edição salva, aquele item passa a ter `qt_liquida == qt_solicitada` de novo (ambos = valor novo),
então o `consumido` calculado a partir dele volta a ser 0 — apagando o rastro de que aquele
pedido já usou parte do orçamento. Uma segunda edição no mesmo pedido veria orçamento cheio de
novo, permitindo estourar o limite cumulativo ao longo de várias edições — exatamente o tipo de
"double-spend" que o docstring de `OrcamentoPedido` (linhas 10-19) foi escrito para impedir no
motor automático, mas que passaria batido na edição manual sem esse ajuste.

**Why it happens:** A função foi desenhada para o caso hoje único (redistribuição, total sempre
igual) — nesse caso, resetar `qt_solicitada = qt_liquida` é inofensivo porque o pedido nunca teve
`qt_liquida != qt_solicitada` para começo de conversa (a diferença solicitada-vs-liquida só existe
hoje para OR "com adequação" processadas pelo motor, nunca para edição manual).

**How to avoid:** Quando `tipo == "sem"`, o novo item precisa preservar o `qt_solicitada`
ORIGINAL — ou herdado do item existente (`item.get("qt_solicitada", item.get("qt_liquida"))` do
`itens_originais`, casado por tamanho), ou, se o tamanho é novo (não existia antes na grade),
decidir explicitamente qual baseline usar (ex.: 0, já que nenhuma quantidade foi "originalmente
pedida" para aquele tamanho pelo cliente na OR — mas isso é uma decisão de planejamento explícita
a documentar, não assumir). Este é provavelmente o ajuste de maior risco de bug silencioso da fase
inteira — merece um teste de propriedade ou pelo menos um teste dedicado de "duas edições
sucessivas no mesmo pedido, a segunda deve ver o consumo da primeira".

**Warning signs:** Testar exatamente o cenário de duas edições sucessivas (não uma só) no mesmo
`nr_pedido` "sem adequação" — se só testar uma edição isolada, este bug não aparece, porque
`consumido_previo_* = 0` está correto na PRIMEIRA edição por definição (D-06).

### Pitfall 3: granularidade do lote — uma chamada de `executar_alteracao_grades_produto` pode tocar múltiplos `nr_pedido`s diferentes

**What goes wrong:** É fácil (mas errado) tratar o orçamento como "um por chamada de API" em vez
de "um por `nr_pedido`". O endpoint `PUT /produtos/grades` recebe até 100 `changes`, cada um com
seu próprio `order_id` (= `nr_pedido`) — o lote inteiro é de UM produto/canal, mas pode abranger
MUITOS clientes/pedidos diferentes. O orçamento ±5% de cada `nr_pedido` é independente; validar a
SOMA de todos os deltas do lote contra um único ledger, em vez de um ledger por `nr_pedido`, ou
aceitaria estouro em um pedido individual (se outro pedido do lote "compensar" na soma), ou
rejeitaria o lote inteiro por causa de um único pedido dentro do limite.

**Why it happens:** O código de `executar_alteracao_grades_produto` já pensa em termos de listas
agregadas por tamanho (`old_qty_by_size`/`new_qty_by_size`) para a checagem de ESTOQUE (que é
por produto, correto agregar) — é fácil copiar esse padrão de agregação para o orçamento por
engano, quando o orçamento precisa ser agregado por `nr_pedido`, não por produto.

**How to avoid:** Calcular o delta (`new_qty - current_qty`) por `state` individualmente dentro do
loop já existente (`for state in states:`), e chamar `load_pedido_budget` uma vez para o
`set` de todos os `nr_pedido`s do lote (eficiente, já suporta lote), mas instanciar um
`OrcamentoPedido` (e chamar `consumir_adicao`/`consumir_corte`) por `nr_pedido` individualmente.

**Warning signs:** Um teste com 2 clientes/pedidos diferentes no mesmo lote, um dentro do limite e
outro estourando — deve rejeitar o lote inteiro (ou só o pedido problemático, dependendo da
decisão de all-or-nothing tomada no planejamento) sem deixar o pedido problemático passar
"escondido" atrás do outro que está OK.

### Pitfall 4: granularidade de aprovação do botão unificado difere da granularidade do lote de edição

**What goes wrong:** O botão "Aprovar OR" de hoje (`onApprove` → `approveProduct(code, channel)` →
`POST /produtos/aprovar` → `aprovar_produto_canal`) aprova **todos** os clientes/pedidos abertos
daquele produto+canal, não só os que acabaram de ser editados
[VERIFIED: `app/modules/pedidos/infrastructure/http/routes.py:212-233`,
`frontend/widgets/pedido-dashboard/ui/orders-list.tsx:791-814`]. Se a fusão
salvar+aprovar (D-09) reusar essa mesma função tal como está, ela vai aprovar QUALQUER pedido
aberto daquele produto/canal — inclusive pedidos "com adequação" misturados no mesmo produto, ou
pedidos "sem adequação" que não foram tocados nesta edição específica.

**Why it happens:** É o comportamento JÁ EXISTENTE e aceito hoje (quando o operador clica "Salvar"
e depois "Aprovar OR" separadamente, o segundo clique já aprova o produto inteiro, não só o que
foi editado) — não é um bug novo desta fase, mas a fusão automática precisa decidir
conscientemente se replica esse escopo (mais simples, consistente com o comportamento manual de
hoje) ou se restringe a aprovação automática só aos `order_id`s que estavam no `payload.changes`
desta chamada (mais conservador, mas divergente do botão manual existente).

**How to avoid:** Esta é uma decisão de planejamento explícita, não uma dedução de pesquisa — a
opção mais segura (menor superfície de mudança de comportamento) é preservar o escopo atual de
"aprova o produto/canal inteiro", já que é exatamente o que acontece hoje quando os dois cliques
são feitos manualmente em sequência. Documentar a escolha no PLAN.md explicitamente.

**Warning signs:** Um teste que edita só 1 de 3 clientes abertos do produto e verifica se os
outros 2 (não tocados) também saem aprovados — o resultado esperado depende da decisão acima, mas
o teste precisa existir para o comportamento não ficar acidental.

## Code Examples

### Onde `tipo` já está disponível sem I/O extra (D-03 resolvido de graça)

```python
# Source: app/modules/pedidos/application/casos_uso.py:269-283 (já existe, sem mudança)
for state in states:
    if state.aprovado_em is not None or state.created_at < limit:
        raise ProdutoBatchConflitoError(...)
    actual_version = calcular_versao_ordem(
        tipo=state.tipo,   # <- já carregado por carregar_ordens_produto_para_update
        created_at=state.created_at,
        aprovado_em=state.aprovado_em,
        itens=state.itens,
    )
```
`state.tipo` já é "com"/"sem" — a leitura de D-03 não precisa de query nova.

### Onde `tipo` já chega pronto no frontend, por linha (nenhum campo novo necessário para isso)

```python
# Source: app/modules/pedidos/infrastructure/sql/estoque_cte.py:99
o.tipo = 'com' AS adequacao,
```
```typescript
// Source: frontend/shared/types/models.ts:27 (já existe)
adequacaoAplicada: boolean;
```
Cada linha (`ClientGrid.orderRef`, tipo `Order`) que a `product-grade-detail-modal.tsx` já recebe
de `/produtos/clientes` já carrega `adequacaoAplicada`. A condição de relaxamento
`hasInvalidClientTotals` deve ser recalculada POR LINHA: uma linha com
`row.orderRef.adequacaoAplicada === false` pode ter total diferente do baseline; uma linha com
`true` continua exigindo igualdade — **nenhum campo novo de "tipo" precisa ser adicionado ao
contrato hoje** para essa parte específica (D-03 no frontend já está resolvido pelo campo
existente).

### A trava atual que precisa virar condicional (frontend)

```typescript
// Source: frontend/features/pedidos/ui/modals/product-grade-detail-modal.tsx:334-338
const invalidTotalRows = useMemo(
  () => dirtyRows.filter(row => rowTotal(row) !== baselineRowTotal(row)),
  [dirtyRows],
);
const hasInvalidClientTotals = invalidTotalRows.length > 0;
```
Precisa passar a ignorar linhas onde `row.orderRef.adequacaoAplicada === false` (ou seja, filtrar
`invalidTotalRows` para só considerar linhas "com adequação" como inválidas por mudança de total;
linhas "sem" com total mudado passam a ser válidas na checagem local, sujeitas à checagem separada
de orçamento vindo do backend).

### Padrão de hook a replicar para o orçamento do pedido

```typescript
// Source: frontend/features/pedidos/model/use-tolerancia-adequacao.ts (padrão completo)
export function useToleranciaAdequacaoFator(): number {
  const query = useQuery({
    queryKey: toleranciaAdequacaoQueryKey,
    queryFn: ({ signal }) => fetchToleranciaAdequacao(signal),
    staleTime: 5 * 60 * 1000,
  });
  return 1 + (query.data ?? DEFAULT_TOLERANCIA_ADEQUACAO);
}
```
Para o orçamento do pedido, o `staleTime` precisa ser bem menor (ou `staleTime: 0` com
invalidação ativa pós-save) porque, ao contrário da tolerância (que muda raramente), o orçamento
muda a cada edição salva do próprio usuário — CONTEXT.md já observa isso em
`## Established Patterns`.

## State of the Art

Não aplicável no sentido usual (não há uma "versão antiga de biblioteca" sendo substituída) — mas
vale registrar a evolução interna do próprio domínio:

| Old Approach | Current Approach | When Changed | Impact |
|---|---|---|---|
| Tolerância fixa 5% hardcoded (`ceil`, por produto isolado) | `OrcamentoPedido` com `floor`, por pedido completo, dois orçamentos independentes | Phase 14 (2026-08-17) | Base que esta fase reaproveita — não recriar |
| Orçamento resetado a cada execução do motor | Orçamento acumulado entre execuções via `load_pedido_budget` real | Phase 15 (fechando ALOC-09) | O mesmo princípio de "nunca resetar entre execuções" se aplica a edições manuais sucessivas (Pitfall 2) |
| Edição manual só redistribui, nunca muda total | Esta fase: "sem adequação" pode mudar total dentro de ±5% | Phase 20 (esta fase) | Primeira vez que o orçamento ±5% é consultado fora do motor automático |

**Deprecated/outdated:** Nenhum código desta área está marcado como deprecated; tudo que esta
fase toca está ativo e em uso.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | Preservar `qt_solicitada` original do item (não resetar) é a correção correta para o Pitfall 2, incluindo o caso de tamanho novo que não existia na grade original (baseline sugerido: 0) | Common Pitfalls, Pitfall 2 | Se a regra de negócio para "tamanho novo" for outra (ex.: herdar do tamanho mais próximo), o ledger pode super ou subestimar consumo em edições que introduzem tamanhos inexistentes antes — cenário raro mas não impossível |
| A2 | A fusão salvar+aprovar deve preservar o escopo atual de `aprovar_produto_canal` (aprova todo o produto/canal, não só os `order_id`s editados) | Common Pitfalls, Pitfall 4 | Se a intenção real de D-09 for aprovar só os pedidos tocados nesta edição, replicar o escopo atual aprovaria pedidos indevidamente junto — mudança de comportamento observável para o usuário final |
| A3 | Quando um pedido do lote estoura o orçamento, o comportamento correto é rejeitar o LOTE INTEIRO (all-or-nothing), consistente com a checagem de estoque já existente na mesma função | Common Pitfalls, Pitfall 3 | Se a intenção for permitir salvar os pedidos que cabem e só rejeitar o que estourou, a implementação precisa de um caminho de erro parcial que a função hoje não tem em nenhum outro ponto — mudança estrutural maior |

**Nenhuma dessas afeta D-01 a D-16** (que são decisões de negócio já fechadas) — são apenas
lacunas mecânicas que o CONTEXT.md deixou explicitamente como "Claude's Discretion" e que a
pesquisa não resolve sozinha porque dependem de uma escolha de produto, não de um fato do código.

## Open Questions

1. **Tamanho novo introduzido numa edição "sem adequação": qual `qt_solicitada` baseline usar?**
   - What we know: Para tamanhos que já existiam na grade original, `qt_solicitada` original está
     disponível em `itens_originais`. Para um tamanho totalmente novo (o cliente não tinha esse
     tamanho na grade antes da edição), não há um `qt_solicitada` prévio a herdar.
   - What's unclear: Se um tamanho novo deve contar como consumo de "adição" integral (baseline 0)
     ou se a regra de negócio trata isso de outra forma.
   - Recommendation: Tratar como adição integral (baseline 0) por ser o comportamento mais
     conservador e consistente com a semântica de "adição" do `OrcamentoPedido` — confirmar com a
     mantenedora se este caso realmente ocorre na prática (grades costumam ser fixas por produto).

2. **Escopo exato da aprovação automática (Pitfall 4 / Assumption A2).**
   - What we know: O comportamento manual hoje (dois cliques) já aprova o produto/canal inteiro.
   - What's unclear: Se a automação de D-09 deve replicar esse escopo ou restringir aos pedidos
     recém-editados.
   - Recommendation: Planejar a versão que replica o escopo atual (menor mudança de
     comportamento observável) e documentar a escolha explicitamente no PLAN.md para revisão da
     mantenedora antes da execução.

3. **All-or-nothing entre pedidos diferentes no mesmo lote quando um estoura o orçamento
   (Pitfall 3 / Assumption A3).**
   - What we know: A checagem de estoque já existente na mesma função é all-or-nothing para o
     lote inteiro.
   - What's unclear: Se a checagem de orçamento deve seguir o mesmo padrão ou permitir sucesso
     parcial.
   - Recommendation: Seguir o padrão já existente (all-or-nothing) por consistência de código e
     simplicidade de raciocínio transacional — confirmar no planejamento.

## Environment Availability

Não aplicável — nenhuma dependência de ferramenta/serviço externo novo. Toda a infraestrutura
(Postgres, Redis, SQLAlchemy async, Next.js/React Query) já está em uso ativo pelo restante do
sistema e não muda nesta fase.

## Validation Architecture

### Test Framework

| Property | Value |
|---|---|
| Backend framework | `pytest` 8.3.0+ com `pytest-asyncio` (`asyncio_mode = "auto"`) |
| Backend config | `backend/pyproject.toml` |
| Backend quick run | `./.venv/Scripts/python.exe -m pytest app/tests/test_pedidos_product_writes.py -x` (⚠️ `uv run` quebra neste ambiente por causa do caminho com "Área" acentuado — ver MEMORY.md do usuário; usar o venv diretamente) |
| Backend full suite | `./.venv/Scripts/python.exe -m pytest` |
| Frontend framework | `vitest` 4.1.5+ com Testing Library, ambiente `jsdom` |
| Frontend config | `frontend/vitest.config.ts` |
| Frontend quick run | `pnpm test -- product-grade-detail-modal` (a partir de `frontend/`) |
| Frontend full suite | `pnpm test` |

### Phase Requirements → Test Map

| Decisão | Behavior | Test Type | Automated Command | File Exists? |
|---|---|---|---|---|
| D-02/D-07/D-08 | Edição "sem adequação" respeita ±5% de adição/corte independentes, por `nr_pedido` | unit/integration | `pytest app/tests/test_pedidos_product_writes.py -k orcamento -x` | ❌ Wave 0 (extender `test_pedidos_product_writes.py`) |
| D-05/D-06 (Pitfall 1) | `load_pedido_budget` reflete consumo de OR `tipo='sem'` já editada | integration | `pytest app/tests/test_pedidos_processing_repository.py -k pedido_budget -x` | ⚠️ Existe o arquivo/padrão, falta caso `tipo="sem"` |
| Pitfall 2 (qt_solicitada) | Duas edições sucessivas no mesmo pedido acumulam consumo corretamente | integration | `pytest app/tests/test_pedidos_product_writes.py -k orcamento_sucessivo -x` | ❌ Wave 0 |
| D-09/D-10 (fusão salvar+aprovar) | Salvar dentro do limite finaliza a OR sem clique separado | integration | `pytest app/tests/test_pedidos_product_writes.py -k salvar_e_aprovar -x` | ❌ Wave 0 |
| D-01/D-11 | "Com adequação" — zero mudança de comportamento (regressão) | integration | `pytest app/tests/test_pedidos_product_writes.py -x` (suíte completa do arquivo, sem filtro) | ✅ já existe |
| D-12/D-13/D-15 | Indicador de orçamento aparece no modal; alerta quando ultrapassa | component | `pnpm test -- product-grade-detail-modal` | ⚠️ Existe o arquivo de teste do modal; falta os casos novos |
| D-09 (frontend) | Botão único "Salvar e Aprovar" some/aparece conforme tipo e limite | component | `pnpm test -- product-grade-detail-modal` | ⚠️ idem acima |

### Sampling Rate

- **Per task commit:** rodar o arquivo de teste específico tocado (backend:
  `test_pedidos_product_writes.py`; frontend: `product-grade-detail-modal.test.tsx`)
- **Per wave merge:** suíte completa de backend + suíte completa de frontend
- **Phase gate:** as duas suítes completas verdes antes de `/gsd-verify-work`

### Wave 0 Gaps

- [ ] Casos novos em `app/tests/test_pedidos_product_writes.py` cobrindo: edição "sem" dentro do
      limite (sucesso + auto-aprovação), edição "sem" que estoura adição, edição "sem" que estoura
      corte, duas edições sucessivas no mesmo pedido (Pitfall 2), lote com múltiplos `nr_pedido`s
      (Pitfall 3), regressão "com adequação" inalterada (D-01/D-11)
- [ ] Caso novo em `app/tests/test_pedidos_processing_repository.py` cobrindo `load_pedido_budget`
      com `tipo="sem"` e `qt_liquida != qt_solicitada` (Pitfall 1)
- [ ] Casos novos em `product-grade-detail-modal.test.tsx` cobrindo: linha "sem adequação" aceita
      total diferente, linha "com adequação" continua rejeitando, indicador de orçamento visível,
      alerta `role="alert"` quando excede, botão unificado "Salvar e Aprovar" para "sem" dentro do
      limite
- [ ] Nenhum framework novo a instalar — gaps são só de casos de teste dentro dos arquivos já
      existentes

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | não (sem mudança) | JWT/SSO já existente, endpoint já atrás de auth |
| V3 Session Management | não (sem mudança) | inalterado |
| V4 Access Control | sim | `PUT /produtos/grades` e `POST /produtos/aprovar` já exigem `require_actor` (`Depends`) — nenhuma mudança de nível de acesso necessária nesta fase |
| V5 Input Validation | sim | Pydantic (`AlterarGradesProdutoRequest`, já valida bounds de `order_id`/tamanhos/quantidade) — a nova validação de orçamento é regra de negócio, não validação de schema, mas deve reusar o mesmo padrão de exceção de domínio (`ProdutoBatchConflitoError` → HTTP 409) já usado para estoque insuficiente |
| V6 Cryptography | não | sem dado sensível novo, sem segredo, sem hash |

### Known Threat Patterns for este stack

| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| TOCTOU (race entre validar orçamento e gravar) | Tampering | Já mitigado pelo desenho: `pg_try_advisory_xact_lock` global + validação dentro da mesma transação (D-07) — não introduzir um `SELECT` de orçamento fora do lock |
| Double-spend de orçamento por reset de estado (Pitfall 2) | Tampering / Repudiation | Preservar `qt_solicitada` original entre edições; sem isso, o sistema "esquece" consumo já feito, permitindo estourar o limite de negócio sem detecção — não é uma vulnerabilidade de segurança clássica, mas é uma falha de integridade de dado financeiro com o mesmo padrão de risco |
| Bypass da validação client-side (D-16 já assume isso) | Tampering | Backend é a única fonte de verdade — qualquer requisição direta ao `PUT /produtos/grades` sem passar pela UI ainda precisa ser rejeitada pela validação do backend |

## Sources

### Primary (HIGH confidence — leitura direta do código nesta sessão)

- `app/modules/pedidos/application/casos_uso.py` — `executar_alteracao_grades_produto`, `aprovar_produto_canal`, `aprovar_ordem_reserva`, `executar_aprovacao_produto`, `executar_aprovacao`, `_adquirir_lock_processamento`
- `app/modules/pedidos/service.py` — confirmação de que é wrapper fino de `casos_uso.py`
- `app/modules/pedidos/domain/orcamento_pedido.py` — `OrcamentoPedido` completo
- `app/modules/pedidos/domain/edicao_grade.py` — `montar_grade_atualizada`
- `app/modules/pedidos/domain/motor_adequacao.py` — `_resolver_orcamento_pedido`, uso de `OrcamentoPedido` (linhas 380-436, 849-872)
- `app/modules/pedidos/processing/domain.py` — `ModoAdequacao`, `tipo = "com"/"sem"` em `build_processing_plan`
- `app/modules/pedidos/processing/infrastructure/adapters.py` — `_PEDIDO_BUDGET_SQL`, `load_pedido_budget` (linhas 98-337)
- `app/modules/pedidos/processing/application/service.py` — chamada em lote de `load_pedido_budget` (linhas 190-264)
- `app/modules/pedidos/infrastructure/http/routes.py` — rotas `PUT /produtos/grades`, `POST /produtos/aprovar`, `POST /{nr_pedido}/aprovar`
- `app/modules/pedidos/infrastructure/write_adapter.py` — `adquirir_lock` via `pg_try_advisory_xact_lock`, `aprovar_produto_canal`
- `app/modules/pedidos/application/schemas.py` — `AlterarGradesProdutoResponse`, `AprovarProdutoResponse`, `ProdutoClienteRowOut`, `ProdutoClientesSummaryOut`, `PedidoCardOut.adequacao_aplicada`
- `app/modules/pedidos/infrastructure/sql/estoque_cte.py` — `o.tipo = 'com' AS adequacao`
- `app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py` — origem da query de `/produtos/clientes`
- `app/tests/test_pedidos_processing_repository.py` — testes de `load_pedido_budget` (linhas 1090-1420), confirma `tipo="com"` na fixture
- `app/tests/test_pedidos_product_writes.py` — inventário de testes existentes de `executar_alteracao_grades_produto`/aprovação
- `frontend/features/pedidos/ui/modals/product-grade-detail-modal.tsx` — `hasInvalidClientTotals`, `save()`, `approve()`
- `frontend/features/pedidos/model/use-tolerancia-adequacao.ts` — padrão de hook a replicar
- `frontend/features/pedidos/api/pedidos.api.ts` — `approveProduct`, `updateProductGrades`, `ProductClientsSummary`
- `frontend/widgets/pedido-dashboard/ui/orders-list.tsx` — wiring de `onApprove`/`aprovarProduto`
- `frontend/shared/types/models.ts` — `Order.adequacaoAplicada`
- `.planning/STATE.md`, `.planning/ROADMAP.md` — histórico de decisões v1.3 e da própria Phase 20

### Secondary (MEDIUM confidence)

- `.planning/codebase/TESTING.md` — comandos de teste (datado de 2026-08-04; comando `uv run`
  substituído por `./.venv/Scripts/python.exe -m` por causa da limitação de caminho conhecida do
  ambiente, registrada na memória do usuário, não no próprio doc)

### Tertiary (LOW confidence)

- Nenhuma — toda a pesquisa desta fase foi feita por leitura direta de código-fonte de primeira
  mão, sem necessidade de busca web (não há biblioteca externa ou API de terceiros envolvida).

## Metadata

**Confidence breakdown:**

- Standard stack: N/A — sem stack novo, 100% reaproveitamento verificado por leitura de código
- Architecture: HIGH — todos os call graphs, endpoints e camadas foram lidos e citados com número de linha
- Pitfalls: HIGH nos dois achados centrais (Pitfall 1 e 2 — confirmados por leitura direta do SQL/domínio e cruzados com teste existente); MEDIUM nas decisões de granularidade (Pitfalls 3/4 — são decisões de produto, não fatos de código)

**Research date:** 2026-09-04
**Valid until:** Enquanto `executar_alteracao_grades_produto`, `_PEDIDO_BUDGET_SQL` e
`montar_grade_atualizada` não forem alterados por outra fase — recomenda-se revalidar se alguma
fase futura tocar esses arquivos antes desta ser executada.
