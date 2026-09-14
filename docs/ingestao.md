# Ingestão de dados (Databricks → PostgreSQL)

A ingestão mantém snapshots locais para que nenhuma consulta de tela dependa do
Databricks. O fluxo lê cinco fontes, normaliza os dados e publica tudo em uma
única transação PostgreSQL:

| Fonte | Destino | Uso |
|-------|---------|-----|
| Pedidos em aberto | `pedidos` | fila aguardando/edição |
| Pedidos processados | `pedidos_processados_erp` | histórico e flags ERP |
| Estoque diário | `estoque` | disponibilidade por tamanho/canal |
| Faturamento | `faturamento_colecao` | 3 coleções mais recentes |
| Referência de tamanhos | `produto_tamanho_posicao` | ordem da grade Linx |

Depois de gravar as fontes, o mesmo commit reconstrói
`pedido_produto_read` e compara pedidos, alertas e histórico com os ledgers
duráveis. Quando encontra novidades, anexa no máximo uma invalidação agregada
por tópico ao outbox (`orders.changed.v1`, `alerts.changed.v1` e
`history.changed`) com as respectivas contagens. Uma falha em qualquer etapa
desfaz todas as escritas.

Código principal:

- leitura: `app/modules/ingestao/infrastructure/databricks_reader.py`;
- normalização: `app/modules/ingestao/domain/agregacao.py`;
- orquestração: `app/modules/ingestao/application/casos_uso.py`;
- persistência: `app/modules/ingestao/infrastructure/repositorio_snapshot.py`.

## Consultas na origem

A view de pedidos em aberto é autoritativa sobre ciclo/status. O backend não
aplica recorte por mês; traz todas as linhas retornadas pela view e mantém apenas
`try_cast(qt_entregar AS DOUBLE) > 0`. Isso evita excluir pedidos válidos antigos
por uma regra temporal duplicada na aplicação.

A leitura ERP filtra o ano de `dt_emissao` por
`INGESTAO_ANO_HISTORICO` (padrão 2026) e exige
`indica_reserva OR indica_embalado`. O faturamento é agregado no próprio
Databricks para as 3 coleções mais recentes e chega ao backend em payload curto.

A view de estoque já deve entregar a foto de `current_date()`, uma linha por
canal/produto/tamanho. O backend preserva `dt_estoque` e alerta quando a origem
envia mais de uma data ou uma foto antiga.

## Resiliência do Databricks

Cada request HTTP tem timeout próprio
(`DATABRICKS_REQUEST_TIMEOUT_SECONDS`, padrão 30 s), mas todas as tentativas,
polls e chunks de uma consulta compartilham um deadline total
(`DATABRICKS_TOTAL_TIMEOUT_SECONDS`, padrão 900 s). O snapshot completo das
cinco fontes também tem orçamento fim a fim independente
(`INGESTAO_TOTAL_TIMEOUT_SECONDS`, padrão 1800 s); quando ele expira, a coleta é
cancelada e nenhuma troca parcial é iniciada ou commitada.
Os limites do task Celery são derivados desse orçamento: soft limit com 30 s e
hard limit com 60 s adicionais para rollback, cancelamento remoto e teardown.

Retries HTTP são bounded e acontecem somente para `429`, `5xx` e falhas de
transporte, com backoff exponencial, jitter e `Retry-After` respeitado até o teto
configurado. Erros permanentes `4xx`, protocolo inválido e estouro de limite não
são repetidos. Timeout/reset durante o submit sem `statement_id` é classificado
como submissão ambígua: o client não repete cegamente o POST, mas o worker pode
repetir o job dentro do máximo de tentativas.

A coleta ainda aplica limites explícitos de polls, chunks, linhas, bytes totais
e tamanho do statement (`DATABRICKS_MAX_POLLS`, `DATABRICKS_MAX_CHUNKS`,
`DATABRICKS_MAX_ROWS`, `DATABRICKS_MAX_BYTES` e
`DATABRICKS_MAX_STATEMENT_BYTES`). Cadeias cíclicas de chunks, manifests
truncados e contagens acima do teto falham fechados. Downloads externos aceitam
somente HTTPS nos sufixos Azure Blob/DFS configurados. Em timeout ou
cancelamento, o client tenta cancelar o statement remoto com um timeout curto,
sem mascarar o erro original. SQL, token, URLs pré-assinadas e corpos de erro
nunca entram nos logs.

O molde `.env.example` também documenta os controles bounded
`DATABRICKS_CANCEL_TIMEOUT_SECONDS`, `DATABRICKS_MAX_ATTEMPTS`,
`DATABRICKS_RETRY_BASE_SECONDS`, `DATABRICKS_RETRY_MAX_SECONDS` e
`DATABRICKS_POLL_INTERVAL_SECONDS`. Os defaults operacionais são 4 tentativas,
backoff de 0,5 s a 8 s, poll a cada 2 s, até 450 polls, 2048 chunks,
1.500.000 linhas, 512 MiB por consulta e statement de até 1 MiB.

## Normalização e invariantes

- Pedidos são agregados por `(nr_pedido, cd_prod_cor, sg_tamanho)`.
- Estoque é agregado por `(cd_prod_cor, sg_tamanho, canal)`; Franquia e
  Multimarca nunca são somados.
- Canais reconhecidos viram `Franquia` ou `Multimarca`; valores desconhecidos
  não são convertidos silenciosamente em outro canal.
- Quantidades e valores são parseados no domínio; linhas sem identidade ou
  campos obrigatórios são descartadas e entram nos diagnósticos do log.
- A origem repete o total financeiro do pedido/produto em cada tamanho. O
  backend distribui esse total em centavos, proporcionalmente à quantidade e
  com desempate determinístico, preservando a soma exata. O ERP aceita ajustes
  negativos legítimos; pedidos em aberto não inventam semântica signed.
- Tamanhos vazios ou acima do contrato são rejeitados; a projeção aceita no
  máximo 100 tamanhos por par.

## Proteções de snapshot

Um retorno vazio ou totalmente inválido de qualquer uma das cinco fontes não
apaga silenciosamente a foto anterior. O caso é registrado como degradação e a
fonte anterior é mantida. Se pedidos vierem incompletos, o ledger realtime de
`orders`/`alerts` não é atualizado, evitando falsos desaparecimentos e falsas
novidades.

Faturamento é ainda mais estrito: coleção, canal e valores monetários são
validados com `Decimal` finito dentro de `Numeric(14,2)`. Uma linha inválida ou
duplicata conflitante invalida a foto inteira, sem aplicar resultado parcial;
os logs registram apenas contagens e categorias bounded, nunca valores brutos.

## Transação, concorrência e projeção

`sincronizar_tudo` usa dois advisory locks com responsabilidades distintas. Um
lock de sessão dedicado cobre o job inteiro e é adquirido antes da primeira
consulta ao Databricks; uma segunda sincronização falha rapidamente sem gerar
duas coletas de vários minutos. As cinco fontes são lidas e agregadas, uma por
vez, fora do lock de mutação do estado de pedidos.

Somente imediatamente antes da primeira escrita PostgreSQL o worker tenta o
lock transacional compartilhado por adequação, faturamento sem adequar, edição
de grade e aprovação. Ele espera por até 12 s, em polls curtos, para não jogar
fora uma coleta válida por causa de um write breve. Enquanto a coleta externa
acontece, writes continuam disponíveis; durante o swap curto eles são
serializados e o lock permanece adquirido até `commit` ou `rollback`.

Dentro da transação:

1. cada snapshot válido preparado é substituído;
2. `pedido_produto_read` é reconstruída por pedido/produto, com precedência ERP;
3. os ledgers detectam somente chaves novas ou alertas recorrentes;
4. cada tópico alterado recebe no máximo um evento agregado no outbox;
5. ocorre um único `commit`.

Graças ao MVCC, leitores veem o snapshot anterior até o commit e depois passam
diretamente ao novo; nunca observam a tabela no intervalo entre `DELETE` e
`INSERT`.

## Agendamento e disparo manual

O Celery beat agenda `app.workers.tasks.ingestao.sincronizar_databricks` com
`INGESTAO_INTERVALO_SEGUNDOS`. O padrão atual é `7200` segundos (2 horas).
Worker e beat rodam fora da API e aplicam migrations antes de iniciar.

Disparo manual, com papel `administrador` ou superior:

```text
POST /api/v1/ingestao/sincronizar
GET  /api/v1/ingestao/sincronizar/{jobId}
```

O POST exige `Idempotency-Key` ASCII seguro de 8 a 128 caracteres, não executa
o Databricks dentro da request e retorna `202`, `Location`, um `jobId` UUID
opaco e a URL de status. Repetir a mesma chave pelo mesmo ator faz replay
seguro; pedidos simultâneos são coalescidos no escopo global `full-refresh`. O
GET autoriza pelo papel administrativo do endpoint, inclusive quando o job
coalescido foi criado por outro administrador. Ele expõe somente os estados
`queued`, `running`, `retrying`, `succeeded`, `failed` e `skipped`, progresso,
tentativas, deadline/timestamps, contagens finais bounded e um `errorCode`
sanitizado — nunca PII, exceção ou resposta do provedor.

O ledger PostgreSQL é a autoridade do job. Redis/Celery transportam apenas o
gatilho: se o broker estiver indisponível depois de persistir a intenção, o POST
continua respondendo `202`, o job permanece `queued` e o reconciliador de 60 s
`app.workers.tasks.durable_jobs.reconciliar_jobs` o reenfileira. Esse worker é
genérico para todos os kinds registrados, não parte do bounded context de
ingestão. Durante a execução, claim, heartbeat e término usam lease/CAS; o
mesmo sweep recupera leases expirados e remove, em lotes de no máximo 500,
somente jobs terminais com mais de 30 dias. O beat periódico também cria um job
autoritativo com owner nulo e idempotência por janela; ele não contorna o
ledger.

Tokens, nomes reais de tabelas e credenciais ficam apenas no `.env`; o molde
versionado é `.env.example`.
