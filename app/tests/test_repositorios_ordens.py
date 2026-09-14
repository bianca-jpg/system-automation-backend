from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import text

from app.modules.pedidos.infrastructure import (
    repositorio_ordens,
    repositorio_ordens_linx,
)
from app.modules.pedidos.infrastructure.repositorio_produto_writes import (
    upsert_modificacoes_em_lote,
)
from app.shared.database.session import async_session_factory


async def test_escritas_em_lote_nao_fazem_n_mais_1_nem_commit(monkeypatch):
    monkeypatch.setattr(repositorio_ordens, "_WRITE_CHUNK_SIZE", 2)
    monkeypatch.setattr(repositorio_ordens_linx, "_WRITE_CHUNK_SIZE", 2)
    db = AsyncMock()
    pares = {(900000000 + index, f"PROD-{index}") for index in range(5)}

    await repositorio_ordens.salvar_processados(db, pares)
    assert db.execute.await_count == 3

    db.execute.reset_mock()
    await repositorio_ordens.salvar_ordens_reserva(
        db,
        {par: [{"grade": index}] for index, par in enumerate(sorted(pares))},
        tipo="com",
    )
    assert db.execute.await_count == 3

    db.execute.reset_mock()
    await repositorio_ordens_linx.salvar_linhas_linx(
        db,
        [
            {
                "nr_pedido": nr,
                "cd_prod_cor": cd,
                "tipo": "com",
                "preco1": index + 1,
            }
            for index, (nr, cd) in enumerate(sorted(pares))
        ],
    )
    assert db.execute.await_count == 3
    db.commit.assert_not_awaited()
    db.get.assert_not_awaited()


async def test_upsert_modificacao_preserva_grade_original():
    nr = 999998001
    cd = "PROD-MODIFICACAO"
    async with async_session_factory() as session:
        await upsert_modificacoes_em_lote(
            session,
            [
                {
                    "nr_pedido": nr,
                    "cd_prod_cor": cd,
                    "items": [{"size": "M", "quantity": 2}],
                    "original_items": [{"size": "M", "quantity": 1}],
                }
            ],
        )
        await upsert_modificacoes_em_lote(
            session,
            [
                {
                    "nr_pedido": nr,
                    "cd_prod_cor": cd,
                    "items": [{"size": "M", "quantity": 3}],
                    "original_items": [{"size": "M", "quantity": 999}],
                }
            ],
        )

        row = (
            await session.execute(
                text(
                    "SELECT items, original_items FROM pedido_modificacoes "
                    "WHERE nr_pedido = :nr AND cd_prod_cor = :cd"
                ),
                {"nr": nr, "cd": cd},
            )
        ).one()
        assert row.items == [{"size": "M", "quantity": 3}]
        assert row.original_items == [{"size": "M", "quantity": 1}]


async def test_salvar_ordens_reserva_nao_sobrescreve_or_aprovada_ou_expirada():
    nr_aprovada = 999998002
    nr_expirada = 999998012
    cd_aprovada = "PROD-OR-APROVADA"
    cd_expirada = "PROD-OR-EXPIRADA"
    async with async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO ordens_reserva "
                "(nr_pedido, cd_prod_cor, tipo, itens, created_at, aprovado_em) "
                "VALUES "
                "(:nr_aprovada, :cd_aprovada, 'sem', '[]'::jsonb, "
                "TIMESTAMPTZ '2026-08-08 12:00:00+00', "
                "TIMESTAMPTZ '2026-08-08 13:00:00+00'), "
                "(:nr_expirada, :cd_expirada, 'sem', '[]'::jsonb, "
                "TIMESTAMPTZ '2026-08-01 12:00:00+00', NULL)"
            ),
            {
                "nr_aprovada": nr_aprovada,
                "cd_aprovada": cd_aprovada,
                "nr_expirada": nr_expirada,
                "cd_expirada": cd_expirada,
            },
        )

        await repositorio_ordens.salvar_ordens_reserva(
            session,
            {
                (nr_aprovada, cd_aprovada): [{"size": "G", "quantity": 4}],
                (nr_expirada, cd_expirada): [{"size": "M", "quantity": 3}],
            },
            tipo="com",
        )
        rows = (
            await session.execute(
                text(
                    "SELECT nr_pedido, tipo, itens, created_at, aprovado_em "
                    "FROM ordens_reserva "
                    "WHERE nr_pedido IN (:nr_aprovada, :nr_expirada) "
                    "ORDER BY nr_pedido"
                ),
                {
                    "nr_aprovada": nr_aprovada,
                    "nr_expirada": nr_expirada,
                },
            )
        ).all()

        assert len(rows) == 2
        assert all(row.tipo == "sem" and row.itens == [] for row in rows)
        approved = next(row for row in rows if row.nr_pedido == nr_aprovada)
        expired = next(row for row in rows if row.nr_pedido == nr_expirada)
        assert approved.aprovado_em == datetime(2026, 8, 8, 13, tzinfo=UTC)
        assert expired.created_at == datetime(2026, 8, 1, 12, tzinfo=UTC)
        assert expired.aprovado_em is None


async def test_aprovar_ordem_distingue_ausencia_mudanca_replay_e_expiracao():
    nr = 999998005
    agora = datetime.now(UTC)
    limite = agora - timedelta(hours=24)
    async with async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO ordens_reserva "
                "(nr_pedido, cd_prod_cor, tipo, itens, created_at, aprovado_em) "
                "VALUES "
                "(:nr, 'PROD-FRESCO', 'com', '[]'::jsonb, :fresco, NULL), "
                "(:nr, 'PROD-EXPIRADO', 'com', '[]'::jsonb, :expirado, NULL)"
            ),
            {
                "nr": nr,
                "fresco": agora - timedelta(hours=1),
                "expirado": agora - timedelta(hours=25),
            },
        )

        ausente = await repositorio_ordens.aprovar_ordem_reserva(
            session,
            nr + 1,
            "PROD-AUSENTE",
            limite_criacao=limite,
        )
        assert ausente.exists is False
        assert ausente.changed is False
        assert ausente.expired is False

        alterado = await repositorio_ordens.aprovar_ordem_reserva(
            session,
            nr,
            "PROD-FRESCO",
            limite_criacao=limite,
        )
        assert alterado.exists is True
        assert alterado.changed is True
        assert alterado.expired is False

        replay = await repositorio_ordens.aprovar_ordem_reserva(
            session,
            nr,
            "PROD-FRESCO",
            limite_criacao=limite,
        )
        assert replay.exists is True
        assert replay.changed is False
        assert replay.expired is False
        assert replay.ja_aprovados == 1

        expirado = await repositorio_ordens.aprovar_ordem_reserva(
            session,
            nr,
            "PROD-EXPIRADO",
            limite_criacao=limite,
        )
        assert expirado.exists is True
        assert expirado.changed is False
        assert expirado.expired is True


async def test_salvar_linhas_linx_upsert_em_lote_preserva_campo_omitido():
    nr = 999998003
    cd = "PROD-LINX-BULK"
    async with async_session_factory() as session:
        await repositorio_ordens_linx.salvar_linhas_linx(
            session,
            [
                {
                    "nr_pedido": nr,
                    "cd_prod_cor": cd,
                    "tipo": "sem",
                    "nome_clifor": "Cliente original",
                    "preco1": 10,
                }
            ],
        )
        await repositorio_ordens_linx.salvar_linhas_linx(
            session,
            [
                {
                    "nr_pedido": nr,
                    "cd_prod_cor": cd,
                    "tipo": "com",
                    "preco1": 20,
                }
            ],
        )

        row = (
            await session.execute(
                text(
                    "SELECT tipo, nome_clifor, preco1 FROM ordens_reserva_linx "
                    "WHERE nr_pedido = :nr AND cd_prod_cor = :cd"
                ),
                {"nr": nr, "cd": cd},
            )
        ).one()
        assert row.tipo == "com"
        assert row.nome_clifor == "Cliente original"
        assert float(row.preco1) == 20.0
