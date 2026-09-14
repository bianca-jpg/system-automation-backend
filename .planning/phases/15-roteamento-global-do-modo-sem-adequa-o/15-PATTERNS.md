# Phase 15: Roteamento global do modo sem adequação - Pattern Map

**Mapped:** 2026-08-17
**Scope note:** Esta fase é majoritariamente edição de arquivos existentes (`ports.py`, `domain.py`, `adapters.py`, `repository.py`, `service.py`). Este mapa foca só nas peças **novas** que precisam de molde concreto: o método de leitura de orçamento (port+adapter), a query SQL com CTE que o implementa, a agregação JSONB de consumido, e os dois testes novos (performance `_024` e revalidação de estoque).

## File Classification

| New/Modified File (trecho) | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `ports.py::ProcessingPlannerSource.load_pedido_budget` (novo método) | port (Protocol method) | request-response | `ports.py::ProcessingPlannerSource.load_stock` (linhas 37-39) | exact — mesmo Protocol, mesma assinatura de coleção de entrada → dict de saída |
| `adapters.py::SqlAlchemyProcessingPlannerSource.load_pedido_budget` (novo método) | service/adapter (SQL) | CRUD (leitura agregada) | `adapters.py::load_stock` (linhas 269-302) e `_PENDING_BASE_SQL` (linhas 29-92) | exact — mesmo padrão de chunk + `ANY()` + CTE compartilhada |
| Query SQL de `total_original` (CTE nova) | SQL/CTE | batch/transform | `_PENDING_BASE_SQL` (`adapters.py:29-92`) | exact — reaproveita o `WITH` como CTE-irmã |
| Query SQL de `consumido_previo` (JSONB) | SQL/CTE | batch/transform | `repositorio_produtos.py::_AVAILABLE_STOCK_CTE` (linhas 40-96, `reservas` CTE) | exact — único precedente real de `jsonb_array_elements` sobre `ordens_reserva.itens` no repo |
| `test_pedidos_processing_sem_adequar_memory_024.py` (novo) | test (performance/medição) | batch | `app/tests/test_pedidos_motor_performance_024.py` | role-match — convenção de nome `_024`, mas o análogo não mede memória real (ver nota abaixo) |
| `test_pedidos_processing_repository.py::test_sem_adequar_revalidates_stock_before_writes` (novo) | test (integration) | CRUD | `test_adequation_revalidates_stock_before_writes` (linhas 382-396, mesmo arquivo) | exact — mesmo arquivo, mesmos helpers (`_seed_source`, `_planned_pair`, `_create_job_and_plan`) |
| `test_pedidos_processing_repository.py::test_orcamento_persiste_entre_execucoes_sucessivas` (novo, ALOC-09) | test (integration) | CRUD | mesmo arquivo — combinar `_seed_source`/`_create_job_and_plan` com os 2 testes de `caplog` do ALOC-09 em `test_pedidos_motor.py` | role-match — não há análogo de "2 execuções sucessivas", compor a partir de duas peças existentes |

## Pattern Assignments

### 1. Novo método de port + adapter — leitura de orçamento

**Analog do Protocol:** `app/modules/pedidos/processing/application/ports.py`, método `load_stock` (linhas 37-39):

```python
    async def load_stock(
        self, product_codes: set[str]
    ) -> dict[str, dict[str, int]]: ...
```

**Padrão a copiar:** método assíncrono no `Protocol` `ProcessingPlannerSource` (linhas 21-45 do arquivo), recebendo um `set` de chaves de escopo e devolvendo um `dict` puro (não dataclass, não TypedDict) — é o padrão consistente de **todos** os métodos deste port (`load_stock`, `load_size_reference` devolvem `dict[str, dict[str, int]]`; `load_adequation_config` devolve `tuple[str, float]`). Nenhum método do port usa dataclass ou TypedDict de retorno — todos usam tipos primitivos (`dict`, `tuple`, `list[dict]`). Portanto o novo método deve seguir a mesma convenção:

```python
    async def load_pedido_budget(
        self, nr_pedidos: set[int]
    ) -> tuple[dict[int, int], dict[int, int], dict[int, int]]: ...
```

(assinatura já resolvida em RESEARCH.md § "Fechamento do ALOC-09", ponto 3 — `total_original_por_pedido`, `consumido_previo_adicao_por_pedido`, `consumido_previo_corte_por_pedido`, todos `dict[int, int]`, coerente com os parâmetros já aceitos por `processar_pedidos` desde a Phase 14, `motor_adequacao.py:462-467`).

**Analog do adapter (implementação real):** `app/modules/pedidos/processing/infrastructure/adapters.py`, método `load_stock` (linhas 269-302):

```python
    async def load_stock(self, product_codes: set[str]) -> dict[str, dict[str, int]]:
        """Carrega só códigos candidatos; cada cálculo usa <=200 alvos."""

        result: dict[str, dict[str, int]] = {channel: {} for channel in CANAIS}
        codes = sorted(product_codes)
        for offset in range(0, len(codes), 200):
            code_chunk = codes[offset : offset + 200]
            stock_rows = (
                await self._db.execute(
                    text(
                        """
                        SELECT cd_prod_cor, sg_tamanho, canal
                        FROM estoque
                        WHERE cd_prod_cor = ANY(CAST(:codes AS text[]))
                        ORDER BY cd_prod_cor, sg_tamanho, canal
                        """
                    ),
                    {"codes": code_chunk},
                )
            ).all()
            ...
```

**O que replicar:**
- `sorted()` da coleção de entrada antes de fatiar em chunks (determinismo de ordem entre execuções — útil para teste e para logs).
- Loop `for offset in range(0, len(items), CHUNK_SIZE)` fatiando em blocos (aqui, RESEARCH.md recomenda ~5.000 `nr_pedido` por chunk, análogo aos 200 códigos de `load_stock`).
- `await self._db.execute(text("..."), {"param": chunk})` com bind nomeado — nunca f-string de valores de usuário dentro do SQL (só a CTE compartilhada `_PENDING_BASE_SQL` é interpolada via f-string, porque é SQL estático, não dado de entrada).
- `ANY(CAST(:codes AS text[]))` é o padrão de array bind deste repo — para `nr_pedido` (inteiro), o equivalente é `ANY(CAST(:nr_pedidos AS integer[]))` ou `= ANY(:nr_pedidos::int[])` (RESEARCH.md já usa essa forma na CTE proposta).
- Retorno como `dict` simples, populado incrementalmente dentro do loop de chunk (`result[...] = ...`), nunca lista intermediária de linhas ORM.

**O que adaptar:** `load_stock` faz duas passadas (busca linhas de `estoque` → depois resolve disponibilidade via `estoque_repo.carregar_estoque_disponivel_alvos`, chamando outro repositório de domínio). O novo método é mais simples — uma única query agregada por chunk, sem segunda chamada a repositório de domínio, porque a agregação (`sum`/`GREATEST`) já acontece no SQL.

---

### 2. Query SQL com CTE — `_PENDING_BASE_SQL` como referência de estilo

**Analog:** `app/modules/pedidos/processing/infrastructure/adapters.py`, linhas 29-92 (`_PENDING_BASE_SQL`) e seu uso em `inspect_pending` (linhas 99-136).

**Padrão a copiar (estrutura do `WITH` compartilhado):**

```python
_PENDING_BASE_SQL = """
pending_base AS (
    SELECT
        p.id, p.nr_pedido, p.cd_prod_cor, p.sg_tamanho, ...
    FROM pedidos p
    WHERE NOT EXISTS (
        SELECT 1 FROM pedidos_processados local_done
        WHERE local_done.nr_pedido = p.nr_pedido
          AND local_done.cd_prod_cor = p.cd_prod_cor
    )
      AND NOT EXISTS (...)
),
...
eligible_items AS (
    SELECT base.*
    FROM pending_base base
    JOIN eligible_pairs USING (nr_pedido, cd_prod_cor)
)
"""
```

E o uso via f-string interpolando a constante dentro de um `WITH` maior, com bind param nomeado para o resto:

```python
    async def inspect_pending(self, channel: ProcessingChannel) -> PendingSnapshotStats:
        row = (
            await self._db.execute(
                text(
                    f"""
                    WITH {_PENDING_BASE_SQL}
                    , pair_stats AS (
                        SELECT
                            nr_pedido, cd_prod_cor,
                            count(*)::integer AS item_count,
                            ...
                        FROM eligible_items
                        GROUP BY nr_pedido, cd_prod_cor
                    )
                    SELECT ...
                    """
                ),
                {"channel": channel.value},
            )
        ).one()
```

**O que replicar exatamente, per RESEARCH.md § "Fechamento do ALOC-09" item 1:**
- Módulo-level constante `_PENDING_BASE_SQL` (ou nova constante irmã) é `str` puro, **sem** `text()` — o `text()` só envolve a query final montada.
- A query de `total_original` deve entrar como **CTE adicional no mesmo `WITH`** de `_PENDING_BASE_SQL` (reaproveitando `eligible_pairs`), não uma constante separada e desconectada:

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

- `.one()` quando a query devolve exatamente 1 linha agregada (`inspect_pending`); `.mappings().all()` ou iteração via `.mappings()` quando devolve N linhas (padrão de `load_pending_pair_page`, linhas 249-251, e `load_stock`).
- `execution_options(yield_per=2_000)` só é usado em `load_pending_items` (linha 150) — leitura de **muitas** linhas via `db.stream(...)`. Para o orçamento (agregado, poucas linhas — uma por `nr_pedido`), **não** é necessário `yield_per`; usar `await self._db.execute(...)` simples seguido de `.all()`/`.mappings()`, igual a `load_stock`.
- Chunking de `nr_pedido`: seguir o mesmo padrão de `load_stock` (`for offset in range(0, len(codes), 200)`), mas com tamanho de chunk maior (~5.000, per RESEARCH.md item 4) porque o limite prático de `ANY(int[])` no Postgres é bem mais alto que `IN (...)` inline.

---

### 3. Agregação de JSONB — precedente real encontrado

**Sim, há precedente direto.** `app/modules/pedidos/infrastructure/repositorio_produtos.py`, CTE `_AVAILABLE_STOCK_CTE` (linhas 40-96), sub-CTE `reservas` (linhas 46-84):

```sql
reservas AS (
    SELECT
        o.cd_prod_cor,
        upper(trim(item ->> 'sg_tamanho')) AS sg_tamanho,
        CASE
          WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
          ...
        END AS canal,
        sum(greatest(coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0), 0)) AS qty
    FROM ordens_reserva o
    CROSS JOIN LATERAL jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
    JOIN stock s
      ON s.cd_prod_cor = o.cd_prod_cor
     AND s.sg_tamanho = upper(trim(item ->> 'sg_tamanho'))
     ...
    WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
      AND NOT EXISTS (...)
    GROUP BY 1, 2, 3
)
```

**Padrão a copiar:**
- `CROSS JOIN LATERAL jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item` — sempre com `coalesce(..., '[]'::jsonb)` para não perder linhas de `ordens_reserva` sem itens (defensivo contra `NULL`).
- Extração de campo com `item ->> 'campo'` (texto) seguido de `nullif(..., '')::integer` quando o campo pode ser vazio, e `coalesce(..., 0)` para tratar ausência — padrão repetido em `repositorio_produtos.py:59, 166, 180`.
- `greatest(diff, 0)` para nunca somar valor negativo — é exatamente o padrão que RESEARCH.md pede aplicar para "adições e cortes não se compensam" (`GREATEST(qt_liquida - qt_solicitada, 0)` e `GREATEST(qt_solicitada - qt_liquida, 0)` em direções separadas, nunca subtraindo diretamente).

**Query nova recomendada (RESEARCH.md item 2), seguindo este molde:**

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

**Diferença a decidir na implementação:** o análogo usa `coalesce(o.itens, '[]'::jsonb)` antes do `jsonb_array_elements`; a query nova em RESEARCH.md omite o `coalesce` — **replicar o padrão do análogo** (adicionar `coalesce(o.itens, '[]'::jsonb)`) para consistência e robustez contra `itens IS NULL`, mesmo que hoje `itens` provavelmente nunca seja `NULL` em `ordens_reserva`.

---

### 4. Teste de performance/medição `_024` — o que reaproveitar

**Analog único:** `app/tests/test_pedidos_motor_performance_024.py` (202 linhas, lido por completo).

**O que NÃO há neste análogo, apesar do nome "performance":** nenhuma medição de RSS/memória real. Os 3 testes do arquivo medem **paridade de resultado** (`test_indice_otimizado_mantem_paridade_exata_com_varredura_legada`), **complexidade algorítmica via contagem de chamadas** (`test_indice_visita_estoque_uma_vez_e_copia_apenas_subset_do_produto`, usando um `Mapping` instrumentado `_EstoqueContado` que conta `__iter__`/itens visitados), e **ausência de PII em logs** (`test_logs_do_motor_expoem_apenas_contagens_e_categorias`). Nenhum usa `resource`, `psutil` ou `tracemalloc` — confirma RESEARCH.md: não há instrumentação de memória automatizada hoje no repositório.

**O que replicar deste arquivo:**
- Convenção de nome de arquivo: `test_pedidos_..._024.py` (sufixo numérico do arquivo, não do teste) — mesmo padrão a seguir para `test_pedidos_processing_sem_adequar_memory_024.py`.
- Helper local `_item(...)` (linhas 12-30) para montar dicts de item sintético sem tocar banco — mesmo padrão de dataset sintético gerado em memória (não lido do banco), reaproveitável para gerar 40-50k pares/100k+ itens conforme RESEARCH.md recomenda.
- Padrão de instrumentação por wrapper/monkeypatch (`_EstoqueContado`, um `Mapping` customizado que conta acessos) — mesma técnica geral de "instrumentar sem mudar a função sob teste": para medição de memória, o equivalente é envolver a chamada com `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` antes/depois, não um `Mapping` — mas o espírito de "instrumentar de fora, sem modificar `build_processing_plan`" é o mesmo.
- Docstring de módulo curta explicando o que o arquivo prova (linha 1: `"""Regressões de paridade e complexidade do índice de estoque do motor."""`) — seguir o mesmo estilo para o arquivo novo.

**O que é preciso construir do zero (sem análogo):** a chamada a `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` antes e depois de `build_processing_plan` com dataset sintético grande em modo `SEM_ADEQUAR`, comparando o delta contra um teto (ex. 700 MiB). Isto é greenfield dentro do repositório — tratar como calibração na primeira execução, não como valor validado (per RESEARCH.md, confidence LOW nesta parte).

---

### 5. Teste de revalidação de capacidade de estoque (FIX-01) — molde direto

**Analog:** `app/tests/test_pedidos_processing_repository.py`, `test_adequation_revalidates_stock_before_writes` (linhas 382-396):

```python
async def test_adequation_revalidates_stock_before_writes(processing_database) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db, stock_qty=1)
        pair = _planned_pair(tipo="com", qty=2)
        repository, spec = await _create_job_and_plan(db, pair)
        await db.commit()
        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        with pytest.raises(ProcessingPlanConflict, match="processing_plan_stale"):
            await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await db.rollback()

    async with factory() as db:
        assert await db.scalar(select(OrdemReserva.nr_pedido)) is None
```

**Estrutura a copiar exatamente para o teste novo (`test_sem_adequar_revalidates_stock_before_writes`):**
1. `async with factory() as db:` — mesma fixture `processing_database` (`_engine, factory = processing_database`).
2. `await _seed_source(db, stock_qty=1)` — helper já existente (linhas 211-235), sem alteração; semeia `pedidos` + `estoque` com `qt_disponivel=stock_qty`.
3. `pair = _planned_pair(tipo="sem", qty=2)` — **a única mudança de dado**: trocar `tipo="com"` por `tipo="sem"` (que já é o **default** do helper, linha 153: `def _planned_pair(*, tipo: str = "sem", qty: int = 2)`), então pode até ser chamado sem `tipo=` explícito, mas RESEARCH.md recomenda passar explícito para legibilidade do teste.
4. `repository, spec = await _create_job_and_plan(db, pair)` — helper existente (linhas 175-208), sem alteração; usa `ProcessingMode.SEM_ADEQUAR` internamente já hoje.
5. `await db.commit()` → `chunk = await repository.load_next_chunk(...)` → `with pytest.raises(ProcessingPlanConflict, match="processing_plan_stale"): await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)` → `await db.rollback()` — sequência idêntica.
6. Assert final idêntico: `assert await db.scalar(select(OrdemReserva.nr_pedido)) is None`.

**O que muda de verdade:** nada na estrutura do teste — só o dado de entrada (`tipo="sem"`) e o comportamento esperado do sistema sob teste (hoje passaria silenciosamente sem erro por causa do `if tipo != "com": continue` em `_assert_stock_capacity`, `repository.py:493-494`; depois do FIX-01, deve levantar o mesmo `ProcessingPlanConflict`). **Não copiar** o `if tipo != "com": continue` — é exatamente a linha que o FIX-01 remove.

## Shared Patterns

### Bind params em SQL cru
**Source:** `adapters.py` (`_PENDING_BASE_SQL`, `load_stock`)
**Apply to:** os dois métodos novos de `load_pedido_budget`
```python
text("... WHERE cd_prod_cor = ANY(CAST(:codes AS text[])) ..."), {"codes": code_chunk}
```
Nunca interpolar valor de usuário via f-string; só a estrutura SQL estática (nomes de CTE) é f-string.

### Chunking defensivo de coleções grandes
**Source:** `adapters.py::load_stock` (`for offset in range(0, len(codes), 200)`)
**Apply to:** `load_pedido_budget`, com chunk maior (~5.000 `nr_pedido`, per RESEARCH.md)

### Retorno como dict/tuple primitivo, nunca dataclass, nos métodos de `ProcessingPlannerSource`
**Source:** `ports.py`, todos os métodos do Protocol
**Apply to:** `load_pedido_budget` — retorno `tuple[dict[int,int], dict[int,int], dict[int,int]]`

### JSONB via `jsonb_array_elements` + `coalesce(..., '[]'::jsonb)` + `GREATEST(diff, 0)`
**Source:** `repositorio_produtos.py::_AVAILABLE_STOCK_CTE`
**Apply to:** a CTE de `consumido_previo` do orçamento

### Estrutura de teste de integração com `processing_database` fixture + helpers `_seed_source`/`_planned_pair`/`_create_job_and_plan`
**Source:** `test_pedidos_processing_repository.py`
**Apply to:** o novo teste de FIX-01 e o novo teste de ALOC-09 (duas execuções sucessivas)

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| Medição real de pico de RSS (`resource.getrusage`) dentro de um teste `_024` | test (performance) | batch | Nenhum teste no repositório usa `resource`/`psutil`/`tracemalloc` hoje — greenfield, tratar como calibração inicial, não como padrão validado (RESEARCH.md confidence LOW) |
| `test_orcamento_persiste_entre_execucoes_sucessivas` (2 execuções sucessivas do mesmo pedido) | test (integration) | CRUD | Não existe teste de "rodar `_plan_once` duas vezes sobre o mesmo pedido" hoje; compor a partir de `_seed_source`/`_create_job_and_plan` (este arquivo) + os 2 testes de `caplog` do ALOC-09 em `app/tests/test_pedidos_motor.py` (não lidos nesta sessão de pattern-mapping, mas citados em RESEARCH.md linha 211) |

## Metadata

**Analog search scope:** `app/modules/pedidos/processing/application/ports.py`, `app/modules/pedidos/processing/infrastructure/adapters.py`, `app/modules/pedidos/processing/infrastructure/repository.py`, `app/modules/pedidos/infrastructure/repositorio_produtos.py`, `app/tests/test_pedidos_processing_repository.py`, `app/tests/test_pedidos_motor_performance_024.py`
**Files scanned:** 6
**Pattern extraction date:** 2026-08-17
