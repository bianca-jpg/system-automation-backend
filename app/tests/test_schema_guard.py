"""Guarda contra drift entre os models SQLAlchemy e o schema real do Postgres.

MOTIVO: em 2026-07-23 uma migration mudou as 3 tabelas do módulo pedidos para a
chave composta (nr_pedido, cd_prod_cor) e o código Python correspondente se
perdeu antes do commit. O resultado foi um drift silencioso de semanas: o model
declarava PK escalar, o banco tinha PK composta, e todo INSERT de OR nova
levantava IntegrityError -> HTTP 500. O frontend engolia o erro e mostrava
"Pedidos processados!", então nada revelava o problema.

Nenhum teste existente podia pegar isso, porque os testes de escrita mockavam a
camada de persistência. Estes dois comparam o metadata declarado contra o
information_schema e falham no primeiro desalinhamento.
"""

import pytest
from sqlalchemy import CheckConstraint, text

# Importar os models registra as tabelas em Base.metadata (mesma lista que
# alembic/env.py usa para o autogenerate).
from app.modules.auth.infrastructure.models import AuthUser  # noqa: F401
from app.modules.comunicacoes.infrastructure.models import (  # noqa: F401
    Comunicacao,
    ComunicacaoEmailDelivery,
)
from app.modules.ingestao.infrastructure.models import (  # noqa: F401
    Estoque,
    FaturamentoColecao,
    Pedido,
    PedidoProcessadoErp,
    PedidoProdutoRead,
    ProdutoTamanhoPosicao,
)
from app.modules.parametros.infrastructure.models import (  # noqa: F401
    Parametro,
    ParametroChangeRequest,
)
from app.modules.pedidos.infrastructure.models import (  # noqa: F401
    EstoqueVirtual,
    OrdemReserva,
    OrdemReservaLinx,
    PedidoModificacao,
    PedidoProcessado,
)
from app.modules.pedidos.processing.infrastructure.models import (  # noqa: F401
    PedidoProcessamentoModel,
    PedidoProcessamentoPlanModel,
)
from app.modules.realtime.infrastructure.models import (  # noqa: F401
    RealtimeObservedEntity,
    RealtimeOutbox,
    RealtimeReadCursor,
    RealtimeTopicState,
)
from app.shared.database.base import Base
from app.shared.database.session import async_session_factory


async def _tabelas_do_banco(session) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    )
    return set(rows.scalars().all())


@pytest.mark.parametrize("nome_tabela", sorted(Base.metadata.tables))
async def test_colunas_do_model_existem_no_banco(nome_tabela):
    """Toda coluna declarada no model existe no banco, com a mesma nullability."""
    tabela = Base.metadata.tables[nome_tabela]
    async with async_session_factory() as session:
        tabelas_do_banco = await _tabelas_do_banco(session)
        assert nome_tabela in tabelas_do_banco, (
            f"{nome_tabela}: tabela declarada no model e AUSENTE no banco. "
            "Falta aplicar ou criar uma migration."
        )

        rows = await session.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": nome_tabela},
        )
        no_banco = {nome: (nullable == "YES") for nome, nullable in rows.all()}

    declaradas = {c.name: c.nullable for c in tabela.columns}

    faltando = sorted(set(declaradas) - set(no_banco))
    assert not faltando, (
        f"{nome_tabela}: colunas no model e AUSENTES no banco: {faltando}. "
        "Falta uma migration."
    )

    divergentes = {
        nome: {"model": declaradas[nome], "banco": no_banco[nome]}
        for nome in declaradas
        if declaradas[nome] != no_banco[nome]
    }
    assert not divergentes, f"{nome_tabela}: nullability divergente: {divergentes}"


@pytest.mark.parametrize("nome_tabela", sorted(Base.metadata.tables))
async def test_chave_primaria_do_model_bate_com_o_banco(nome_tabela):
    """A PK declarada é exatamente a PK do banco.

    É este o assert que pega a classe de bug de 2026-07-23: PK composta no banco
    contra PK escalar no model (ou o inverso).
    """
    tabela = Base.metadata.tables[nome_tabela]
    async with async_session_factory() as session:
        tabelas_do_banco = await _tabelas_do_banco(session)
        assert nome_tabela in tabelas_do_banco, (
            f"{nome_tabela}: tabela declarada no model e AUSENTE no banco. "
            "Falta aplicar ou criar uma migration."
        )

        rows = await session.execute(
            text(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                " AND a.attnum = ANY(i.indkey) "
                "WHERE n.nspname = 'public' AND c.relname = :t AND i.indisprimary"
            ),
            {"t": nome_tabela},
        )
        pk_banco = set(rows.scalars().all())

    pk_model = {c.name for c in tabela.primary_key.columns}
    assert pk_model == pk_banco, (
        f"{nome_tabela}: PK do model {sorted(pk_model)} != PK do banco "
        f"{sorted(pk_banco)}. Todo INSERT vai falhar com IntegrityError."
    )


@pytest.mark.parametrize(
    "nome_tabela",
    [
        "realtime_outbox",
        "realtime_topic_state",
        "realtime_read_cursors",
        "realtime_observed_entities",
        "communication_email_deliveries",
        "pedido_processamentos",
        "pedido_processamento_plan",
    ],
)
async def test_checks_e_indices_criticos_do_model_batem_com_banco(nome_tabela):
    """Outboxes e metadata devem descrever as mesmas invariantes."""

    tabela = Base.metadata.tables[nome_tabela]
    indices_model = {index.name for index in tabela.indexes if index.name}
    checks_model = {
        constraint.name
        for constraint in tabela.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name
        and isinstance(constraint.name, str)
    }
    async with async_session_factory() as session:
        indices_banco = set(
            (
                await session.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public' AND tablename = :t"
                    ),
                    {"t": nome_tabela},
                )
            )
            .scalars()
            .all()
        )
        checks_banco = set(
            (
                await session.execute(
                    text(
                        "SELECT con.conname FROM pg_constraint con "
                        "JOIN pg_class rel ON rel.oid = con.conrelid "
                        "JOIN pg_namespace n ON n.oid = rel.relnamespace "
                        "WHERE n.nspname = 'public' AND rel.relname = :t "
                        "AND con.contype = 'c'"
                    ),
                    {"t": nome_tabela},
                )
            )
            .scalars()
            .all()
        )

    assert indices_model <= indices_banco, (
        f"{nome_tabela}: índices do model ausentes no banco: "
        f"{sorted(indices_model - indices_banco)}"
    )
    assert checks_model == checks_banco, (
        f"{nome_tabela}: CHECKs model={sorted(checks_model)} "
        f"banco={sorted(checks_banco)}"
    )
