# Phase 3: Escrita da OR no formato Linx - Research

**Researched:** 2026-08-05
**Domain:** Persistência transacional (SQLAlchemy 2.0 async / PostgreSQL) — gravação/upsert de uma linha derivada em 3 fluxos existentes do módulo `pedidos`
**Confidence:** HIGH (o domínio é 100% código-fonte real do repositório; nenhuma biblioteca nova)

## Project Constraints (from CLAUDE.md)

Diretivas obrigatórias de `./CLAUDE.md`, com a mesma autoridade de uma decisão travada:

- **Consultar o mapa do codebase (`.planning/codebase/`) antes de editar** — seguido nesta pesquisa (`CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md` lidos e citados abaixo).
- **Consultar `backstage_get_coding_standards` via MCP do Backstage antes de editar** — indisponível neste ambiente (confirmado nas Fases 1 e 2 também); o executor deve tentar a chamada e, se ausente, seguir os padrões reais do repositório documentados aqui.
- **Stack travada**: Python 3.13 + FastAPI 0.115 + SQLAlchemy 2.0 async + asyncpg + Alembic — **sem novas dependências** neste milestone. Confirmado: esta fase não precisa de nenhum pacote novo (só código sobre tabelas já criadas nas Fases 1/2).
- **Compatibilidade de contrato HTTP**: nenhuma mudança nos contratos de `/adequar`, `/sem_adequar`, `/alterar-grade` — a escrita Linx é um efeito colateral interno, não deve alterar o shape das respostas existentes.
- **Mantenedora dev iniciante**: soluções simples e didáticas > otimizações sofisticadas — influencia a recomendação de upsert (ver "Standard Stack" → "Alternatives Considered").
- **Repositórios não commitam**: quem decide `db.commit()` é o caso de uso — confirmado em `salvar_ordens_reserva`, `salvar_processados`, `substituir_referencia_tamanhos`; a escrita Linx deve seguir o mesmo contrato.
- **Commits Conventional Commits em `feat/` a partir de `develop`.**

## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01** — Produto com ZERO posições na referência (`produto_tamanho_posicao`): a linha Linx **não é gravada**; um warning nomeia o produto; a OR interna (`ordens_reserva`) nasce normalmente, sem mudança de comportamento na tela. Grade **parcialmente** convertida (alguns tamanhos sem posição, mas ao menos um tamanho COM posição) continua sendo gravada — regra da Fase 2, já implementada em `converter_grade_para_posicoes`.
- **D-02** — A linha Linx é **espelho**: `executar_alteracao_grade` (`PUT /alterar-grade`) também regrava a linha Linx com as novas quantidades. Esta fase toca **3 fluxos**, não 2.
- **D-03** — Coerência aritmética: `valor_embalado` = soma exata dos `vl_liquido` dos itens daquele cliente×produto×cor; `preco1` = `valor_embalado ÷ qtde_embalada`, arredondado a 2 casas. Divergência de centavos em `preco1 × qtde_embalada` é aceita (registrar no código para revisão quando o contrato real do barramento existir).
- **D-04** — Sem backfill: só gerações **novas** produzem linha Linx. As 4 ORs de teste já existentes em `ordens_reserva` não são convertidas.

### Claude's Discretion

- Nomes de arquivos/funções novos (seguir os análogos do módulo `pedidos`).
- Onde exatamente montar a linha (função de domínio pura vs no caso de uso) e como carregar a referência de posição por produto (uma query só para os produtos da rodada).
- Como preencher `nome_clifor` quando o item não traz `client` (precedente: `f"Cliente {nr}"`).
- Estratégia de upsert (`uq_ordens_reserva_linx_chave` já existe para isso).

### Deferred Ideas (OUT OF SCOPE)

- Envio ao Linx via barramento, status de envio e reconciliação (INTG-01..03) — aguarda acesso da TI.
- Preenchimento de `FILIAL`, `ROMANEIO`, `CAIXA`, `REPRESENTANTE`, `ENTREGA`, `PACKS`, `ITEM` (sem fonte hoje).
- Política de retenção/limpeza de `ordens_reserva_linx`.
- Backfill das ORs anteriores.
- Ligar o frontend ao `PUT /alterar-grade`.

## Phase Requirements

| ID | Descrição | Suporte da pesquisa |
|----|-----------|----------------------|
| LINX-02 | Cada geração (com ou sem adequação) grava/atualiza 1 linha por cliente×produto×cor com `nome_clifor`, `pedido`, `produto`, `cor_produto`, `preco1`, `valor_embalado`, `qtde_embalada` — na mesma transação | Seções "Architecture Patterns" (Pattern 1-4), "Code Examples" e "Mapeamento campo a campo" cobrem os 3 pontos de integração (`executar_adequacao`, `executar_sem_adequacao`, `executar_alteracao_grade`), a query batelada da referência, a função pura de montagem de linha e o upsert; "Validation Architecture" mapeia os 5 critérios do ROADMAP para testes concretos |

## Summary

Esta fase não introduz nenhuma peça de infraestrutura nova — é composição de blocos já existentes e provados nas Fases 1 e 2: o schema `OrdemReservaLinx` (82 colunas, `uq_ordens_reserva_linx_chave`), a função pura `converter_grade_para_posicoes` (grade→`e1..e48`) e o padrão "repositório não commita, caso de uso decide" já usado por `salvar_ordens_reserva`/`salvar_processados`. O trabalho real é de **encanamento**: (1) buscar a referência de posição só para os produtos da rodada atual (nunca as 569.726 linhas inteiras), (2) montar a linha Linx com uma função pura de domínio que decide, sozinha, se a linha deve existir (D-01: zero posições → `None`), e (3) persistir com upsert antes do `db.commit()` já existente em cada um dos 3 fluxos tocados.

O ponto mais delicado não é técnico, é de **localização de responsabilidade**: `OrdemReservaLinx` tem PK própria (`id` autoincrement) e a chave natural `(nr_pedido, cd_prod_cor)` é só uma `UniqueConstraint` — diferente de `PedidoModificacao`, cuja PK **é** a chave natural. Isso significa que o upsert não pode reusar `db.get(Model, pk)` como `salvar_ordens_reserva` faz; precisa de um `SELECT ... WHERE nr_pedido = ? AND cd_prod_cor = ?` antes de decidir INSERT ou UPDATE — o mesmo padrão que `carregar_modificacoes`/`aprovar_ordem_reserva` já usam para tabelas com PK diferente da chave de negócio.

O segundo ponto delicado é performance: `produto_tamanho_posicao` tem 569.726 linhas reais (medido no Plano 02-04). Uma função que carregasse a tabela inteira num dict a cada geração seria um desperdício claro. A recomendação (ver "Common Pitfalls" → Pitfall 2) é: filtrar por `cd_prod_cor IN (...)` só com os produtos da rodada — que, pelo desenho dos fluxos `/adequar`/`/sem_adequar` (que processam TODOS os pares pendentes de uma vez, não um cliente por vez), pode ser algumas centenas a poucos milhares de produtos distintos, não milhões. O limite de parâmetros do protocolo PostgreSQL (65.535 por statement) está muito acima dessa escala — não é o risco real; o risco real é esquecer o filtro e carregar a tabela inteira.

**Primary recommendation:** Nova função pura em `app/modules/pedidos/domain/ordem_reserva_linx.py` (`montar_linha_linx`) que recebe a grade agregada, a referência de posição JÁ FILTRADA para aquele produto e os metadados do item, e devolve `dict | None` (D-01 expresso como `None`); uma nova função de leitura cross-context em `ingestao/api_leitura.py` (`obter_referencia_posicoes_por_produtos`) que busca só os `cd_prod_cor` da rodada; e um novo repositório `repositorio_ordens_linx.py` com `salvar_linhas_linx` seguindo o padrão SELECT-antes-de-decidir (não `db.get`, porque a PK não é a chave natural) — chamado nos 3 pontos de integração antes do `db.commit()` já existente.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Decisão "gravar ou não a linha Linx" (D-01) | API / Backend (domínio puro) | — | É regra de negócio pura, sem I/O — pertence a `domain/`, não ao caso de uso nem ao repositório |
| Cálculo de `preco1`/`valor_embalado`/`qtde_embalada` (D-03) | API / Backend (domínio puro) | — | Aritmética determinística sobre dados já em memória; mesma camada que já calcula `_format_items`/`listar_ordens_reserva` |
| Split de `cd_prod_cor` em `produto`/`cor_produto` | API / Backend (domínio puro) | — | Transformação de string sem I/O; mesma camada de `converter_grade_para_posicoes` |
| Carregamento em lote da referência de posição por produtos da rodada | API / Backend (infraestrutura/leitura cross-context) | Database / Storage | Query batelada contra `produto_tamanho_posicao` (módulo `ingestao`), consumida por `pedidos` via `api_leitura.py` — mesmo padrão já usado para estoque/pedidos processados no ERP |
| Upsert de `ordens_reserva_linx` | API / Backend (infraestrutura/repositório) | Database / Storage | `uq_ordens_reserva_linx_chave` já existe; repositório decide INSERT vs UPDATE, caso de uso decide commit |
| Orquestração e ponto de commit único (3 fluxos) | API / Backend (application/casos de uso) | — | `executar_adequacao`/`executar_sem_adequacao`/`executar_alteracao_grade` já são os únicos donos do `db.commit()` — a escrita Linx entra antes dele, nunca ganha commit próprio |

## Standard Stack

### Core

Nenhuma dependência nova. Reaproveita integralmente o que já está resolvido em `uv.lock` (confirmado nas Fases 1 e 2, sem mudança desde então):

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| SQLAlchemy | 2.0.49 [VERIFIED: uv.lock, Fase 1] | `select(...).where(...)`, `db.add`, upsert manual | ORM já em uso em todo o módulo `pedidos` |
| asyncpg | 0.31.0 [VERIFIED: uv.lock, Fase 1] | Driver Postgres da sessão async | Já em uso |
| pytest / pytest-asyncio | 8.3+ / 0.24+ [VERIFIED: pyproject.toml] | Testes de integração dos 3 fluxos + rollback conjunto | Padrão do repo |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| **Upsert via SELECT + decide (INSERT/UPDATE) em Python**, mesmo padrão de `salvar_ordens_reserva`/`carregar_modificacoes` | `INSERT ... ON CONFLICT (nr_pedido, cd_prod_cor) DO UPDATE` (`sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)`) | **Recomendação: SELECT + decide.** Nenhum precedente de `on_conflict_do_update` existe no repo (`grep` confirmou zero ocorrências) — introduzir SQL específico do dialeto Postgres quebraria a consistência de padrão que todo o resto do módulo `pedidos` segue, e a constraint do projeto prioriza "soluções simples e didáticas" para uma mantenedora júnior. O volume por rodada é baixo ("poucas linhas por rodada", confirmado no CONTEXT.md) — o custo de 1 SELECT extra por produto é irrelevante. `ON CONFLICT` ganharia atomicidade contra corrida concorrente (2 chamadas simultâneas de `/adequar`), mas isso já é um risco pré-existente e fora de escopo desta fase (nenhuma das 3 rotas tem lock hoje; só a ingestão tem advisory lock, adicionado por outro incidente). Se o volume crescer ou corrida concorrente virar problema real, revisitar com `ON CONFLICT` é uma migração de repositório isolada, sem tocar o restante do desenho. |
| Query batelada `WHERE cd_prod_cor IN (...)` por chunks | Carregar `produto_tamanho_posicao` inteira num dict (como poderia parecer natural copiando o padrão de `carregar_estoque`) | **Rejeitado.** A tabela tem 569.726 linhas reais (medido, Plano 02-04) — mesmo que caiba em memória, seria uma query cara e desnecessária a cada `/adequar`/`/sem_adequar`. Nenhuma fase anterior filtrou por produto porque a Fase 2 testou a conversão com dados sintéticos/amostra — esta é a primeira vez que o carregamento em produção precisa ser desenhado. |

## Package Legitimacy Audit

**Não aplicável.** Esta fase não instala nenhum pacote novo — usa exclusivamente bibliotecas já resolvidas em `uv.lock`. O gate de legitimidade de pacotes não precisa rodar.

## Architecture Patterns

### System Architecture Diagram

```
        POST /adequar          POST /sem_adequar         PUT /alterar-grade
             │                        │                          │
             ▼                        ▼                          ▼
  executar_adequacao()      executar_sem_adequacao()   executar_alteracao_grade()
             │                        │                          │
             │  monta {(nr,cd): itens}│  (mesmo shape)            │  UM par (nr,cd)
             ▼                        ▼                          ▼
     salvar_ordens_reserva()  salvar_ordens_reserva()     upsert_modificacao()
     (ordens_reserva, JSONB)  (ordens_reserva, JSONB)     (pedido_modificacoes)
             │                        │                          │
             ▼                        ▼                          ▼
  ┌──────────────────────── NOVO: gravação Linx (mesma transação) ─────────┐
  │                                                                        │
  │  1. cd_prod_cors = {cd for (nr, cd) in pares}                         │
  │  2. referencia = obter_referencia_posicoes_por_produtos(db, cd_prod_cors)│  <- ingestao/api_leitura.py
  │     (SELECT cd_prod_cor, sg_tamanho, nr_posicao                       │
  │      FROM produto_tamanho_posicao WHERE cd_prod_cor IN (chunk))       │
  │  3. para cada (nr, cd, itens):                                        │
  │       grade = agrega itens por sg_tamanho -> {sg_tamanho: qtd}         │
  │       ref_produto = referencia.get(cd, {})                            │
  │       linha = montar_linha_linx(nr, cd, grade, ref_produto, meta, tipo)│  <- domain/ordem_reserva_linx.py (pura)
  │       # D-01: ref_produto == {} -> linha é None (warning já emitido)  │
  │       if linha: linhas.append(linha)                                  │
  │  4. await salvar_linhas_linx(db, linhas)   # upsert, sem commit       │  <- infrastructure/repositorio_ordens_linx.py
  │                                                                        │
  └────────────────────────────────────────────────────────────────────────┘
             │                        │                          │
             ▼                        ▼                          ▼
        await db.commit()      await db.commit()          await db.commit()
     (ordens_reserva +               (idem)                (pedido_modificacoes +
      ordens_reserva_linx no                                 ordens_reserva_linx no
      mesmo commit)                                           mesmo commit)
```

### Recommended Project Structure

```
app/modules/ingestao/
└── api_leitura.py                         # + obter_referencia_posicoes_por_produtos(db, cd_prod_cors)

app/modules/pedidos/
├── infrastructure/
│   ├── repositorio_ingestao_readmodel.py  # + carregar_referencia_posicoes (alias, mesmo padrão de carregar_pedidos_itens)
│   └── repositorio_ordens_linx.py         # NOVO — salvar_linhas_linx (upsert, sem commit)
├── domain/
│   ├── grade_linx.py                      # já existe (Fase 2) — não altera assinatura
│   └── ordem_reserva_linx.py               # NOVO — montar_linha_linx (pura, decide D-01)
├── application/
│   └── casos_uso.py                       # executar_adequacao / executar_sem_adequacao / executar_alteracao_grade — 3 pontos de integração
└── service.py                             # + re-export das funções novas (mesmo padrão de converter_grade_para_posicoes)

app/tests/
├── test_ordem_reserva_linx.py              # NOVO — testes puros de montar_linha_linx (D-01, D-03)
└── test_pedidos_routes.py                  # + casos mockados dos 3 fluxos + teste de rollback conjunto (sem TestClient)
```

### Pattern 1: Função pura de domínio decide "gravar ou não" (D-01) devolvendo `None`

**What:** `montar_linha_linx` recebe a referência de posição JÁ FILTRADA para aquele produto (`dict[sg_tamanho, nr_posicao]`, pode ser `{}`) e devolve `None` se estiver vazia — sem logar de novo (o warning de D-01 é responsabilidade do CHAMADOR, que sabe nomear o produto no contexto certo; ver Pattern 4). Se não estiver vazia (mesmo parcial), delega a `converter_grade_para_posicoes` (que já trata tamanhos parcialmente sem posição, D-04 da Fase 2) e monta o dict completo da linha.

**When to use:** Sempre que os 3 fluxos precisarem decidir se uma linha Linx deve ser persistida.

**Why `None` e não uma exceção:** Segue o mesmo padrão de `_montar_pedidos`/`listar_pedidos_abertos` (que retornam listas vazias/`None` para "nada aqui", nunca exceção para casos de negócio esperados) e do próprio `executar_adequacao` (`None` = "nenhum pedido" → 404, não erro). D-01 é um caso de negócio esperado (produto sem cadastro na referência), não uma falha de sistema.

**Example (assinatura recomendada):**
```python
# app/modules/pedidos/domain/ordem_reserva_linx.py
from app.modules.pedidos.domain.grade_linx import converter_grade_para_posicoes


def montar_linha_linx(
    nr_pedido: int,
    cd_prod_cor: str,
    itens: list[dict],
    referencia_produto: dict[str, int],
    tipo: str,
) -> dict | None:
    """Monta a linha Linx de UM par (nr_pedido, cd_prod_cor).

    D-01: se `referencia_produto` vier vazio (produto sem NENHUM tamanho
    cadastrado em produto_tamanho_posicao), devolve None — quem chama decide
    o warning (precisa do contexto de "por quê": zero posições, não tamanho
    faltando). Grade parcialmente convertida (D-04 da Fase 2) continua sendo
    gravada normalmente.
    """
    if not referencia_produto:
        return None

    grade: dict[str, int] = {}
    for item in itens:
        grade[item["sg_tamanho"]] = grade.get(item["sg_tamanho"], 0) + item["qt_liquida"]

    posicoes, _ignorados = converter_grade_para_posicoes(grade, referencia_produto, cd_prod_cor)

    qtde_embalada = sum(grade.values())
    valor_embalado = round(sum(item["vl_liquido"] for item in itens), 2)  # D-03: soma exata
    # D-03: preco1 arredondado a 2 casas; aceita divergência de centavos em
    # preco1 * qtde_embalada (o total financeiro é a fonte de verdade, não o
    # produto preco*qtde). Guard de divisão por zero: grade vazia é defensivo,
    # não esperado (todo par com OR tem ao menos 1 item).
    preco1 = round(valor_embalado / qtde_embalada, 2) if qtde_embalada else None

    produto, _sep, cor_produto = cd_prod_cor.partition("|")
    if not _sep:
        # Nunca observado em dados reais (Fase 2 sempre viu "|", inclusive no
        # produto sujo ".1|001") — defensivo: usa o código inteiro como
        # produto e deixa cor_produto vazio, sem lançar exceção.
        produto, cor_produto = cd_prod_cor, None

    meta = itens[0]
    return {
        "nr_pedido": nr_pedido,
        "cd_prod_cor": cd_prod_cor,
        "tipo": tipo,
        "nome_clifor": meta.get("client") or f"Cliente {nr_pedido}",
        "produto": produto,
        "cor_produto": cor_produto,
        "pedido": nr_pedido,
        "preco1": preco1,
        "valor_embalado": valor_embalado,
        "qtde_embalada": qtde_embalada,
        **posicoes,  # e1..e48
    }
```

### Pattern 2: Query batelada da referência — nunca carregar a tabela inteira

**What:** Nova função em `ingestao/api_leitura.py` (mesma superfície pública já usada por `pedidos` para ler dados de `ingestao` sem acessar `ingestao.models` diretamente) que recebe a lista de `cd_prod_cor` da rodada e devolve `{cd_prod_cor: {sg_tamanho: nr_posicao}}` — só para esses produtos.

**When to use:** No início de cada um dos 3 fluxos, uma única vez por rodada (não por produto).

**Example:**
```python
# app/modules/ingestao/api_leitura.py — nova função, mesmo estilo das existentes
from app.modules.ingestao.models import ProdutoTamanhoPosicao

_TAMANHO_CHUNK = 500  # margem larga sob o limite de 65535 params do protocolo Postgres;
                       # evita 1 statement gigante quando a rodada tiver muitos produtos


async def obter_referencia_posicoes_por_produtos(
    db: AsyncSession, cd_prod_cors: set[str]
) -> dict[str, dict[str, int]]:
    """Referência tamanho->posição SÓ dos produtos informados — nunca a tabela
    inteira (569.726 linhas reais). Devolve {} para produtos sem NENHUMA
    posição cadastrada (não gera KeyError; D-01 é decidido por quem chama)."""
    if not cd_prod_cors:
        return {}

    referencia: dict[str, dict[str, int]] = {}
    lista = list(cd_prod_cors)
    for inicio in range(0, len(lista), _TAMANHO_CHUNK):
        chunk = lista[inicio : inicio + _TAMANHO_CHUNK]
        rows = (
            await db.execute(
                select(
                    ProdutoTamanhoPosicao.cd_prod_cor,
                    ProdutoTamanhoPosicao.sg_tamanho,
                    ProdutoTamanhoPosicao.nr_posicao,
                ).where(ProdutoTamanhoPosicao.cd_prod_cor.in_(chunk))
            )
        ).all()
        for cd, tam, pos in rows:
            referencia.setdefault(cd, {})[tam] = pos
    return referencia
```

E o wrapper cross-context em `pedidos/infrastructure/repositorio_ingestao_readmodel.py` (mesmo padrão de `carregar_pedidos_itens`/`carregar_estoque_fisico`):
```python
from app.modules.ingestao.api_leitura import (
    obter_referencia_posicoes_por_produtos as carregar_referencia_posicoes,
    ...  # imports já existentes
)
```

### Pattern 3: Upsert por SELECT + decide (não `db.get`, porque a PK não é a chave natural)

**What:** `OrdemReservaLinx.id` é a PK (autoincrement); `(nr_pedido, cd_prod_cor)` é só `UniqueConstraint`. Isso é diferente de `PedidoModificacao` (cuja PK **é** `(nr_pedido, cd_prod_cor)`, permitindo `db.get(PedidoModificacao, (nr, cd))`). Para `OrdemReservaLinx` o padrão certo é `SELECT ... WHERE nr_pedido = ? AND cd_prod_cor = ?` seguido de update-ou-insert — o mesmo padrão que `carregar_modificacoes`/`aprovar_ordem_reserva` já usam.

**Example:**
```python
# app/modules/pedidos/infrastructure/repositorio_ordens_linx.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def salvar_linhas_linx(db: AsyncSession, linhas: list[dict]) -> None:
    """Upsert de linhas Linx por (nr_pedido, cd_prod_cor).

    `linhas` vem de montar_linha_linx (já filtrado — D-01 descartou os None
    antes de chegar aqui). Sem commit próprio — o caso de uso decide."""
    from app.modules.pedidos.models import OrdemReservaLinx

    for linha in linhas:
        obj = (
            await db.execute(
                select(OrdemReservaLinx).where(
                    OrdemReservaLinx.nr_pedido == linha["nr_pedido"],
                    OrdemReservaLinx.cd_prod_cor == linha["cd_prod_cor"],
                )
            )
        ).scalar_one_or_none()
        if obj is None:
            db.add(OrdemReservaLinx(**linha))
        else:
            for campo, valor in linha.items():
                setattr(obj, campo, valor)
```

### Pattern 4: Warning de D-01 no ponto de chamada (não na função pura)

**What:** A função pura devolve `None` silenciosamente (não loga) porque não sabe o "porquê" do chamador nem quer duplicar responsabilidade de logging entre domínio e aplicação. O `logger.warning` nomeando o produto entra no laço do caso de uso, no mesmo espírito dos avisos agregados por produto da Fase 2.

**Example:**
```python
# dentro de executar_adequacao / executar_sem_adequacao, após salvar_ordens_reserva:
linhas_linx = []
cd_prod_cors = {cd for _nr, cd in ors_a_gravar}
referencia = await carregar_referencia_posicoes(db, cd_prod_cors)
for (nr, cd), itens in ors_a_gravar.items():
    linha = montar_linha_linx(nr, cd, itens, referencia.get(cd, {}), tipo="com")
    if linha is None:
        logger.warning(
            "produto %s sem nenhuma posição em produto_tamanho_posicao — "
            "linha Linx não gravada (OR interna segue normal).", cd,
        )
        continue
    linhas_linx.append(linha)
await salvar_linhas_linx(db, linhas_linx)
```

### Anti-Patterns to Avoid

- **Carregar `produto_tamanho_posicao` inteira num dict:** 569.726 linhas reais — desperdício de memória/tempo a cada geração. Sempre filtrar por `cd_prod_cor IN (...)` da rodada.
- **Usar `db.get(OrdemReservaLinx, (nr_pedido, cd_prod_cor))`:** a PK é `id`, não a chave natural — `db.get` com essa tupla não vai encontrar nada e sempre vai tentar INSERT, colidindo com `uq_ordens_reserva_linx_chave` na segunda rodada (`IntegrityError`).
- **Logar o warning de D-01 dentro da função pura de domínio:** duplicaria a decisão entre camadas e tornaria o teste puro de `montar_linha_linx` dependente de `caplog` sem necessidade — mantenha a função pura "burra" (calcula e decide `None`/dict), o warning é do chamador.
- **Inventar formato para `pedido_produto`/`pedido_cor_produto`:** ver "Open Questions" — sem uma amostra real do CSV confirmando o formato exato da denormalização, preencher com um formato adivinhado viola o espírito do critério 5 ("nenhum valor inventado"). Recomendação: deixar `NULL` nesta fase.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Upsert idempotente | Lógica de "tentar INSERT, capturar `IntegrityError`, fazer UPDATE" | SELECT + decide, usando `uq_ordens_reserva_linx_chave` como guia (Pattern 3) | Já existe precedente idêntico no repo (`carregar_modificacoes`/`upsert_modificacao`); capturar exceção de integridade para controle de fluxo é mais frágil e menos legível |
| Conversão grade→posições | Reimplementar o mapeamento tamanho→e1..e48 | `converter_grade_para_posicoes` (Fase 2, já testada com dados reais) | Duplicar essa lógica quebraria a fonte única de verdade e o teste já existente |
| Detecção de "produto sem posição" | Verificar se algum tamanho específico está ausente | Checar se `referencia_produto` (já filtrada por `cd_prod_cor`) está vazia ANTES de chamar `converter_grade_para_posicoes` | É exatamente o sinal que D-01 pede — "zero posições", não "algum tamanho sem posição" (esse já é tratado por D-04 da Fase 2 dentro da própria função de conversão) |

**Key insight:** Toda a "matéria-prima" desta fase já foi construída e testada nas Fases 1 e 2. O risco não é técnico, é de desenho de fronteira: decidir corretamente ONDE cada responsabilidade mora (domínio puro vs infraestrutura vs caso de uso) evita testes frágeis e retrabalho.

## Common Pitfalls

### Pitfall 1: Confundir "zero posições" (D-01) com "algum tamanho sem posição" (D-04/Fase 2)
**What goes wrong:** Se o gate D-01 checar `ignorados` (2º retorno de `converter_grade_para_posicoes`) em vez de checar `referencia_produto` vazio ANTES de converter, um produto com 5 tamanhos pedidos e só 1 cadastrado na referência (4 `ignorados`, 1 convertido) seria erroneamente descartado — quando a regra correta (D-01) é: descartar só se **nenhum** tamanho tiver posição.
**Why it happens:** Os dois conceitos (zero posições vs. parcialmente convertida) são fáceis de confundir porque usam a mesma função de conversão.
**How to avoid:** Checar `if not referencia_produto: return None` ANTES de chamar `converter_grade_para_posicoes` — nunca inferir a partir de `ignorados`.
**Warning signs:** Teste que espera uma linha Linx para um produto com conversão parcial e recebe `None`.

### Pitfall 2: Carregar a referência inteira "porque é mais simples"
**What goes wrong:** `SELECT * FROM produto_tamanho_posicao` sem `WHERE` carrega 569.726 linhas a cada chamada de `/adequar`/`/sem_adequar` — lento e caro, especialmente porque esses 2 endpoints já fazem `carregar_pedidos_itens`/`carregar_estoque` (queries que também escalam com o volume de pedidos abertos).
**Why it happens:** É o caminho de menor esforço e parece "simples e didático" à primeira vista — mas confunde simplicidade de código com eficiência de execução.
**How to avoid:** Sempre filtrar por `cd_prod_cor IN (...)` com os produtos da rodada atual (Pattern 2). O chunking em lotes de ~500 é a única complexidade extra, e é opcional na prática (a rodada raramente vai passar de algumas centenas de produtos distintos) — mas é barato de implementar e remove qualquer dúvida sobre o limite de parâmetros do protocolo Postgres (65.535).
**Warning signs:** Query sem cláusula `WHERE` contra `ProdutoTamanhoPosicao` em qualquer código deste módulo.

### Pitfall 3: Escrever a linha Linx DEPOIS do `db.commit()` existente
**What goes wrong:** Os 3 fluxos já têm um `await db.commit()` no final. Se a gravação Linx entrar depois dele (por engano, ou porque parece "mais seguro" separar), o critério 3 do ROADMAP (rollback conjunto) simplesmente não se sustenta: uma falha na escrita Linx deixaria `ordens_reserva` já commitada e `ordens_reserva_linx` sem a linha correspondente.
**Why it happens:** É tentador tratar a escrita Linx como "só mais um passo depois", já que ela é conceitualmente um efeito colateral/projeção de saída.
**How to avoid:** A chamada a `salvar_linhas_linx` entra SEMPRE antes do `await db.commit()` já existente em cada um dos 3 fluxos — nunca ganha commit próprio.
**Warning signs:** Um `await db.commit()` extra aparecendo perto do código novo, ou a chamada nova aparecendo depois do commit existente no diff.

### Pitfall 4: Split de `cd_prod_cor` assumindo sempre exatamente 1 `"|"`
**What goes wrong:** `cd_prod_cor.split("|")` (sem limite) devolveria uma lista com mais de 2 elementos se o produto tivesse mais de um `"|"` — improvável nos dados observados, mas `str.partition("|")` é defensivo por natureza (sempre 3 elementos: antes, separador, depois) e não lança `ValueError` como `a, b = texto.split("|")` faria se o separador estivesse ausente.
**Why it happens:** A amostra real (Fase 2) só mostrou exatamente 1 `"|"` por `cd_prod_cor` (inclusive no produto sujo `.1|001`), mas "nunca observado" não é "garantido pelo contrato de dados".
**How to avoid:** Usar `.partition("|")` (Pattern 1) em vez de `.split("|")` desestruturado; tratar ausência de `"|"` como caso defensivo (produto = string inteira, cor = `None`), nunca como exceção não tratada.
**Warning signs:** `ValueError: too many values to unpack` em produção.

## Mapeamento campo a campo (`ordens_reserva_linx`)

Fonte primária: `.planning/PROJECT.md` (Key Decisions + Context), `.planning/ROADMAP.md` (critérios 1-5 da Fase 3), `01-RESEARCH.md` (tipos das colunas). Todas as colunas do layout Linx são `nullable=True` no model; só as de controle são `NOT NULL`.

### Preenchidas nesta fase

| Coluna | Fonte | Fórmula/Regra |
|---|---|---|
| `nome_clifor` | item da grade | `meta.get("client") or f"Cliente {nr_pedido}"` [CITED: precedente em `_montar_pedidos`] |
| `produto` | split de `cd_prod_cor` | `cd_prod_cor.partition("|")[0]` |
| `cor_produto` | split de `cd_prod_cor` | `cd_prod_cor.partition("|")[2]` (ou `None` se `"|"` ausente) |
| `pedido` | identidade do par | `nr_pedido` (= "número da OR" no Linx, decisão travada em PROJECT.md) |
| `preco1` | D-03 | `round(valor_embalado / qtde_embalada, 2)` |
| `valor_embalado` | D-03 | `round(sum(vl_liquido dos itens), 2)` — soma exata, fonte de verdade financeira |
| `qtde_embalada` | D-03 | `sum(qt_liquida dos itens)` |
| `e1`..`e48` | Fase 2 (`converter_grade_para_posicoes`) | grade agregada por `sg_tamanho` → posições, usando a referência filtrada por produto |
| `tipo` | contexto do fluxo | `"com"` (executar_adequacao) / `"sem"` (executar_sem_adequacao/executar_alteracao_grade herda o tipo já gravado da OR original — ver Open Questions) |
| `nr_pedido`, `cd_prod_cor` | colunas de controle | idênticas às usadas em `OrdemReserva` — chave de correlação |
| `created_at` | coluna de controle | `server_default=func.now()` — automática no INSERT; **não atualizada** em regravações (D-02 regrava os campos de dados, não o timestamp de criação original; ver Open Questions sobre necessidade de `updated_at`) |

### Permanecem NULL nesta fase (critério 5 do ROADMAP)

`filial`, `item`, `pedido_cor_produto`, `romaneio`, `caixa`, `pedido_produto`, `packs`, `entrega`, `caixa_fechada`, `representante`, `ipi`, `preco2`, `preco3`, `preco4`, `desconto_item`, `origem`, `mata_saldo`, `item_pedido`, `ordem_producao`, `licenciado_royalties`, `percent_desconto`, `caixa_virtual` — todas sem fonte conhecida hoje (INTG-03, Future Requirements). **`pedido_produto`/`pedido_cor_produto` são um caso especial**: o CONCEITO de denormalização é conhecido (PROJECT.md cita "denormalizações"), mas nenhum exemplo real do CSV confirma o FORMATO exato da string — ver Open Questions. Recomendação desta pesquisa: deixar `NULL` também, para não inventar um formato não verificado (viola o espírito do critério 5).

## Testes: molde e estratégia

### Molde dos fluxos `/adequar` e `/sem_adequar` (mockados via HTTP)

`app/tests/test_pedidos_routes.py` já tem o padrão exato a seguir (`test_adequar_pedidos_fluxo_completo_mockado`, linhas ~172-206; `test_sem_adequar_pedidos_marca_processado_mockado`, linhas ~215-236): autenticação via `actor_token` fixture (role `OPERACIONAL`), todas as funções de I/O do caso de uso mockadas com `patch("app.modules.pedidos.application.casos_uso.<nome>", new=AsyncMock())`, e assert de shape da resposta + `mock.assert_awaited_once()`. Para esta fase, adicionar aos dois testes existentes (ou criar variantes):

```python
with (
    ... # mocks já existentes (carregar_pedidos_itens, carregar_estoque, etc.)
    patch("app.modules.pedidos.application.casos_uso.salvar_ordens_reserva", new=AsyncMock()) as mock_salvar_or,
    patch("app.modules.pedidos.application.casos_uso.carregar_referencia_posicoes", new=AsyncMock(return_value={"X": {"M": 1}})),
    patch("app.modules.pedidos.application.casos_uso.salvar_linhas_linx", new=AsyncMock()) as mock_salvar_linx,
    ...
):
    resp = client.post("/api/v1/pedidos/adequar", headers=auth_header(actor_token))

mock_salvar_linx.assert_awaited_once()
```

Isso prova os critérios 1/2 do ROADMAP (a chamada acontece) sem tocar o banco real — mas **não prova o conteúdo da linha nem o upsert real**; para isso, testes na camada de serviço (próxima seção).

### Teste do conteúdo da linha (sem banco) — molde de `test_grade_linx.py`

`montar_linha_linx` é pura — testar como `converter_grade_para_posicoes` já é testada (`app/tests/test_grade_linx.py`): sem banco, sem mocks, só `assert` sobre o dict devolvido. Casos mínimos: (a) referência vazia → `None` (D-01); (b) grade completa → todos os campos + `e1..e48` corretos; (c) `preco1`/`valor_embalado`/`qtde_embalada` conferem com D-03 usando os números reais do CSV citados no CONTEXT.md (`311,24 × 3 = 933,72`); (d) `cd_prod_cor` sem `"|"` → `produto` = string inteira, `cor_produto` = `None`, sem exceção.

### Teste do upsert real (sem TestClient) — molde de `test_salvar_ordens_reserva_*`

Igual a `test_salvar_ordens_reserva_par_novo_insere_sem_erro`/`test_salvar_ordens_reserva_par_existente_atualiza_sem_erro` (linhas ~315-341 de `test_pedidos_routes.py`): `async with async_session_factory() as session`, chama `salvar_linhas_linx` diretamente, `await session.flush()` (nunca `commit()`), depois um segundo `salvar_linhas_linx` com o mesmo `(nr_pedido, cd_prod_cor)` mas valores diferentes, e assert que ainda existe **1** linha (não 2) com os valores novos — prova direta do critério 4 (upsert, não duplica).

### Teste do rollback conjunto (critério 3) — molde de `test_sincronizar_tudo_falha_na_2a_etapa_...`

`app/tests/test_ingestao_sync.py` (linhas ~440-471 e ~495-517) já tem o padrão exato: `patch(..., new=AsyncMock(side_effect=RuntimeError("boom")))` num passo do meio, `async with async_session_factory() as session: with pytest.raises(RuntimeError): await service.sincronizar_tudo(session)`, e depois **uma sessão nova** (`verificacao_depois`) confirmando que nada foi persistido — porque saindo do `async with` sem `commit()` descarta a transação inteira. Para esta fase, o mesmo padrão, chamando `casos_uso.executar_sem_adequacao` (ou `executar_adequacao`) diretamente (não via `TestClient`, que comita de verdade):

```python
async def test_rollback_conjunto_quando_gravacao_linx_falha():
    nr_fake = 999999999
    dados_fake = [{"nr_pedido": nr_fake, "cd_prod_cor": "TESTE_ROLLBACK", "sg_tamanho": "M",
                   "ds_grupo": "G", "qt_liquida": 1, "vl_liquido": 10.0}]
    with (
        patch("app.modules.pedidos.application.casos_uso.carregar_pedidos_itens", new=AsyncMock(return_value=dados_fake)),
        patch("app.modules.pedidos.application.casos_uso.carregar_processados", new=AsyncMock(return_value=set())),
        patch("app.modules.pedidos.application.casos_uso.carregar_pares_processados_erp", new=AsyncMock(return_value=set())),
        patch("app.modules.pedidos.application.casos_uso.salvar_linhas_linx", new=AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        async with async_session_factory() as session:
            with pytest.raises(RuntimeError):
                await casos_uso.executar_sem_adequacao(session)
            # saída do "async with" sem commit descarta ordens_reserva + ordens_reserva_linx

    async with async_session_factory() as verificacao:
        assert await verificacao.scalar(
            text("SELECT count(*) FROM ordens_reserva WHERE nr_pedido = :nr"), {"nr": nr_fake}
        ) == 0
        assert await verificacao.scalar(
            text("SELECT count(*) FROM ordens_reserva_linx WHERE nr_pedido = :nr"), {"nr": nr_fake}
        ) == 0
```

**Nota:** injetar a falha em `salvar_linhas_linx` (o passo NOVO) é mais direto e específico desta fase do que injetar em `recalcular_estoque_virtual` (passo pré-existente, tocado pela outra sessão paralela — evitar, ver aviso de trabalho paralelo).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Formato de split `cd_prod_cor.partition("|")` (produto = antes, cor = depois) está correto para todos os casos reais | Pattern 1, Pitfall 4 | Baixo — confirmado por amostra real da Fase 2 (`AC.02.0002\|163`, `.1\|001`); risco só em um `cd_prod_cor` nunca visto sem `"|"`, tratado defensivamente |
| A2 | `pedido_produto`/`pedido_cor_produto` devem ficar `NULL` nesta fase (formato de denormalização não confirmado por amostra real) | "Mapeamento campo a campo" | Baixo — mais seguro que inventar um formato; se o planner preferir preencher com um formato específico, precisa de confirmação do usuário primeiro (ver Open Questions) |
| A3 | `tipo` da linha Linx em `executar_alteracao_grade` deve herdar o `tipo` já gravado em `ordens_reserva` (não recalculado) | "Mapeamento campo a campo", Open Questions | Médio — se a decisão certa for outra (ex.: sempre `"sem"` porque é uma edição manual), o campo fica com valor diferente do esperado; baixo impacto real porque nenhum consumidor atual (barramento) lê esse campo ainda |
| A4 | Tamanho de chunk de 500 para a query batelada da referência é adequado | Pattern 2, Pitfall 2 | Baixo — bem abaixo do limite real de 65.535 parâmetros do protocolo Postgres; é só uma escolha de "quão grande é 1 statement", não um limite de correção |

## Open Questions (RESOLVED)

> **Todas resolvidas no planejamento.** (1) `pedido_produto`/`pedido_cor_produto` ficam NULL —
> entraram na lista de "colunas sem fonte" do critério 5 (Plano 03-01). (2) Fluxo de
> `PUT /alterar-grade`: investigado a fundo — a edição hoje só afeta a listagem, nunca a geração;
> por isso a **D-02 foi REVISTA** no `03-CONTEXT.md` e esta fase NÃO toca `executar_alteracao_grade`
> (a correção virou o milestone v1.2, requirements GRADE-01..04). (3) `updated_at`: não adicionado —
> fora de escopo.
>
> **ATENÇÃO ao ler este documento isoladamente:** as seções abaixo foram escritas ANTES da revisão
> de escopo e descrevem 3 pontos de integração. O `03-CONTEXT.md` é a fonte de verdade: são 2.

1. **Formato exato de `pedido_produto`/`pedido_cor_produto`.**
   - What we know: PROJECT.md cita que são "denormalizações" de pedido+produto(+cor), sem exemplo real de string.
   - What's unclear: separador, ordem dos componentes, se incluem zeros à esquerda etc.
   - Recommendation: deixar `NULL` nesta fase (mesmo tratamento de "sem fonte confirmada"); se o usuário tiver acesso a uma amostra real do CSV com esses campos preenchidos, revisar em uma fase futura (INTG-03) — não vale a pena adivinhar e arriscar formato errado no futuro load.

2. **`tipo` da linha Linx quando `executar_alteracao_grade` regrava (D-02).**
   - What we know: `OrdemReserva.tipo` já existe para o par e não muda em `executar_alteracao_grade` hoje (a função não toca `tipo`, só `itens` via `upsert_modificacao`... na verdade `PedidoModificacao`, não `OrdemReserva` diretamente — a edição de grade acontece ANTES da geração da OR, através de `pedido_modificacoes`, que substitui os itens na montagem de `_montar_pedidos`. É preciso confirmar se D-02 se refere a editar uma OR JÁ GERADA ou a editar a grade de um pedido AINDA ABERTO).
   - What's unclear: **isto merece atenção extra do planner** — `executar_alteracao_grade` hoje opera sobre `pedido_modificacoes` (grades de pedidos ainda não processados) e não sobre `OrdemReserva` diretamente. Ler novamente D-02: "editar a grade (`PUT /alterar-grade`) regrava a linha Linx com as novas quantidades... como a OR só irá ao ERP depois". Se `PUT /alterar-grade` só é chamável ANTES de o par ter sido processado (sem OR ainda), então D-02 implica que a linha Linx passa a ser gravada também aqui, ANTES de existir alguma OR — o que muda o significado de "espelho" (não é regravar uma linha existente, é a PRIMEIRA gravação Linx para aquele par, fora do fluxo /adequar ou /sem_adequar). Isso precisa ser confirmado olhando o fluxo real do frontend/negócio: `alterar-grade` acontece antes ou depois da geração da OR?
   - Recommendation: o planner deve verificar, junto da usuária ou relendo o fluxo de negócio completo (`docs/adequacao.md`, mesmo defasado, ou perguntando), SE `PUT /alterar-grade` é chamado (a) antes da geração (edita o pedido aberto, a OR nasce já com a grade editada — não precisaria de regravação Linx separada, o /adequar ou /sem_adequar subsequente já cobre) ou (b) depois da geração (edita uma OR já existente — aí sim `executar_alteracao_grade` precisa gravar/regravar a linha Linx, como D-02 descreve). O código atual (`executar_alteracao_grade` opera sobre `dados = await carregar_pedidos_itens(db)`, isto é, pedidos AINDA ABERTOS, não ORs) sugere fortemente o caso (a) — o que tornaria a "regravação Linx" deste fluxo, na prática, inexistente hoje (não há OR/linha Linx para regravar ainda). **Esta é a maior incerteza desta pesquisa** — o planner precisa decidir se `executar_alteracao_grade` deve: (i) gravar uma linha Linx só se já existir uma para aquele par (equivalente a D-02 tal como descrito), ou (ii) não fazer nada (porque o fluxo real de "editar grade de OR já gerada" ainda não existe no código — é o gap explícito documentado em Out of Scope/UI-01/Deferred "Ligar o frontend ao PUT /alterar-grade").
   - **Ver também:** `.planning/REQUIREMENTS.md` Out of Scope: "Ligar o frontend ao `PUT /alterar-grade` — Gap conhecido, mas é outro fluxo". Isso sugere que o endpoint existe mas não é usado pela tela hoje — reforça a leitura de que `executar_alteracao_grade` é, na prática, código já existente mas não exercitado em produção. D-02 pode estar preparando o backend para o dia em que o frontend usar isso, mais do que corrigindo um comportamento observável hoje.

3. **`created_at` vs. `updated_at` em regravações (D-02/upsert em geral).**
   - What we know: o model `OrdemReservaLinx` só tem `created_at` (`server_default=func.now()`), sem `updated_at`. `OrdemReserva` (modelo interno) também não tem `updated_at`.
   - What's unclear: se uma regravação (upsert de UPDATE, não INSERT) deve ou não atualizar algum timestamp para refletir "última vez que este dado mudou" — relevante para auditoria futura de "quando a linha foi enviada por último ao Linx".
   - Recommendation: não adicionar `updated_at` nesta fase (fora do success criteria explícito, e adicionar coluna via migration é escopo maior do que "escrita" — seria uma migration 016 nova); se o `created_at` continuar representando só a primeira gravação, documentar isso no docstring do repositório, como já é convenção no módulo (`PedidoModificacao.updated_at` tem precedente análogo — poderia ser copiado numa fase futura, se necessário).

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.3+ / pytest-asyncio 0.24+ [VERIFIED: pyproject.toml] |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_ordem_reserva_linx.py app/tests/test_pedidos_routes.py -v` |
| Full suite command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |

**Importante:** `uv run` local falha no Windows (pyicu) — todos os comandos abaixo devem ser executados via `docker compose -f .docker/docker-compose.yml exec -T api uv run ...`. `ruff` não é executável no container (permission denied) — não usar como gate. `app/tests/conftest.py` conecta num Postgres real (`dev_db`); os testes de upsert/rollback usam `session.flush()`/`pytest.raises` + verificação com sessão separada, nunca `commit()`, para não poluir dados reais.

### Phase Requirements → Test Map

| Req ID (critério ROADMAP) | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| Critério 1 (`/adequar` grava nome_clifor/pedido/produto/cor_produto/preco1/valor_embalado/qtde_embalada) | Fluxo completo mockado grava linha Linx com os campos certos | integration (mockado) | `... pytest app/tests/test_pedidos_routes.py -k adequar_pedidos_fluxo_completo -v` | ❌ Wave 0 — teste existente precisa dos novos mocks/asserts |
| Critério 2 (`/sem_adequar` grava a mesma linha) | idem, fluxo sem adequação | integration (mockado) | `... pytest app/tests/test_pedidos_routes.py -k sem_adequar -v` | ❌ Wave 0 — idem |
| Critério 3 (mesma transação — rollback conjunto) | Falha após salvar linha Linx desfaz `ordens_reserva` também | integration (sessão real, sem TestClient) | `... pytest app/tests/test_pedidos_routes.py -k rollback_conjunto -v` | ❌ Wave 0 — teste novo (molde na seção "Testes" acima) |
| Critério 4 (upsert, não duplica) | 2 chamadas sucessivas de `salvar_linhas_linx` para o mesmo par atualizam, não duplicam | integration (sessão real, sem TestClient) | `... pytest app/tests/test_pedidos_routes.py -k salvar_linhas_linx -v` | ❌ Wave 0 — teste novo, molde de `test_salvar_ordens_reserva_par_existente_atualiza_sem_erro` |
| Critério 5 (colunas sem fonte ficam NULL) | Linha gravada tem `filial`/`romaneio`/etc. = `None` | unit (puro, sem banco) | `... pytest app/tests/test_ordem_reserva_linx.py -k campos_sem_fonte -v` | ❌ Wave 0 — teste novo, junto de `montar_linha_linx` |
| D-01 (zero posições → não grava + warning) | `montar_linha_linx` devolve `None`; caso de uso loga o warning nomeando o produto | unit (puro) + integration (caplog) | `... pytest app/tests/test_ordem_reserva_linx.py -k zero_posicoes -v` | ❌ Wave 0 — teste novo |
| D-03 (coerência aritmética) | `preco1`/`valor_embalado`/`qtde_embalada` batem com a fórmula, usando os números reais do CSV (`311,24 × 3 = 933,72`) | unit (puro) | `... pytest app/tests/test_ordem_reserva_linx.py -k coerencia_aritmetica -v` | ❌ Wave 0 — teste novo |

### Sampling Rate

- **Por commit de task:** `... pytest app/tests/test_ordem_reserva_linx.py app/tests/test_pedidos_routes.py -v` (rápido, cobre a lógica nova)
- **Por merge de wave:** suíte completa (`... pytest -q`) — hoje ~248 passed (número inclui trabalho de outra sessão em andamento; a Fase 3 deve adicionar a esse total, nunca reduzi-lo)
- **Phase gate:** suíte completa verde + os 5 critérios do ROADMAP mapeados na tabela acima, antes de `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `app/modules/pedidos/domain/ordem_reserva_linx.py` — não existe ainda (função `montar_linha_linx`)
- [ ] `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py` — não existe ainda (função `salvar_linhas_linx`)
- [ ] `app/modules/ingestao/api_leitura.py::obter_referencia_posicoes_por_produtos` — não existe ainda
- [ ] `app/tests/test_ordem_reserva_linx.py` — não existe ainda (testes puros de D-01/D-03/split)
- [ ] Casos novos em `app/tests/test_pedidos_routes.py` — mocks/asserts da gravação Linx nos 2 testes de fluxo existentes + teste novo de rollback conjunto + teste novo de upsert real

## Security Domain

`security_enforcement` não está definido em `.planning/config.json` → tratado como habilitado por padrão. Esta fase não expõe nenhum endpoint novo (os 3 endpoints tocados já existem e já são protegidos por RBAC) — a superfície de ataque nova é mínima.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Não | Nenhum endpoint novo; os 3 existentes já exigem JWT via `Depends(get_db)` + RBAC |
| V3 Session Management | Não | idem |
| V4 Access Control | Não (nesta fase) | `/adequar`/`/sem_adequar`/`/alterar-grade` já exigem role `OPERACIONAL`+ (RBAC pré-existente); esta fase não altera quem pode chamar |
| V5 Input Validation | Parcial | `cd_prod_cor` vem de dados já validados/ingeridos (não é input de usuário HTTP direto nesta fase); o split defensivo (`.partition`, Pitfall 4) é a validação relevante aqui, não input HTTP |
| V6 Cryptography | Não | Nenhum segredo/dado criptográfico armazenado em `ordens_reserva_linx` |

### Known Threat Patterns for este stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Exposição de dados de cliente (`nome_clifor`) numa tabela sem os mesmos controles de acesso de `ordens_reserva` | Information Disclosure | Fora do escopo desta fase (nenhum endpoint de LEITURA de `ordens_reserva_linx` é criado); revisar RBAC quando um endpoint de consulta/exportação for adicionado (rastreado implicitamente em INTG-01) |
| SQL injection via `IN (...)` dinâmico | Tampering | `select(...).where(Column.in_(chunk))` do SQLAlchemy usa bind parameters (nunca interpolação de string); manter esse padrão, nunca montar `IN (...)` via f-string |

## Sources

### Primary (HIGH confidence)
- `app/modules/pedidos/application/casos_uso.py` — os 3 fluxos exatos a modificar (nomes de função, ponto de commit, shapes de dados em mãos)
- `app/modules/pedidos/infrastructure/repositorio_ordens.py` — `salvar_ordens_reserva`, molde direto do novo repositório
- `app/modules/pedidos/models.py` — `OrdemReservaLinx` (82 colunas, PK `id`, `uq_ordens_reserva_linx_chave`)
- `app/modules/pedidos/domain/grade_linx.py` — `converter_grade_para_posicoes`, reaproveitada sem alteração de assinatura
- `app/modules/ingestao/api_leitura.py`, `app/modules/pedidos/infrastructure/repositorio_ingestao_readmodel.py` — padrão cross-context de leitura entre módulos
- `app/tests/test_pedidos_routes.py` — molde de autenticação/mocks dos fluxos + molde de testes de escrita sem TestClient
- `app/tests/test_ingestao_sync.py` (linhas ~440-471, ~495-517) — molde exato do teste de rollback conjunto (`side_effect` + `pytest.raises` + sessão de verificação separada)
- `app/tests/test_grade_linx.py` — molde de teste puro para função de domínio nova
- `.planning/phases/02-.../02-04-SUMMARY.md` — volume real de `produto_tamanho_posicao` (569.726 linhas) e evidência de que posições não começam em 1
- `.planning/phases/01-.../01-RESEARCH.md` — layout completo do CSV Linx (32 colunas, tipos, fontes/confiança)
- `.planning/codebase/CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md` — padrões de nomenclatura, teste e riscos conhecidos do repo

### Secondary (MEDIUM confidence)
- WebSearch "PostgreSQL asyncpg maximum number of parameters per query limit 65535" — confirma o limite de 65.535 parâmetros por statement do protocolo PostgreSQL (usado só para dimensionar o tamanho de chunk do Pattern 2, não é um risco real na escala desta fase)

### Tertiary (LOW confidence)
- Nenhuma — pesquisa 100% baseada em código-fonte real do repositório e documentos de planejamento já produzidos pelo próprio milestone.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — nenhuma tecnologia nova; todo o código-base já existe e foi lido diretamente
- Architecture: HIGH — os 3 pontos de integração, o padrão de upsert e o padrão de leitura cross-context têm precedente direto e citável no repositório
- Pitfalls: HIGH — derivados de diferenças estruturais reais (PK vs. chave natural) e do volume real medido (569.726 linhas)
- Mapeamento de campos: MEDIUM — os 7 campos exigidos pelos critérios 1/2/5 são HIGH (citados/derivados diretamente); `pedido_produto`/`pedido_cor_produto` são LOW quanto ao formato exato (por isso a recomendação de deixar NULL)
- Open Question 2 (`executar_alteracao_grade`/D-02): risco real de má interpretação — recomendo que o planner confirme com a usuária ANTES de implementar essa parte, para não construir sobre uma leitura errada de D-02

**Research date:** 2026-08-05
**Valid until:** Estável — nenhuma dependência de versão de biblioteca; revalidar apenas se a Open Question 2 (fluxo real de `PUT /alterar-grade`) for esclarecida de um jeito que mude o desenho de `executar_alteracao_grade`, ou se uma amostra real do CSV confirmar o formato de `pedido_produto`/`pedido_cor_produto`.
