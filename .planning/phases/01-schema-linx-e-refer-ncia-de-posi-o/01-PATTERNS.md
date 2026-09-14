# Phase 1: Schema Linx e referência de posição - Pattern Map

**Mapped:** 2026-08-04
**Files analyzed:** 5 (2 models, 1 migration, 2 arquivos de import)
**Analogs found:** 5 / 5

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `app/modules/ingestao/models.py` (+ `ProdutoTamanhoPosicao`) | model | CRUD (full-refresh, tabela referência) | `Estoque`/`FaturamentoColecao` no mesmo arquivo (linhas 83-122) | exact — mesmo arquivo, mesmo estilo `Mapped` |
| `app/modules/pedidos/models.py` (+ `OrdemReservaLinx`) | model | CRUD (projeção de saída, sem FK) | `OrdemReserva` no mesmo arquivo (linhas 33-50) | exact — mesmo arquivo, mesmo estilo `Column` |
| `alembic/versions/015_*.py` | migration | batch (DDL puro, create_table) | `alembic/versions/013_faturamento_colecao.py` (arquivo completo) | exact — mesmo padrão simples sem condicional |
| `alembic/env.py` (edição) | config | event-driven (import time, popula `target_metadata`) | próprio arquivo, linhas 10-26 (bloco de imports existente) | exact — só estender bloco já existente |
| `app/tests/test_schema_guard.py` (edição) | test | request-response (assert via `information_schema`) | próprio arquivo, linhas 23-36 (bloco de imports existente) | exact — só estender bloco já existente |

## Pattern Assignments

### `app/modules/ingestao/models.py` — nova classe `ProdutoTamanhoPosicao` (model, CRUD full-refresh)

**Analog:** classes `Estoque` e `FaturamentoColecao` no mesmo arquivo (`app/modules/ingestao/models.py`, linhas 83-122)

**Imports pattern** (já existentes no topo do arquivo, linhas 8-14 — nenhum import novo necessário, os símbolos já estão importados):
```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base
```

**Core pattern** (estilo `Mapped`/`mapped_column`, PK autoincrement + `UniqueConstraint` de chave natural + `synced_at` de auditoria) — copiar a forma de `Estoque` (linhas 83-98):
```python
class Estoque(Base):
    __tablename__ = "estoque"
    __table_args__ = (
        UniqueConstraint(
            "cd_prod_cor", "sg_tamanho", "canal", name="uq_estoque_chave"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cd_prod_cor: Mapped[str] = mapped_column(String(64), nullable=False)
    sg_tamanho: Mapped[str] = mapped_column(String(16), nullable=False)
    canal: Mapped[str] = mapped_column(String(32), nullable=False)
    qt_disponivel: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

**Aplicar ao novo model** (adaptação já validada em RESEARCH.md "Code Examples", trocar apenas nome/colunas):
```python
class ProdutoTamanhoPosicao(Base):
    """Referência tamanho -> posição da grade Linx (view Databricks
    `system_automation_prod_tamanho_ref`). Full refresh a cada sync, como as demais
    fontes do módulo ingestao.
    """

    __tablename__ = "produto_tamanho_posicao"
    __table_args__ = (
        UniqueConstraint(
            "cd_prod_cor", "sg_tamanho", name="uq_produto_tamanho_posicao_chave"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cd_prod_cor: Mapped[str] = mapped_column(String(64), nullable=False)
    sg_tamanho: Mapped[str] = mapped_column(String(16), nullable=False)
    nr_posicao: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

**Sem tratamento de erro/validação específico:** models `ingestao` são declarações ORM puras; validação/erro fica na camada de serviço (fora de escopo desta fase).

---

### `app/modules/pedidos/models.py` — nova classe `OrdemReservaLinx` (model, CRUD projeção de saída)

**Analog:** classe `OrdemReserva` no mesmo arquivo (`app/modules/pedidos/models.py`, linhas 33-50)

**Imports pattern** — os imports existentes no topo (linhas 13-16) **precisam ser estendidos** com `UniqueConstraint` (não usado hoje neste arquivo, mas presente em `ingestao/models.py`) e todos os tipos de coluna do layout Linx:
```python
# Atual (linhas 13-16):
from sqlalchemy import Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB

from app.shared.database.base import Base

# Precisa virar (adicionar Boolean, Numeric, UniqueConstraint — JSONB permanece
# porque OrdemReserva/PedidoModificacao continuam usando):
from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB

from app.shared.database.base import Base
```

**Core pattern** (estilo `Column` sem `Mapped`, chave de controle NOT NULL + campos de layout externo nullable) — copiar a forma de `OrdemReserva` (linhas 33-50):
```python
class OrdemReserva(Base):
    __tablename__ = "ordens_reserva"

    nr_pedido = Column(Integer, primary_key=True)
    cd_prod_cor = Column(String(64), primary_key=True)
    tipo = Column(String(8), nullable=False)  # 'sem' | 'com'
    itens = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    aprovado_em = Column(DateTime(timezone=True), nullable=True)
```

**Aplicar ao novo model** (via RESEARCH.md "Code Examples" — usar `id` autoincrement + `UniqueConstraint(nr_pedido, cd_prod_cor)` em vez de PK composta, decisão travada em PROJECT.md; declarar `e1..e48` explicitamente, uma linha por coluna, NUNCA via loop/`exec` no model — só na migration isso é aceitável):
```python
class OrdemReservaLinx(Base):
    """Bandeja de saída no layout aceito pelo ERP Linx (Tabelas_de_OR.csv).

    Correlaciona com OrdemReserva via (nr_pedido, cd_prod_cor), mas SEM FK: é
    uma projeção de saída para um sistema externo, não o modelo interno.
    """

    __tablename__ = "ordens_reserva_linx"
    __table_args__ = (
        UniqueConstraint(
            "nr_pedido", "cd_prod_cor", name="uq_ordens_reserva_linx_chave"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    nr_pedido = Column(Integer, nullable=False, index=True)
    cd_prod_cor = Column(String(64), nullable=False)
    tipo = Column(String(8), nullable=False)  # 'sem' | 'com'
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Layout Linx (Tabelas_de_OR.csv) — todas nullable; ver 01-RESEARCH.md
    # "Layout completo do CSV Linx" para tipo/fonte de cada coluna.
    nome_clifor = Column(String(255), nullable=True)
    produto = Column(String(64), nullable=True)
    cor_produto = Column(String(16), nullable=True)
    filial = Column(String(32), nullable=True)
    item = Column(Integer, nullable=True)
    pedido = Column(Integer, nullable=True)
    pedido_cor_produto = Column(String(64), nullable=True)
    romaneio = Column(String(32), nullable=True)
    caixa = Column(String(32), nullable=True)
    pedido_produto = Column(String(64), nullable=True)
    packs = Column(Integer, nullable=True)
    entrega = Column(DateTime(timezone=True), nullable=True)
    caixa_fechada = Column(Boolean, nullable=True)
    representante = Column(String(255), nullable=True)
    ipi = Column(Numeric(14, 2), nullable=True)
    preco1 = Column(Numeric(14, 2), nullable=True)
    preco2 = Column(Numeric(14, 2), nullable=True)
    preco3 = Column(Numeric(14, 2), nullable=True)
    preco4 = Column(Numeric(14, 2), nullable=True)
    desconto_item = Column(Numeric(14, 2), nullable=True)
    valor_embalado = Column(Numeric(14, 2), nullable=True)
    qtde_embalada = Column(Integer, nullable=True)
    origem = Column(String(32), nullable=True)
    mata_saldo = Column(Boolean, nullable=True)
    e1 = Column(Integer, nullable=True)
    e2 = Column(Integer, nullable=True)
    # ... e3 até e47, uma linha cada, explícito (ver anti-pattern abaixo)
    e48 = Column(Integer, nullable=True)
    item_pedido = Column(Integer, nullable=True)
    ordem_producao = Column(String(64), nullable=True)
    licenciado_royalties = Column(Boolean, nullable=True)
    percent_desconto = Column(Numeric(6, 2), nullable=True)
    caixa_virtual = Column(String(32), nullable=True)
```

**Anti-pattern a evitar (documentado em RESEARCH.md Pitfall 4):** não adicionar `ForeignKeyConstraint` de `nr_pedido`/`cd_prod_cor` para `OrdemReserva` — são campos de correlação, não FK (tabela é bandeja de saída independente).

---

### `alembic/versions/015_schema_linx_referencia_posicao.py` (migration, batch DDL)

**Analog:** `alembic/versions/013_faturamento_colecao.py` (arquivo completo, 42 linhas — reproduzido abaixo)

```python
"""tabela faturamento_colecao (agregado pré-calculado do ERP por semestre × canal)

Revision ID: 013
Revises: 012
Create Date: 2026-08-03

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "faturamento_colecao",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("colecao", sa.Integer(), nullable=False),
        sa.Column("canal", sa.String(length=32), nullable=False),
        sa.Column("vl_planejado", sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column("vl_distribuido", sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("colecao", "canal", name="uq_faturamento_colecao_canal"),
    )


def downgrade() -> None:
    op.drop_table("faturamento_colecao")
```

**Como adaptar para a 015:**
- `revision = "015"`, `down_revision = "014"` (head atual confirmado em CLAUDE.md).
- Duas chamadas `op.create_table` (`produto_tamanho_posicao` primeiro, `ordens_reserva_linx` depois) — sem lógica condicional, sem `information_schema` (isso pertence só à migration 014, que trata drift em tabelas já populadas — **não copiar esse padrão**, ver RESEARCH.md Pitfall 2).
- DDL completo já está pronto em `01-RESEARCH.md`, seção "Pattern 1" (linhas 174-250 do research) — usar literalmente, incluindo o atalho `*[sa.Column(f"e{i}", sa.Integer(), nullable=True) for i in range(1, 49)]` (aceitável só na migration, nunca no model).
- `downgrade()`: `op.drop_table` na ordem inversa (`ordens_reserva_linx` primeiro, depois `produto_tamanho_posicao`).

**Erro/validação:** não aplicável — DDL estático sem `op.execute` dinâmico, sem interpolação de input externo (ver RESEARCH.md "Known Threat Patterns").

---

### `alembic/env.py` (config, edição de bloco de imports existente)

**Analog:** o próprio arquivo, bloco de imports já existente (linhas 10-26)

**Estado atual:**
```python
from app.modules.ingestao.models import (  # noqa: F401
    Estoque,
    FaturamentoColecao,
    Pedido,
    PedidoProcessadoErp,
)
from app.modules.pedidos.models import (  # noqa: F401
    OrdemReserva,
    PedidoModificacao,
    PedidoProcessado,
)
```

**Como editar:** adicionar `ProdutoTamanhoPosicao` (ordem alfabética, ao lado de `Pedido`/`PedidoProcessadoErp`) e `OrdemReservaLinx` (ordem alfabética, ao lado de `OrdemReserva`):
```python
from app.modules.ingestao.models import (  # noqa: F401
    Estoque,
    FaturamentoColecao,
    Pedido,
    PedidoProcessadoErp,
    ProdutoTamanhoPosicao,
)
from app.modules.pedidos.models import (  # noqa: F401
    OrdemReserva,
    OrdemReservaLinx,
    PedidoModificacao,
    PedidoProcessado,
)
```

Restante do arquivo (linhas 1-9, 27-75) **não muda**.

---

### `app/tests/test_schema_guard.py` (test, edição de bloco de imports existente)

**Analog:** o próprio arquivo, bloco de imports já existente (linhas 23-36)

**Estado atual:**
```python
from app.modules.ingestao.models import (  # noqa: F401
    Estoque,
    FaturamentoColecao,
    Pedido,
    PedidoProcessadoErp,
)
from app.modules.parametros.models import Parametro, ParametroChangeRequest  # noqa: F401
from app.modules.pedidos.models import (  # noqa: F401
    OrdemReserva,
    PedidoModificacao,
    PedidoProcessado,
)
```

**Como editar:** mesmo par de nomes que em `env.py`:
```python
from app.modules.ingestao.models import (  # noqa: F401
    Estoque,
    FaturamentoColecao,
    Pedido,
    PedidoProcessadoErp,
    ProdutoTamanhoPosicao,
)
from app.modules.parametros.models import Parametro, ParametroChangeRequest  # noqa: F401
from app.modules.pedidos.models import (  # noqa: F401
    OrdemReserva,
    OrdemReservaLinx,
    PedidoModificacao,
    PedidoProcessado,
)
```

O teste é parametrizado sobre `sorted(Base.metadata.tables)` (linha 49) — nenhuma outra mudança de código é necessária: as duas tabelas novas passam a ser testadas automaticamente por `test_colunas_do_model_existem_no_banco` e pelo teste de PK equivalente, assim que os imports acima existirem.

---

## Shared Patterns

### Estilo de model por módulo (não é um padrão único do projeto — é por arquivo)
**Fonte:** `app/modules/ingestao/models.py` (100% `Mapped`/`mapped_column`) vs. `app/modules/pedidos/models.py` (100% `Column` sem `Mapped`)
**Aplicar a:** `ProdutoTamanhoPosicao` segue `ingestao` (`Mapped`); `OrdemReservaLinx` segue `pedidos` (`Column`). Não uniformizar entre os dois arquivos — é inconsistência real e pré-existente do repo, não uma escolha desta fase.

### Import explícito de models (sem autodiscovery)
**Fonte:** `alembic/env.py` linhas 10-26, `app/tests/test_schema_guard.py` linhas 23-36
**Aplicar a:** todo model novo precisa ser importado nos DOIS lugares — senão o autogenerate-diff-check e o schema guard ficam cegos para a tabela nova (ver RESEARCH.md Pitfall 1).

### `create_table` simples, sem lógica condicional
**Fonte:** `alembic/versions/005_ingestao_databricks.py`, `009_pedidos_processados_erp.py`, `013_faturamento_colecao.py`
**Aplicar a:** migration 015 (tabelas 100% novas, sem drift possível). **Não copiar** o padrão condicional de `014_or_por_produto.py` (esse é exclusivo do cenário de tabela já populada com drift histórico).

### Colunas de auditoria/controle (`synced_at` / `created_at` com `server_default=func.now()`)
**Fonte:** todas as classes de `ingestao/models.py` (padrão `synced_at`) e `OrdemReserva`/`PedidoProcessado` em `pedidos/models.py` (padrão `created_at`)
**Aplicar a:** `ProdutoTamanhoPosicao.synced_at` e `OrdemReservaLinx.created_at`, exatamente como nos analogs.

## No Analog Found

Nenhum arquivo desta fase ficou sem analog — os 5 arquivos têm correspondência direta no próprio módulo/arquivo em que serão editados.

## Metadata

**Analog search scope:** `alembic/versions/` (005, 009, 013, 014), `app/modules/ingestao/models.py`, `app/modules/pedidos/models.py`, `alembic/env.py`, `app/tests/test_schema_guard.py`
**Files scanned:** 8
**Pattern extraction date:** 2026-08-04
