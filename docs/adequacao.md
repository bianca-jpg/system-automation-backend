# Motor de adequação

O motor decide, comparando cada pedido com o estoque disponível, o que fazer:
gerar uma **Ordem de Reserva (OR)** — total ou parcial — ou deixar o pedido em
**stand-by**.

As regras puras ficam em `app/modules/pedidos/domain/`. O processamento global
durável é um subcontexto DDD em `app/modules/pedidos/processing/`, com ports na
aplicação e adapters SQL/Celery na infraestrutura.

## Ações da tela

| Ação | Endpoint | Papel | O que faz |
|------|----------|-------|-----------|
| Criar processamento | `POST /api/v1/pedidos/processamentos` | `operacional` | Agenda `adequar` ou `sem_adequar` para Todos/Franquia/Multimarca |
| Consultar processamento | `GET /api/v1/pedidos/processamentos/{jobId}` | `operacional` | Lê progresso, tentativas e resultado bounded |
| Alterar grades do produto | `PUT /api/v1/pedidos/produtos/grades?productCode=...&channel=...` | `operacional` | Salva 1–100 clientes com versão otimista e commit único |
| Aprovar produto | `POST /api/v1/pedidos/produtos/aprovar?productCode=...&channel=...` | `operacional` | Aprova o produto/canal em lote e de forma idempotente |

Os antigos `POST /adequar`, `POST /sem_adequar` e
`PUT /api/v1/pedidos/{nr}/alterar-grade` foram removidos em 2026-08-26 (antes
disso respondiam `410 Gone` desde a migração para o processamento durável).
Assim não existe atalho síncrono capaz de carregar a base inteira ou perder
uma edição concorrente. `POST /{nr}/aprovar` permanece como contrato de
compatibilidade por pedido.

Processamento, edição, aprovação e a troca do snapshot disputam o mesmo advisory
lock de mutação. Edição/aprovação retornam `409` quando o lock está ocupado. O
worker de processamento usa try-lock antes de fazer claim: contenção mantém o
job `queued` e não consome suas três tentativas; Celery e o reconciliador tentam
novamente até o lock liberar ou o deadline de 15 minutos encerrar o job.

O kind `orders.processing.v1` é roteado exclusivamente para
`orders_processing`. O worker geral consome somente `celery`; o worker dedicado
usa concurrency 1 e prefetch 1, evitando dois planos pesados no mesmo container.
Os composes reservam 1280 MiB e limitam o processo a 1536 MiB, margem baseada no
pico real de 434 MiB para 132.405 itens e em cerca de 600 MiB projetados no teto
de 200 mil itens. O child é
reciclado após 20 tasks ou ao terminar uma task acima de 1 GiB de RSS; esse
limiar não interrompe uma execução corrente.

Os parâmetros do motor (`criterio_selecao`, `tolerancia_adequacao`) vêm do
módulo de **parâmetros** (com fallback para os defaults de `settings`).

## Orquestração durável

1. O POST grava `durable_jobs` e `pedido_processamentos` na mesma transação. A
   publicação no broker só acontece após o commit e tem deadline curto. Falha
   de publish continua retornando `202`; o beat reconcilia depois.
   Antes de criar uma intenção, a API consulta o binding idempotente e aplica
   rate limit HMAC de 3/min por usuário e 10/min por IP. Replay bypassa Redis;
   uma intenção nova falha fechado sem row quando Redis está indisponível.
2. O worker obtém o lock advisory de sessão e só então reivindica o job. Um
   heartbeat independente renova o lease de 60 s a cada 20 s, inclusive durante
   o trecho CPU-bound do motor.
3. O preflight operacional limita a foto a 100.000 pares, 200.000 itens, 100
   itens por par e 96 MiB. `sem_adequar` passa a hidratar a mesma foto global
   que `adequar` já usa: `load_pending_items` traz a foto elegível inteira do
   canal para Python numa única leitura, e `build_processing_plan` monta o
   plano chamando o motor de adequação (`processar_pedidos`) com o modo
   correspondente para os dois casos, dentro do worker isolado — não existe
   mais um caminho de paginação exclusivo para `sem_adequar`. Ambos produzem
   plano fingerprintado em `pedido_processamento_plan`.
4. A aplicação ocorre em checkpoints transacionais de até 250 pares. Estoque é
   lido/recalculado em sublotes de até 200 alvos; antes de cada write, fonte e
   capacidade são revalidadas contra a foto atual.

5. Após o último checkpoint, resultado, evento realtime determinístico e status
   `succeeded` entram no mesmo commit. Crash/hard kill é recuperado pelo sweeper
   de lease do ledger.

Em ECS, essa fila requer um serviço/task dedicado já provisionado com a mesma
memória mínima e command de fila. O pipeline deste repositório atualiza apenas a
API e não tenta adivinhar identificadores externos de workers.

## Regras puras — `processar_pedidos`

1. **Agrupa por produto** (`cd_prod_cor`): `{produto: {pedido: itens}}`,
   ignorando os pares `(nr_pedido, cd_prod_cor)` já processados.
2. **Crédito**: pedidos com `status_credito` "sem crédito" vão para stand-by —
   não consomem estoque e reaparecem na listagem (`is_sem_credito`).
3. **Estoque por produto e canal** = soma das variações de tamanho daquele
   `cd_prod_cor` apenas no canal em processamento.
4. **Prioridade global (faturamento parcial)**: os pedidos com crédito são
   ordenados por `(valor faturável disponível, nº de produtos com estoque)`
   decrescente. Quem consegue faturar mais tem preferência no estoque
   compartilhado.
5. Para cada produto, consome o estoque **nessa ordem de prioridade**, com
   despacho por modo: `sem_adequar` chama `aplicar_tudo_ou_nada` (tudo-ou-nada
   por par, sem ledger de orçamento); `adequar` chama `adequar_grade_produto`
   (passada 1 — decide o corte, usando o ledger do pedido) e, só para os
   produtos que saíram da passada 1 com folga total, `conceder_adicao_pedido`
   (passada 2 — extras). Nos dois modos, o **estoque local é decrementado** a
   cada OR gerada (o estoque é compartilhado entre pedidos).

## Alocação por pedido — ledger e duas passadas

O motor itera por **PEDIDO** (não mais por produto isolado), em ordem de
prioridade global, instanciando um `OrcamentoPedido` **uma única vez por
pedido**. O ledger tem **dois orçamentos independentes** — adição e corte —,
cada um `floor(total_original_do_pedido × tolerancia_adequacao)` em aritmética
inteira (tolerância configurável pelo parâmetro de negócio
`tolerancia_adequacao`, default 5%), que **não se compensam** entre si: esgotar o orçamento de corte não libera espaço
no de adição, e vice-versa. `total_original` é a soma de **todos** os
produtos do pedido (não só os pendentes desta execução), e o consumido em
execuções anteriores é somado antes de calcular o restante, para o orçamento
nunca resetar entre execuções sucessivas (ALOC-09).

A decisão de quantidade acontece em **duas passadas**:

1. **Passada 1** (`adequar_grade_produto`, produtos ordenados por corte
   necessário crescente): decide o corte via `ledger.consumir_corte` —
   tudo-ou-nada. Ou o produto inteiro vira **"Gerar OR"**, com cada tamanho em
   `min(qt_pedida, estoque)`, ou vira **"Pedido em Stand By"** sem gastar
   orçamento nenhum.
2. **Passada 2** (`conceder_adicao_pedido`, produtos ordenados por preço
   unitário médio decrescente, só entre os que saíram da passada 1 com folga
   total): concede peças extras aos tamanhos de fronteira via
   `ledger.consumir_adicao` — parcial, nunca mais do que o estoque disponível
   permite.

Depois das duas passadas, `vl_liquido` de cada produto é recalculado de uma
vez via rateio de **Hamilton** (`ratear_hamilton`), eliminando o drift de
`round()` item a item (FIX-02).

`sem_adequar` não usa o ledger: cada par é tudo-ou-nada via
`aplicar_tudo_ou_nada`, sem orçamento de adição/corte envolvido.

### Ordenação de tamanhos

Os "extremos" (menor e maior), usados pela passada 2 (`conceder_adicao_pedido`)
para conceder peças extras, saem de `get_tamanho_idx` / `RANKINGS`, que sabe
ordenar diferentes grades: alfabética (`XPP…5G`), numérica de calça/terno,
calçado, cinto, camisa numérica e `UN` (único). Tamanhos puramente numéricos
ordenam pelo próprio número.

## Resultado

Internamente, `processar_pedidos` retorna:

- `resultados` — itens por par `(nr_pedido, cd_prod_cor)`;
- `selecionados` — pares que geraram OR (faturamento total ou parcial);
- `preteridos` — pares sem estoque, em stand-by;
- `bloqueados_credito` — números de pedido aguardando liberação;
- `pares_processados` — somente os pares que efetivamente geraram OR e saem da
  fila; stand-by reaparece em uma tentativa futura.

As ORs são persistidas em `ordens_reserva` (tipo `com`); o modo `sem_adequar`
persiste com tipo `sem`.

O GET do job devolve apenas `plannedCount`, `appliedCount`, `deferredCount` e
`blockedCreditCount`, nunca grades, clientes, lease ou exceção. Desde o
roteamento global da Fase 15 (ARCH-01), `deferredCount`/`blockedCreditCount`
refletem `preteridos`/`bloqueados_credito` reais nos **dois modos** —
antes do roteamento, `sem_adequar` sempre devolvia os dois zerados, porque não
passava pelo motor. As telas fazem
polling pelo `statusUrl` e, ao receber estado terminal/realtime, recarregam as
consultas REST paginadas. Não existe retry HTTP manual: idempotência, retry
bounded e reconciliação pertencem ao ledger/worker.

## Edição de grade por produto

A tela primeiro consulta
`GET /api/v1/pedidos/produtos/clientes?productCode=...&channel=...`. Cada cliente
vem com uma `version` opaca calculada sobre o estado autoritativo. O `PUT`
devolve sucesso somente depois de validar o lote inteiro:

1. produto, canal e pedidos precisam coincidir exatamente;
2. cada pedido aparece uma única vez e mantém sua quantidade total;
3. a versão enviada precisa continuar atual;
4. cada grade contém 1–100 tamanhos válidos e quantidades não negativas;
5. a nova reserva por tamanho não pode exceder o estoque virtual disponível
   somado à reserva anterior daquele próprio lote;
6. valores financeiros são recalculados no servidor, em centavos;
7. modificações, integração Linx e evento realtime usam a mesma transação.

Se qualquer cliente conflitar, o backend retorna `409` e não persiste nenhum
item. O frontend deve recarregar os clientes, mostrar a versão autoritativa e só
reenviar após decisão explícita do usuário.
