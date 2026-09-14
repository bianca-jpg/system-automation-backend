---
phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
plan: 04
subsystem: ingestao
tags: [databricks, checkpoint, celery, psql, evidencia-real]

# Dependency graph
requires:
  - phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
    provides: "5ª fonte completa e ligada ao sincronizar_tudo (Planos 02-01 e 02-03)"
provides:
  - "Critério 1 do ROADMAP provado fora de mock: sync real gravou 569.726 linhas em produto_tamanho_posicao (tabela estava com 0)"
  - "Critério 3 do ROADMAP provado fora de mock: 2ª sincronização manteve a contagem em 569.726 (substituição, não acumulação)"
  - "Amostra real capturada para a fixture do Plano 02-05: AC.02.0002|163 (6 tamanhos, posições 1..6)"
  - "Evidência de D-01 em dados reais: 5 linhas com nr_posicao inválido descartadas pelo parse tolerante"
affects: [02-05-fixture-real-criterio-4]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Sync real disparado pela task Celery (`celery call`) em vez do endpoint HTTP: o full refresh das 5 fontes leva ~5-7 min e estoura qualquer timeout de cliente HTTP"
    - "Posições NÃO são necessariamente contíguas a partir de 1 (produto .1|001 usa 2..5) — a conversão não pode assumir posição inicial 1"

key-files:
  created: []
  modified: []
---

# Plano 02-04 — Checkpoint: sync real e captura de amostra

**Tipo:** `checkpoint:human-verify` (nenhum arquivo de código criado/modificado)
**Executado em:** 2026-08-05, contra o Databricks real e o Postgres de dev (`dev_db`)

## Evidência dos critérios do ROADMAP

### Preparação
`DATABRICKS_TABELA_TAMANHO_REF=programa_estagio.refined.system_automation_prod_tamanho_ref` acrescentada ao `.env`
(gitignored; já documentada no `.env.example` pelo Plano 02-01) e stack recriada
(`docker compose up -d --force-recreate api celery_worker celery_beat`).

### Critério 1 — a 5ª fonte grava a referência (fora de mock) ✅

Contagem ANTES: `SELECT COUNT(*) FROM produto_tamanho_posicao;` → **0**

Retorno da task `app.workers.tasks.ingestao.sincronizar_databricks[546ed9e8-…]`, concluída em 424,4s:

```
{'status': 'success', 'pedidos_inseridos': 114339, 'estoque_chaves': 46791,
 'processados_erp': 685152, 'faturamento_colecoes': 6,
 'referencia_tamanho_posicao': 569726}
```

Contagem DEPOIS: **569726** (igual ao contador retornado).

Log da etapa nova, que também comprova **D-01 em dados reais**:

```
Referência tamanho->posição sincronizada: 569726 itens (569734 linhas brutas, 5 descartadas).
```

As 5 linhas descartadas são registros da view com `nr_posicao` inválido — o parse tolerante
(D-01) as ignorou sem interromper o sync, exatamente como decidido. Nenhum aviso de conflito
de posição (D-03) apareceu nos dados reais.

### Critério 3 — full refresh sem duplicar (fora de mock) ✅

Sincronização repetida, sequencialmente, com a anterior já concluída
(task `19519cb8-…`, 312,4s):

```
{'status': 'success', ..., 'referencia_tamanho_posicao': 569726}
```

Contagem depois da 2ª: **569726** — idêntica, nunca 1.139.452. Substituição confirmada.

### Amostra real para o Plano 02-05 (critério 4, LINX-03) ✅

```
cd_prod_cor,sg_tamanho,nr_posicao
AC.02.0002|163,17,1
AC.02.0002|163,19,2
AC.02.0002|163,21,3
AC.02.0002|163,23,4
AC.02.0002|163,25,5
AC.02.0002|163,27,6
```

Grade numérica real de 6 tamanhos em posições contíguas 1..6. Escolhido entre os produtos com
`cd_prod_cor` no padrão `XX.NN.NNNN|NNN` e ≥5 tamanhos.

**Segunda amostra, deliberadamente irregular** (vale como caso de teste): o produto `.1|001`
(código sujo na origem) usa as posições **2,3,4,5** para `P,M,G,GG` — ou seja, **posições não
começam necessariamente em 1**. A conversão não pode assumir contiguidade a partir de 1.

## Desvios e achados

1. **Endpoint HTTP não serve para disparar o sync completo.** `POST /api/v1/ingestao/sincronizar`
   estourou o timeout do cliente (o full refresh das 5 fontes leva 5-7 min). O sync foi disparado
   pela task Celery (`celery call`), que é o mesmo caminho do beat e roda sem timeout de HTTP.
   A primeira tentativa via curl abortou o cliente, mas o servidor concluiu o trabalho.

2. **Bug de concorrência descoberto e corrigido fora desta fase.** Ao disparar a 2ª sincronização
   enquanto a 1ª ainda rodava, ela abortou com
   `duplicate key value violates unique constraint "uq_pedido_item"`. Causa: o full refresh
   (DELETE sem WHERE + INSERT) não é seguro sob concorrência — o DELETE da segunda avalia um
   snapshot anterior ao COMMIT da primeira. Os dados permaneceram íntegros (rollback da transação
   inteira, o que confirma de novo a garantia de commit único), mas ~7 min de trabalho foram
   perdidos. Corrigido em commit separado, fora do escopo desta fase
   (`4e92aee fix(ingestao): serializa sincronizacoes com advisory lock do Postgres`):
   `sincronizar_tudo` agora toma `pg_try_advisory_xact_lock`, a rota devolve **409** e a task do
   beat devolve `{"status": "skipped"}`. Por isso os números do critério 3 vêm de uma
   sincronização **sequencial**, não concorrente.

3. **Teste frágil exposto pelos dados reais.** `test_sincronizar_referencia_tamanhos_view_vazia_mantem_snapshot_anterior`
   (Plano 02-03) fixava a contagem esperada em `1`, o que só valia com a tabela vazia. Com a
   referência real ingerida passou a falhar (`assert 569727 == 1`). Corrigido no mesmo commit
   acima para comparar com a contagem observada antes.

## Self-Check: PASSED

- Critério 1: `referencia_tamanho_posicao = 569726 > 0`, com a tabela partindo de 0 ✅
- Critério 3: contagem estável em 569726 após a 2ª sincronização ✅
- Amostra real com ≥3 tamanhos capturada e registrada acima ✅
- Nenhum valor inventado: todos os números vêm de logs do worker e de `psql` ✅
- Suíte completa após as correções: **240 passed, 1 warning** (warning pré-existente do FastAPI) ✅
