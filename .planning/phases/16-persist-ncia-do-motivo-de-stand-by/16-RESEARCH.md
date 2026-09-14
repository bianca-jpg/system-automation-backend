# Phase 16: Persistência do motivo de stand by - Research

**Researched:** 2026-08-20
**Domain:** Extensão do motor de adequação (`domain/motor_adequacao.py`) e do processamento durável (`processing/`) para persistir, em tabela dedicada, o motivo de cada par fora da OR — resolvendo o gap de desenho do furo de grade que a pesquisa do milestone não previu.
**Confidence:** HIGH — todas as afirmações abaixo vieram de leitura linha a linha do código nesta sessão (`motor_adequacao.py`, `furo_de_grade.py`, `processing/domain.py`, `processing/application/ports.py`/`service.py`, `processing/infrastructure/repository.py`, `repositorio_snapshot.py`, migrations 017/020/031, e os 3 arquivos de teste apontados). Nenhuma dedução por nome de símbolo.

## Summary

O gap de desenho identificado no `16-CONTEXT.md` tem solução fechada e barata: `tem_furo_de_grade` é chamada em **exatamente 2 lugares** dentro de `_processar_pedidos_canal` (um por modo — `SEM_ADEQUAR` na linha 750, `ADEQUAR` na linha 821), e o terceiro motivo (`sem_estoque`) nasce sempre em `_commitar_par` quando `gerou_or` é `False`. Isso significa que a Rota 1 do CONTEXT.md (aditiva no motor) é implementável com uma nova chave `preteridos_motivo: dict[tuple[int,str], str]` no dicionário de retorno de `processar_pedidos`, populada nos 3 pontos exatos listados abaixo — sem tocar nenhuma chave existente e sem quebrar nenhum teste (confirmado: nenhum teste faz `assert resultado == {...}` no dicionário inteiro; todos acessam chaves específicas).

O ponto de extensão do `PlanDraft` precisa carregar o motivo por par (não só o par nu), porque agora há 3 valores em vez de 2. A assinatura recomendada muda ligeiramente da proposta original da pesquisa do milestone: `deferred_pairs: tuple[tuple[int, str, str, str], ...]` — `(nr_pedido, cd_prod_cor, canal, motivo)`, motivo ∈ `{'sem_estoque', 'furo_grade'}` — e `blocked_credit_pairs: tuple[tuple[int, str, str], ...]` — `(nr_pedido, cd_prod_cor, canal)`, motivo sempre `'sem_credito'` implícito (lista homogênea, não precisa de 4º campo). O fan-out de crédito e a resolução de canal por par acontecem em `build_processing_plan` (Python puro, sem I/O), não no writer.

STANDBY-06 (contador de execuções consecutivas) tem desenho fechado: coluna `execucoes_consecutivas INTEGER NOT NULL DEFAULT 1`, incrementada via `ON CONFLICT ... DO UPDATE SET execucoes_consecutivas = pedido_standby_motivo.execucoes_consecutivas + 1` (SQLAlchemy Core `pg_insert(...).on_conflict_do_update(...)`, não SQL cru com `text()` — o padrão já existe em `repository.py` para `store_plan`). O contador é **agnóstico ao motivo**: incrementa sempre que o par já existia na tabela, mesmo que o motivo tenha mudado — porque a linha só é apagada quando o par sai de stand-by de verdade (vira OR ou desaparece do full refresh), e nesses casos um novo INSERT reinicia em 1 naturalmente.

**Recomendação primária:** implementar em 3 waves sequenciais (motor puro → extensão `PlanDraft`/persistência → migration isolada), nesta ordem específica porque a migration não depende de nenhum código Python e pode ser feita em paralelo, mas os testes de repositório dependem do modelo ORM que a migration cria.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Classificar motivo (crédito/estoque/furo) por par | Domain (`motor_adequacao.py`) | — | O motor já calcula a decisão; é a única fonte da verdade, não deve ser recomputado a jusante |
| Capturar/fan-out do motivo para persistência | Application (`processing/domain.py::build_processing_plan`) | — | DTO intermediário `PlanDraft`, sem I/O, mesma camada que já descarta essa informação hoje |
| Persistir/upsert linhas de stand-by | Infrastructure (`processing/infrastructure/repository.py`) | Database (migration) | Novo método no port `ProcessingRepository`, mesma transação de `store_plan` |
| Limpar linha ao gerar OR | Infrastructure (`SqlAlchemyProcessingWriter.apply_pairs`) | Database | Simétrico ao que já acontece ali com `pedidos_processados`/`ordens_reserva`/`estoque_virtual` |
| Limpar linha órfã (pedido saiu por outro caminho) | Infrastructure (`repositorio_snapshot.py::reconstruir_pedido_produto_read`) | Database | Full refresh já roda a cada 2h na mesma transação |
| Consulta/leitura para UI (tags, filtro) | Fora de escopo (Phase 17) | — | Esta fase só produz o dado; não expõe rota nova |

## Package Legitimacy Audit

Não aplicável — esta fase não introduz nenhuma dependência externa nova (biblioteca de terceiros). Todo o trabalho usa SQLAlchemy/Alembic/pytest já presentes no projeto. Nenhum `pip`/`npm install` necessário.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STANDBY-01 | O motivo pelo qual cada par ficou de fora é persistido a cada processamento, sem inflar o resultado do job | § "Gap de desenho resolvido" (assinatura de `preteridos_motivo`/`PlanDraft`), § "Persistência — `record_standby_reasons`", § Migration |
| STANDBY-06 | O sistema registra há quantas execuções consecutivas um par está em stand by | § "STANDBY-06 — contador de execuções consecutivas" |
</phase_requirements>

## Gap de desenho resolvido: motivo do furo de grade

### Onde `tem_furo_de_grade` é chamada (confirmado por leitura direta)

Exatamente **2 call sites**, ambos dentro de `_processar_pedidos_canal` (`motor_adequacao.py`), um por modo — não 3 como o CONTEXT.md considerava possível:

1. **`SEM_ADEQUAR`** — `motor_adequacao.py:750-763`. Se `tem_furo`, `preteridos.append(par)` e (se `not resultados_apenas_selecionados`) grava em `resultados[par]` via `marcar_stand_by(itens_par, motivo=motivo_furo, ...)`; depois `continue` — nunca chega a `aplicar_tudo_ou_nada`/`_commitar_par`.
2. **`ADEQUAR`** — `motor_adequacao.py:821-834`. Mesmo padrão: se `tem_furo`, `preteridos.append(par)` + `marcar_stand_by(..., motivo=motivo_furo)`, `continue` — nunca chega a `adequar_grade_produto`/`_commitar_par`.

Em ambos, `motivo_furo` é uma **string dinâmica com o tamanho específico** (`furo_de_grade.py:63`: `f"Furo de grade: tamanho {sg_tamanho} sem estoque reservável"`) — não um valor de enum. Esse texto livre continua existindo em `resultados[par][item]["motivo_stand_by"]` (quando não descartado por `resultados_apenas_selecionados=True`); **não é ele que vai para a coluna `motivo` da tabela nova** — a coluna precisa de um valor canônico curto (`'furo_grade'`), não da frase completa.

### Terceiro caminho que produz `preteridos`: `_commitar_par`

`_commitar_par` (`motor_adequacao.py:422-452`) é chamado **depois** que o furo já foi descartado (2 call sites: linha 771 no ramo `SEM_ADEQUAR`, linha 901 no ramo `ADEQUAR`). Quando `gerou_or` é `False` — porque `aplicar_tudo_ou_nada` (falta de estoque para a grade completa) ou `adequar_grade_produto` (orçamento de corte excedido, `_MOTIVO_ORCAMENTO_CORTE`, ou `est_disp=0` em todos os tamanhos) devolveram só itens em stand-by — o par entra em `preteridos` **sem** ter passado pelo furo. Este é sempre o bucket `'sem_estoque'` no vocabulário de 3 valores.

### Achado adicional não previsto pelo CONTEXT.md: ramo "canal não identificado"

Existe um **quarto** ponto que também escreve em `preteridos`, no nível de `processar_pedidos` (não em `_processar_pedidos_canal`): o laço `pares_sem_canal` (`motor_adequacao.py:524-546`), motivo `_MOTIVO_SEM_CANAL` ("Canal não identificado..."). **Verificado: este ramo é código morto no caminho real de `build_processing_plan`.** `_eligible_pairs` (`processing/domain.py:382-417`) já exige, ANTES de chamar o motor, que todo par tenha canal canônico homogêneo (`_pair_channel`, linha 372-379) — levanta `InvalidProcessingRequest("pending_pair_channel_is_not_canonical")` se não. Como `flat` (a lista que chega a `processar_pedidos`) é montada só a partir de `eligible.values()`, nenhum item com canal não identificável chega ao motor por este caminho. O ramo continua existindo no motor por generalidade (outros chamadores/testes o exercitam isoladamente), mas para esta fase ele nunca dispara.

**Recomendação (mantida por defensividade, não porque o caminho real precise):** o motor ainda deve popular `preteridos_motivo[par] = 'sem_estoque'` também neste ramo, para manter a invariante `len(preteridos) == len(preteridos_motivo)` válida universalmente — custa 1 linha e evita que um chamador futuro (fora de `build_processing_plan`) quebre essa invariante silenciosamente. `[ASSUMED]` — decisão de engenharia defensiva, não uma exigência de negócio; ver Assumptions Log A1.

### Novo vocabulário canônico — arquivo dedicado

Recomenda-se um módulo novo e pequeno, seguindo o padrão já estabelecido pelo projeto para conceitos puros de domínio (`furo_de_grade.py`, `orcamento_pedido.py`, `rateio.py`):

```python
# app/modules/pedidos/domain/standby_motivo.py
"""Vocabulário canônico dos motivos de stand by persistidos (STANDBY-01/04).

Um único lugar para as 3 strings evita que motor, `processing/domain.py` e o
CHECK constraint da migration divirjam entre si.
"""

SEM_CREDITO = "sem_credito"
SEM_ESTOQUE = "sem_estoque"
FURO_GRADE = "furo_grade"

MOTIVOS_VALIDOS = frozenset({SEM_CREDITO, SEM_ESTOQUE, FURO_GRADE})

__all__ = ["SEM_CREDITO", "SEM_ESTOQUE", "FURO_GRADE", "MOTIVOS_VALIDOS"]
```

Importado por `motor_adequacao.py` (para popular `preteridos_motivo`) e por `processing/domain.py` (para validar `PlanDraft.deferred_pairs`/montar as linhas). **Nunca redigitar as 3 strings soltas em mais de um arquivo** — é exatamente o vetor do risco descrito em "Riscos de execução" abaixo.

### Mudanças exatas em `motor_adequacao.py`

1. `merged` (dict inicial em `processar_pedidos`, linha 513-519) ganha `"preteridos_motivo": {}`.
2. `_processar_pedidos_canal`: variável local nova `preteridos_motivo: dict[tuple[int,str], str] = {}` ao lado de `preteridos: list = []` (linha ~613-614).
3. Nos 2 call sites de `tem_furo_de_grade` (linhas 750-763 e 821-834): logo após `preteridos.append(par)`, adicionar `preteridos_motivo[par] = FURO_GRADE`.
4. `_commitar_par` (linha 422-452): novo parâmetro `preteridos_motivo: dict[tuple[int,str], str]`. No ramo `else` (linha 449-452, quando `not gerou_or`), além de `preteridos.append(par)`, fazer `preteridos_motivo[par] = SEM_ESTOQUE`. Atualizar os 2 call sites de `_commitar_par` (linha 771 e linha 901) para passar o novo dict.
5. Early-return de `_processar_pedidos_canal` quando `estrutura` vazia (linha 604-610) e o dict de retorno final (linha 939-948): adicionar `"preteridos_motivo": dict(preteridos_motivo)`.
6. Laço `pares_sem_canal` (linha 524-546, nível de `processar_pedidos`): adicionar `merged["preteridos_motivo"][par] = SEM_ESTOQUE` (ver nota acima — defensivo, path morto para `build_processing_plan`).
7. Merge por canal (linha 563-567): adicionar `merged["preteridos_motivo"].update(parcial["preteridos_motivo"])`.

**Nenhuma chave existente muda de forma.** `preteridos_motivo` é 100% aditiva. Confirmado por grep: nenhum teste em `test_pedidos_motor.py`/`test_pedidos_motor_furo_grade.py` faz `assert resultado == {...}` no dicionário completo — todos leem chaves específicas (`resultado["preteridos"]`, `resultado["selecionados"]`, etc.), então a chave nova não quebra nada.

### Mudanças exatas em `processing/domain.py::build_processing_plan`

`PlanDraft` (linha 269-300) ganha 2 campos novos, **com default vazio** (posição no final, após `plan_hash`, para não exigir reordenar campos sem default):

```python
deferred_pairs: tuple[tuple[int, str, str, str], ...] = ()   # (nr_pedido, cd_prod_cor, canal, motivo)
blocked_credit_pairs: tuple[tuple[int, str, str], ...] = ()  # (nr_pedido, cd_prod_cor, canal)
```

`__post_init__` ganha 3 validações defensivas novas (barato, e é exatamente o que fecha o risco #1 da seção "Riscos de execução"):

```python
deferred_keys = {(nr, code) for nr, code, _canal, _motivo in self.deferred_pairs}
blocked_keys = {(nr, code) for nr, code, _canal in self.blocked_credit_pairs}
if deferred_keys & blocked_keys:
    raise InvalidProcessingRequest("standby_pair_dual_classification")
if len(self.deferred_pairs) != self.deferred_count:
    raise InvalidProcessingRequest("deferred_pairs_count_mismatch")
if len({nr for nr, _c, _ca in self.blocked_credit_pairs}) != self.blocked_credit_count:
    raise InvalidProcessingRequest("blocked_credit_pairs_count_mismatch")
if any(motivo not in MOTIVOS_VALIDOS - {SEM_CREDITO} for *_, motivo in self.deferred_pairs):
    raise InvalidProcessingRequest("invalid_standby_motivo")
```

Em `build_processing_plan` (linha 486-601), logo após obter `result` do motor (linha 535-547) e antes de calcular `deferred_count`/`blocked_credit_count` (linha 554-555):

```python
preteridos_motivo = result.get("preteridos_motivo", {})
pairs_by_order: dict[int, list[str]] = defaultdict(list)
for nr, code in eligible:
    pairs_by_order[nr].append(code)

deferred_pairs = tuple(
    (
        nr, code,
        _pair_channel(eligible[(nr, code)]).value,
        preteridos_motivo.get((nr, code), SEM_ESTOQUE),
    )
    for nr, code in sorted(result["preteridos"])
)
blocked_credit_pairs = tuple(
    (nr, code, _pair_channel(eligible[(nr, code)]).value)
    for nr in sorted(set(result["bloqueados_credito"]))
    for code in sorted(pairs_by_order.get(nr, ()))
)
```

Todo `pair` em `result["preteridos"]` está garantidamente em `eligible` (é subconjunto de `flat`, que é montado só a partir de `eligible.values()`), então `eligible[(nr, code)]` nunca levanta `KeyError` e `_pair_channel(...)` nunca retorna `None` (já validado por `_eligible_pairs`). `defaultdict` já está importado no topo do arquivo.

`plan_hash` (linha 584-594) — seguindo a recomendação já registrada em `ARCHITECTURE.md` §4 — passa a incluir os dois campos novos (ordenados, já são), para que uma divergência silenciosa no motivo persistido invalide o hash de replay:

```python
"deferredPairs": [list(t) for t in deferred_pairs],
"blockedCreditPairs": [list(t) for t in blocked_credit_pairs],
```

E o `return PlanDraft(...)` (linha 595-601) passa `deferred_pairs=deferred_pairs, blocked_credit_pairs=blocked_credit_pairs`.

**Por que o fan-out de crédito roda aqui, não no writer (resolve a "Claude's Discretion" do CONTEXT.md):** `eligible` já está em memória, sem I/O, no exato ponto em que a informação hoje é descartada. Fazer o fan-out em SQL no writer exigiria uma consulta adicional para "todos os produtos elegíveis daquele pedido nesta rodada" — dado que já existe de graça em Python. Mantém o writer burro (só faz upsert das linhas prontas), consistente com o resto do desenho do `processing/`.

## Standard Stack

Nenhuma biblioteca nova. Reutiliza integralmente o que já está em uso:

| Componente | Já usado em | Papel nesta fase |
|---|---|---|
| `sqlalchemy.dialects.postgresql.insert` (`pg_insert`) | `repository.py::store_plan`, `create_spec` | Upsert de `pedido_standby_motivo`, incluindo o incremento `execucoes_consecutivas = pedido_standby_motivo.execucoes_consecutivas + 1` via `set_={...}` (SQLAlchemy Core, não SQL cru) |
| `sqlalchemy.delete` + `tuple_(...).in_(...)` | `repository.py::_assert_plan_still_current` (linha 289-290) | Limpeza em `apply_pairs` |
| `sqlalchemy.text()` | `repositorio_snapshot.py` (INSERTs grandes) | Limpeza órfã no full refresh (`DELETE ... WHERE NOT EXISTS`) |
| Alembic manual (`op.create_table`) | todas as migrations | Tabela nova `pedido_standby_motivo` |

**Version verification:** não aplicável — sem pacote novo a versionar.

## Architecture Patterns

### Diagrama de fluxo

```
processar_pedidos (motor)
  │
  ├─ furo de grade (2 call sites, 1 por modo) ──► preteridos_motivo[par] = 'furo_grade'
  ├─ _commitar_par (não gerou_or)               ──► preteridos_motivo[par] = 'sem_estoque'
  ├─ sem_credito_nrs                             ──► bloqueados_credito (por nr_pedido, já existia)
  │
  ▼
build_processing_plan (processing/domain.py)
  │
  ├─ fan-out bloqueados_credito × eligible[nr]  ──► blocked_credit_pairs (nr, cd, canal)
  ├─ preteridos + preteridos_motivo              ──► deferred_pairs (nr, cd, canal, motivo)
  │
  ▼
PlanDraft { rows, deferred_pairs, blocked_credit_pairs, plan_hash (inclui os 2 novos) }
  │
  ▼
_plan_once (service.py)
  │
  ├─ processing.store_plan(draft)                (linha 260-264, existente)
  ├─ processing.record_standby_reasons(           ◄── NOVA chamada, MESMA transação,
  │     job_id, deferred_pairs, blocked_credit_pairs, recorded_at)   ANTES do commit (linha 276)
  │
  ▼ (commit)
pedido_standby_motivo (tabela nova) ──► upsert por (nr_pedido, cd_prod_cor), execucoes_consecutivas++
  │
  ├─ apply_pairs (writer) ──► DELETE da linha quando o par vira OR
  └─ reconstruir_pedido_produto_read (full refresh, a cada 2h) ──► DELETE de linhas órfãs
```

### Recommended Project Structure

```
app/modules/pedidos/
├── domain/
│   ├── standby_motivo.py          # NOVO — vocabulário canônico (3 constantes)
│   └── motor_adequacao.py         # editado — preteridos_motivo
├── infrastructure/
│   └── models.py                  # editado — + PedidoStandbyMotivo (mesmo arquivo do EstoqueVirtual)
├── processing/
│   ├── domain.py                   # editado — PlanDraft + build_processing_plan
│   ├── application/
│   │   ├── ports.py                 # editado — + ProcessingRepository.record_standby_reasons
│   │   └── service.py               # editado — 1 chamada nova em _plan_once
│   └── infrastructure/
│       └── repository.py            # editado — + record_standby_reasons, + DELETE em apply_pairs
alembic/versions/
└── 032_pedido_standby_motivo.py     # NOVO — migration isolada
```

**Onde colocar o modelo ORM novo:** `app/modules/pedidos/infrastructure/models.py` (o MESMO arquivo do `EstoqueVirtual`), **não** em `processing/infrastructure/models.py`. Justificativa (confirmada lendo os dois arquivos): `processing/infrastructure/models.py` só contém tabelas do ciclo de vida do job durável (`pedido_processamentos`, `pedido_processamento_plan`, ambas com `ForeignKey("durable_jobs.id", ondelete="CASCADE")`); `pedidos/infrastructure/models.py` é onde vivem as projeções recalculáveis independentes do job (`EstoqueVirtual`) — exatamente o precedente que o `16-CONTEXT.md` e o `ARCHITECTURE.md` §3.2 apontam para esta tabela. Esse arquivo usa o estilo `Column(...)` (não `Mapped[...]`/`mapped_column`) — seguir o estilo local do arquivo, não o de `processing/infrastructure/models.py`.

### Pattern: upsert com incremento condicional (STANDBY-06)

```python
# Source: padrão já usado em repository.py::store_plan (pg_insert) e
# repositorio_estoque_virtual.py (ON CONFLICT DO UPDATE ... RETURNING)
from sqlalchemy.dialects.postgresql import insert as pg_insert

async def record_standby_reasons(
    self,
    *,
    job_id: UUID,
    deferred_pairs: Sequence[tuple[int, str, str, str]],
    blocked_credit_pairs: Sequence[tuple[int, str, str]],
    recorded_at: datetime,
) -> None:
    values: list[dict[str, Any]] = [
        {
            "nr_pedido": nr, "cd_prod_cor": code, "canal": canal,
            "motivo": motivo, "job_id": job_id, "atualizado_em": recorded_at,
        }
        for nr, code, canal, motivo in deferred_pairs
    ] + [
        {
            "nr_pedido": nr, "cd_prod_cor": code, "canal": canal,
            "motivo": SEM_CREDITO, "job_id": job_id, "atualizado_em": recorded_at,
        }
        for nr, code, canal in blocked_credit_pairs
    ]
    if not values:
        return
    stmt = pg_insert(PedidoStandbyMotivo).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            PedidoStandbyMotivo.nr_pedido,
            PedidoStandbyMotivo.cd_prod_cor,
        ],
        set_={
            "canal": stmt.excluded.canal,
            "motivo": stmt.excluded.motivo,
            "job_id": stmt.excluded.job_id,
            "execucoes_consecutivas": (
                PedidoStandbyMotivo.execucoes_consecutivas + 1
            ),
            "atualizado_em": stmt.excluded.atualizado_em,
        },
    )
    await self._db.execute(stmt)
```

Isso gera SQL equivalente a `SET execucoes_consecutivas = pedido_standby_motivo.execucoes_consecutivas + 1` — Postgres permite referenciar a coluna da tabela-alvo (valor PRÉ-update) dentro do `SET` de um `ON CONFLICT DO UPDATE` sem nenhum truque adicional; **não precisa de leitura prévia nem de `text()` cru**. Isto responde diretamente à pergunta do additional_context sobre "como isso fica em SQL puro" — a resposta é que não precisa ser SQL puro: SQLAlchemy Core já expressa isso naturalmente porque `set_` aceita qualquer expressão de coluna, incluindo a própria coluna do modelo somada a uma constante.

**Por que os dois lotes (`deferred_pairs` + `blocked_credit_pairs`) podem ir em UM único `INSERT ... VALUES (...)`:** estruturalmente nunca colidem no mesmo par dentro da mesma execução — um pedido bloqueado por crédito nunca entra no laço principal que gera `preteridos` (`sem_credito_nrs` é excluído de `ordens_credito` antes do laço de prioridade, `motor_adequacao.py:687`), então `(nr_pedido, cd_prod_cor)` nunca aparece simultaneamente nos dois grupos — evita o erro do Postgres "ON CONFLICT DO UPDATE command cannot affect row a second time".

### Pattern: contador agnóstico ao motivo (STANDBY-06)

**Decisão fechada:** o contador **não** reseta quando o motivo muda (ex. estava `sem_estoque`, virou `sem_credito` numa rodada seguinte) — ele conta "quantas execuções seguidas este PAR está em ALGUM stand-by", agnóstico ao motivo específico, seguindo a leitura literal do requirement ("um par está em stand by"). Ele só reseta quando a LINHA é apagada (par saiu de stand-by de verdade — virou OR ou desapareceu do full refresh) e depois reaparece em uma execução futura — nesse caso o `INSERT` (não o `UPDATE`) cria uma linha nova com `execucoes_consecutivas = 1` via o `DEFAULT 1` da coluna. `[ASSUMED]` — decisão de leitura de negócio, não travada por nenhum documento; ver Assumptions Log A2.

### Anti-Patterns to Avoid

- **Redigitar as 3 strings de motivo em mais de um arquivo:** usar sempre as constantes de `domain/standby_motivo.py`. Foi o vetor exato do risco descrito abaixo.
- **Fazer o fan-out de crédito no writer (SQL):** informação já pronta em `eligible` no domínio; buscar de novo no banco duplicaria uma fonte de verdade.
- **Chamar `record_standby_reasons` depois de `unit_of_work.commit()`:** quebra a atomicidade com `store_plan` — ver "Riscos de execução".

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Upsert com incremento condicional | Leitura prévia (`SELECT ... FOR UPDATE` seguido de `UPDATE`/`INSERT` manual) | `pg_insert(...).on_conflict_do_update(set_={"col": Model.col + 1})` | Atômico numa única instrução, sem round-trip extra, sem race entre leitura e escrita |
| Vocabulário de motivo duplicado | Strings soltas em cada arquivo que precisa do valor | `domain/standby_motivo.py` (3 constantes) | Único lugar de verdade — motor, `processing/domain.py` e a migration referenciam a mesma fonte conceitual (a migration não pode *importar* Python, mas o CHECK constraint deve literalmente copiar os 3 valores desta constante, comentário cruzado) |

**Key insight:** esta fase não introduz nenhum problema de domínio novo que justifique biblioteca externa — é inteiramente extensão de padrões já em uso no próprio repositório (SQLAlchemy Core, Alembic manual).

## Common Pitfalls

### Pitfall 1: CHECK constraint com só 2 valores
**O que dá errado:** a migration ser escrita antes de alguém reler `furo_de_grade.py`/`motor_adequacao.py` linha a linha, replicando o DDL de 2 valores do `ARCHITECTURE.md` §3.2 (que foi escrito ANTES do gap ser descoberto).
**Por que acontece:** o documento de pesquisa mais citado (`ARCHITECTURE.md`) contém um DDL desatualizado que ainda lista `motivo IN ('sem_credito', 'sem_estoque')` — quem copiar o SQL de lá sem ler este RESEARCH.md primeiro herda o bug.
**Como evitar:** o CHECK da migration desta fase DEVE ser `motivo IN ('sem_credito', 'sem_estoque', 'furo_grade')` — 3 valores — desde o primeiro `CREATE TABLE`. Nunca alterar o CHECK depois de dados em produção.
**Sinais de alerta:** qualquer teste que insira uma linha com `motivo='furo_grade'` e receba `IntegrityError`/`CheckViolation`.

### Pitfall 2: `record_standby_reasons` depois do commit
**O que dá errado:** se a chamada nova em `_plan_once` (service.py) for inserida DEPOIS de `await unit_of_work.commit()` (linha 276), um crash entre as duas escritas deixa o plano congelado (`planning_state = 'planned'`) SEM nenhuma linha em `pedido_standby_motivo` — e como `_plan_once` retorna cedo sempre que `planning_state is PlanningState.PLANNED` (linha 199-201), NUNCA existe uma nova tentativa que refaça a escrita perdida.
**Por que acontece:** parece "seguro" separar as duas chamadas porque `store_plan` já tem sua própria transação interna consistente; mas a atomicidade entre as duas SÓ existe se ambas rodarem na mesma sessão/transação, ANTES do commit único que já existe no fluxo.
**Como evitar:** inserir a chamada logo após `spec = await processing.store_plan(...)` (linha 260-264) e ANTES de `await unit_of_work.commit()` (linha 276) — mesma sessão (`self._db`), mesma transação implícita.
**Sinais de alerta:** teste de integração que simula crash entre as duas chamadas (injetando exceção) deve mostrar QUE NADA foi commitado — nem o plano nem o motivo.

### Pitfall 3: contador dessincronizado do full refresh
**O que dá errado:** se a limpeza órfã em `reconstruir_pedido_produto_read` rodar ANTES dos dois INSERTs (pending/erp) da mesma função, ela vai comparar contra uma `pedido_produto_read` ainda com o conteúdo ANTIGO (ou vazia, se já passou pelo `DELETE FROM pedido_produto_read` da linha 256) — apagando linhas de stand-by que na verdade continuam válidas.
**Por que acontece:** a função já faz `DELETE FROM pedido_produto_read` (linha 256) antes de reinserir — um desenvolvedor apressado pode inserir a limpeza de `pedido_standby_motivo` logo depois desse DELETE, antes dos 2 INSERTs subsequentes (linha 257-258).
**Como evitar:** a limpeza de `pedido_standby_motivo` deve rodar DEPOIS de `pending = await db.execute(text(_PENDING_READ_INSERT_SQL))` e `erp = await db.execute(text(_ERP_READ_INSERT_SQL))` (linha 257-258), nunca antes.
**Sinais de alerta:** teste que popula `pedido_standby_motivo` com um par que também está em `pedido_produto_read` (source='pending') e roda o full refresh — a linha deve sobreviver.

## Code Examples

### Migration (padrão do repositório — `op.create_table` manual, sem autogenerate)

```python
# Source: padrão observado em alembic/versions/017_estoque_virtual.py,
# 020_pedido_produto_read.py, 031_auth_users_sso_provider.py
"""tabela pedido_standby_motivo (STANDBY-01/06)

Revision ID: 032
Revises: 031
Create Date: 2026-08-20

Projeção recalculável, no mesmo padrão de `estoque_virtual`/`pedido_produto_read`:
grão par (nr_pedido, cd_prod_cor), fora do ciclo de vida do job durável
(`pedido_processamentos`/`pedido_processamento_plan` são efêmeros, com
ON DELETE CASCADE a partir de `durable_jobs` e sujeitos a expurgo).

3 motivos desde o início (`sem_credito`, `sem_estoque`, `furo_grade`) — mudar o
CHECK depois de dados em produção é migration extra evitável (ver 16-RESEARCH.md
§ "Gap de desenho resolvido").

`execucoes_consecutivas` (STANDBY-06): incrementado a cada upsert em que o par
já existia, agnóstico ao motivo específico; reseta implicitamente quando a
linha é apagada (par saiu de stand-by) e reaparece depois (INSERT novo,
DEFAULT 1).

`downgrade()` só remove esta projeção recalculável — nenhuma tabela fonte é
alterada, e um re-upgrade a reconstrói vazia (o próximo processamento
repopula).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pedido_standby_motivo",
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("canal", sa.String(length=16), nullable=False),
        sa.Column("motivo", sa.String(length=16), nullable=False),
        sa.Column(
            "execucoes_consecutivas",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("job_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("nr_pedido", "cd_prod_cor"),
        sa.CheckConstraint(
            "canal IN ('Franquia', 'Multimarca')",
            name="ck_psm_canal",
        ),
        sa.CheckConstraint(
            "motivo IN ('sem_credito', 'sem_estoque', 'furo_grade')",
            name="ck_psm_motivo",
        ),
        sa.CheckConstraint(
            "execucoes_consecutivas >= 1",
            name="ck_psm_execucoes_consecutivas",
        ),
    )


def downgrade() -> None:
    op.drop_table("pedido_standby_motivo")
```

**Nota de estilo:** usar `from sqlalchemy.dialects import postgresql` e `postgresql.UUID(as_uuid=True)` (import direto, como em `020_pedido_produto_read.py:35`), não `sa.dialects.postgresql...` (o pseudocódigo acima simplificou o import — o plano de execução deve usar o import explícito). Sem FK para `durable_jobs` (informativo/auditoria — job pode ser expurgado, mesma decisão do `ARCHITECTURE.md` §3.2).

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `build_processing_plan` descarta `preteridos`/`bloqueados_credito`, só conta (`deferred_count`/`blocked_credit_count`) | `PlanDraft` carrega os pares com motivo (`deferred_pairs`/`blocked_credit_pairs`) | Esta fase | Nenhum contrato externo muda (job continua com 4 contadores); só o DTO interno passa a reter mais dado |
| Coluna `motivo` (heurística) só recalculada pós-hoc no estágio `historico` (`repositorio_produtos.py:260-266`) | Fonte real gravada no momento do processamento (Phase 17 troca a heurística por `EXISTS (... pedido_standby_motivo ...)`) | Fora de escopo desta fase (Phase 17) | Fase 16 só produz o dado; a heurística antiga continua rodando até a Phase 17 trocar |

**Deprecated/outdated:** o DDL de `motivo IN ('sem_credito', 'sem_estoque')` em `ARCHITECTURE.md` §3.2 está desatualizado — substituído pelo DDL de 3 valores deste documento.

## Runtime State Inventory

Não aplicável — esta fase não é rename/refactor/migração de nome. É uma tabela nova, aditiva, sem renomear nada existente.

## Testes — impacto na suíte existente

### Ajuste mecânico (quebra sem mudança de comportamento)

| Arquivo | O que quebra | Ajuste necessário |
|---|---|---|
| `app/tests/test_pedidos_processing_service.py` (fakes em ~linha 611 e ~linha 725, `class Fake...Repository` implementando só `store_plan`) | `AttributeError: 'Fake...Repository' object has no attribute 'record_standby_reasons'` assim que `service.py::_plan_once` passar a chamar o novo método | Adicionar um método `record_standby_reasons` (no-op ou gravando em lista, conforme o teste precisar) a cada fake que hoje implementa `ProcessingRepository` estruturalmente |
| `app/tests/test_pedidos_processing_repository.py` (fixture `processing_database`, linha 72-92, lista de tabelas limpas) | Nenhuma falha imediata (a tabela nova simplesmente não é limpa entre testes), mas dados de stand-by de um teste podem vazar para o próximo se não for adicionada à lista | Adicionar `PedidoStandbyMotivo` à tupla de modelos limpos em `cleanup()` |

### Comportamento novo (testes que ainda não existem)

| Teste novo | Prova |
|---|---|
| `test_pedidos_motor.py` (ou novo `test_pedidos_motor_standby_motivo.py`) — furo de grade em `SEM_ADEQUAR` e em `ADEQUAR` | `resultado["preteridos_motivo"][par] == "furo_grade"` nos dois modos |
| idem — falta de estoque genérica (sem furo) | `resultado["preteridos_motivo"][par] == "sem_estoque"` |
| idem — orçamento de corte excedido (ADEQUAR) | `resultado["preteridos_motivo"][par] == "sem_estoque"` (mesmo bucket que falta de estoque simples) |
| `test_pedidos_processing_domain.py` — `build_processing_plan` com par furo/estoque/crédito misturados | `draft.deferred_pairs` contém a tupla `(nr, cd, canal, motivo)` correta; `draft.blocked_credit_pairs` tem fan-out por TODOS os produtos elegíveis do pedido bloqueado (STANDBY-02) |
| idem — `PlanDraft.__post_init__` | Levanta `InvalidProcessingRequest` se um par aparecer em `deferred_pairs` E `blocked_credit_pairs` simultaneamente (guarda defensiva) |
| idem — `plan_hash` | Dois `PlanDraft` com `rows` idênticas mas `deferred_pairs`/`blocked_credit_pairs` diferentes têm `plan_hash` diferente |
| `test_pedidos_processing_repository.py` — `record_standby_reasons` (DB real) | Primeira chamada faz INSERT com `execucoes_consecutivas=1`; segunda chamada sobre o MESMO par faz UPDATE com `execucoes_consecutivas=2`; motivo pode mudar entre chamadas (estoque→crédito) sem duplicar linha |
| idem — `apply_pairs` (DB real) | Depois de `apply_pairs` gravar a OR de um par, a linha correspondente em `pedido_standby_motivo` não existe mais |
| `test_pedidos_read_projection.py` (já existe, cobre `reconstruir_pedido_produto_read`) — novo caso | Um par em `pedido_standby_motivo` cujo par NÃO está mais em `pedido_produto_read` (source='pending') é apagado pelo full refresh; um par que AINDA está sobrevive |
| Integração `_plan_once` (service, DB real ou fake com sessão real) | `store_plan` + `record_standby_reasons` são atômicos — simular exceção entre as duas chamadas e confirmar que NADA foi commitado (Pitfall 2) |

**Linha de base a preservar:** 826 passed, 16 skipped, 1 failed (falha conhecida pré-existente em `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql`, teardown asyncpg — não desta fase). Rodar sempre via Docker: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | O ramo `pares_sem_canal` do motor deve popular `preteridos_motivo` com `'sem_estoque'` por defensividade, mesmo sendo código morto no caminho real de `build_processing_plan` | Gap de desenho resolvido § "Achado adicional" | Baixo — se pulado, só quebra a invariante `len(preteridos) == len(preteridos_motivo)` para chamadores hipotéticos futuros fora de `build_processing_plan`; nenhum impacto no caminho de produção atual |
| A2 | O contador `execucoes_consecutivas` é agnóstico ao motivo (não reseta quando o motivo muda, só quando a linha é apagada) | Architecture Patterns § "contador agnóstico ao motivo" | Médio — se a mantenedora quiser reset por mudança de motivo, a query de upsert muda (precisa comparar `motivo` antigo vs novo antes de decidir incrementar ou resetar para 1) — é uma migration/query diferente, não uma mudança cosmética |

## Open Questions (RESOLVED)

1. **Índice em `motivo` para filtro (Phase 17)**
   - O que sabemos: Phase 17 vai filtrar por motivo (STANDBY-05); a tabela nova não tem índice em `motivo` isoladamente (só a PK composta `(nr_pedido, cd_prod_cor)`).
   - O que é incerto: se o volume esperado de linhas (pares em stand-by simultâneos, bem abaixo do teto de 100k pares do preflight) justifica um índice dedicado agora ou se é prematuro.
   - Recomendação: não adicionar nesta fase — a tabela tende a ser pequena (só pares atualmente parados, não histórico); se a Phase 17 medir lentidão real no filtro, adicionar `CREATE INDEX ix_psm_motivo ON pedido_standby_motivo (motivo)` como migration separada naquela fase.

## Environment Availability

Não aplicável — mudança 100% interna ao Postgres/Alembic/pytest já disponíveis no ambiente Docker do projeto. Nenhuma dependência externa nova.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.3.0+ / pytest-asyncio 0.24.0+ |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_motor.py app/tests/test_pedidos_motor_furo_grade.py app/tests/test_pedidos_processing_domain.py -q` |
| Full suite command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STANDBY-01 (critério 1 — motivo consultável sem inflar o job) | `record_standby_reasons` grava 1 linha por par preterido/bloqueado, sem alterar `ProcessingResult.as_dict()` (4 contadores) | integration (DB real) | `pytest app/tests/test_pedidos_processing_repository.py -k record_standby_reasons -x` | ❌ Wave 2 |
| STANDBY-01 (motivo furo de grade distinto) | `preteridos_motivo[par] == 'furo_grade'` nos 2 modos | unit | `pytest app/tests/test_pedidos_motor.py -k furo_grade_motivo -x` | ❌ Wave 1 |
| STANDBY-06 (critério 2 — contador incrementa, não duplica) | 2ª chamada de `record_standby_reasons` sobre o mesmo par faz UPDATE com `execucoes_consecutivas` incrementado | integration (DB real) | `pytest app/tests/test_pedidos_processing_repository.py -k execucoes_consecutivas -x` | ❌ Wave 2 |
| Critério 3 (par que recebe OR some da consulta) | `apply_pairs` apaga a linha de `pedido_standby_motivo` do par que virou OR | integration (DB real) | `pytest app/tests/test_pedidos_processing_repository.py -k apply_pairs_removes_standby -x` | ❌ Wave 2 |
| Critério 4 (full refresh limpa órfãos) | `reconstruir_pedido_produto_read` apaga linha cujo par não está mais em `pedido_produto_read` source='pending' | integration (DB real) | `pytest app/tests/test_pedidos_read_projection.py -k standby_orphan_cleanup -x` | ❌ Wave 2 |

### Sampling Rate

- **Por commit de task:** rodar o subconjunto relevante (motor puro nas tasks de Wave 1; repositório/migration nas tasks de Wave 2/3)
- **Por merge de wave:** `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` (suíte completa)
- **Gate de fase:** suíte completa verde (826+novos passed, 16 skipped, 1 failed conhecida) antes de `/gsd-verify-work`

### Wave 0 Gaps

- [ ] Nenhum framework/fixture novo necessário — `processing_database` (fixture existente em `test_pedidos_processing_repository.py`) só precisa incluir `PedidoStandbyMotivo` na lista de cleanup.
- [ ] Nenhuma instalação de dependência de teste necessária.

*(Gaps são mínimos porque toda a infraestrutura de teste — fixture de DB real, padrão de fakes de porto — já existe e só precisa de extensão pontual, listada na seção "Testes" acima.)*

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Não | Esta fase não expõe rota HTTP nova — é 100% interno (motor + persistência); autenticação já é responsabilidade da Phase 17 (consulta REST) |
| V3 Session Management | Não | Idem |
| V4 Access Control | Não | Idem |
| V5 Input Validation | Sim | `PlanDraft.__post_init__` valida o vocabulário de `motivo` em Python (`MOTIVOS_VALIDOS`) ANTES de qualquer INSERT — dupla validação com o `CHECK` constraint da migration (defesa em profundidade, não redundância inútil: pega o bug ANTES de bater no banco, com mensagem de erro mais específica) |
| V6 Cryptography | Não | Nenhum dado sensível novo (motivo de stand-by não é PII nem segredo) |

### Known Threat Patterns for este stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Injeção via valor de `motivo`/`canal` construído dinamicamente | Tampering | Todos os valores inseridos vêm de constantes controladas pelo código (`domain/standby_motivo.py`, `ProcessingChannel`), nunca de input de usuário — não há superfície de injeção nesta fase |
| Corrupção de contador por race entre 2 execuções concorrentes do mesmo job | Tampering | Já mitigado estruturalmente: o lock advisório de sessão (`ensure_held`, `claim_processing_job`) garante processamento serializado — nunca 2 planos concorrentes gravando o mesmo par simultaneamente |

## Riscos de execução

1. **CHECK constraint com 2 valores (não 3) — o risco mais provável de "descoberta tardia".** O `ARCHITECTURE.md` §3.2, o documento mais citado pelo `16-CONTEXT.md`, contém um DDL desatualizado com `motivo IN ('sem_credito', 'sem_estoque')`. Uma Task de "criar a migration" que copie aquele DDL sem ler este documento primeiro reproduz o bug que a Fase 16 existe para resolver. **Mitigação recomendada para o plano:** a Task de migration deve citar EXPLICITAMENTE este `16-RESEARCH.md` (não o `ARCHITECTURE.md`) como fonte do DDL, e o `PlanDraft.__post_init__` (validação Python, ver acima) funciona como um segundo gate que pegaria a inconsistência em teste, ANTES de qualquer INSERT real bater no CHECK do banco.
2. **`record_standby_reasons` sendo escrita fora da transação de `store_plan`** (ver Pitfall 2 acima) — perda silenciosa e permanente do motivo para um plano que já ficou congelado, sem re-tentativa possível. Mitigação: Task explícita de "wiring em `_plan_once`" deve ter um teste de integração que injeta falha entre as duas chamadas e confirma rollback total.
3. **Esquecer o fan-out de crédito e persistir só 1 linha por pedido bloqueado** (em vez de 1 linha por produto elegível) — quebraria STANDBY-02 silenciosamente (a Phase 17 mostraria a tag só num produto do cliente, não em todos). Mitigação: teste explícito com pedido de 2+ produtos bloqueado por crédito, verificando `len(blocked_credit_pairs) == número de produtos elegíveis daquele pedido`.
4. **Motivo de furo de grade sendo persistido como a STRING LONGA** (`"Furo de grade: tamanho M sem estoque reservável"`) em vez do valor canônico (`"furo_grade"`) — quebraria o CHECK constraint imediatamente (bom, falha rápido) OU, pior, se alguém alargar a coluna para caber a string longa "só para não dar erro", perde a normalização e quebra o filtro da Phase 17. Mitigação: `preteridos_motivo` deve conter SEMPRE os valores curtos de `domain/standby_motivo.py`, nunca a string de `motivo_stand_by` (que continua existindo em paralelo, com outro propósito — texto para log/depuração, não para a coluna).

## Sources

Todas as afirmações vieram de leitura direta do código e das migrations nesta sessão (2026-08-20) — domínio interno, sem ecossistema de terceiros a pesquisar.

### Primary (HIGH confidence)
- `app/modules/pedidos/domain/motor_adequacao.py` — íntegro, linha a linha (call sites de `tem_furo_de_grade`, `_commitar_par`, `processar_pedidos`/`_processar_pedidos_canal`)
- `app/modules/pedidos/domain/furo_de_grade.py` — íntegro
- `app/modules/pedidos/processing/domain.py` — íntegro (`PlanDraft`, `build_processing_plan`, `_eligible_pairs`, `_pair_channel`)
- `app/modules/pedidos/processing/application/ports.py` — íntegro
- `app/modules/pedidos/processing/application/service.py` — trecho de `_plan_once` (linha 184-281)
- `app/modules/pedidos/processing/infrastructure/repository.py` — íntegro
- `app/modules/pedidos/processing/infrastructure/models.py` — íntegro (estilo `Mapped[...]`)
- `app/modules/pedidos/infrastructure/models.py` — trecho (`EstoqueVirtual`, estilo `Column(...)`)
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py` — íntegro (`reconstruir_pedido_produto_read`)
- `alembic/versions/031_auth_users_sso_provider.py`, `020_pedido_produto_read.py`, `017_estoque_virtual.py` — padrão de estilo de migration
- `uv run alembic heads` via Docker — confirmado head real = `031`
- `app/tests/test_pedidos_motor.py`, `test_pedidos_motor_furo_grade.py`, `test_pedidos_processing_domain.py`, `test_pedidos_processing_repository.py`, `test_pedidos_processing_service.py` — grep de raio de impacto e leitura de trechos
- `.planning/research/v1.3/ARCHITECTURE.md` §3 (3.1-3.5), §4 — desenho base (DDL desatualizado no ponto do gap, corrigido aqui)
- `.planning/phases/16-persist-ncia-do-motivo-de-stand-by/16-CONTEXT.md` — decisões travadas e o gap original
- `.planning/REQUIREMENTS.md` — STANDBY-01, STANDBY-06, critérios de sucesso do ROADMAP
- `.planning/ROADMAP.md` §Phase 16 — 4 critérios de sucesso
- `.planning/codebase/TESTING.md` — padrão de teste (pytest, fixtures, Docker)
- `.claude/CLAUDE.md` — convenções de projeto, `alembic/env.py` noqa

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — nenhuma dependência nova, tudo já em uso no repositório
- Architecture (gap de desenho, fan-out, upsert): HIGH — verificado linha a linha, incluindo os 2 call sites exatos de `tem_furo_de_grade` e a prova de que o ramo `sem_canal` é código morto neste caminho
- Pitfalls: HIGH — os 3 pitfalls vêm de leitura direta de código real (não hipotéticos), incluindo o DDL desatualizado do `ARCHITECTURE.md`
- STANDBY-06 (contador agnóstico ao motivo): MEDIUM — desenho técnico é HIGH confidence, mas a decisão de negócio "agnóstico vs. reseta por motivo" é `[ASSUMED]` (A2), não travada por nenhum documento — vale confirmar com a mantenedora se a leitura literal do requirement for insuficiente durante o plano

**Research date:** 2026-08-20
**Valid until:** 30 dias (domínio interno estável — não depende de biblioteca externa com ciclo de release próprio)
