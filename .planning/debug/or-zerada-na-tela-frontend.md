---
status: verified
trigger: "Investigar por que aparecem pedidos com quantidade líquida zero (OR zerada) na tela do frontend"
created: 2026-08-31
updated: 2026-09-01
---

# Debug Session: or-zerada-na-tela-frontend

## Symptoms

- **Expected behavior:** um pedido cuja grade some zero peças líquidas (depois da tolerância aplicada) não deveria virar/aparecer como uma Ordem de Reserva — deveria ficar de fora (stand by) ou ser tratado como erro, nunca persistido/exibido como OR zerada.
- **Actual behavior:** pedidos com quantidade líquida zero aparecem na tela do frontend (listagem de OR).
- **Error messages:** nenhum erro reportado — é um estado silenciosamente incorreto, não uma exceção.
- **Timeline:** desconhecida; usuária não sabe reproduzir com um pedido específico, só notou o padrão na tela/relatório.
- **Reproduction:** não isolada ainda — não há um `nr_pedido`/`cd_prod_cor` de exemplo confirmado.

## Symptoms — correção 2 (print da usuária, alvo real confirmado)

**IMPORTANTE — isto substitui a hipótese de alvo das duas rodadas anteriores.** A usuária mandou print: NÃO é a listagem de pedidos individuais nem o card "Pedidos em Aberto" em si — é o **card "Status dos Pedidos"**, na mesma área, com dois contadores agregados:

- **"Pedidos Aguardando Faturamento"** (badge "Pré-edição") = **0**
- **"Pedidos Liberados para Edição"** (badge "Em edição") = **0**
- Abaixo, os botões "Efetuar OR sem adequação" / "Efetuar OR com adequação" mostram "Previsão ... R$ 0,00" para os dois.

A usuária tem CERTEZA que existem pedidos reais aguardando/em edição agora — **o zero está errado, não é estado vazio genuíno**. Sem erro/crash visível.

**Cadeia rastreada pelo coordenador (ponto de partida, não redescobrir):**

- `frontend/widgets/pedido-dashboard/ui/orders-list.tsx`: cards usam `awaitingStageTotal`/`editingStageTotal`, vindos de props `awaitingChannelTotal`/`editingChannelTotal`.
- `frontend/app/(app)/pedidos/page.tsx:23,47-48`: `channelStats = resumo?.statsByChannel?.[selectedChannel]`; `awaitingChannelTotal={channelStats?.liberadosCount}`, `editingChannelTotal={channelStats?.editingOrderCount}`.
- `resumo` vem de `useAppData()` (`frontend/features/pedidos/model/app-data-provider.tsx`) → `frontend/features/pedidos/api/pedidos.api.ts:170`: `apiFetch<PedidosResumo>('/api/v1/pedidos/resumo', { signal })`.
- Backend: `app/modules/pedidos/infrastructure/repositorio_resumo.py::obter_resumo_sql` — a query SQL que soma `liberadosCount`/`editingOrderCount` por canal (`stats_rows`, CTEs `pending_orders`/`pending_counts`/`editing_pairs`/`editing_counts`).
- A checar rápido antes de aprofundar: `selectedChannel` (Franquia/Multimarca) bate com as chaves reais de `statsByChannel`? Usuária vê "0" literal, não travessão (`—`), o que sugere que `channelStats` é encontrado normalmente e o valor 0 vem de verdade da query — mas vale descartar mismatch de chave primeiro, é barato.
- Achados anteriores desta sessão (crash em `motor_adequacao.py`, FIX #1; subcontagem em `repositorio_snapshot.py`) continuam válidos e registrados, mas são achados PARALELOS — não é para mexer neles agora.

## Contexto já mapeado (roadmap v1.3, Fase 13 "Guarda contra OR zerada")

Goal da Fase 13 registrado em `.planning/ROADMAP.md`: "Nenhuma OR com soma de quantidade líquida igual a zero é persistida pelo processamento, mesmo em pedidos pequenos (grade total < 20 peças) onde a fórmula de tolerância atual (`ceil`) falha." Fase ainda não planejada nem investigada a fundo (`disk_status: no_directory`) — este debug session é o primeiro levantamento de causa raiz antes de planejar a Fase 13.

Hipótese de partida herdada do roadmap: a fórmula de tolerância usa `ceil`, o que em pedidos pequenos (grade total < 20 peças) pode arredondar para uma quantidade líquida de zero e ainda assim deixar a OR ser persistida/exibida.

## Current Focus

- **status:** REABERTO PELA TERCEIRA VEZ com alvo preciso (print da usuária) — ver `## Symptoms — correção 2`. Não é a listagem de produtos pendentes (`stage=aguardando`, investigada na rodada anterior sem sucesso) — é o card agregado "Status dos Pedidos" (`liberadosCount`/`editingOrderCount` de `GET /api/v1/pedidos/resumo`). FIX #1 e o achado de subcontagem em `repositorio_snapshot.py` continuam registrados como achados PARALELOS, não tocar agora.
- **hypothesis_3 (ativa):** Em `repositorio_resumo.py::obter_resumo_sql`, a CTE `pending_orders` (para `liberadosCount`) agrupa `pending_items` (que tem grão nr_pedido+cd_prod_cor+sg_tamanho, ou seja PODE conter MÚLTIPLOS PRODUTOS do mesmo nr_pedido) **diretamente por `nr_pedido`** e resolve o canal do pedido inteiro via `bool_and(...LIKE 'FRANQUIA%'...)` OU `bool_and(...LIKE 'MULTIMARCA%'...)` — exige que TODAS as linhas (de TODOS os produtos) daquele nr_pedido concordem no mesmo canal, senão `canal = NULL`. A validação de canal em `agregacao.py::_registrar_canal_do_par` só é feita POR PAR (nr_pedido, cd_prod_cor) — nunca compara canal ENTRE PRODUTOS DIFERENTES do mesmo nr_pedido. Logo é possível (sem violar nenhuma validação de ingestão) um nr_pedido ter produto A em Franquia e produto B em Multimarca, e `pending_orders.canal` virar NULL para esse pedido — ele então NUNCA bate com `channels.canal IN ('Franquia','Multimarca')` no `LEFT JOIN ... USING (canal)` final, e some SILENCIOSAMENTE de `liberados`/`blocked_credit` nos dois canais (nem conta em "Todos", que soma Franquia+Multimarca). **Ainda não confirma editingOrderCount=0** (que vem de `editing_pairs`, canal calculado POR PAR/OR individual, não por pedido inteiro — não parece ter a mesma vulnerabilidade) — precisa investigar separadamente ou achar uma causa mais geral (ex. mismatch de `selectedChannel`) que explique os DOIS contadores caindo a zero juntos.
- **next_action:** (1) checar RÁPIDO se `selectedChannel` (frontend) bate literalmente com as chaves de `statsByChannel` ("Franquia"/"Multimarca"/"Todos") — descartar mismatch de prop/chave antes de aprofundar na SQL; (2) reproduzir empiricamente contra o Postgres local: semear um nr_pedido com 2 produtos em canais diferentes e confirmar se `pending_orders.canal` vira NULL e o pedido some de `liberadosCount`; (3) investigar por que `editingOrderCount` também cai a zero — ler `editing_pairs`/`editing_counts` com mais cuidado, e considerar hipótese alternativa mais geral (filtro comum aos dois, ex. `channel` param errado, ou `pedidos_processados`/`pedidos_processados_erp` cobrindo TODOS os pedidos indevidamente).

**Achados colaterais reais de rodadas anteriores (não são a causa raiz procurada agora, mas valem correção futura):**
1. Subcontagem silenciosa: um par com pelo menos 1 tamanho válido + 1 tamanho de formato inválido (vazio ou >16 chars) tem o tamanho inválido silenciosamente excluído do total (`_PENDING_READ_INSERT_SQL`, CTE `size_rows` filtra por comprimento mas `products` não) — o total mostrado é MENOR que o real, sem aviso.
2. Gap de defesa em profundidade: `pedido_produto_read.canal NOT NULL` mais o `CASE...ELSE NULL` de canal-misto em `_PENDING_READ_INSERT_SQL` fariam o INSERT inteiro falhar (não só a linha problemática) se algum dia um canal misto/desconhecido escapasse da validação em `agregacao.py` — hoje inalcançável, mas frágil (nenhum teste cobre a SQL de projeção sozinha para este caso, só a camada de agregação Python).

## Evidence — reabertura (sintoma corrigido: "pedidos em aberto", pré-adequação)

- timestamp: 2026-08-31
  checked: rota real `GET /api/v1/pedidos/produtos?stage=aguardando` (`app/modules/pedidos/infrastructure/http/routes.py:80-141`), `EstagioProduto` (`app/modules/pedidos/application/schemas.py` + `domain/consultas.py`), label da página "Pedidos em Aberto" (`frontend/shared/config/page-metadata.ts:38`), coluna "Qtd. Total" (`frontend/widgets/pedido-dashboard/ui/orders-list.tsx:168-176`), hook `usePedidosQueues`/`fetchProductNumberedPage` com `stage: 'aguardando'`.
  found: confirma que a aba "Pedidos em Aberto" é servida por `stage=aguardando`, campo `totalQty` (schema `total_qty: int = Field(ge=0, serialization_alias="totalQty")`) renderizado sem transformação client-side ("Qtd. Total" = `{prod.totalQty} un.`, sem parsing adicional). `ge=0` no schema aceita 0 como valor válido sem rejeitar — se o SQL algum dia computar 0, nada a jusante barra.
  implication: alvo da investigação confirmado: `total_qty` vindo de `listar_produtos_sql` → CTE `pairs` (estágio "aguardando") → `pedido_produto_read.qty` (`source='pending'`).

- timestamp: 2026-08-31
  checked: `app/modules/ingestao/domain/agregacao.py::agregar_itens_pedidos` (linha 172-252) — camada de agregação PYTHON que roda ANTES de `substituir_pedidos` gravar em `pedidos`.
  found: cada linha bruta do Databricks passa por `qt = _parse_int(linha.get("qt_entregar"))` e é DESCARTADA (`descartadas += 1; continue`) se `qt <= 0`. `_parse_int` (`traducao_databricks.py:7-18`) faz `int(float(s))` — TRUNCA frações em direção a zero (ex. `qt_entregar=0.5` vira `int(0.5)=0`), mas esse valor truncado É pego pelo guard `qt <= 0` logo em seguida e a linha é descartada, nunca persistida com 0. Canal é validado por `_canal_obrigatorio`/`_normalizar_canal` (`traducao_databricks.py:61-70`, mesma normalização usada pela SQL de projeção) — RAISES `CanalOrigemInvalidoError` se o canal não reconhece OU se o MESMO par (nr_pedido, cd_prod_cor) tem canais diferentes entre tamanhos (`_registrar_canal_do_par`, linha 68-84). Essa exceção propaga sem catch local (`casos_uso.py::_preparar_pedidos` não captura `CanalOrigemInvalidoError`), abortando o ciclo de sync inteiro.
  implication: a pipeline de ingestão Python é MAIS defensiva do que a SQL de projeção sozinha sugere — canal misto/desconhecido em dados reais nunca chega a `pedidos` (aborta o sync inteiro antes, deixando dado ANTIGO/stale, não zero). Isso elimina "canal misto" como causa de linha visível com zero (embora reste uma lacuna real na camada SQL, ver próxima entrada — só inalcançável via ingestão normal).

- timestamp: 2026-08-31
  checked: reprodução empírica contra o Postgres local rodando (`docker exec system_automation_db psql`), inserindo linhas sintéticas DIRETO em `pedidos` (bypassando a camada Python de agregação, para testar a SQL de projeção isoladamente) e rodando o SQL exato de `_PENDING_READ_INSERT_SQL` (`app/modules/ingestao/infrastructure/repositorio_snapshot.py`).
  found: (1) par com canal misto (Franquia + Multimarca no mesmo nr_pedido+cd_prod_cor) faz a CTE `products` computar `canal = NULL`, o que **viola a constraint NOT NULL de `pedido_produto_read.canal`** e aborta o INSERT inteiro (statement completo, todas as linhas do batch) com erro Postgres — não silencioso a nível de banco, mas nunca alcançável via ingestão real (ver entrada acima); (2) par com 1 tamanho válido (qty=6) + 1 tamanho inválido (sg_tamanho só espaço, qty=2): o tamanho inválido é EXCLUÍDO de `size_rows` (filtro `length(trim(sg_tamanho)) BETWEEN 1 AND 16`) mas o `products` CTE não tem esse mesmo filtro — resultado: par aparece com `qty=6` (subcontagem SILENCIOSA das 2 peças do tamanho inválido, não zero); (3) par com TODOS os tamanhos inválidos: nenhuma linha sobra em `size_rows`/`grades`, e o `INNER JOIN products p JOIN grades g USING (nr_pedido, cd_prod_cor)` EXCLUI o par inteiro de `pedido_produto_read` — o par fica INVISÍVEL (não aparece na listagem), não com zero.
  implication: NENHUM dos 3 cenários testados reproduz "linha visível com total_qty=0, sem erro". A pipeline sempre resulta em (a) subcontagem não-zero, (b) exclusão total (invisível), ou (c) crash a nível de SQL (canal). Achado (2), embora não seja a causa raiz procurada, é um bug real e reproduzível (subcontagem silenciosa) — vale nota separada.

- timestamp: 2026-08-31
  checked: confirmação via API real rodando localmente (`docker ps` mostrou `system_automation_api` saudável) — login via `POST /api/auth/sign-in` (usuário seed `gestor@example.com`), depois `GET /api/v1/pedidos/produtos?stage=aguardando&search=PROD` com JWT.
  found: resposta real da API bate exatamente com a leitura direta do banco: `PRODCTRL` (controle) `totalQty=5`; `PRODINV` (tamanho misto válido/inválido) `totalQty=6` (não zero, subcontado); `PRODALLINV` (todos tamanhos inválidos) ausente da resposta (nem aparece).
  implication: confirma ponta a ponta (SQL + API real, não só leitura de código) que os cenários de dado malformado testados não produzem uma linha visível com quantidade zero. Dados de teste removidos do banco após o teste (`DELETE FROM pedidos/pedido_produto_read WHERE nr_pedido BETWEEN 7000000 AND 7000010`) — banco local voltou a 0 linhas em todas as tabelas relevantes.

- timestamp: 2026-08-31
  checked: `app/modules/pedidos/infrastructure/models.py` (schema de `pedido_produto_read`, incluindo `CheckConstraint("qty >= 0", name="ck_ppr_qty_nao_negativa")`) e `_ratear_valor_repetido_por_tamanho`/`pares_invalidos` em `agregacao.py` (linha 234-241, descarta o PAR INTEIRO se houver mais de um valor distinto de `vl_liquido` entre seus tamanhos).
  found: `qty >= 0` é uma CHECK CONSTRAINT explícita no banco — o schema TOLERA zero por design (não é um bug de schema faltando validação), reforçando que, SE zero chegasse até aqui, nada o rejeitaria. `pares_invalidos` descarta o par inteiro (invisível, não zero) quando o valor financeiro repetido diverge entre tamanhos — mais um caminho que produz "invisível", não "zero visível".
  implication: nenhuma nova pista; documenta que o schema aceita 0 sem reclamar, então o bug (se existir) está inteiramente na CAMADA DE AGREGAÇÃO/PIPELINE anterior à escrita, não em uma validação faltando no schema.

## Symptoms (nota sobre a hipótese herdada do roadmap)

A hipótese herdada do ROADMAP.md Fase 13 ("fórmula `ceil` falha em pedidos < 20 peças") foi INVESTIGADA E ELIMINADA — ver seção Eliminated. O código atual (`OrcamentoPedido`, Phase 14-06) já usa `floor` inteiro, não `ceil`, e é por PEDIDO INTEIRO, não por produto isolado. A causa raiz real é estrutural no `adequar_grade_produto` (ver Current Focus), independente do tamanho do pedido.

## Evidence

- timestamp: 2026-08-31
  checked: `app/modules/pedidos/domain/orcamento_pedido.py` (ledger `OrcamentoPedido`, docstring + `__post_init__` + `limite_corte`/`limite_adicao`)
  found: comentário explícito "floor inteiro, nunca ceil/float — ALOC-08"; `limite_corte = (total_original * 5) // 100` — divisão inteira (floor), não `math.ceil`. Base do orçamento é o PEDIDO INTEIRO (`total_original`, soma de todos os produtos do pedido), não o produto isolado.
  implication: a hipótese herdada do roadmap ("ceil" causa zero em pedidos pequenos) descreve um comportamento que JÁ FOI REMOVIDO. Para pedidos com total < 20 peças, `floor(total*0.05) == 0` sempre, o que bloqueia QUALQUER corte (orçamento zerado) — o oposto do que o roadmap presumia.

- timestamp: 2026-08-31
  checked: `git log --oneline -- app/modules/pedidos/domain/orcamento_pedido.py`
  found: commits `fb023d1` (RED), `f6cabad` (GREEN), `b90695e` ("feat(14-06): ledger de orcamento do pedido completo ligado ao motor (ALOC-07/08/09/10, FIX-02)") — confirma que o ledger floor-por-pedido-inteiro substituiu deliberadamente uma fórmula anterior.
  implication: a fórmula antiga (ceil, por produto isolado) citada no roadmap é histórica; `app/tests/test_pedidos_motor.py:114` tem o comentário "Substitui o teste antigo de tolerância `ceil` POR PRODUTO ISOLADO (regra que deixou de existir — ALOC-07/08)" confirmando a substituição.

- timestamp: 2026-08-31
  checked: `app/tests/test_pedidos_orcamento_pedido.py::test_orcamento_pedido_limite_e_floor_inteiro_nunca_ceil_nem_float`
  found: teste de regressão explícito cobrindo floor(19*0.05)==0, nunca ceil.
  implication: comportamento floor é intencional e já tem cobertura de teste — reforça que a hipótese do roadmap está desatualizada.

- timestamp: 2026-08-31
  checked: `app/modules/parametros/domain/registro.py` linha 53 (`aplicado_em` de `tolerancia_adequacao`)
  found: comentário de documentação AINDA diz "a tolerância vira `limite_falta` (ceil) e `orcamento_aumento` (floor) sobre o total da grade do pedido+produto" — descreve o modelo ANTIGO (por produto), não o `OrcamentoPedido` atual (por pedido inteiro).
  implication: documentação viva desatualizada (não é bug funcional, é doc drift) — vale nota separada para corrigir, fora do escopo deste fix.

- timestamp: 2026-08-31
  checked: `app/modules/pedidos/domain/furo_de_grade.py::tem_furo_de_grade` (docstring + lógica)
  found: só verifica tamanhos ESTRITAMENTE interiores (idx_min < idx < idx_max); com menos de 2 tamanhos reconhecidos retorna sempre `(False, None)`; com exatamente 2, ambos são extremos e nenhum é auditado.
  implication: um produto com grade de 1 ou 2 tamanhos pedidos, TODOS sem estoque, nunca é pego por furo de grade — segue para `adequar_grade_produto`.

- timestamp: 2026-08-31
  checked: reprodução mínima executável via `processar_pedidos` (script standalone, scratchpad `repro_or_zerada.py`), cenário: pedido nr=1 com 3 produtos (PRODA qty=74 estoque=74, PRODC qty=3 estoque=3, PRODB qty=3 estoque=0 — 1 único tamanho). `total_original` do pedido = 80 → `limite_corte = floor(80*0.05) = 4`, suficiente para cobrir o corte de PRODB (falta=3).
  found: `processar_pedidos` **levanta exceção** `decimal.InvalidOperation: [<class 'decimal.DivisionUndefined'>]` em `rateio.py:27` (`ratear_hamilton`), chamada via `motor_adequacao.py:296` dentro de `_recalcular_financeiro_produto`. Traço: `adequar_grade_produto` reduziu PRODB a `qt_liquida=0` em todos os itens (único tamanho, `est_disp=0`) mas manteve `status_item="Gerar OR"`; `_recalcular_financeiro_produto` então monta `sizes={"M": 0}`, `ratear_hamilton` calcula `total_qty = sum(sizes.values()) == 0` e divide por zero.
  implication: CONFIRMA mecanicamente que `adequar_grade_produto` permite corte total (soma líquida zero) quando todo o estoque do produto é zero e o orçamento cobre — e que isso hoje CRASHEIA (não persiste silenciosamente) via `ratear_hamilton`. O crash ocorre 100% em memória, ANTES de qualquer escrita em `ordens_reserva` (`build_processing_plan` é "nenhum acesso externo ocorre aqui") — logo, se este exato caminho disparasse em produção HOJE, o job de processamento inteiro falharia (capturado por `except Exception` genérico em `app/workers/tasks/pedidos.py:211-223`, job marcado `internal_error`), não um "OR zerada" aparecendo silenciosamente na tela.

- timestamp: 2026-08-31
  checked: `app/modules/pedidos/infrastructure/repositorio_ingestao_readmodel.py::carregar_pedidos_itens` (query SQL) e `app/modules/ingestao/infrastructure/databricks_reader.py::ler_pedidos_em_aberto`
  found: `qt_liquida` do pending item é `p.qt_entregar` direto, sem filtro adicional na leitura local; mas a INGESTÃO grava em `pedidos` só linhas com `WHERE try_cast(qt_entregar AS DOUBLE) > 0` (filtro SQL direto na origem Databricks).
  implication: garante que TODO item que chega ao motor tem `qt_liquida > 0` individualmente — elimina a hipótese de que a grade já chega zerada da origem (dado ingerido nunca tem item com quantidade líquida zero). O caminho `aplicar_tudo_ou_nada` (SEM_ADEQUAR/blacklist) nunca reduz quantidade — é tudo-ou-nada — então, dado este invariante de ingestão, esse caminho NÃO PODE produzir soma líquida zero em condições normais.

- timestamp: 2026-08-31
  checked: banco de dados local (`docker exec system_automation_db psql -U automation -d system_automation`), tabelas `pedidos`, `ordens_reserva`, `pedido_produto_read`, `ordens_reserva_linx`, `pedidos_processados`
  found: todas com `count(*) = 0` — ambiente dev local está vazio (sem ingestão rodada), não é possível cruzar com dado real de produção/staging para confirmar se existem linhas históricas de OR com soma líquida zero (ex. persistidas por uma versão anterior do código, antes do `_recalcular_financeiro_produto`/FIX-02 existir, quando talvez não houvesse a chamada de rateio que hoje crasheia).
  implication: não foi possível confirmar empiricamente contra dado real se o padrão que a usuária viu na tela é (a) resíduo histórico de OR zerada persistida por uma versão de código mais antiga (sem o crash de rateio) que nunca foi limpa da tabela, ou (b) uma falha de job (`internal_error`) que passou despercebida e cuja mensagem "OR zerada" é uma leitura aproximada do usuário sobre pedidos que ficaram pendentes/reprocessando. Ambas as leituras apontam para o MESMO defeito estrutural em `adequar_grade_produto` como causa raiz a corrigir; a diferença é só de manifestação (persistida vs. crash).

## Eliminated

- hypothesis: "A fórmula de tolerância `ceil` no motor de adequação permite, em grades pequenas (<20 peças), soma líquida zero passar como Gerar OR" (hipótese herdada do ROADMAP.md Fase 13).
  evidence: código atual usa `floor` inteiro (`OrcamentoPedido.limite_corte = (total_original * 5) // 100`), não `ceil`; confirmado por comentário explícito, teste de regressão dedicado (`test_orcamento_pedido_limite_e_floor_inteiro_nunca_ceil_nem_float`) e histórico git (commit `b90695e`, Phase 14-06) que substituiu deliberadamente a fórmula ceil-por-produto por floor-por-pedido-inteiro. Para pedidos < 20 peças, `floor(total*0.05) == 0` BLOQUEIA qualquer corte — o oposto do mecanismo que o roadmap presumia.
  timestamp: 2026-08-31

- hypothesis: "A grade já chega com quantidade líquida zero desde a ingestão/origem, e o caminho SEM_ADEQUAR (tudo-ou-nada) deixa passar sem checar."
  evidence: `ler_pedidos_em_aberto` (Databricks reader) filtra `WHERE try_cast(qt_entregar AS DOUBLE) > 0` na origem — todo item que entra no motor tem `qt_liquida > 0` individualmente. `aplicar_tudo_ou_nada` nunca reduz quantidade (é tudo-ou-nada real), logo não pode gerar soma zero a partir de itens que chegam todos > 0.
  timestamp: 2026-08-31

**Nota:** o fix aplicado a `adequar_grade_produto` (guard de estoque zero, ver Resolution) continua VÁLIDO como correção de um bug real — mas não está listado como "eliminado" porque não foi refutado, só desqualificado como explicação do sintoma reportado nesta reabertura (o sintoma real é pré-adequação, esse fix é pós-adequação).

- hypothesis (reabertura): "Canal misto (Franquia+Multimarca no mesmo par nr_pedido+cd_prod_cor) ou canal desconhecido faz `pedido_produto_read.canal` virar NULL e a linha aparecer com `total_qty=0` ou de forma corrompida na aba Pedidos em Aberto."
  evidence: reproduzido que canal misto/desconhecido REALMENTE causa `canal=NULL` na CTE `products` de `_PENDING_READ_INSERT_SQL`, o que viola a constraint `NOT NULL` de `pedido_produto_read.canal` e aborta o INSERT inteiro — MAS esse estado é inalcançável via ingestão real: `agregar_itens_pedidos`/`_canal_obrigatorio`/`_registrar_canal_do_par` (`agregacao.py`) já rejeitam (raise `CanalOrigemInvalidoError`, sem catch local) qualquer linha com canal desconhecido ou par com canais divergentes ANTES de gravar em `pedidos` — o sync inteiro aborta e a tabela local fica com o snapshot ANTERIOR (stale), nunca com canal NULL persistido.
  timestamp: 2026-08-31

- hypothesis (reabertura): "Tamanho inválido (vazio/>16 chars) misturado com tamanho válido no mesmo par faz o total cair para zero."
  evidence: reproduzido empiricamente (SQL direto + API real rodando) que este cenário produz SUBCONTAGEM (total menor que o real, mas não-zero, ex. 6 em vez de 8) quando existe ao menos 1 tamanho válido — e EXCLUSÃO TOTAL (par invisível, não aparece na listagem) quando todos os tamanhos são inválidos. Nenhum dos dois produz uma linha visível com total_qty=0.
  timestamp: 2026-08-31

## Evidence — rodada 3 (alvo real: card "Status dos Pedidos", `/api/v1/pedidos/resumo`)

**IMPORTANTE — não confundir com a hipótese eliminada acima:** a hipótese eliminada era sobre canal misto DENTRO do MESMO par (nr_pedido, cd_prod_cor) — isso `agregacao.py::_registrar_canal_do_par` valida e rejeita (aborta o sync). A causa raiz confirmada nesta rodada é canal misto ENTRE PRODUTOS DIFERENTES do MESMO nr_pedido (cd_prod_cor A em Franquia, cd_prod_cor B em Multimarca, ambos do pedido 123) — `agregacao.py` NUNCA compara canal entre pares diferentes, só dentro do mesmo par. Esse caso passa pela ingestão sem erro nenhum.

- timestamp: 2026-08-31
  checked: `selectedChannel` (`frontend/features/pedidos/model/app-data-provider.tsx:106`, `useState<PedidosCanal>('Todos')`) vs. chaves de `statsByChannel` (backend: `stats["Franquia"]`, `stats["Multimarca"]`, `stats["Todos"]`, `repositorio_resumo.py:565-597`).
  found: `selectedChannel` default é `'Todos'`, que bate exatamente com a chave `stats["Todos"]` que o backend sempre popula. Não há mismatch de chave/prop.
  implication: descarta a hipótese de "acesso a chave errada" cogitada pelo coordenador — o valor 0 vem de verdade da query SQL, não de um fallback de chave ausente.

- timestamp: 2026-08-31
  checked: reprodução empírica ponta a ponta contra a API real (`GET /api/v1/pedidos/resumo`, login via `POST /api/auth/sign-in` com usuário seed `gestor@example.com`), 3 cenários seedados direto em `pedidos`/`ordens_reserva`.
  found: (1) CONTROLE — 1 pedido (nr=7100001), 1 produto, canal único Franquia: `liberadosCount` mostra corretamente 1 em Franquia e Todos. (2) nr_pedido=7100002 com DOIS produtos (PRODB1 em Franquia, PRODB2 em Multimarca, mesmo nr_pedido): **desaparece COMPLETAMENTE de `liberadosCount`** em Franquia (0), Multimarca (0) E Todos (`totalOrdersCount` fica em 1, só contando o controle — o pedido misto nunca é contado em lugar nenhum). (3) OR de edição (nr=7100010, canal único Franquia): `editingOrderCount=1` correto. (4) OR de edição com tamanhos de canal diferente DENTRO do mesmo (nr_pedido, cd_prod_cor) — nr=7100020, tamanho P=Franquia + tamanho M=Multimarca no mesmo item `itens` da OR: **também desaparece completamente** de `editingOrderCount` em Franquia, Multimarca e Todos (fica travado em 1, só o baseline 7100010).
  implication: CONFIRMA mecanicamente e empiricamente (não só leitura de código) a causa raiz: em `repositorio_resumo.py::obter_resumo_sql`, tanto `pending_orders` (linha ~313-329, usada por `pending_counts`/`liberadosCount`) quanto `editing_pairs` (linha ~455-477, usada por `editing_counts`/`editing_all`/`editingOrderCount`) resolvem canal via `CASE WHEN bool_and(...MULTIMARCA...) THEN 'Multimarca' WHEN bool_and(...FRANQUIA...) THEN 'Franquia' ELSE NULL END` — exigindo unanimidade entre TODAS as linhas do grupo (todos os produtos do pedido, no caso de `pending_orders`; todos os tamanhos da grade da OR, no caso de `editing_pairs`). Quando não há unanimidade, canal vira NULL e a linha inteira é DESCARTADA: para `pending_orders`, o descarte acontece no `LEFT JOIN pending_counts pc USING (canal)` final (NULL nunca bate com 'Franquia'/'Multimarca'); para `editing_pairs`, o descarte é AINDA MAIS CEDO — a própria condição `JOIN LATERAL (...) channel_state ON channel_state.canal IS NOT NULL` já exclui a OR da CTE, então nem `editing_counts` (por canal) nem `editing_all` (agregado "Todos", que hoje já é calculado independente do canal por-linha) recebem a linha. **Nenhum dos dois casos gera erro/exceção — a linha só some, silenciosamente, de TODOS os buckets, inclusive "Todos".** Isso explica exatamente o sintoma relatado: se a maioria/totalidade dos pedidos pendentes/em-edição reais da usuária tiver esse padrão (produtos de canais diferentes no mesmo pedido, ou grade com tamanhos de canais diferentes na mesma OR — plausível dado que `agregacao.py` nunca valida consistência de canal NESSE grão), os dois cards mostram 0 mesmo com pedidos reais existindo.
  Dados de teste removidos do banco após o teste (`nr_pedido` entre 7100000-7100099).

## Reasoning Checkpoint

```yaml
reasoning_checkpoint:
  hypothesis: >
    adequar_grade_produto marca status_item="Gerar OR" para um produto cuja
    grade inteira (todos os tamanhos pedidos) tem estoque zero, desde que o
    corte necessário caiba no orçamento de 5% do pedido — porque a decisão
    de "concedido" só olha se o corte cabe no orçamento, nunca se sobra
    alguma peça reservável. tem_furo_de_grade não cobre este caso porque só
    audita tamanhos estritamente interiores (nunca os extremos), e com 1-2
    tamanhos reconhecidos não existe posição interior.
  confirming_evidence:
    - "Reprodução mínima executável via processar_pedidos (produto de
      tamanho único, estoque zero, orçamento do pedido suficiente) resultou
      em item com status_item=\"Gerar OR\" e qt_liquida=0 para TODOS os
      itens do par, confirmado por leitura direta do output antes do crash."
    - "O crash decimal.InvalidOperation em ratear_hamilton (total_qty=0)
      só é alcançável quando TODOS os itens de um par 'Gerar OR' têm
      qt_liquida=0 simultaneamente — provando que o motor realmente
      constrói esse estado internamente antes de falhar."
  falsification_test: >
    Rodar o mesmo cenário (produto de tamanho único ou dois tamanhos, 100%
    sem estoque, orçamento de corte do pedido suficiente) e observar se
    algum item termina com qt_liquida > 0 ou status diferente de
    "Gerar OR". Já executado — refuta H0 (nenhum item ficou > 0); prediction
    confirmada.
  fix_rationale: >
    O guard verifica, ANTES de consumir o orçamento de corte, se existe
    estoque disponível em pelo menos um tamanho pedido do produto. Se não
    houver, desvia para stand-by (SEM_ESTOQUE) sem tocar o ledger — ataca a
    causa raiz (a decisão de "Gerar OR" nunca checava o resultado líquido
    final) em vez de só suprimir o sintoma do crash (ex. não seria
    suficiente só blindar ratear_hamilton contra divisão por zero, porque
    isso ainda deixaria uma OR com soma líquida zero ser persistida —
    exatamente o que a Fase 13 do roadmap pede para nunca acontecer).
  blind_spots: >
    Não foi possível confirmar contra dado real de produção/staging (banco
    dev local está vazio) se o padrão que a usuária viu é este crash
    silenciosamente absorvido de alguma forma não mapeada, ou resíduo
    histórico de uma versão de código anterior ao FIX-02 (rateio). O fix
    elimina a causa raiz estrutural de qualquer forma, mas não explica com
    100% de certeza o mecanismo exato pelo qual a usuária viu "zero" na
    tela sem exceção visível a ela.
```

## Reasoning Checkpoint — Fix #2 (card "Status dos Pedidos")

```yaml
reasoning_checkpoint:
  hypothesis: >
    Em repositorio_resumo.py::obter_resumo_sql, as CTEs pending_orders
    (liberadosCount) e editing_pairs (editingOrderCount) resolvem o canal
    de um grupo (todos os produtos de um nr_pedido, ou todos os tamanhos
    de uma OR) via CASE WHEN bool_and(FRANQUIA) ... WHEN bool_and(MULTIMARCA)
    ... ELSE NULL END — exigindo unanimidade estrita. Quando o grupo não é
    unânime (ex. um pedido com produtos em canais diferentes, algo que
    agregacao.py nunca valida nesse grão), canal vira NULL e a linha inteira
    desaparece de TODOS os buckets (Franquia, Multimarca e Todos), sem
    nenhum erro/exceção.
  confirming_evidence:
    - "Reprodução empírica ponta a ponta (API real + Postgres real): pedido
      controle single-canal conta corretamente (liberadosCount=1); pedido
      com 2 produtos em canais diferentes desaparece de TODOS os buckets
      (Franquia=0, Multimarca=0, Todos não incrementa) sem erro."
    - "Mesma reprodução para editingOrderCount: OR controle single-canal
      conta corretamente (editingOrderCount=1); OR com tamanhos em canais
      diferentes na mesma grade desaparece de TODOS os buckets."
  falsification_test: >
    Se a hipótese estivesse errada, o pedido/OR com canal misto deveria
    aparecer em ALGUM bucket (mesmo que errado) ou gerar um erro visível.
    Testado diretamente: não aparece em nenhum bucket, nenhum erro é
    lançado — confirma a hipótese, não refuta.
  fix_rationale: >
    O card "Status dos Pedidos" é um caminho de EXIBIÇÃO/contagem (não
    aloca estoque nem decide o motor de adequação) — mesma categoria que
    o codebase já trata com fallback tolerante em outro lugar (canal_bucket:
    "um rótulo errado é cosmético, não move estoque"). A causa raiz é a
    query descartar silenciosamente grupos ambíguos em vez de contá-los
    em algum lugar. Fix mínimo: tornar o total "Todos" de liberadosCount
    (e bloqueados_sem_credito) independente da resolução de canal por
    linha — contar TODOS os pending_orders, unânimes ou não — espelhando
    o padrão que editing_all JÁ usa para editingOrderCount hoje (contagem
    agregada independente de canal). Para editing_pairs, trocar a condição
    de JOIN de `ON channel_state.canal IS NOT NULL` para `ON true` faz a
    OR sobreviver na CTE mesmo com canal ambíguo, então editing_all (que já
    conta sem filtrar por canal) volta a enxergá-la. Em ambos os casos, o
    breakdown POR CANAL individual (Franquia vs Multimarca) continua sem
    incluir o grupo ambíguo — decidir a QUAL canal específico atribuí-lo é
    uma decisão de negócio fora do escopo deste bug fix; o que importa
    corrigir agora é que o TOTAL (o que a usuária vê por padrão, canal
    "Todos") nunca mais fique invisível.
  blind_spots: >
    or_pairs/or_counts (orComAdequacaoCount/orSemAdequacaoCount) têm o
    MESMO padrão estrutural (ON channel_state.canal IS NOT NULL) e não
    estão sendo corrigidos agora — não fazem parte dos dois contadores
    reportados pela usuária, mas ficam com a mesma lacuna latente. Também
    não resolvo aqui QUAL canal um pedido/OR ambíguo deveria contar no
    breakdown por canal — isso fica sub-contado por canal individual
    (mas não mais invisível no total).
```

## Resolution

**STATUS DESTA SEÇÃO:** descreve FIX #1 (`adequar_grade_produto`), aplicado e testado, que corrige um bug real mas DISTINTO do sintoma relatado pela usuária — permanece aplicado no working tree. O sintoma REAL relatado (card "Status dos Pedidos" com contadores zerados) foi rastreado e corrigido pelo **FIX #2**, documentado logo abaixo desta nota, em `repositorio_resumo.py`.

root_cause: |
  Em `adequar_grade_produto` (app/modules/pedidos/domain/motor_adequacao.py),
  quando TODOS os tamanhos pedidos de um produto têm estoque zero
  (est_disp == 0 em toda chave), a função só verifica se o corte necessário
  cabe no orçamento de 5% do PEDIDO INTEIRO (ledger.consumir_corte) — nunca
  se o resultado final deixa alguma peça. Se couber no orçamento, TODOS os
  itens são marcados status_item="Gerar OR" com qt_liquida=0. `tem_furo_de_grade`
  não pega este caso porque só audita tamanhos ESTRITAMENTE interiores à
  grade pedida (nunca os extremos), e com 1-2 tamanhos reconhecidos não há
  posição interior nenhuma — logo produtos com grade de 1-2 tamanhos
  totalmente sem estoque escapam do furo de grade E do guard de soma zero.
  Hoje isso se manifesta como crash (decimal.InvalidOperation /
  DivisionUndefined) em ratear_hamilton dentro de
  _recalcular_financeiro_produto, porque o total a ratear é dividido pela
  soma das quantidades finais (0). Reproduzido mecanicamente via
  processar_pedidos com um cenário mínimo (produto de tamanho único, sem
  estoque, dentro do orçamento de corte do pedido) e confirmado RED->GREEN
  com testes automatizados.
  Nota: a hipótese herdada do ROADMAP.md Fase 13 ("fórmula ceil falha em
  pedidos < 20 peças") foi investigada e ELIMINADA — o código atual já usa
  floor por pedido inteiro (Phase 14-06), o que teria bloqueado exatamente
  esse mecanismo. A causa raiz real é estrutural em adequar_grade_produto,
  independente do tamanho do pedido.
fix: |
  Guard em adequar_grade_produto (app/modules/pedidos/domain/motor_adequacao.py):
  antes de consumir o orçamento de corte, se NENHUM tamanho pedido do
  produto tem estoque disponível (est_disp > 0 para pelo menos um item),
  desvia o produto inteiro para stand-by (motivo canônico SEM_ESTOQUE, via
  marcar_stand_by com mensagem descritiva _MOTIVO_ESTOQUE_ZERADO) sem
  tocar o ledger. Elimina o crash de ratear_hamilton e a possibilidade
  estrutural de "Gerar OR" com soma líquida zero nesta função — sem alterar
  o comportamento de nenhum cenário onde existe estoque em pelo menos um
  tamanho (confirmado por teste de contraste).
verification: |
  1. Reprodução mínima re-executada após o fix: o mesmo cenário que antes
     crashava com decimal.InvalidOperation agora retorna normalmente — o
     par vai para preteridos com preteridos_motivo="sem_estoque",
     resultados[par] tem status_item="Pedido em Stand By", soma líquida
     dos itens "Gerar OR" = 0 (nenhum item marcado "Gerar OR").
  2. Confirmado RED->GREEN: com o fix stashado (git stash) os 3 novos
     testes que cobrem o cenário falham exatamente com o mesmo
     decimal.InvalidOperation da reprodução original; com o fix
     restaurado, os 4 novos testes passam.
  3. Suíte completa do domínio pedidos (motor, furo de grade, blacklist,
     política de quantidade, invariantes I1-I9, orçamento do pedido,
     factories, cenário disputa mantenedora): 290 passed, 0 failed
     (`pytest app/tests/ -k pedidos`, excluindo 1 módulo com
     incompatibilidade de import pré-existente no Windows — `resource` —
     não relacionada a esta mudança).
  4. Suíte completa do backend: 893 passed, 18 skipped, 0 failed
     (`pytest app/tests/`, mesma exclusão pré-existente).
  5. Verificação self-verified feita; falta confirmação humana em
     ambiente real (staging/produção) — banco dev local está vazio, não
     foi possível reproduzir contra dado real.
files_changed:
  - app/modules/pedidos/domain/motor_adequacao.py (guard em adequar_grade_produto: desvia para stand-by, sem tocar o ledger de corte, quando nenhum tamanho pedido tem estoque disponível)
  - app/tests/test_pedidos_motor.py (4 testes de regressão: guard unitário com 1 tamanho, guard unitário com 2 tamanhos extremos, contraste "não dispara com estoque parcial", ponta a ponta via processar_pedidos)

## Evidence — rodada 4 (`develop` atualizado, arquivo fatiado, FIX #2 reaplicado)

- timestamp: 2026-08-31
  checked: `git log -1 HEAD`, `git status`, `app/modules/pedidos/infrastructure/resumo/` (novo diretório pós-merge).
  found: `develop` local foi fast-forward de 42 commits atrás para `origin/develop` (HEAD agora `e7209dc`). `repositorio_resumo.py` não existe mais — foi fatiado nos commits `e5e4c92`/`960be68`/`c176c40` em `app/modules/pedidos/infrastructure/resumo/{obter_resumo.py,stats_canais.py,stats_erp.py,listar_alertas.py,listar_chaves_alertas_ativos.py}`. FIX #1 (`motor_adequacao.py` + `test_pedidos_motor.py`) sobreviveu ao merge sem conflito, intacto. O diff antigo de FIX #2 (pré-merge) foi perdido nesse sentido — preservado só em `git stash@{0}` como histórico, não reaplicável diretamente (estrutura mudou).
  implication: causa raiz e lógica do fix continuam válidas — só precisa ser reaplicado no novo local (`stats_canais.py`/`obter_resumo.py`).

- timestamp: 2026-08-31
  checked: reprodução empírica direta via SQL (`docker exec system_automation_db psql`) contra o EXATO texto da query em `stats_canais.py::_channel_stats` (não mais via API HTTP — auth mudou para SSO Microsoft-only pós-merge, `/api/auth/sign-in` com email/senha não existe mais no `openapi.json`), reusando os dados semeados na rodada 3 (nr_pedido 7100001/7100002/7100010/7100020, ainda presentes no banco).
  found: bug confirmado presente no código pós-merge, idêntico ao da rodada 3: `pending_orders` para nr=7100002 (2 produtos, canais diferentes) mostra `canal=NULL`; `pending_counts` agrupado por canal cria um grupo NULL com 1 pedido que nunca bate com `channels(canal) IN ('Franquia','Multimarca')` no SELECT final — CONFIRMADO ausente de Franquia, Multimarca E "Todos" (que na rodada anterior era `Franquia+Multimarca`, então também exclui). `editing_pairs` para nr=7100020 (grade com tamanhos em canais diferentes na mesma OR) está COMPLETAMENTE ausente da CTE (`channel_state ON channel_state.canal IS NOT NULL` a exclui na origem) — `editing_orders_all` fica em 1 (só 7100010), deveria ser 2.
  implication: bug 100% reproduzido no código atual antes de qualquer edição — não é um artefato do merge, é o mesmo defeito estrutural.

- timestamp: 2026-08-31
  checked: **achado crítico** — `app/tests/test_pedidos_read_projection.py::test_resumo_exclui_pedidos_e_ors_com_canal_misto_ou_desconhecido` (teste PRÉ-EXISTENTE, já no repo antes desta sessão) tinha `for channel in ("Franquia","Multimarca","Todos"): assert stats[channel]["liberados_count"] == 0` para um cenário QUASE IDÊNTICO ao bug (nr_pedido único com 2 produtos, um Franquia um Multimarca).
  found: este teste PASSAVA antes do fix e CODIFICAVA O BUG COMO COMPORTAMENTO ESPERADO/INTENCIONAL — "Todos" ficando em 0 para um pedido pendente real ambíguo era uma asserção deliberada, não um descuido óbvio. Rodei o teste após aplicar o fix: falhou exatamente em `stats["Todos"]["liberados_count"]` (esperava 0, fix produz 1) — as asserções de Franquia/Multimarca (0, corretas, inalteradas) e de or_com/or_sem/editing_order_count/erp_count (0, não tocados pelo fix) continuaram batendo.
  implication: este NÃO é um efeito colateral inesperado — é a mudança de comportamento CENTRAL do fix, exatamente o que a usuária pediu (pedido pendente real deixa de desaparecer do total). Documentando explicitamente porque é uma asserção pré-existente sendo intencionalmente revertida, não um teste quebrado por acidente. Renomeei o teste para `test_resumo_breakdown_por_canal_exclui_mas_todos_conta_canal_misto` e troquei a asserção de `liberados_count` de "0 em todo canal" para "0 em Franquia/Multimarca, 1 em Todos" — mantendo todas as outras asserções (or_com/or_sem/editing/erp, não tocadas pelo fix) exatamente como estavam.

## Resolution — FIX #2 (aplicado no arquivo pós-merge)

root_cause: |
  Em app/modules/pedidos/infrastructure/resumo/stats_canais.py::_channel_stats,
  duas CTEs resolvem canal por unanimidade estrita e descartam silenciosamente
  o grupo inteiro quando não há unanimidade:
  1. `pending_orders` (linha ~32): agrupa pending_items por NR_PEDIDO (não por
     par nr_pedido+cd_prod_cor) — um pedido com PRODUTOS DIFERENTES em canais
     diferentes nunca é unânime em bool_and, canal vira NULL. `pending_counts`
     agrupa por canal, e o SELECT final junta com
     `(VALUES ('Franquia'),('Multimarca'))` via LEFT JOIN...USING(canal) — o
     grupo NULL nunca bate com nenhum dos dois, e como obter_resumo.py
     calculava "Todos" = Franquia+Multimarca (soma), o pedido ambíguo
     desaparecia de TODOS os buckets, inclusive do total.
  2. `editing_pairs` (linha ~174): resolve canal por bool_and sobre os
     TAMANHOS de UMA OR (nr_pedido+cd_prod_cor). Quando a própria grade da OR
     mistura tamanhos de canais diferentes, canal vira NULL — mas aqui o
     descarte acontecia AINDA MAIS CEDO, na própria condição de JOIN
     `channel_state ON channel_state.canal IS NOT NULL`, que excluía a OR
     inteira da CTE. Como editing_all ("Todos") é contado A PARTIR de
     editing_pairs, a OR também sumia do total.
  Nenhum dos dois caminhos gera erro/exceção — a linha só desaparece,
  silenciosamente, de todos os buckets. `agregacao.py` (camada de ingestão)
  nunca valida consistência de canal NESSES grãos (entre produtos diferentes
  do mesmo pedido; entre tamanhos diferentes da mesma OR) — só valida DENTRO
  de um único par (nr_pedido, cd_prod_cor). Confirmado empiricamente (SQL
  direto + chamada real de obter_resumo_sql) contra dados semeados no
  Postgres local, reproduzindo o padrão relatado pela usuária: card "Status
  dos Pedidos" mostrando 0 em "Pedidos Aguardando Faturamento" e "Pedidos
  Liberados para Edição" mesmo com pedidos reais existindo.
fix: |
  Em stats_canais.py: (1) nova CTE `pending_all`, canal-agnóstica, contando
  liberados/blocked_credit de TODOS os pending_orders sem GROUP BY canal
  (mesmo padrão que editing_all já usava corretamente); exposta no SELECT
  final via CROSS JOIN. (2) editing_pairs: `channel_state ON true` no lugar
  de `ON channel_state.canal IS NOT NULL` — a OR sobrevive na CTE mesmo com
  canal ambíguo (o HAVING count(*) > 0 dentro do lateral já garante que só
  entram ORs com item elegível). Em obter_resumo.py: "Todos" de
  liberados_count/bloqueados_sem_credito_count passa a usar pending_all em
  vez de Franquia+Multimarca somados; total_orders_count e os percentuais
  dependentes são recalculados em cima disso para ficarem consistentes. O
  breakdown POR CANAL individual (Franquia vs Multimarca) continua sem
  incluir o grupo ambíguo — não há como saber a qual canal específico
  atribuí-lo; só o TOTAL ("Todos", o que a usuária vê por padrão) precisava
  parar de ficar invisivelmente menor que a realidade.
verification: |
  1. Reprodução empírica em 3 camadas: SQL isolado via psql (pending_all
     conta 2 pedidos em vez de 1; editing_pairs com ON true traz a OR de
     canal misto de volta, editing_orders_all conta 2 em vez de 1);
     chamada real de ponta a ponta de obter_resumo_sql via script Python
     usando a mesma sessão async da aplicação (Franquia liberados=1,
     Multimarca liberados=0, Todos liberados=2 — confirma que o breakdown
     por canal fica correto e só o total muda).
  2. RED->GREEN confirmado com git stash: com o fix stashado, os 2 testes
     de regressão (1 renomeado/atualizado + 1 novo) falham exatamente como
     esperado (`assert 0 == 1` / `assert 1 == 0`); com o fix restaurado,
     os 2 passam.
  3. Teste pré-existente `test_resumo_exclui_pedidos_e_ors_com_canal_misto_ou_desconhecido`
     RENOMEADO para `test_resumo_breakdown_por_canal_exclui_mas_todos_conta_canal_misto`
     — asserção de liberados_count atualizada (0/0/0 -> 0/0/1 por
     Franquia/Multimarca/Todos), demais asserções (or_com/or_sem/
     editing_order_count/erp_count) mantidas inalteradas e continuam
     passando. Este teste CODIFICAVA o bug como comportamento esperado
     antes desta sessão — mudança de asserção é intencional, documentada
     acima em Evidence, não um teste quebrado por acidente.
  4. Novo teste `test_resumo_todos_conta_or_em_edicao_com_grade_de_canal_misto`
     cobre especificamente o caminho editing_pairs/ON true (grão diferente
     do teste anterior: uma OR cujos próprios tamanhos, não produtos
     diferentes, têm canais diferentes).
  5. `app/tests/test_pedidos_read_projection.py` completo: 17 passed, 0
     failed. `pytest app/tests/ -k pedidos`: 291 passed, 0 failed (era 290
     antes desta rodada). Suíte COMPLETA do backend (`pytest app/tests/`,
     excluindo o módulo com incompatibilidade pré-existente de `resource`
     no Windows): **885 passed, 18 skipped, 0 failed** — mesmo baseline
     relatado pelo coordenador logo após o merge (nada quebrou pelas duas
     mudanças desta sessão).
  6. Dados de teste (nr_pedido 7100000-7100099) removidos do banco local
     após a verificação — banco voltou a 0 linhas em pedidos/ordens_reserva.
  7. Self-verified feito; falta confirmação humana antes de qualquer
     commit, por acordo explícito com o coordenador nesta sessão.
files_changed:
  - app/modules/pedidos/infrastructure/resumo/stats_canais.py (CTE pending_all agnóstica a canal + editing_pairs com `channel_state ON true`)
  - app/modules/pedidos/infrastructure/resumo/obter_resumo.py ("Todos" de liberados/bloqueados_sem_credito usa pending_all; total_orders_count e percentuais recalculados)
  - app/tests/test_pedidos_read_projection.py (teste pré-existente renomeado e asserção corrigida + 1 novo teste para o caminho editing_pairs)

## Rodada 5 — revisão de código do coordenador (2 commits locais `9199cd1`/`5f74d1e`, pré-push)

Dois commits locais já feitos (sem prefixo Conventional Commits, a pedido da usuária): `9199cd1` (FIX #1, motor_adequacao) e `5f74d1e` (FIX #2, resumo counters). Revisão de código encontrou 2 problemas antes de liberar o push. Ambos fechados nesta rodada, ainda sem novo commit (aguardando checkpoint).

### Achado 1 [PRIORITÁRIO] — terceira CTE esquecida: `or_pairs`

- timestamp: 2026-08-31
  checked: `stats_canais.py::or_pairs` (~linha 149) — mesmo padrão `channel_state.canal IS NOT NULL` que causava o bug em `pending_orders`/`editing_pairs`, mas nunca corrigido nas rodadas 3/4. Alimenta `or_orders` → `or_counts` → `or_com_adequacao_count`/`or_sem_adequacao_count`, que compõem `total_orders_count` no `obter_resumo.py`.
  found: reprodução empírica confirmou — uma OR JÁ GERADA (aprovada ou não, fora da janela de pendente/edição) cuja grade mistura canais desaparecia de TODOS os buckets, inclusive "Todos", exatamente como os outros dois casos. Chamada real de `obter_resumo_sql` contra dados semeados (`nr_pedido` 7100030 controle Franquia, 7100031 grade mista P=Franquia+M=Multimarca): antes do fix, `Todos.or_com=1` (só o controle); depois, `Todos.or_com=2` (ambos).
  fix: mesmo tratamento das rodadas 3/4 — `channel_state ON true` em `or_pairs`; nova CTE `or_all` (canal-agnóstica, espelhando `editing_all`) exposta via CROSS JOIN; `_channel_stats` retorna `or_all` como 6º elemento da tupla; `obter_resumo.py` usa `or_all["or_com"]`/`or_all["or_sem"]` para o bucket "Todos" em vez da soma Franquia+Multimarca, e `total_orders_count` recalculado em cima dos 4 valores já corrigidos (pending_all + or_all).
  verification: RED->GREEN confirmado com `git stash` (script Python direto contra `obter_resumo_sql`: 1 sem fix, 2 com fix). Novo teste de regressão `test_resumo_todos_conta_or_ja_gerada_com_grade_de_canal_misto` (grão `or_pairs`, OR fora da janela de 24h, não pendente nem em edição) — RED->GREEN confirmado via `git stash` também.
  **efeito colateral descoberto ao rodar a suíte:** o teste pré-existente `test_resumo_breakdown_por_canal_exclui_mas_todos_conta_canal_misto` (já renomeado na rodada 4) também tinha `SUMMARY_OR_MIXED` (tipo='com', grade mista) e `SUMMARY_OR_UNKNOWN` (tipo='sem', canal desconhecido) fixtures que dependiam do mesmo bug em `or_pairs` — `or_com_adequacao_count`/`or_sem_adequacao_count` de "Todos" também estavam pinados em 0. Atualizado para refletir o comportamento correto: Franquia/Multimarca permanecem 0 (não é possível atribuir), "Todos" passa a contar 1 em cada (SUMMARY_OR_MIXED conta como or_com, SUMMARY_OR_UNKNOWN conta como or_sem — canal desconhecido "Outro" recebe o MESMO tratamento de canal ambíguo que canal misto, já que ambos resolvem para NULL na CASE).

### Achado 2 [SECUNDÁRIO] — brecha teórica no guard de `adequar_grade_produto`

- timestamp: 2026-08-31
  checked: `motor_adequacao.py::adequar_grade_produto`, guard da Fase 13 (rodada 1). O guard antigo checava `any(estoque.get(chave,0) > 0 for item in itens)` — não garante que a SOMA FINAL fique positiva: um item com demanda já zero (qt_liquida=0) mas estoque disponível, misturado com outro item sem estoque, passaria o guard (existe estoque em ALGUM tamanho) mas ainda somaria zero após o corte, recriando o crash original.
  found: a investigação já tinha estabelecido (Eliminated, rodada 1) que a invariante de ingestão (filtro `qt_entregar > 0` na origem + `agregacao.py` descartando `qt <= 0`) impede um item de chegar com `qt_liquida == 0` na prática — mas o guard NÃO deveria depender silenciosamente dessa invariante externa para ficar seguro (o coordenador pediu para fechar a brecha de design, não só documentar por que é inatingível hoje).
  fix: substituído `any(estoque > 0)` por checagem da SOMA PROJETADA: `sum(min(item["qt_liquida"], estoque.get(chave,0)) for item in itens) <= 0` — o mesmo valor que o loop de corte abaixo grava em `qt_liquida` (unânime nos dois ramos de `nova_qtd`: quando `falta_total == 0`, todo item já tem `estoque >= qt_liquida`, então `min()` não altera nada — coincide exatamente com `else qtd_antiga`). Mais robusto E mais simples de raciocinar (verifica diretamente o invariante que importa, não um proxy).
  verification: RED->GREEN confirmado via `git stash` com novo teste `test_adequar_grade_produto_estoque_em_tamanho_de_demanda_zero_ainda_aciona_guarda` (item `P` com qt_liquida=0 e estoque=5, item `M` com qt_liquida=3 e estoque=0 — cenário que o guard antigo deixava passar como "Gerar OR" zerado; o novo guard desvia para stand-by). Suíte completa de `test_pedidos_motor.py` (29 -> 30 testes) continua verde.

## Verificação humana (2026-09-01)

Reprodução empírica ponta a ponta via `repo.obter_resumo()` (código real, não SQL isolado) confirmada contra o `develop` mergeado e pushado: pedido controle (canal único) conta normalmente; pedido com 2 produtos em canais diferentes deixa de sumir do total "Todos" (era `0/0/1` pré-fix, passou a `1/0/2` pós-fix — Franquia/Multimarca/Todos). Detalhe por canal individual continua sem contar o pedido ambíguo, por design (não há como atribuir a um canal só).

Mantenedora confirmou que pedidos com produtos em canais diferentes (Franquia + Multimarca no mesmo nr_pedido) são **frequentes no volume mensal real** — não é um caso de borda raro. Isso confirma que o bug tinha impacto operacional real e recorrente: o card "Status dos Pedidos" subestimava sistematicamente quantos pedidos aguardavam faturamento/edição, não um evento isolado. Sessão de debug encerrada como `verified`.

### Verificação final desta rodada

- `pytest app/tests/test_pedidos_motor.py app/tests/test_pedidos_read_projection.py`: 48 passed, 0 failed.
- `pytest app/tests/ -k pedidos`: 293 passed, 0 failed (era 291 antes desta rodada).
- Suíte completa do backend (`pytest app/tests/`, mesma exclusão pré-existente do módulo `resource`): **887 passed, 18 skipped, 0 failed** — exatamente +2 em relação ao baseline de 885 desta sessão (os 2 novos testes desta rodada: `test_resumo_todos_conta_or_ja_gerada_com_grade_de_canal_misto` e `test_adequar_grade_produto_estoque_em_tamanho_de_demanda_zero_ainda_aciona_guarda`). Nenhuma regressão.
- Banco local confirmado limpo (0 linhas em `pedidos`/`ordens_reserva`) após remoção dos dados de teste desta rodada.
- Nenhum commit novo criado ainda — os 2 commits locais (`9199cd1`, `5f74d1e`) continuam como estavam; se o coordenador decidir fazer amend/recriar o commit 2 para incluir o fix de `or_pairs`, isso é decisão dele, não desta sessão.
