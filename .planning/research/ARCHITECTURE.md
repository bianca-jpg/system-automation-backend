# Architecture Research — Cabeçalho de OR com numeração sequencial

**Domínio:** Persistência de cabeçalho de negócio para Ordem de Reserva (OR), preparando integração futura com ERP Linx
**Pesquisado em:** 2026-08-04
**Confiança:** HIGH — pesquisa fundada na leitura direta do código atual (`repositorio_ordens.py`, `casos_uso.py`, `models.py`, `schemas.py`, `routes.py`, migration 014, frontend `product-grade-detail-modal.tsx`), não em fontes externas. O domínio é o próprio codebase, não um ecossistema de mercado.

## Visão Geral da Solução

Hoje a "OR de negócio" (1 produto × N clientes) só existe montada em runtime: `listar_ordens_reserva` faz `GROUP BY cd_prod_cor` sobre as linhas de `ordens_reserva` (grão `(nr_pedido, cd_prod_cor)`, fixado na migration 014). Não existe nenhuma tabela ou número que identifique essa OR — por isso o frontend usa o literal `255315`.

A mudança introduz uma tabela de cabeçalho **por produto por rodada de geração**, referenciada pelas linhas existentes via FK:

```
┌───────────────────────────────────────────────────────────────────┐
│  USE CASE: executar_adequacao() / executar_sem_adequacao()        │
│  (application/casos_uso.py) — decide QUANDO comitar               │
└───────────────────────┬─────────────────────────────────────────┘
                         │ chama
                         ▼
┌───────────────────────────────────────────────────────────────────┐
│  REPOSITORY: salvar_ordens_reserva()                               │
│  (infrastructure/repositorio_ordens.py) — NUNCA comita             │
│                                                                     │
│  1. agrupa resultados por cd_prod_cor                              │
│  2. para cada produto NOVO na rodada:                              │
│       cabecalho = OrdemReservaCabecalho(cd_prod_cor, tipo)          │
│       db.add(cabecalho); await db.flush()  # <- pega o id sem comitar│
│  3. grava cada OrdemReserva com or_id = cabecalho.id                │
└───────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────────────────┐
│  TABELAS (Postgres)                                                │
│  ordens_reserva_cabecalho (id Identity start=255316, cd_prod_cor,  │
│    tipo, codigo_linx NULL, criada_em)                              │
│  ordens_reserva (nr_pedido, cd_prod_cor, or_id FK NOT NULL, ...)   │
└───────────────────────────────────────────────────────────────────┘
```

O grão de leitura do negócio não muda — continua "1 cabeçalho = 1 produto = o que o Linx recebe". O que muda é que agora existe um `id` de banco para isso, em vez de ele ser reconstruído a cada `GROUP BY`.

## Ponto de Integração 1 — Onde criar os cabeçalhos (flush vs. commit)

**Decisão: dentro do repositório, com `flush()` — não no caso de uso, e sem violar "repositório não comita".**

A convenção documentada em `repositorio_ordens.py` ("Nenhuma [operação] faz commit: quem decide quando commitar é o caso de uso") é sobre **commit**, não sobre **flush**. `flush()` envia o INSERT ao Postgres e materializa o valor gerado pela sequência `Identity` no atributo Python do objeto, mas continua dentro da mesma transação — se o caso de uso não chamar `db.commit()` depois, tudo é revertido junto (incluindo o cabeçalho). Ou seja, `flush()` é uma operação puramente técnica de sincronização com o banco, igual em espírito ao `db.add()` que o repositório já faz hoje; `commit()` é a decisão de negócio de "isso está pronto para ficar visível para todo mundo".

Concretamente, em `repositorio_ordens.py`:

```python
async def _criar_cabecalho(db: AsyncSession, cd_prod_cor: str, tipo: str) -> int:
    """Cria 1 cabeçalho de OR para este produto nesta rodada. NÃO comita —
    apenas flush() para obter o id gerado pela Identity antes de linkar as linhas."""
    from app.modules.pedidos.models import OrdemReservaCabecalho

    cabecalho = OrdemReservaCabecalho(cd_prod_cor=cd_prod_cor, tipo=tipo)
    db.add(cabecalho)
    await db.flush()
    return cabecalho.id


async def salvar_ordens_reserva(db: AsyncSession, resultados: dict, tipo: str) -> None:
    from app.modules.pedidos.models import OrdemReserva

    cabecalho_por_produto: dict[str, int] = {}
    for (nr, cd), itens in resultados.items():
        obj = await db.get(OrdemReserva, (nr, cd))
        if obj is None:
            if cd not in cabecalho_por_produto:
                cabecalho_por_produto[cd] = await _criar_cabecalho(db, cd, tipo)
            db.add(OrdemReserva(
                nr_pedido=nr, cd_prod_cor=cd, tipo=tipo, itens=itens,
                or_id=cabecalho_por_produto[cd],
            ))
        else:
            obj.tipo = tipo
            obj.itens = itens
            # obj.or_id NÃO é tocado: a linha já pertence a um cabeçalho de uma
            # rodada anterior; atualizar seus itens não é "gerar uma OR nova".
```

**Por que aqui e não no caso de uso:**
- `salvar_ordens_reserva` já é o único lugar que decide "esta é uma linha nova (INSERT) vs. uma linha existente (UPDATE)" (o `if obj is None`). O cabeçalho só deve ser criado no caminho de INSERT — decidir isso no caso de uso duplicaria essa lógica de upsert em duas camadas.
- O caso de uso (`executar_adequacao`, `executar_sem_adequacao`) já só passa para `salvar_ordens_reserva` pares que **não** estavam em `ja_processados` — na prática o caminho de UPDATE quase nunca é exercitado, mas o guard acima (`obj.or_id` intocado) é a rede de segurança correta caso isso mude.
- **1 cabeçalho por produto por rodada**, não por par: dentro de uma mesma chamada de `salvar_ordens_reserva` (= uma rodada), vários clientes (pares) do mesmo produto compartilham o cabeçalho recém-criado — por isso o `cabecalho_por_produto` dict de cache local evita criar um cabeçalho por par.
- O `commit()` continua exatamente onde está hoje: uma única vez, no caso de uso, depois de `salvar_ordens_reserva` + `salvar_processados` (ver `executar_adequacao` e `executar_sem_adequacao` atuais). Cabeçalho e linhas entram e saem juntos da transação — não há risco de cabeçalho "orfão" sem linhas, nem linhas sem cabeçalho.

**Onde colocar o model:** `OrdemReservaCabecalho` vai em `app/modules/pedidos/models.py`, junto de `OrdemReserva` — é o mesmo módulo, mesmo agregado de negócio (a OR), e evita criar um módulo novo só para uma tabela. Nenhuma lógica de domínio pura é necessária para a criação do cabeçalho (gerar o `id` é responsabilidade do Postgres via `Identity`, não uma regra de negócio) — por isso não há mudança na camada `domain/`; isso é deliberado e respeita a fronteira já existente entre `domain` (regra pura) e `infrastructure` (persistência).

## Ponto de Integração 2 — Impacto em `carregar_ordens_reserva` (tupla 6→7+ elementos)

**Consumidores atuais (únicos 2 call sites, confirmados por grep em `casos_uso.py`):**

1. `_montar_pedidos` — `for nr_or, cd_or, tipo, itens_or, created_at, aprovado_em in await carregar_ordens_reserva(db):`
2. `listar_ordens_reserva` — `for nr, cd, tipo, itens, created_at, aprovado_em in await carregar_ordens_reserva(db):`

Ambos desestruturam a tupla **por posição**. Isso é o problema real, não o "6 vs 7": qualquer novo campo inserido no meio quebra silenciosamente os dois sites (valores deslizam para a variável errada, sem erro de sintaxe — só comportamento errado em produção). E o campo que falta não é só `or_id`: para expor `codigoLinx` em `GET /ordens-reserva` (feature deste milestone) é preciso trazer também `codigo_linx` do cabeçalho — ou seja, o retorno passa a ter **8 campos**, não 7, tornando a tupla posicional ainda mais arriscada de estender.

**Recomendação: `NamedTuple`, não tupla posicional crua nem `dataclass`.**

```python
class LinhaOrdemReserva(NamedTuple):
    nr_pedido: int
    cd_prod_cor: str
    tipo: str
    itens: list
    created_at: datetime
    aprovado_em: datetime | None
    or_id: int
    codigo_linx: str | None


async def carregar_ordens_reserva(db: AsyncSession) -> list[LinhaOrdemReserva]:
    """Retorna 1 linha por (nr_pedido, cd_prod_cor), com o cabeçalho já joinado."""
    from app.modules.pedidos.models import OrdemReserva, OrdemReservaCabecalho

    rows = (
        await db.execute(
            select(OrdemReserva, OrdemReservaCabecalho.codigo_linx)
            .join(OrdemReservaCabecalho, OrdemReserva.or_id == OrdemReservaCabecalho.id)
            .order_by(OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor)
        )
    ).all()
    return [
        LinhaOrdemReserva(
            r.OrdemReserva.nr_pedido, r.OrdemReserva.cd_prod_cor, r.OrdemReserva.tipo,
            r.OrdemReserva.itens, r.OrdemReserva.created_at, r.OrdemReserva.aprovado_em,
            r.OrdemReserva.or_id, r.codigo_linx,
        )
        for r in rows
    ]
```

Chamadas nos dois sites passam de desestruturação posicional para acesso por atributo:

```python
for linha in await carregar_ordens_reserva(db):
    ...  linha.or_id, linha.codigo_linx, linha.created_at ...
```

**Por que `NamedTuple` e não `dataclass`:**
- É uma projeção 1:1 de uma linha de banco para leitura, imutável por natureza — exatamente o caso de uso canônico de `NamedTuple`. Continua sendo uma tupla de verdade (`isinstance(x, tuple)` é `True`), então qualquer código legado que ainda desempacote por posição num teste antigo continua funcionando durante a transição — reduz o "big bang" da mudança.
- `dataclass(frozen=True)` exigiria `@dataclass` + import extra e não ganha nada em troca aqui (não há métodos, não há necessidade de mutabilidade); é uma opção igualmente válida mas mais verbosa para este caso simples — e a mantenedora é dev iniciante (constraint do PROJECT.md: "soluções simples e didáticas"), então a opção com menos cerimônia vence.
- Zero dependência nova, zero mudança de import fora do stdlib (`typing.NamedTuple`).

**Efeito cascata nos dois consumidores:**

- `_montar_pedidos` (seção B, "ORs geradas pelo app"): hoje agrupa por `nr_or` em `ors_por_pedido`. Precisa passar a acumular também `or_id` (é o mesmo para todos os itens de um mesmo produto dentro do pedido, mas pode variar entre produtos diferentes do mesmo pedido — então o carimbo de `nrOr` é por **item**, não por card). O carimbo "itens de `GET /pedidos` carimbados com `nrOr`" (feature do milestone) acontece em `_format_items`: adicionar um parâmetro opcional `or_id: int | None = None` a `_format_items` e escrever `"nrOr": or_id` em cada dict retornado. **Importante:** como `GET /pedidos` (`listar_pedidos`) não usa `response_model` — devolve `dict` cru via `JSONResponse` — este carimbo é só uma chave nova no dict, **sem** tocar `schemas.py`.
- `listar_ordens_reserva` (usado por `GET /ordens-reserva`, que **tem** `response_model=list[OrdemReservaOut]`): aqui sim é preciso estender `schemas.py` — `OrdemReservaOut` ganha `nrOr: int` e `codigoLinx: str | None = None`; `geradaEm: int` (epoch ms, mesmo padrão de `processadoEm`/`aprovadoEm`) fica melhor lido do `criada_em` do cabeçalho do que reconstruído a partir do `min(created_at)` das linhas — já que todo o grupo de linhas do mesmo produto na mesma rodada nasce na mesma transação, ambos os valores coincidem na prática, mas `criada_em` do cabeçalho é a fonte canônica e explícita para esse campo, então é ela que deve alimentar `geradaEm`.

## Ordem de Build Recomendada

A ordem sugerida no prompt (models → migration → escrita → leitura → schemas → frontend) está correta; o detalhamento das dependências entre passos:

1. **`models.py`** — declarar `OrdemReservaCabecalho` (o alvo do schema) e adicionar a coluna `or_id` em `OrdemReserva`. Vem primeiro porque a migration seguinte é escrita manualmente neste projeto (ver migration 014 — não é autogerada), e o autor precisa ter o modelo-alvo claro antes de escrever o DDL equivalente à mão.
2. **Migration (015)** — cria `ordens_reserva_cabecalho` (com `Identity(start=255316)`); adiciona `or_id` em `ordens_reserva` em **duas etapas dentro da mesma migration** (nullable → backfill → `NOT NULL` + FK) — ver pitfall dedicado abaixo. Só depois desta migration rodar é que o `or_id` existe no banco e o passo 3 pode ser escrito/testado contra um schema real.
3. **Escrita** (`repositorio_ordens.py::salvar_ordens_reserva` + `_criar_cabecalho`) — depende do schema existir (passo 2). Sem isto, nenhuma OR nova ganha `or_id`, então não há dado real para o passo 4 ler.
4. **Leitura** (`repositorio_ordens.py::carregar_ordens_reserva` + `LinhaOrdemReserva` + os dois consumidores em `casos_uso.py`) — depende do passo 3 já estar gerando `or_id` corretamente (senão o join do passo 4 lê `NULL`/dados incoerentes de teste).
5. **Schemas** (`schemas.py::OrdemReservaOut` + `ClienteDaOrdem`) — depende do passo 4 já produzir `nrOr`/`codigoLinx`/`geradaEm` em Python; adicionar os campos no schema antes disso só criaria `ValidationError` ou campos fantasma.
6. **Testes de backend** — "1 OR por produto por rodada" (feature explícita do milestone) só é testável depois dos passos 3–4 existirem; mas nada impede escrever os testes em paralelo ao passo 3 (TDD) se a mantenedora preferir — a única dependência dura é migration (passo 2) antes de qualquer teste que toque o banco.
7. **Frontend** (`product-grade-detail-modal.tsx` + tipos em `entities/pedido`) — só faz sentido depois do passo 5 expor os campos via API; é o único passo que cruza para o outro repositório git (`frontend`), e é aditivo puro (contrato só ganha campos, constraint do PROJECT.md), então pode ser feito em uma branch/PR separada sem coordenação apertada com o backend além de "os campos já existem em produção/staging".

**Pitfall a registrar na migration 015:** `or_id` é `NOT NULL` desde o dia 1 (decisão já tomada no PROJECT.md, para não espalhar `if or_id is None` pelo código). Mas a tabela `ordens_reserva` já tem linhas hoje. Adicionar uma coluna `NOT NULL` a uma tabela populada falha se feito num único `ALTER TABLE ADD COLUMN ... NOT NULL` sem default. O padrão seguro (e o que a migration 014 já demonstra ao lidar com o schema drift) é a sequência clássica dentro da mesma migration: `add_column` nullable → `UPDATE`/backfill retroativo (criar 1 cabeçalho por produto distinto encontrado nas linhas legadas, e apontar o `or_id` das linhas para ele) → `alter_column(nullable=False)` → `create_foreign_key`. **Decisão de agrupamento no backfill ainda aberta:** como as linhas legadas não têm um marcador confiável de "rodada" (isso é justamente o que a migration 014 já perdeu ao descartar dados no caminho "banco antigo"), a estratégia mais simples e segura é 1 cabeçalho por `cd_prod_cor` distinto presente em `ordens_reserva` — não por `(cd_prod_cor, tipo, data)`. Isso é uma decisão de produto/dado que deve ser confirmada explicitamente na fase de implementação da migration, não assumida.

## Ponto de Integração 3 — Preparando para a integração Linx sem refatorar depois

O objetivo (explícito no PROJECT.md) é: quando o barramento existir, a integração só precisa **ler cabeçalho + linhas e preencher `codigo_linx`** — nenhuma migração adicional nas tabelas de linha, nenhum retrabalho no grão. Para isso se sustentar de fato, hoje é preciso desenhar 3 coisas:

**1. O cabeçalho já é o "aggregate root" certo para o outbox futuro.** `codigo_linx` nullable na própria tabela de cabeçalho já funciona como um status implícito hoje: `NULL` = "ainda não enviado/confirmado no Linx", não-nulo = "confirmado". Isso evita precisar de uma coluna de status agora (decisão já tomada como Out of Scope) sem fechar a porta para status mais ricos depois — uma eventual tabela `integracao_linx_eventos` ou colunas extra (`status_envio`, `tentativas`, `erro`) seriam uma migration **aditiva só na tabela de cabeçalho**, nunca em `ordens_reserva` (linhas). O barramento opera no grão de produto (cabeçalho), não no grão de cliente (linha) — igual ao Linx hoje.

**2. Criar o seam de publicação agora, mesmo sem publisher.** Recomenda-se que `salvar_ordens_reserva` **retorne** a lista de ids de cabeçalho recém-criados na rodada (hoje a função retorna `None`):

```python
async def salvar_ordens_reserva(db: AsyncSession, resultados: dict, tipo: str) -> list[int]:
    ...
    return list(cabecalho_por_produto.values())
```

Isso é uma mudança mínima e aditiva (não quebra nada, já que ninguém hoje usa o retorno) que cria exatamente o ponto de extensão que o outbox pattern vai precisar: o caso de uso (`executar_adequacao`/`executar_sem_adequacao`) passa a ter, em memória, a lista de cabeçalhos novos **antes do commit**. Quando o barramento existir, o mesmo caso de uso poderá chamar, na mesma transação, algo como `await registrar_evento_outbox(db, cabecalho_ids, tipo_evento="OR_CRIADA")` — a atomicidade "OR e evento nascem juntos ou nenhum dos dois nasce" (a garantia central do transactional outbox) só é possível porque o commit continua sendo um só, no caso de uso, e o repositório continua sem comitar. Se essa convenção for violada agora (ex.: repositório comitando sozinho), a garantia de atomicidade da futura integração fica impossível de obter sem refatorar o fluxo de escrita inteiro — por isso vale reforçar a convenção precisamente neste milestone, não relaxá-la.

**3. Isolar a futura integração em módulo próprio, não dentro de `pedidos`.** Seguindo o mesmo padrão já usado para Databricks (`app/modules/ingestao/`), a integração Linx deve nascer como `app/modules/integracao_linx/` (ou nome equivalente) com sua própria camada `domain/application/infrastructure`, e **consumir** dados de `pedidos` — nunca o inverso. Concretamente:
   - Uma futura `carregar_cabecalhos_pendentes(db)` (nova, no módulo Linx, lendo `ordens_reserva_cabecalho WHERE codigo_linx IS NULL` com join nas linhas) é o ponto de leitura dedicado à integração — **não** reaproveitar `carregar_ordens_reserva` (que serve o formato JSON pensado para o frontend/dashboard). Misturar os dois geraria acoplamento entre o contrato de tela e o contrato do barramento, que provavelmente têm formatos diferentes (o Linx fala XML/JSON próprio, desconhecido hoje).
   - Uma futura `atualizar_codigo_linx(db, cabecalho_id, codigo)` fica em `pedidos/infrastructure/repositorio_ordens.py` (é o dono da tabela) mas é chamada pelo caso de uso do módulo Linx — um `UPDATE` de 1 linha, sem tocar `ordens_reserva`.
   - Isso mantém `pedidos` sem nenhuma dependência de HTTP/fila externa e o módulo Linx isolado e testável como os demais, replicando exatamente o padrão de `ingestao` (que já fala com um sistema externo — Databricks — sem que `pedidos` saiba disso).

## Novo vs. Modificado — Resumo

| Componente | Novo / Modificado | Arquivo |
|---|---|---|
| `OrdemReservaCabecalho` (ORM) | **Novo** | `app/modules/pedidos/models.py` |
| Coluna `or_id` em `OrdemReserva` | **Modificado** (nova coluna + FK) | `app/modules/pedidos/models.py` |
| Migration 015 | **Novo** | `alembic/versions/015_*.py` |
| `_criar_cabecalho()` | **Novo** | `app/modules/pedidos/infrastructure/repositorio_ordens.py` |
| `salvar_ordens_reserva()` | **Modificado** (agrupa por produto, cria cabeçalho, atribui `or_id`, retorna ids novos) | idem |
| `carregar_ordens_reserva()` | **Modificado** (join com cabeçalho, retorno passa a ser `list[LinhaOrdemReserva]`) | idem |
| `LinhaOrdemReserva` (NamedTuple) | **Novo** | idem (ou `models.py`, à escolha de quem implementar) |
| `_montar_pedidos()` | **Modificado** (consome `LinhaOrdemReserva`, carimba `nrOr` nos itens) | `app/modules/pedidos/application/casos_uso.py` |
| `_format_items()` | **Modificado** (parâmetro opcional `or_id`) | idem |
| `listar_ordens_reserva()` | **Modificado** (expõe `nrOr`/`codigoLinx`/`geradaEm`) | idem |
| `OrdemReservaOut` | **Modificado** (+`nrOr`, `+codigoLinx`) | `app/modules/pedidos/schemas.py` |
| Testes "1 OR por produto por rodada" | **Novo** | `app/tests/test_pedidos_*.py` |
| `product-grade-detail-modal.tsx` | **Modificado** (remove `255315` hardcoded) | `frontend/features/pedidos/ui/modals/` |
| Tipos de `OrdemReserva`/`ClienteDaOrdem` | **Modificado** (+campos) | `frontend/entities/pedido/` |
| Módulo `integracao_linx/` + outbox | **Fora de escopo deste milestone** (seam preparado, não implementado) | — |

## Padrões a Seguir

### Padrão: Flush para obter PK gerada pelo banco, commit permanece no caso de uso

**O quê:** repositório usa `db.flush()` (não `db.commit()`) sempre que precisa do valor de uma coluna gerada pelo Postgres (`Identity`, `server_default`) antes de terminar a operação corrente.
**Quando usar:** toda vez que uma FK depende de um ID que só existe depois do INSERT (exatamente o caso cabeçalho→linha aqui).
**Trade-off:** nenhum custo relevante — `flush()` é uma operação já emitida implicitamente pelo SQLAlchemy antes de qualquer `SELECT`/`commit`; torná-la explícita só documenta a intenção.

### Padrão: Tupla nomeada (`NamedTuple`) para retorno de repositório com múltiplos campos

**O quê:** funções de leitura de repositório que hoje retornam tuplas posicionais cruas devem migrar para `NamedTuple` sempre que ganharem um campo novo (o gatilho natural para parar de crescer a tupla às cegas).
**Quando usar:** qualquer `carregar_*` que hoje devolve `tuple[...]` com 4+ elementos e mais de 1 consumidor.
**Trade-off:** migração mecânica (troca desestruturação por atributo nos 2 call sites) — barato agora, caro depois se um 3º consumidor aparecer antes da mudança.

## Anti-Padrões a Evitar

### Anti-Padrão: Criar o cabeçalho no caso de uso, passando o `id` "de fora" para o repositório

**O que seria feito:** caso de uso chama uma função `criar_cabecalho` diretamente e distribui os `id`s manualmente antes de chamar `salvar_ordens_reserva`.
**Por que é ruim:** duplica a lógica de "isto é uma linha nova?" em duas camadas (o caso de uso teria que replicar o `if obj is None` que hoje só existe dentro do repositório), e abre espaço para o caso de uso criar um cabeçalho para uma linha que na verdade vai cair no caminho de UPDATE dentro do repositório — produzindo cabeçalhos órfãos (nenhuma linha nova apontando pra eles) dentro da mesma transação.
**Fazer em vez disso:** manter toda a decisão de upsert e criação de cabeçalho dentro de `salvar_ordens_reserva`, como detalhado acima.

### Anti-Padrão: Reaproveitar `carregar_ordens_reserva` (formato de tela) como fonte de dados da futura integração Linx

**O que seria feito:** o futuro módulo Linx importa e chama `carregar_ordens_reserva` de `pedidos/infrastructure`.
**Por que é ruim:** acopla o contrato do barramento ao formato pensado para o dashboard (JSON de tela, com `itens` cru e campos como `nrOr`/`geradaEm` já formatados em epoch ms para o front) — qualquer mudança de UI arrisca quebrar a integração externa, e vice-versa.
**Fazer em vez disso:** o módulo Linx define sua própria função de leitura (ex.: `carregar_cabecalhos_pendentes`), com o formato que o barramento exige, ainda que ela reutilize as mesmas tabelas.

## Considerações de Escala

Não é um driver relevante para esta mudança — o volume de ORs (produtos × rodadas de geração) é ordens de grandeza menor que o volume de pedidos/itens já tratado hoje (a própria `CONCERNS.md` do mapa do codebase já mapeia a ausência de índices como dívida técnica pré-existente, não algo introduzido aqui). Único ponto concreto: a FK `ordens_reserva.or_id → ordens_reserva_cabecalho.id` se beneficia de um índice (Postgres cria automaticamente para a PK do lado referenciado, mas não para a coluna FK do lado referenciante) — vale considerar `index=True` na coluna `or_id` do lado de `OrdemReserva`, já que o join de leitura (`carregar_ordens_reserva`) passa a rodar em toda chamada de `GET /pedidos` e `GET /ordens-reserva`.

## Fontes

- `app/modules/pedidos/infrastructure/repositorio_ordens.py` (código atual, lido diretamente)
- `app/modules/pedidos/application/casos_uso.py` (código atual, lido diretamente)
- `app/modules/pedidos/models.py` (código atual, lido diretamente)
- `app/modules/pedidos/schemas.py` (código atual, lido diretamente)
- `app/modules/pedidos/routes.py` (código atual, lido diretamente)
- `alembic/versions/014_or_por_produto.py` (padrão de migration hand-written já usado no repo, incluindo tratamento de coluna NOT NULL em tabela populada)
- `frontend/features/pedidos/ui/modals/product-grade-detail-modal.tsx` (linha 240, o `255315` hardcoded)
- `.planning/PROJECT.md`, `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/STRUCTURE.md`, `.planning/codebase/CONCERNS.md`, `.planning/codebase/CONVENTIONS.md` (mapa do codebase, 2026-08-04)

---
*Architecture research for: cabeçalho de OR + preparação Linx*
*Researched: 2026-08-04*
