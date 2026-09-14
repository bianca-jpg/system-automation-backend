# Phase 16: Persistência do motivo de stand by - Pattern Map

**Mapped:** 2026-08-20
**Files analyzed:** 8 (1 migration nova, 1 model ORM, 1 módulo de vocabulário, 3 edições — motor/domain/repository/ports/service, 1 teste de migration, 1 ponto de limpeza no full refresh)
**Analogs found:** 8 / 8 (todos com analog forte — este é trabalho quase inteiramente de extensão de padrões já vigentes, não código genuinamente novo)

O `16-RESEARCH.md` já contém pseudocódigo quase pronto para consumo do planner (§ "Code Examples", § "Architecture Patterns"). Este documento complementa com os **trechos reais e verificados nesta sessão** (caminho + linha), preenchendo import styles exatos e mostrando o "antes/depois" de cada ponto de edição.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `alembic/versions/032_pedido_standby_motivo.py` (novo) | migration | CRUD (create table) | `alembic/versions/020_pedido_produto_read.py` (estrutura DDL); `031_auth_users_sso_provider.py` (docstring/cabeçalho enxuto) | exact |
| `app/modules/pedidos/domain/standby_motivo.py` (novo) | model/vocabulário | transform (constantes puras) | `app/modules/pedidos/domain/furo_de_grade.py` (módulo de domínio puro, sem I/O) | exact |
| `app/modules/pedidos/infrastructure/models.py` → `+ PedidoStandbyMotivo` | model | CRUD | `EstoqueVirtual` (mesmo arquivo, linhas 82-129) | exact |
| `app/modules/pedidos/domain/motor_adequacao.py` (editado) | service (domínio puro) | transform | próprio arquivo — `preteridos`/`_commitar_par` já existentes | exact (extensão in-place) |
| `app/modules/pedidos/processing/domain.py` → `PlanDraft` (editado) | model (DTO)/service | transform | próprio arquivo — `PlanDraft.__post_init__`, `build_processing_plan` | exact (extensão in-place) |
| `app/modules/pedidos/processing/application/ports.py` → `+ record_standby_reasons` | route/port (Protocol) | request-response (contrato) | `ProcessingPlannerSource.load_pedido_budget` (linhas 37-39) | exact |
| `app/modules/pedidos/processing/infrastructure/repository.py` → `+ record_standby_reasons`, `+ DELETE em apply_pairs` | service/CRUD | CRUD (upsert) + event-driven (limpeza em transação) | `store_plan` (linhas 106-158, upsert de `PedidoProcessamentoPlanModel`); `apply_pairs` (linhas 409-465, limpeza multi-tabela na mesma transação) | exact |
| `app/modules/pedidos/processing/application/service.py` → `_plan_once` (editado) | service (orquestração) | request-response | próprio arquivo — chamada de `store_plan` já existente (linha ~260-264) | exact (1 chamada nova) |
| `app/modules/ingestao/infrastructure/repositorio_snapshot.py` → `reconstruir_pedido_produto_read` (editado) | service | batch/event-driven | próprio arquivo — `DELETE FROM pedido_produto_read` (linha 256) + INSERTs (257-258) | exact (extensão in-place) |
| `app/tests/test_migration_032_pedido_standby_motivo.py` (novo, se a fase decidir cobrir) | test | batch (upgrade/downgrade) | `app/tests/test_migration_030_auth_otp_code_hash.py` (íntegro) | exact |

## Pattern Assignments

### 1. Migration Alembic — `alembic/versions/032_pedido_standby_motivo.py`

**Analogs:** `alembic/versions/020_pedido_produto_read.py` (estrutura de `create_table`/CHECK/downgrade) + `alembic/versions/031_auth_users_sso_provider.py` (cabeçalho enxuto, sem preflight SQL — mais próximo do tamanho desta migration).

**Cabeçalho/revisão** (padrão confirmado em `031`, linhas 1-28):
```python
"""torna password_hash opcional e adiciona auth_provider em auth_users

Revision ID: 031
Revises: 030
Create Date: 2026-08-13

[docstring explicando o PORQUÊ, não o QUE — mesmo estilo do RESEARCH.md já
redigiu para a migration 032]
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
```
**Confirmar a head real antes de codar:** `uv run alembic heads` (via Docker) — não assumir que `031` continua sendo a última; se outra migration entrou entre 14/15 e 16, `down_revision` muda.

**Import do dialeto Postgres** (padrão confirmado em `020_pedido_produto_read.py:34-35`, NÃO usar `sa.dialects.postgresql...`):
```python
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
...
sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
```
(A migration 032 precisa desse import para a coluna `job_id UUID`, ainda que não precise de `JSONB` como a 020.)

**`op.create_table` com `CheckConstraint`/`PrimaryKeyConstraint`** — molde direto de `020_pedido_produto_read.py:358-426` (mesma forma sintática: colunas → `CheckConstraint`s nomeados (`ck_ppr_*`) → `PrimaryKeyConstraint`). Aplicar ao 1:1, trocando `ck_ppr_*` por `ck_psm_*` e usando o DDL de 3 valores já fechado no `16-RESEARCH.md` (`motivo IN ('sem_credito', 'sem_estoque', 'furo_grade')` — **nunca** copiar o DDL de 2 valores do `ARCHITECTURE.md` §3.2, que está desatualizado — ver Pitfall 1 do RESEARCH).

**`downgrade()` de projeção recalculável** — molde exato de `020_pedido_produto_read.py:432-433`:
```python
def downgrade() -> None:
    op.drop_table("pedido_produto_read")
```
Replicar 1:1 trocando o nome da tabela — nenhuma tabela fonte é tocada, mesma justificativa ("projeção recalculável, re-upgrade reconstrói vazia").

**O que replicar:** cabeçalho, estilo de import, `CheckConstraint`s nomeados, `downgrade()` de 1 linha.
**O que adaptar:** sem preflight SQL (a 020 tem porque migra dado existente; a 032 nasce vazia — não backfill necessário), sem `LOCK TABLE` (não há dado concorrente a proteger na criação).

---

### 2. Model ORM — `app/modules/pedidos/infrastructure/models.py::PedidoStandbyMotivo`

**Analog:** `EstoqueVirtual`, mesmo arquivo, linhas 82-129 — mesma natureza (projeção pequena, recalculável, fora do ciclo de vida do job durável).

Trecho real (linhas 112-129):
```python
    __tablename__ = "estoque_virtual"
    __table_args__ = (
        UniqueConstraint(
            "cd_prod_cor", "sg_tamanho", "canal", name="uq_estoque_virtual_chave"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    cd_prod_cor = Column(String(64), nullable=False)
    sg_tamanho = Column(String(16), nullable=False)
    canal = Column(String(32), nullable=False)
    qt_disponivel = Column(Integer, nullable=False, default=0, server_default="0")
    # Foto de estoque que originou esta projeção. Divergir da dt_estoque atual de
    # `estoque` é o sinal de que a projeção está velha e precisa ser refeita.
    dt_estoque = Column(Date, nullable=True)
    recalculado_em = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

**O que replicar:**
- Estilo `Column(...)` explícito (NÃO `Mapped[...]`/`mapped_column` — esse é o estilo de `processing/infrastructure/models.py`, arquivo errado para esta tabela, conforme já decidido no RESEARCH.md).
- Docstring de bloco no topo da classe explicando o PORQUÊ (padrão do "placebo"/projeção, mesmo tom).
- `server_default` casando com o `DEFAULT`/`CHECK` da migration (aqui: `execucoes_consecutivas` com `server_default=sa.text("1")` na migration ↔ `default=1, server_default="1"` no Column).

**O que adaptar:**
- PK aqui é **composta** `(nr_pedido, cd_prod_cor)` — usar `primary_key=True` nas duas colunas, não um `id` autoincrement + `UniqueConstraint` como em `EstoqueVirtual`.
- Sem FK para `durable_jobs` (`job_id` é informativo/auditoria, conforme já decidido).

Esqueleto de classe (a partir do padrão acima + DDL da migration):
```python
class PedidoStandbyMotivo(Base):
    """Motivo pelo qual um par (nr_pedido, cd_prod_cor) está fora de uma OR
    nesta rodada de processamento (STANDBY-01/06).

    [mesmo tom de docstring de EstoqueVirtual: por que existe / o que é /
    quando a linha é apagada — ver domain/standby_motivo.py para o vocabulário]
    """

    __tablename__ = "pedido_standby_motivo"

    nr_pedido = Column(Integer, primary_key=True)
    cd_prod_cor = Column(String(64), primary_key=True)
    canal = Column(String(16), nullable=False)
    motivo = Column(String(16), nullable=False)
    execucoes_consecutivas = Column(Integer, nullable=False, default=1, server_default="1")
    job_id = Column(postgresql.UUID(as_uuid=True), nullable=False)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```
Conferir imports já presentes no topo de `models.py` (`Column`, `Integer`, `String`, `DateTime`, `func`) e adicionar `from sqlalchemy.dialects.postgresql import UUID as PgUUID` (ou `postgresql.UUID`) se ainda não importado nesse arquivo especificamente (verificar antes de duplicar import).

---

### 3. Upsert com SQLAlchemy Core (`pg_insert(...).on_conflict_do_update(...)`)

**Precedente confirmado no próprio módulo `processing/infrastructure/repository.py`** — import no topo (linha 13):
```python
from sqlalchemy.dialects.postgresql import insert as pg_insert
```
usado em `store_plan` (linha 145-147, insert simples sem conflito):
```python
            await self._db.execute(
                pg_insert(PedidoProcessamentoPlanModel).values(values)
            )
```
e em `apply_pairs` (linha 429-441, `on_conflict_do_nothing` + `returning`):
```python
        inserted = (
            await self._db.scalars(
                pg_insert(OrdemReserva)
                .values(values)
                .on_conflict_do_nothing(
                    index_elements=[
                        OrdemReserva.nr_pedido,
                        OrdemReserva.cd_prod_cor,
                    ]
                )
                .returning(OrdemReserva.nr_pedido)
            )
        ).all()
```
**Nenhum precedente de `on_conflict_do_update` com incremento** existe hoje neste arquivo — o RESEARCH.md já resolveu isso com o padrão correto do dialeto (SQLAlchemy 2.0), que replica o mesmo import `pg_insert` acima. Usar literalmente o trecho já fechado no `16-RESEARCH.md` (§ "Pattern: upsert com incremento condicional"):
```python
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
            "execucoes_consecutivas": PedidoStandbyMotivo.execucoes_consecutivas + 1,
            "atualizado_em": stmt.excluded.atualizado_em,
        },
    )
    await self._db.execute(stmt)
```
**O que replicar:** import `pg_insert` idêntico ao já usado no arquivo; método assíncrono na mesma classe `SqlAlchemyProcessingRepository` (mesmo padrão de `store_plan`/`apply_pairs`, sem `text()` cru).
**O que adaptar:** primeiro uso de `on_conflict_do_update` (não `do_nothing`) no arquivo — nenhum ajuste estrutural necessário além disso, é a mesma API do dialeto.

---

### 4. Novo método de port + implementação (`record_standby_reasons`)

**Analog mais recente:** `ProcessingPlannerSource.load_pedido_budget` (Fase 15), `ports.py:37-39`:
```python
    async def load_pedido_budget(
        self, nr_pedidos: set[int]
    ) -> tuple[dict[int, int], dict[int, int], dict[int, int]]: ...
```
Mesmo padrão de assinatura `Protocol` com `...` como corpo, tipos explícitos em `Sequence`/`tuple`/`Mapping` (imports já no topo do arquivo, linha 5: `from collections.abc import Mapping, Sequence`).

**Adicionar em `ProcessingRepository`** (logo após `store_plan`, linha 61, antes de `load_next_chunk`):
```python
    async def record_standby_reasons(
        self,
        *,
        job_id: UUID,
        deferred_pairs: Sequence[tuple[int, str, str, str]],
        blocked_credit_pairs: Sequence[tuple[int, str, str]],
        recorded_at: datetime,
    ) -> None: ...
```
(assinatura já fechada e testada quanto à forma no `16-RESEARCH.md`).

**Implementação real** na classe `SqlAlchemyProcessingRepository` (mesmo arquivo de `store_plan`, `repository.py`) — usar o corpo já fechado no RESEARCH.md § "Pattern: upsert com incremento condicional" (reproduzido acima no item 3). Seguir a mesma convenção de nome de parâmetro `job_id: UUID` já usada em `store_plan`/`create_spec`.

**O que replicar:** estilo `Protocol` (`...` como corpo, sem implementação na interface); nome de parâmetros nomeados (`*,` keyword-only, igual a todos os outros métodos do port).
**O que adaptar:** este método não retorna nada (`-> None`), diferente dos outros métodos do port que retornam `ProcessingSpec`/tuplas — isso é aceitável, `ProcessingWriter.apply_pairs` (linha 80) já tem o mesmo formato (`-> None`, side-effect puro).

---

### 5. Ponto de limpeza em transação existente

**5a. `apply_pairs`** (`repository.py:409-465`) — inserir o `DELETE` de `pedido_standby_motivo` **depois** do bloco de `salvar_processados`/Linx/estoque virtual (linha 445-465), mesma transação (nenhum commit explícito nesta função — commit acontece fora, no `ProcessingUnitOfWork`, igual ao resto do módulo). Usar `sqlalchemy.delete` + `tuple_(...).in_(...)`, precedente já confirmado em `_assert_plan_still_current` (linha 289-290, referenciado no RESEARCH.md):
```python
        await self._db.execute(
            delete(PedidoStandbyMotivo).where(
                tuple_(
                    PedidoStandbyMotivo.nr_pedido,
                    PedidoStandbyMotivo.cd_prod_cor,
                ).in_(pairs)
            )
        )
```
`pairs` já existe como `set[(nr_pedido, cd_prod_cor)]`, construído na linha 412 (`pairs = {(row.nr_pedido, row.cd_prod_cor) for row in rows}`) — reaproveitar a mesma variável, sem nova consulta.

**5b. `reconstruir_pedido_produto_read`** (`repositorio_snapshot.py:248-261`) — trecho real:
```python
    await db.execute(text("DELETE FROM pedido_produto_read"))
    pending = await db.execute(text(_PENDING_READ_INSERT_SQL))
    erp = await db.execute(text(_ERP_READ_INSERT_SQL))
    pending_dml = cast(CursorResult[Any], pending)
    erp_dml = cast(CursorResult[Any], erp)
    return int(pending_dml.rowcount or 0) + int(erp_dml.rowcount or 0)
```
**Inserir a limpeza órfã DEPOIS da linha 258** (depois dos dois INSERTs, nunca antes — Pitfall 3 do RESEARCH.md), usando `text()` (mesmo estilo do restante da função, que já é 100% `text()`/SQL cru, não Core):
```python
    await db.execute(text(
        "DELETE FROM pedido_standby_motivo psm "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM pedido_produto_read ppr"
        "  WHERE ppr.source = 'pending'"
        "    AND ppr.nr_pedido = psm.nr_pedido"
        "    AND ppr.cd_prod_cor = psm.cd_prod_cor"
        ")"
    ))
```
**O que replicar:** estilo `text()` cru desta função específica (diferente do resto do módulo `processing/`, que usa SQLAlchemy Core) — seguir o estilo LOCAL do arquivo, não importar Core aqui.
**O que adaptar:** nada estrutural — é um `DELETE` adicional na mesma transação, sem novo `db.commit()`.

---

### 6. Teste de migration

**Convenção confirmada:** sim, existe padrão explícito e recente — `app/tests/test_migration_030_auth_otp_code_hash.py` (íntegro, 138 linhas). Estrutura:
- Fixture `migration_database_url` isola um banco descartável (prefixo `automation_migration_test`, `DROP SCHEMA ... CASCADE` no setup/teardown) — trecho real (linhas 78-85):
```python
@pytest.fixture
def migration_database_url() -> Iterable[str]:
    database_url = _isolated_database_url()
    _reset(database_url)
    try:
        yield database_url
    finally:
        _reset(database_url)
```
- Helper `_alembic(database_url, *arguments)` chama `python -m alembic upgrade/downgrade <rev>` via `subprocess.run` com `DATABASE_URL` sobrescrito no ambiente (linhas 64-75) — nunca chama Alembic in-process.
- Teste único cobre: `upgrade` até a revisão alvo → assert de schema (`information_schema.columns`) → insert de dado real → assert do dado → `downgrade` → assert de reversão → `upgrade` de novo (round-trip completo).

**Aplicar à migration 032:** `test_migration_032_pedido_standby_motivo.py` deve replicar 1:1 a estrutura acima — `upgrade "032"` → assert das 3 `CheckConstraint`s (inserir linha com `motivo='furo_grade'` deve funcionar; inserir com `motivo='invalido'` deve levantar `IntegrityError`/`CheckViolation`) → `downgrade "031"` → assert que a tabela não existe mais (`to_regclass`) → `upgrade "032"` de novo.

**O que replicar:** toda a infraestrutura de isolamento (`_isolated_database_url`, `_reset`, `_alembic` via subprocess) — não reescrever, importar/copiar o padrão do arquivo mais recente listado.
**O que adaptar:** os asserts de schema/dado são específicos da tabela nova (3 valores de `motivo`, `execucoes_consecutivas >= 1`, PK composta) em vez de `character_maximum_length` de uma coluna alterada.

## Shared Patterns

### Import do dialeto Postgres para upsert
**Source:** `app/modules/pedidos/processing/infrastructure/repository.py:13`
```python
from sqlalchemy.dialects.postgresql import insert as pg_insert
```
**Apply to:** `record_standby_reasons` (novo método no mesmo arquivo, mesmo import já presente — não duplicar).

### Estilo `Column(...)` explícito para tabelas fora do job durável
**Source:** `app/modules/pedidos/infrastructure/models.py` (`EstoqueVirtual`, `PedidoModificacao`)
**Apply to:** `PedidoStandbyMotivo` — **não** usar `Mapped[...]`/`mapped_column`, que é o estilo de `processing/infrastructure/models.py` (tabelas do job durável, com FK `ON DELETE CASCADE` para `durable_jobs`).

### Migration manual (sem autogenerate), docstring explicando o porquê
**Source:** todas as migrations do repo (`020`, `031`)
**Apply to:** `032_pedido_standby_motivo.py` — cabeçalho com `Revision ID`/`Revises`/`Create Date`, docstring de bloco no topo do arquivo (não comentários inline), `CheckConstraint`s nomeados com prefixo curto (`ck_psm_*`), `downgrade()` mínimo para projeção recalculável.

### Transação única para múltiplas escritas relacionadas
**Source:** `apply_pairs` (`repository.py:409-465`, já encadeia `OrdemReserva` → `pedidos_processados` → Linx → `estoque_virtual` sem commit intermediário)
**Apply to:** a nova chamada `record_standby_reasons` em `_plan_once` (service.py) DEVE entrar **antes** de `unit_of_work.commit()` — nunca depois (Pitfall 2 do RESEARCH.md, já mapeado com números de linha exatos).

## No Analog Found

Nenhum arquivo desta fase ficou sem analog — é trabalho de extensão pura sobre padrões já maduros no repositório. O único ponto sem precedente EXATO é o uso de `on_conflict_do_update` com incremento de coluna (item 3 acima); tratado como extensão direta do padrão `pg_insert` já em uso, não como padrão desconhecido.

## Metadata

**Analog search scope:** `app/modules/pedidos/` (domain, infrastructure, processing/*), `app/modules/ingestao/infrastructure/`, `alembic/versions/` (017, 020, 030, 031), `app/tests/test_migration_*.py`
**Files scanned:** 11 (3 migrations lidas por completo, 2 arquivos de model, 1 ports.py, 1 repository.py — 2 seções, 1 repositorio_snapshot.py — 1 seção, 1 teste de migration completo, 2 documentos de contexto/pesquisa da própria fase)
**Pattern extraction date:** 2026-08-20
