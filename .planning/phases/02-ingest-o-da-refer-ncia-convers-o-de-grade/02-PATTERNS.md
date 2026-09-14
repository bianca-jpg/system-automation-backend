# Phase 2: Ingestão da referência + conversão de grade - Pattern Map

**Mapped:** 2026-08-05
**Files analyzed:** 8 (6 modificados + 1 novo módulo domain + 2 arquivos de teste)
**Analogs found:** 8 / 8

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `app/modules/ingestao/infrastructure/databricks_reader.py` (+ `ler_referencia_tamanhos`) | infrastructure/reader | request-response (HTTP → list[dict]) | `ler_faturamento_colecao` / `ler_estoque` no mesmo arquivo | exact (mesmo arquivo, mesmo papel) |
| `app/modules/ingestao/domain/traducao_databricks.py` (+ `_parse_posicao`) | domain/utility (parser puro) | transform | `_parse_int` no mesmo arquivo | exact |
| `app/modules/ingestao/domain/agregacao.py` (+ `agregar_referencia_tamanhos`) | domain/utility (agregação pura) | transform/CRUD-dedup | `agregar_estoque` no mesmo arquivo | exact |
| `app/modules/ingestao/infrastructure/repositorio_snapshot.py` (+ `substituir_referencia_tamanhos`) | infrastructure/repository | CRUD (delete+insert) | `substituir_faturamento_colecao` no mesmo arquivo | exact |
| `app/modules/ingestao/application/casos_uso.py` (+ `sincronizar_referencia_tamanhos`, `sincronizar_tudo` estendido) | application/use-case | event-driven orchestration | `sincronizar_faturamento_colecao` + `sincronizar_tudo` no mesmo arquivo | exact |
| `app/modules/ingestao/schemas.py` (+ campo `referencia_tamanho_posicao`, fix `faturamento_colecoes`) | model/schema (Pydantic response) | request-response | `SincronizacaoResponse` no mesmo arquivo | exact |
| `app/shared/config/settings.py` + `.env.example` (+ `databricks_tabela_tamanho_ref`) | config | — | `databricks_tabela_pedidos_processados` no mesmo arquivo | exact |
| `app/modules/pedidos/domain/grade_linx.py` (novo) — `converter_grade_para_posicoes` | domain (função pura) | transform | `app/modules/pedidos/domain/motor_adequacao.py` (`agrupar_por_produto`) | role-match (função pura que loga) |
| `app/modules/pedidos/service.py` (+ re-export) | service/barrel | — | bloco de re-export de `motor_adequacao` no mesmo arquivo | exact |
| `app/tests/test_ingestao_sync.py` (+ casos da 5ª fonte) | test | integration | casos existentes de `sincronizar_faturamento_colecao`/`sincronizar_tudo` no mesmo arquivo | exact |
| `app/tests/test_grade_linx.py` (novo) | test | unit (sem I/O) | `app/tests/test_pedidos_motor.py` (estilo geral) — ver Research para skeleton pronto | role-match |

## Pattern Assignments

### `app/modules/ingestao/infrastructure/databricks_reader.py` (infrastructure/reader)

**Analog:** `ler_faturamento_colecao` / `ler_estoque` (mesmo arquivo, linhas 32-43 e 69-127)

**Imports** (linhas 1-8):
```python
from app.shared.config.settings import get_settings
from app.shared.infrastructure.databricks_client import DatabricksError, executar_consulta
```

**Core pattern — reader simples, sem filtro** (`ler_estoque`, linhas 32-42):
```python
async def ler_estoque() -> list[dict]:
    settings = get_settings()
    tabela = settings.databricks_tabela_estoque
    if not tabela:
        raise DatabricksError("DATABRICKS_TABELA_ESTOQUE não configurada no .env.")

    statement = (
        "SELECT ds_canal, cd_prod_cor, sg_tamanho, qt_disponivel "
        f"FROM {tabela}"
    )
    return await executar_consulta(statement)
```

**Aplicar em `ler_referencia_tamanhos`:** mesmo esqueleto — `get_settings()` → checar `databricks_tabela_tamanho_ref` (raise `DatabricksError` se vazio) → `SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM {tabela}` → `return await executar_consulta(statement)`. Não reimplementar HTTP/paginação — `executar_consulta` já cobre INLINE + fallback EXTERNAL_LINKS.

---

### `app/modules/ingestao/domain/traducao_databricks.py` (domain/utility)

**Analog:** `_parse_int` (linhas 5-11)

**Core pattern**:
```python
def _parse_int(v) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v or "").strip()
    return int(float(s.replace(",", "."))) if s else 0
```

**Aplicar em `_parse_posicao`:** reaproveitar `_parse_int` e só adicionar validação de faixa 1..48 por cima (retorna `None` para descarte em vez de `0`, pois `0` seria uma posição real inválida e mascararia o descarte — D-01):
```python
def _parse_posicao(v) -> int | None:
    n = _parse_int(v)
    return n if 1 <= n <= 48 else None
```
Nenhum import novo necessário (função vive no mesmo arquivo, sem dependências externas).

---

### `app/modules/ingestao/domain/agregacao.py` (domain/utility)

**Analog:** `agregar_estoque` (linhas 58-73), estilo de assinatura `tuple[dict, int]` compartilhado por todas as 4 funções irmãs

**Imports** (linhas 1-12):
```python
from collections import defaultdict

from app.modules.ingestao.domain.traducao_databricks import (
    _normalizar_canal,
    _parse_bool,
    _parse_float,
    _parse_int,
    _texto,
)
```
(precisa importar `_parse_posicao` adicional quando criado)

**Core pattern — dedup + contagem de descarte**:
```python
def agregar_estoque(linhas: list[dict]) -> tuple[dict[tuple, int], int]:
    agregado: dict[tuple, int] = defaultdict(int)
    ignoradas = 0
    for linha in linhas:
        cd = str(linha.get("cd_prod_cor") or "").strip()
        tam = str(linha.get("sg_tamanho") or "").strip().upper()
        canal = _normalizar_canal(linha.get("ds_canal"))
        qt = _parse_int(linha.get("qt_disponivel"))
        if not cd or not tam or not canal:
            ignoradas += 1
            continue
        agregado[(cd, tam, canal)] += qt
    return agregado, ignoradas
```

**Desvio novo em `agregar_referencia_tamanhos` (sem precedente exato no arquivo — D-03/D-04):** estender a assinatura para `tuple[dict, int, dict[str, list[str]]]`, mantendo um índice reverso auxiliar `posicao_por_produto: dict[cd_prod_cor, dict[nr_posicao, sg_tamanho]]` para detectar colisão de posição entre dois tamanhos DIFERENTES do mesmo produto (distinto de dedup por chave `(cd, tam)`, que o dict `agrupado` já resolve nativamente). Acumular avisos em `conflitos_por_produto` (nunca logar dentro do loop — Pitfall 4 do RESEARCH.md). Ver RESEARCH.md seção "Pattern 3" para o corpo completo já pronto (linhas 234-265 do research).

---

### `app/modules/ingestao/infrastructure/repositorio_snapshot.py` (infrastructure/repository)

**Analog:** `substituir_faturamento_colecao` (linhas 47-58)

**Imports** (linhas 1-11):
```python
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestao.models import Estoque, FaturamentoColecao, Pedido, PedidoProcessadoErp
```
(adicionar `ProdutoTamanhoPosicao` a este import)

**Core pattern — delete+insert incondicional, sem commit**:
```python
async def substituir_faturamento_colecao(db: AsyncSession, linhas: Iterable[dict]) -> None:
    """Substitui o agregado de faturamento por (colecao, canal).
    Linhas esperadas: {colecao, canal, vl_planejado, vl_distribuido}."""
    await db.execute(delete(FaturamentoColecao))
    db.add_all(
        FaturamentoColecao(**{
            **linha,
            "vl_planejado": Decimal(str(linha["vl_planejado"])),
            "vl_distribuido": Decimal(str(linha["vl_distribuido"])),
        })
        for linha in linhas
    )
```

**Aplicar em `substituir_referencia_tamanhos`:** mesmo padrão, sem conversão Decimal (campos são inteiros): `delete(ProdutoTamanhoPosicao)` seguido de `db.add_all(ProdutoTamanhoPosicao(**item) for item in itens)`. **CRÍTICO:** este repositório permanece incondicional — NÃO colocar aqui o guard D-02 (ver Pitfall 3 do RESEARCH.md); a decisão "chamar ou não" pertence ao caso de uso.

---

### `app/modules/ingestao/application/casos_uso.py` (application/use-case)

**Analog:** `sincronizar_faturamento_colecao` (linhas 88-99) + `sincronizar_tudo` (linhas 102-114)

**Imports** (linhas 14-33) — seguem o padrão de agrupar por camada (agregacao → reader → repositorio):
```python
from app.modules.ingestao.domain.agregacao import (
    agregar_estoque,
    agregar_itens_pedidos,
    agregar_itens_processados_erp,
    processar_faturamento_colecao,
)
from app.modules.ingestao.infrastructure.databricks_reader import (
    ler_estoque,
    ler_faturamento_colecao,
    ler_pedidos_em_aberto,
    ler_pedidos_processados_erp,
)
from app.modules.ingestao.infrastructure.repositorio_snapshot import (
    substituir_estoque,
    substituir_faturamento_colecao,
    substituir_pedidos,
    substituir_pedidos_processados_erp,
)
```
(estender cada bloco com os nomes novos: `agregar_referencia_tamanhos`, `ler_referencia_tamanhos`, `substituir_referencia_tamanhos`)

**Core pattern — sync de uma fonte (sem guard)**:
```python
async def sincronizar_faturamento_colecao(db: AsyncSession) -> int:
    linhas = await ler_faturamento_colecao()
    processadas = processar_faturamento_colecao(linhas)
    await substituir_faturamento_colecao(db, processadas)
    logger.info(
        "Faturamento por coleção sincronizado: %d pontos (%d linhas brutas).",
        len(processadas), len(linhas),
    )
    return len(processadas)
```

**Orquestração — `sincronizar_tudo` (linhas 102-114, ponto de comprometimento único)**:
```python
async def sincronizar_tudo(db: AsyncSession) -> dict[str, int]:
    """Sincroniza pedidos, estoque, processados-ERP e faturamento numa única transação (full refresh)."""
    pedidos = await sincronizar_pedidos(db)
    estoque = await sincronizar_estoque(db)
    processados_erp = await sincronizar_pedidos_processados_erp(db)
    faturamento = await sincronizar_faturamento_colecao(db)
    await db.commit()
    return {
        "pedidos_inseridos": pedidos,
        "estoque_chaves": estoque,
        "processados_erp": processados_erp,
        "faturamento_colecoes": faturamento,
    }
```

**Adaptação com desvios D-01/D-02/D-03/D-04:** ver RESEARCH.md seção "Pattern 1" — código completo já pronto (linhas 162-194 do research) para `sincronizar_referencia_tamanhos`, incluindo o guard `if not agrupado:` (retorna `SELECT COUNT(*)` atual da tabela, NÃO chama `substituir_referencia_tamanhos`) e o loop pós-agregação que loga 1 warning por produto em conflito. Adicionar a 5ª chamada em `sincronizar_tudo` ANTES do `await db.commit()`, seguindo a mesma ordem posicional das 4 existentes.

---

### `app/modules/ingestao/schemas.py` (model/schema)

**Analog:** `SincronizacaoResponse` (arquivo completo, 8 linhas)

**Estado atual (com bug confirmado — falta `faturamento_colecoes`)**:
```python
from pydantic import BaseModel


class SincronizacaoResponse(BaseModel):
    status: str
    pedidos_inseridos: int
    estoque_chaves: int
    processados_erp: int = 0
```

**Aplicar:** adicionar `faturamento_colecoes: int = 0` (fix do bug pré-existente, mesmo arquivo já tocado nesta fase) e `referencia_tamanho_posicao: int = 0` (campo novo da 5ª fonte). Sem `model_config` — Pydantic v2 usa `extra='ignore'` por default, então **qualquer campo faltante aqui é silenciosamente descartado da resposta HTTP** (bug confirmado por execução direta, ver RESEARCH.md Pitfall 1). Vale adicionar um assert de contrato no teste (`assert "referencia_tamanho_posicao" in resp.json()`).

---

### `app/shared/config/settings.py` + `.env.example` (config)

**Analog:** `databricks_tabela_pedidos_processados` (settings.py linhas 79-83)

**Core pattern**:
```python
# Tabela do ERP com os flags indica_reserva/indica_embalado — fonte de verdade
# sobre o que já foi processado de fato (reserva feita / pedido embalado).
databricks_tabela_pedidos_processados: str = Field(
    default="", validation_alias="DATABRICKS_TABELA_PEDIDOS_PROCESSADOS"
)
```

**Aplicar:**
```python
databricks_tabela_tamanho_ref: str = Field(
    default="", validation_alias="DATABRICKS_TABELA_TAMANHO_REF"
)
```
E em `.env.example` (mesmo bloco de `DATABRICKS_TABELA_PEDIDOS`/`ESTOQUE`/`PEDIDOS_PROCESSADOS`, linhas 35-39):
```bash
# Referência tamanho->posição da grade Linx (view programa_estagio.refined.system_automation_prod_tamanho_ref)
DATABRICKS_TABELA_TAMANHO_REF=catalogo.schema.tamanho_ref
```

---

### `app/modules/pedidos/domain/grade_linx.py` (novo — domain, função pura)

**Analog de estilo:** `app/modules/pedidos/domain/motor_adequacao.py` — precedente de "função pura que loga" (docstring do arquivo, linha 4: "Todas as funções aqui são puras (sem banco/HTTP)")

**Imports/estrutura do módulo análogo** (linhas 1-19):
```python
"""Motor de adequação: casamento pedido x estoque, isolado por canal.

Orquestração sem I/O — `processar_pedidos` -> `_processar_pedidos_canal` ->
`adequar_grade_produto`. Todas as funções aqui são puras (sem banco/HTTP).
"""

import logging
...

logger = logging.getLogger(__name__)
```

**Core pattern — função pura que agrega e loga um resumo (não por item)** (`agrupar_por_produto`, linhas 34-54):
```python
def agrupar_por_produto(dados: list, ja_processados: set) -> dict:
    estrutura = defaultdict(lambda: defaultdict(list))
    ignorados = set()
    for item in dados:
        par = (item["nr_pedido"], item["cd_prod_cor"])
        if par in ja_processados:
            ignorados.add(par)
            continue
        estrutura[item["cd_prod_cor"]][item["nr_pedido"]].append(item)
    if ignorados:
        logger.info(
            "[Skip] %d par(es) (pedido, produto) já processados anteriormente.",
            len(ignorados),
        )
    return estrutura
```

**Aplicar em `converter_grade_para_posicoes`:** "puro" neste código-base significa "sem I/O de banco/rede", não "sem logging" — a função loga diretamente via `logger.warning` (agregado por produto — cada chamada já é 1 produto, então é natural 1 linha por chamada) e TAMBÉM devolve `(posicoes, tamanhos_ignorados)` para permitir asserts diretos no teste sem `caplog` (padrão sem precedente no repo — ver RESEARCH.md Pitfall 5). Corpo completo pronto em RESEARCH.md "Pattern 4" (linhas 278-324 do research):
```python
def converter_grade_para_posicoes(
    grade: dict[str, int], referencia: dict[str, int], cd_prod_cor: str
) -> tuple[dict[str, int], list[str]]:
    posicoes = {f"e{n}": 0 for n in range(1, 49)}
    ignorados: list[str] = []
    conflitos: list[str] = []
    ocupante_da_posicao: dict[int, str] = {}

    for sg_tamanho, qtd in grade.items():
        pos = referencia.get(sg_tamanho)
        if pos is None:
            ignorados.append(sg_tamanho)
            continue
        if pos in ocupante_da_posicao and ocupante_da_posicao[pos] != sg_tamanho:
            conflitos.append(f"{ocupante_da_posicao[pos]}/{sg_tamanho}->pos{pos}")
        ocupante_da_posicao[pos] = sg_tamanho
        posicoes[f"e{pos}"] = qtd

    if ignorados:
        logger.warning("produto %s: tamanhos sem posição na referência: %s", cd_prod_cor, ignorados)
    if conflitos:
        logger.warning("produto %s: conflito de posição na grade: %s", cd_prod_cor, conflitos)

    return posicoes, ignorados
```

---

### `app/modules/pedidos/service.py` (barrel/re-export)

**Analog:** bloco de re-export de `motor_adequacao` (linhas 11-17, 45-61)

**Core pattern**:
```python
from app.modules.pedidos.domain.motor_adequacao import (
    adequar_grade_produto,
    agrupar_por_produto,
    is_sem_credito,
    marcar_stand_by,
    processar_pedidos,
)
...
__all__ = [
    ...
    # motor de adequação e relatórios (consumidos por test_pedidos_motor.py)
    "adequar_grade_produto",
    "agrupar_por_produto",
    "is_sem_credito",
    "marcar_stand_by",
    "processar_pedidos",
    ...
]
```

**Aplicar:** adicionar `from app.modules.pedidos.domain.grade_linx import converter_grade_para_posicoes` e incluir `"converter_grade_para_posicoes"` no `__all__` (comentário sugerido: `# conversão grade->posições (consumida por test_grade_linx.py e pela Fase 3)`). **Obrigatório** — sem isso, os testes que importam via `from app.modules.pedidos.service import converter_grade_para_posicoes` (mesmo estilo de `test_pedidos_motor.py`) falham, e a Fase 3 (consumidora real) também depende deste ponto único de import.

---

### `app/tests/test_ingestao_sync.py` (test/integration)

**Analog:** casos existentes de `sincronizar_faturamento_colecao`/`sincronizar_tudo` (linhas 1-31, 259-330)

**Imports/fixture de mock** (linhas 14-30):
```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select

from app.modules.ingestao import service
from app.modules.ingestao.models import Estoque, Pedido, PedidoProcessadoErp
from app.shared.config.settings import get_settings
from app.shared.database.session import async_session_factory
from app.shared.infrastructure.databricks_client import DatabricksError


def _mock_consulta(rows):
    return patch(
        "app.modules.ingestao.infrastructure.databricks_reader.executar_consulta",
        new=AsyncMock(return_value=rows),
    )
```
(adicionar `ProdutoTamanhoPosicao` ao import de `models`)

**Core pattern — teste de orquestração da 5ª etapa em `sincronizar_tudo`** (linhas 259-285, estender para 5 mocks):
```python
async def test_sincronizar_tudo_orquestra_as_quatro_etapas_e_soma_resultado():
    db_fake = AsyncMock()
    with (
        patch("app.modules.ingestao.application.casos_uso.sincronizar_pedidos", new=AsyncMock(return_value=3)) as m1,
        patch("app.modules.ingestao.application.casos_uso.sincronizar_estoque", new=AsyncMock(return_value=7)) as m2,
        patch("app.modules.ingestao.application.casos_uso.sincronizar_pedidos_processados_erp", new=AsyncMock(return_value=2)) as m3,
        patch("app.modules.ingestao.application.casos_uso.sincronizar_faturamento_colecao", new=AsyncMock(return_value=6)) as m4,
    ):
        resultado = await service.sincronizar_tudo(db_fake)

    m1.assert_awaited_once_with(db_fake)
    ...
    db_fake.commit.assert_awaited_once()
    assert resultado == {...}
```
Adicionar o 5º mock (`sincronizar_referencia_tamanhos`) e o campo `referencia_tamanho_posicao` no dict esperado.

**Padrão do guard D-02 (skeleton pronto, RESEARCH.md linhas 460-478):** usa `async_session_factory()` real sem commit, adiciona um `ProdutoTamanhoPosicao` via `session.flush()`, mocka a view devolvendo `[]`, e verifica que a linha pré-existente permanece intacta após `sincronizar_referencia_tamanhos`.

---

### `app/tests/test_grade_linx.py` (novo — test/unit, sem I/O)

**Analog:** estilo geral de `app/tests/test_pedidos_motor.py` (testes puros do módulo `pedidos`, sem mocks de banco/HTTP)

**Padrão de import** (idêntico ao usado em `test_pedidos_motor.py`):
```python
from app.modules.pedidos.service import converter_grade_para_posicoes
```

**Estrutura recomendada** (RESEARCH.md linhas 483-510) — asserts diretos sobre o valor de retorno, evitando `caplog` (padrão sem precedente no repo):
```python
def test_converter_grade_para_posicoes_com_dados_reais():
    grade = {"P": 2, "M": 10, "GG": 1}
    posicoes, ignorados = converter_grade_para_posicoes(
        grade, _REFERENCIA_REAL_PRODUTO_X, cd_prod_cor="PRODUTO_X|001"
    )
    assert posicoes["e3"] == 2
    assert ignorados == []
```

**ATENÇÃO — bloqueio conhecido:** a fixture `_REFERENCIA_REAL_PRODUTO_X` precisa ser capturada com dados REAIS via `psql` após uma sincronização real da 5ª fonte (Open Question 3 do RESEARCH.md — `produto_tamanho_posicao` está vazia hoje). O plano deve incluir uma task `checkpoint:human-verify` antes de escrever este arquivo com valores definitivos.

## Shared Patterns

### Full refresh sem commit próprio (delete+insert)
**Source:** `app/modules/ingestao/infrastructure/repositorio_snapshot.py` (docstring do arquivo, linhas 1-3)
**Apply to:** `substituir_referencia_tamanhos`
```python
"""Persistência das 3 tabelas de snapshot (full refresh: delete + insert na
mesma transação, sem commit próprio — quem decide quando commitar é
sincronizar_tudo)."""
```
Regra: repositórios de snapshot são sempre incondicionais e simétricos entre si — nenhuma lógica de negócio (guards) deve viver ali.

### Parse tolerante (nunca lança exceção)
**Source:** `app/modules/ingestao/domain/traducao_databricks.py`, `_parse_int` (linhas 5-11)
**Apply to:** `_parse_posicao`
Toda conversão vinda do Databricks passa por um `_parse_*` que nunca lança — na pior hipótese devolve um valor seguro. Descarte de linha inválida é decisão da camada de agregação, não do parser.

### Log de resumo único no caso de uso (nunca por linha)
**Source:** `sincronizar_estoque`/`sincronizar_faturamento_colecao` (casos_uso.py, linhas 51-63, 88-99)
**Apply to:** `sincronizar_referencia_tamanhos`
```python
logger.info(
    "... sincronizado: %d itens (%d linhas brutas, %d descartadas/ignoradas).",
    len(agregado), len(linhas), descartadas,
)
```
Extensão nova desta fase (D-04): além do resumo, 1 warning agregado por produto para conflitos/descartes — nunca dentro do loop de linhas brutas.

### Env var de tabela origem: settings.py + .env.example
**Source:** `app/shared/config/settings.py` linhas 79-83 + `.env.example` linhas 35-39
**Apply to:** `databricks_tabela_tamanho_ref`
Padrão: `Field(default="", validation_alias="DATABRICKS_TABELA_<NOME>")` em `settings.py`, com linha correspondente comentada em `.env.example` no mesmo bloco das outras tabelas Databricks.

### Barrel re-export em `service.py` (obrigatório para módulos consumidos por teste)
**Source:** `app/modules/pedidos/service.py` linhas 11-17, 45-61
**Apply to:** `grade_linx.py` → `service.py`
Toda função de domínio pura consumida por teste (`test_pedidos_motor.py`, `test_grade_linx.py` novo) OU por outra camada (Fase 3) precisa ser importada e listada em `__all__` no `service.py` do módulo — é o ponto único de import externo.

### `extra='ignore'` silencioso do Pydantic v2 (armadilha confirmada)
**Source:** `app/modules/ingestao/schemas.py`, `SincronizacaoResponse`
**Apply to:** qualquer schema de resposta que recebe `**dict` maior que os campos declarados
Sem `model_config` explícito, campos não declarados passados via `**kwargs` no construtor são descartados sem erro nem warning — confirmado por execução direta (ver RESEARCH.md Pitfall 1). Sempre declarar todo campo que `sincronizar_tudo` devolve.

## No Analog Found

Nenhum arquivo desta fase ficou sem analog — todos os 11 itens têm um "irmão" direto no mesmo módulo/arquivo (fontes 1-4 da ingestão) ou um precedente de estilo claro (`motor_adequacao.py` para função pura que loga). Os únicos elementos genuinamente sem precedente EXATO no código (não sem analog, mas sem cópia 1:1 possível) estão documentados no RESEARCH.md como padrões novos, com corpo de código já pronto:

| Elemento | Motivo | Onde está o corpo pronto |
|---|---|---|
| Guard "0 linhas válidas → não substituir" (D-02) | Nenhuma das 4 fontes existentes tem esse guard — é a primeira exceção ao full-refresh incondicional | RESEARCH.md "Pattern 1", linhas 162-194 |
| Log agregado por produto (D-03/D-04) | As 4 agregações existentes só emitem 1 contador global; nenhuma loga por produto | RESEARCH.md "Pattern 3", linhas 234-265 |
| Índice reverso `posicao_por_produto` para detectar conflito de posição | Nenhuma agregação existente precisa detectar colisão cruzada de chave | RESEARCH.md "Pattern 3", mesmo bloco |

## Metadata

**Analog search scope:** `app/modules/ingestao/` (infrastructure, domain, application, schemas.py), `app/modules/pedidos/domain/` e `service.py`, `app/shared/config/settings.py`, `.env.example`, `app/tests/test_ingestao_sync.py`
**Files scanned:** 8 arquivos de código-fonte lidos integralmente + `.env.example`/`settings.py` (seções) + `test_ingestao_sync.py` (seções-chave)
**Pattern extraction date:** 2026-08-05
