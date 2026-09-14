# Phase 3: Escrita da OR no formato Linx - Pattern Map

**Mapped:** 2026-08-05
**Files analyzed:** 7 (2 modificados, 5 criados)
**Analogs found:** 7 / 7

**Nota sobre o escopo real (CONTEXT.md D-02 REVISTA):** esta fase toca **somente**
`executar_adequacao` e `executar_sem_adequacao`. `executar_alteracao_grade` NÃO é
tocado — os trechos de RESEARCH.md que mencionam um "3º ponto de integração" ou
"D-02 espelho em `executar_alteracao_grade`" foram escritos antes da revisão do
CONTEXT.md e não se aplicam ao plano final; documentados aqui só para o planner
saber por que a pesquisa fala em 3 fluxos e o contexto fala em 2.

**Nota sobre trabalho paralelo:** outra sessão tem mudanças não commitadas em
`casos_uso.py`, `repositorio_ordens.py`, `models.py`, `service.py` e
`test_pedidos_routes.py` para a feature `estoque_virtual` (placebo documentado no
CLAUDE.md). Os excerpts abaixo foram lidos no estado atual do disco e podem
mudar de linha (mas não de nome de função) até a implementação. Por isso as
referências abaixo citam **nomes de função**, não números de linha, para os
arquivos afetados por essa sessão paralela. `estoque_virtual` NÃO é material
desta fase — é citado apenas como ponto de integração que a gravação Linx deve
respeitar (entrar na mesma transação, sem reordenar o gatilho existente).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `app/modules/pedidos/domain/ordem_reserva_linx.py` (NOVO) | domain (função pura) | transform | `app/modules/pedidos/domain/grade_linx.py` | exact |
| `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py` (NOVO) | service/repository | CRUD (upsert) | `carregar_modificacoes`/`upsert_modificacao` e `aprovar_ordem_reserva` em `repositorio_ordens.py` | role-match (chave natural ≠ PK) |
| `app/modules/ingestao/api_leitura.py` (MODIFICADO — nova função) | service (leitura cross-context) | request-response (batch SELECT) | funções existentes do próprio arquivo (`obter_estoque_por_canal`, etc.) | exact |
| `app/modules/pedidos/infrastructure/repositorio_ingestao_readmodel.py` (MODIFICADO — novo re-export) | service (wrapper cross-context) | request-response | próprio arquivo (`carregar_pares_processados_erp`) | exact |
| `app/modules/pedidos/application/casos_uso.py` (MODIFICADO — `executar_adequacao`, `executar_sem_adequacao`) | controller/caso de uso | CRUD + transação | os próprios `executar_adequacao`/`executar_sem_adequacao` (padrão já existente) | exact |
| `app/modules/pedidos/service.py` (MODIFICADO — re-export) | config/barrel | — | próprio arquivo (padrão de `__all__`) | exact |
| `app/tests/test_pedidos_routes.py` (MODIFICADO) + `app/tests/test_ordem_reserva_linx.py` (NOVO) | test | request-response / unit | `test_adequar_pedidos_fluxo_completo_mockado`, `test_salvar_ordens_reserva_par_*`, `app/tests/test_grade_linx.py`, `app/tests/test_ingestao_sync.py` (rollback) | exact |

## Pattern Assignments

### `app/modules/pedidos/domain/ordem_reserva_linx.py` (domain, transform)

**Analog:** `app/modules/pedidos/domain/grade_linx.py`

**Imports pattern:**
```python
"""Conversão pura da grade interna de tamanhos para as colunas posicionais e1..e48 do layout Linx — sem I/O de banco/rede."""

import logging

logger = logging.getLogger(__name__)
```
Repita esse cabeçalho: módulo `domain/` sem imports de SQLAlchemy, docstring de módulo explicando "sem I/O", `logger` só se for logar (aqui a decisão do CONTEXT.md/RESEARCH.md é: a função pura **não loga** — o warning de D-01 é do chamador, ver Pattern 4 abaixo). Importa `converter_grade_para_posicoes` de `grade_linx.py`:
```python
from app.modules.pedidos.domain.grade_linx import converter_grade_para_posicoes
```

**Core pattern — assinatura e corpo** (copiar a forma de `converter_grade_para_posicoes`: recebe dicts/primitivas já prontos, devolve dict/tupla, docstring explica os casos de borda e cita a decisão que motivou cada um):
```python
def converter_grade_para_posicoes(
    grade: dict[str, int], referencia: dict[str, int], cd_prod_cor: str
) -> tuple[dict[str, int], list[str]]:
    """Converte a grade interna `{sg_tamanho: qtd}` de UM produto nas colunas
    posicionais `{"e1": qtd, ..., "e48": qtd}`, usando a referência
    `{sg_tamanho: nr_posicao}` já filtrada para aquele `cd_prod_cor`.
    ...
    """
    posicoes = {f"e{n}": 0 for n in range(1, 49)}
    ...
    return posicoes, ignorados
```
A nova função `montar_linha_linx` segue o mesmo espírito: entra pura, sai `dict | None`
(D-01 = `None` quando `referencia_produto` está vazio, checado **antes** de chamar
`converter_grade_para_posicoes` — não a partir do 2º retorno `ignorados`, ver
RESEARCH.md Pitfall 1). Split de `cd_prod_cor` via `.partition("|")` (defensivo,
nunca `.split("|")` desestruturado — RESEARCH.md Pitfall 4).

**Error handling:** nenhum try/except — igual ao análogo, casos de borda são
tratados por `if`/`return None`, nunca exceção (mesmo padrão de `_montar_pedidos`/
`executar_adequacao`, onde `None` significa "caso de negócio esperado", não falha).

**Validation:** guard de divisão por zero (`qtde_embalada == 0`) antes de calcular
`preco1` — defensivo, não esperado (todo par com OR tem ao menos 1 item).

**Testing analog:** `app/tests/test_grade_linx.py` (72 linhas) — testes puros, sem
banco, sem mocks, só `assert` sobre o dict/tupla devolvido. Copiar essa estrutura
para `test_ordem_reserva_linx.py`.

---

### `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py` (repository, upsert)

**Analog primário (upsert por SELECT, não `db.get`):** `carregar_modificacoes` +
`aprovar_ordem_reserva` em `app/modules/pedidos/infrastructure/repositorio_ordens.py`
— **NÃO** usar `salvar_ordens_reserva`/`upsert_modificacao` como molde de lookup
porque ambos usam `db.get(Model, pk)`, e a PK de `OrdemReservaLinx` é `id`
(autoincrement), não a chave natural `(nr_pedido, cd_prod_cor)` (que é só
`UniqueConstraint`). Usar `db.get` aqui nunca encontraria a linha existente e toda
segunda gravação colidiria com `uq_ordens_reserva_linx_chave` (`IntegrityError`).

**Imports pattern** (idêntico ao arquivo análogo):
```python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

Par = tuple[int, str]
```
Import do model dentro da função, não no topo do módulo (padrão já usado em
todas as funções de `repositorio_ordens.py`: `from app.modules.pedidos.models import OrdemReserva` fica dentro do corpo).

**Core pattern — SELECT + decide (copiar a forma de `aprovar_ordem_reserva`, que
já faz `select(...).where(...)` seguido de laço que decide o que mudar):**
```python
async def aprovar_ordem_reserva(
    db: AsyncSession, nr_pedido: int, cd_prod_cor: str | None = None
) -> bool:
    from app.modules.pedidos.models import OrdemReserva

    stmt = select(OrdemReserva).where(OrdemReserva.nr_pedido == nr_pedido)
    if cd_prod_cor is not None:
        stmt = stmt.where(OrdemReserva.cd_prod_cor == cd_prod_cor)

    ors = (await db.execute(stmt)).scalars().all()
    if not ors:
        return False
    for obj in ors:
        if obj.aprovado_em is None:
            obj.aprovado_em = func.now()
    return True
```
`salvar_linhas_linx` deve fazer, por linha da lista recebida, um
`select(OrdemReservaLinx).where(OrdemReservaLinx.nr_pedido == ..., OrdemReservaLinx.cd_prod_cor == ...)`,
`scalar_one_or_none()`, e `db.add(OrdemReservaLinx(**linha))` se `None`, senão
`setattr` campo a campo (igual ao molde de `salvar_ordens_reserva`, que faz
`obj.tipo = tipo; obj.itens = itens` no ramo de UPDATE).

**Contrato "não commita":** docstring de módulo idêntica em espírito à de
`repositorio_ordens.py`:
```python
"""Repositório das tabelas próprias do módulo pedidos: ...
Todas operam no grão canônico `(nr_pedido, cd_prod_cor)` — ver models.py. Nenhuma
faz commit: quem decide quando commitar é o caso de uso.
"""
```

**Error handling:** nenhum — segue o padrão do módulo (deixa `IntegrityError`/etc.
propagar para o caso de uso decidir, nunca captura silenciosamente).

**Testing analog:** `test_salvar_ordens_reserva_par_novo_insere_sem_erro` e
`test_salvar_ordens_reserva_par_existente_atualiza_sem_erro` (`app/tests/test_pedidos_routes.py`)
— `async with async_session_factory() as session:`, chama o repositório direto,
`await session.flush()` (nunca `commit()`), e no segundo teste faz um segundo
upsert do mesmo par e confirma 1 linha só com os valores novos.

---

### `app/modules/ingestao/api_leitura.py` (nova função `obter_referencia_posicoes_por_produtos`)

**Analog:** as próprias funções já expostas nesse arquivo (`obter_estoque_por_canal`,
`obter_pedidos_processados_erp`, `obter_foto_estoque`) — mesma superfície
pública de leitura cross-context que `pedidos` consome via
`repositorio_ingestao_readmodel.py`.

**Core pattern — query em lote filtrada, nunca a tabela inteira:**
```python
from app.modules.ingestao.models import ProdutoTamanhoPosicao

async def obter_referencia_posicoes_por_produtos(
    db: AsyncSession, cd_prod_cors: set[str]
) -> dict[str, dict[str, int]]:
    if not cd_prod_cors:
        return {}
    rows = (
        await db.execute(
            select(
                ProdutoTamanhoPosicao.cd_prod_cor,
                ProdutoTamanhoPosicao.sg_tamanho,
                ProdutoTamanhoPosicao.nr_posicao,
            ).where(ProdutoTamanhoPosicao.cd_prod_cor.in_(cd_prod_cors))
        )
    ).all()
    referencia: dict[str, dict[str, int]] = {}
    for cd, tam, pos in rows:
        referencia.setdefault(cd, {})[tam] = pos
    return referencia
```
`ProdutoTamanhoPosicao` (model real, 4 colunas relevantes) tem
`UniqueConstraint("cd_prod_cor", "sg_tamanho", name="uq_produto_tamanho_posicao_chave")`
— confirma que `(cd_prod_cor, sg_tamanho)` é único, então `referencia[cd][tam] = pos`
nunca sobrescreve silenciosamente um valor divergente sem que isso seja um bug de
dado, não de código.

**Pitfall a evitar (RESEARCH.md Pitfall 2):** nunca fazer `select(ProdutoTamanhoPosicao)`
sem `.where(...)` — a tabela tem 569.726 linhas reais (medido na Fase 2).

**Wrapper cross-context** em `repositorio_ingestao_readmodel.py`, mesmo padrão de
`carregar_pares_processados_erp` (que já importa de `ingestao.api_leitura` com
alias):
```python
from app.modules.ingestao.api_leitura import (
    obter_referencia_posicoes_por_produtos as carregar_referencia_posicoes,
)
```

---

### `app/modules/pedidos/application/casos_uso.py` (`executar_adequacao`, `executar_sem_adequacao`)

**Analog:** os próprios fluxos, no estado atual (ver aviso de trabalho paralelo —
citar por nome de função, não linha).

**Ponto de integração exato — ANTES do `await db.commit()` já existente, DEPOIS
de `salvar_ordens_reserva`/`salvar_processados`/`recalcular_estoque_virtual`.**
Em `executar_adequacao`, a gravação Linx entra dentro do bloco
`if ors_a_gravar:`, na mesma ordem em que o `estoque_virtual` já está encaixado
(não reordenar o gatilho de `recalcular_estoque_virtual`):
```python
    if ors_a_gravar:
        await salvar_ordens_reserva(db, ors_a_gravar, tipo="com")
        await salvar_processados(db, pares_com_or)
        await recalcular_estoque_virtual(db)
        # <<< NOVO: gravação Linx entra aqui, antes do commit >>>
        await db.commit()
```
Em `executar_sem_adequacao`, mesma posição, entre `recalcular_estoque_virtual` e
`await db.commit()`:
```python
    await salvar_ordens_reserva(db, dict(resultados), tipo="sem")
    await salvar_processados(db, set(resultados.keys()))
    await recalcular_estoque_virtual(db)
    # <<< NOVO: gravação Linx entra aqui, antes do commit >>>
    await db.commit()
```

**Import pattern a seguir** (topo do arquivo, mesmo bloco de imports de
`repositorio_ordens`/`repositorio_ingestao_readmodel`):
```python
from app.modules.pedidos.infrastructure.repositorio_ingestao_readmodel import (
    carregar_referencia_posicoes,  # NOVO
    ...  # já existentes
)
from app.modules.pedidos.infrastructure.repositorio_ordens_linx import (
    salvar_linhas_linx,  # NOVO
)
from app.modules.pedidos.domain.ordem_reserva_linx import montar_linha_linx  # NOVO
```

**Warning de D-01 no ponto de chamada, não na função pura** — copiar o mesmo
espírito dos avisos agregados por produto já usados em `grade_linx.py`
(`logger.warning("produto %s: ...", cd_prod_cor, ...)`):
```python
cd_prod_cors = {cd for _nr, cd in ors_a_gravar}
referencia = await carregar_referencia_posicoes(db, cd_prod_cors)
linhas_linx = []
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

**Error handling:** nenhum try/except novo — deixar propagar (mesmo padrão dos
2 fluxos hoje: se algo falhar antes do commit, a exceção sobe e a sessão é
descartada sem commit, gerando o rollback conjunto exigido pelo critério 3).

---

### `app/modules/pedidos/service.py` (re-export — D-02a)

**Analog:** o próprio arquivo — bloco de imports + `__all__` já expõe
`salvar_ordens_reserva` (comentário `# escrita direta na camada de serviço
(consumida por test_pedidos_routes.py)`) e `converter_grade_para_posicoes`
(comentário `# conversão grade->posições Linx`).

**Pattern a copiar:**
```python
from app.modules.pedidos.domain.ordem_reserva_linx import montar_linha_linx
from app.modules.pedidos.infrastructure.repositorio_ordens_linx import salvar_linhas_linx

__all__ = [
    ...,
    # função pública de (re)gravar a linha Linx de um par — D-02a, chamável
    # de fora dos 2 fluxos de geração (ex.: futura edição manual, v1.2)
    "montar_linha_linx",
    "salvar_linhas_linx",
]
```
D-02a pede **uma função pública única** de (re)gravar a linha Linx de um par —
o par `montar_linha_linx` + `salvar_linhas_linx` já cobre isso sem precisar de
uma 3ª função nova: quem quiser regravar de fora chama os dois em sequência,
igual ao caso de uso faz.

---

### Testes

**`app/tests/test_ordem_reserva_linx.py` (NOVO)** — molde exato:
`app/tests/test_grade_linx.py` (72 linhas, sem banco, sem mocks). Casos mínimos
segundo RESEARCH.md: referência vazia → `None` (D-01); grade completa → todos os
campos + `e1..e48`; `preco1`/`valor_embalado`/`qtde_embalada` com os números reais
do CSV (`311,24 × 3 = 933,72`, D-03); `cd_prod_cor` sem `"|"` → defensivo, sem
exceção.

**`app/tests/test_pedidos_routes.py` (MODIFICADO)** — molde exato:
`test_adequar_pedidos_fluxo_completo_mockado` (função, não linha — outra sessão
está editando este arquivo). Padrão: `patch(...)` de TODAS as funções de I/O do
caso de uso com `AsyncMock`, request via `client.post(...)`, assert de shape +
`mock.assert_awaited_once()`. Adicionar aos 2 testes existentes:
```python
patch("app.modules.pedidos.application.casos_uso.carregar_referencia_posicoes", new=AsyncMock(return_value={"X": {"M": 1}})),
patch("app.modules.pedidos.application.casos_uso.salvar_linhas_linx", new=AsyncMock()) as mock_salvar_linx,
```
e no final: `mock_salvar_linx.assert_awaited_once()`.

**Teste de upsert real (sem TestClient)** — molde exato:
`test_salvar_ordens_reserva_par_novo_insere_sem_erro` /
`test_salvar_ordens_reserva_par_existente_atualiza_sem_erro` (mesmo arquivo,
função `async_session_factory`, `session.flush()`, nunca `commit()`).

**Teste de rollback conjunto (critério 3)** — molde exato:
`app/tests/test_ingestao_sync.py`, os testes que injetam `side_effect=RuntimeError`
num passo do meio via `patch(...)`, chamam o caso de uso dentro de
`async with async_session_factory() as session: with pytest.raises(RuntimeError): ...`,
e verificam com uma **segunda sessão** que nada foi persistido. Para esta fase,
injetar a falha em `salvar_linhas_linx` (o passo NOVO desta fase) — não em
`recalcular_estoque_virtual` (passo da sessão paralela, evitar tocar).

## Shared Patterns

### Repositórios não commitam
**Source:** docstring de `app/modules/pedidos/infrastructure/repositorio_ordens.py`
("Nenhuma faz commit: quem decide quando commitar é o caso de uso.")
**Apply to:** `repositorio_ordens_linx.py` inteiro.

### Domínio puro sem I/O, pode logar mas decide "gravar ou não" via `None`
**Source:** `app/modules/pedidos/domain/grade_linx.py` (aviso agregado por
produto) + `app/modules/pedidos/application/casos_uso.py::executar_adequacao`
(retorno `None` = caso de negócio, não erro).
**Apply to:** `domain/ordem_reserva_linx.py::montar_linha_linx` — decide `None`
silenciosamente; o `logger.warning` fica no caso de uso (Pattern 4 do RESEARCH.md).

### Upsert por SELECT + decide quando PK ≠ chave natural
**Source:** `carregar_modificacoes`/`aprovar_ordem_reserva` em `repositorio_ordens.py`.
**Apply to:** `repositorio_ordens_linx.py::salvar_linhas_linx` — nunca `db.get`
para `OrdemReservaLinx` (PK é `id`, chave natural é só `UniqueConstraint`).

### Query em lote filtrada, nunca a tabela inteira
**Source:** o próprio módulo `ingestao/api_leitura.py` (todas as funções
existentes filtram por período/coleção, nenhuma faz `select(Model)` sem `where`).
**Apply to:** `obter_referencia_posicoes_por_produtos` — sempre
`.where(ProdutoTamanhoPosicao.cd_prod_cor.in_(cd_prod_cors))`.

### Split defensivo de `cd_prod_cor`
**Source:** RESEARCH.md Pitfall 4 (`.partition("|")`, nunca `.split("|")`
desestruturado) — sem precedente direto no código ainda, mas é o único ponto do
módulo que precisa desse split; segue o espírito defensivo já usado em
`converter_grade_para_posicoes` (nunca lança exceção para dado de negócio
inesperado).
**Apply to:** `domain/ordem_reserva_linx.py::montar_linha_linx`.

### Barrel de `service.py` — reexport de tudo que é consumido por testes ou por outro módulo
**Source:** `app/modules/pedidos/service.py` (padrão de comentários por bloco em
`__all__`).
**Apply to:** adicionar `montar_linha_linx` e `salvar_linhas_linx` ao `__all__`
com comentário explicando D-02a.

## No Analog Found

Nenhum arquivo desta fase ficou sem analog — todos os 7 têm padrão direto e
citável no repositório atual (é o próprio ponto forte desta fase, confirmado em
RESEARCH.md: "nenhuma peça de infraestrutura nova").

## Metadata

**Analog search scope:** `app/modules/pedidos/` (domain, infrastructure,
application, service.py), `app/modules/ingestao/api_leitura.py`,
`app/modules/ingestao/models.py`, `app/tests/test_pedidos_routes.py`,
`app/tests/test_grade_linx.py`, `app/tests/test_ingestao_sync.py`.
**Files scanned:** 9 (todos lidos integralmente ou em seções direcionadas, sem
re-leitura de intervalos já vistos).
**Pattern extraction date:** 2026-08-05
