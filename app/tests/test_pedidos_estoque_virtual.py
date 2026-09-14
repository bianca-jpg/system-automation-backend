"""Unit tests da regra do estoque virtual (PLACEBO) — pura, sem I/O.

Ver o docstring de `EstoqueVirtual` em app/modules/pedidos/models.py para o porquê
do placebo e a condição de remoção. O cenário que motiva tudo isto está em
`test_nao_superaloca_entre_ciclos_de_sync`.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, func, select, update

from app.modules.ingestao.infrastructure.models import Estoque
from app.modules.pedidos.domain.estoque_virtual import calcular_estoque_virtual
from app.modules.pedidos.infrastructure.models import EstoqueVirtual, OrdemReserva
from app.modules.pedidos.infrastructure.repositorio_estoque_virtual import (
    carregar_estoque,
    recalcular_estoque_virtual,
)
from app.shared.database.session import async_session_factory

HOJE = date(2026, 8, 5)
_MEIO_DIA = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _item(sg_tamanho, qt, canal: str | None = "Franquia", cd_prod_cor="PROD1"):
    return {
        "cd_prod_cor": cd_prod_cor,
        "sg_tamanho": sg_tamanho,
        "qt_liquida": qt,
        "canal": canal,
    }


def _or(nr_pedido, cd_prod_cor, itens, tipo="com", created_at=_MEIO_DIA):
    """Tupla no formato de `carregar_ordens_reserva`."""
    return (nr_pedido, cd_prod_cor, tipo, itens, created_at, None)


def test_desconta_or_do_dia_da_foto():
    foto = {("PROD1", "M", "Franquia"): 10}
    ors = [_or(1, "PROD1", [_item("M", 4)])]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "M", "Franquia")] == 6


def test_or_anterior_a_foto_nao_desconta():
    """Reset diário: a foto de hoje é o novo ponto de partida, então a OR de ontem
    não desconta mais. É o limite aceito do placebo — a peça reaparece amanhã."""
    foto = {("PROD1", "M", "Franquia"): 10}
    ors = [_or(1, "PROD1", [_item("M", 4)], created_at=_MEIO_DIA - timedelta(days=1))]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "M", "Franquia")] == 10


def test_par_no_erp_nao_desconta_de_novo():
    """Se a reserva já foi feita no Linx, a foto do Databricks já a reflete —
    descontar aqui tiraria a mesma peça duas vezes."""
    foto = {("PROD1", "M", "Franquia"): 10}
    ors = [_or(1, "PROD1", [_item("M", 4)])]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, pares_no_erp={(1, "PROD1")})

    assert virtual[("PROD1", "M", "Franquia")] == 10


def test_desconto_isolado_por_canal():
    """O requisito central: OR de Multimarca não pode reduzir estoque de Franquia."""
    foto = {
        ("PROD1", "M", "Franquia"): 10,
        ("PROD1", "M", "Multimarca"): 10,
    }
    ors = [_or(1, "PROD1", [_item("M", 4, canal="Multimarca")])]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "M", "Franquia")] == 10
    assert virtual[("PROD1", "M", "Multimarca")] == 6


def test_piso_em_zero_nunca_negativa():
    foto = {("PROD1", "M", "Franquia"): 3}
    ors = [_or(1, "PROD1", [_item("M", 10)])]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "M", "Franquia")] == 0


def test_or_tipo_sem_tambem_desconta():
    foto = {("PROD1", "M", "Franquia"): 10}
    ors = [_or(1, "PROD1", [_item("M", 4)], tipo="sem")]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "M", "Franquia")] == 6


def test_item_sem_canal_valido_e_ignorado():
    """Coerente com o motor: sem canal não há estoque contra o qual descontar."""
    foto = {("PROD1", "M", "Franquia"): 10}
    ors = [
        _or(1, "PROD1", [_item("M", 4, canal=None)]),
        _or(2, "PROD1", [_item("M", 3, canal="CANAL_NOVO")]),
    ]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "M", "Franquia")] == 10


def test_usa_cd_prod_cor_da_or_nao_do_item():
    """`cd_prod_cor` da OR é a identidade da reserva; um item com código divergente
    não deve descontar do produto errado."""
    foto = {("PROD1", "M", "Franquia"): 10, ("OUTRO", "M", "Franquia"): 10}
    ors = [_or(1, "PROD1", [_item("M", 4, cd_prod_cor="OUTRO")])]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "M", "Franquia")] == 6
    assert virtual[("OUTRO", "M", "Franquia")] == 10


def test_dt_foto_none_desconta_tudo():
    """Sem data na origem não há âncora, então o conservador é assumir que nenhuma
    OR foi refletida na foto."""
    foto = {("PROD1", "M", "Franquia"): 10}
    ors = [_or(1, "PROD1", [_item("M", 4)], created_at=_MEIO_DIA - timedelta(days=30))]

    virtual = calcular_estoque_virtual(foto, ors, None, set())

    assert virtual[("PROD1", "M", "Franquia")] == 6


def test_soma_varias_ors_do_mesmo_produto_e_tamanho():
    foto = {("PROD1", "M", "Franquia"): 10}
    ors = [
        _or(1, "PROD1", [_item("M", 4)]),
        _or(2, "PROD1", [_item("M", 3)]),
    ]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "M", "Franquia")] == 3


def test_grade_com_varios_tamanhos_desconta_cada_um():
    foto = {
        ("PROD1", "P", "Franquia"): 5,
        ("PROD1", "M", "Franquia"): 5,
        ("PROD1", "G", "Franquia"): 5,
    }
    ors = [_or(1, "PROD1", [_item("P", 1), _item("M", 2), _item("G", 5)])]

    virtual = calcular_estoque_virtual(foto, ors, HOJE, set())

    assert virtual[("PROD1", "P", "Franquia")] == 4
    assert virtual[("PROD1", "M", "Franquia")] == 3
    assert virtual[("PROD1", "G", "Franquia")] == 0


def test_nao_superaloca_entre_ciclos_de_sync():
    """O cenário que o placebo existe para resolver.

    Foto congelada com 10 peças. Pedido A leva as 10 às 10h e gera OR. Ao meio-dia
    o sync relê a MESMA foto (10 peças) — sem o virtual, o pedido B veria 10
    disponíveis e seria atendido também: 20 peças reservadas contra 10 físicas.
    """
    foto = {("PROD1", "M", "Franquia"): 10}
    or_do_pedido_a = [_or(1, "PROD1", [_item("M", 10)])]

    virtual = calcular_estoque_virtual(foto, or_do_pedido_a, HOJE, set())

    assert foto[("PROD1", "M", "Franquia")] == 10, "a foto física não é mutada"
    assert virtual[("PROD1", "M", "Franquia")] == 0, "o pedido B não vê disponível"


# ---------------------------------------------------------------------------
# Auto-cura de `carregar_estoque` (toca o banco).
#
# Como test_ingestao_sync: estes testes NUNCA commitam — a foto é substituída
# dentro da transação e descartada no rollback, sem tocar dados reais.
# ---------------------------------------------------------------------------

_CD_TESTE = "TESTE.EV.0001|001"


def _sem_erp(func):
    """Decorator: evita ler a tabela de processados-ERP inteira (dados reais,
    grande) — nenhum destes testes depende dela. `patch` é context manager
    SÍNCRONO, então não pode entrar no mesmo `async with` da sessão."""
    return patch(
        "app.modules.pedidos.infrastructure.repositorio_estoque_virtual"
        ".carregar_pares_processados_erp",
        new=AsyncMock(return_value=set()),
    )(func)


@_sem_erp
async def test_carregar_estoque_reprojeta_quando_or_e_mais_nova_que_a_projecao():
    """REGRESSÃO: a checagem de frescor olhava só a data da foto, então uma OR
    criada depois do último recálculo ficava invisível e a leitura devolvia
    disponível que já estava reservado."""
    chave = f"{_CD_TESTE}_M"
    async with async_session_factory() as session:
        await session.execute(delete(Estoque))
        session.add(
            Estoque(
                cd_prod_cor=_CD_TESTE,
                sg_tamanho="M",
                canal="Multimarca",
                qt_disponivel=10,
                dt_estoque=date.today(),
            )
        )
        await session.flush()

        # Projeção gravada, ainda sem nenhuma OR do dia.
        await recalcular_estoque_virtual(session)
        assert (await carregar_estoque(session))["Multimarca"][chave] == 10

        # OR nasce DEPOIS do recálculo, sem passar pelo gatilho. O `created_at` é
        # explícito porque `func.now()` devolve o instante de início da TRANSAÇÃO:
        # criada aqui pelo default, a OR empataria com `recalculado_em` — que é o
        # caso do caminho de produção (OR + recálculo na mesma transação, sem
        # reprojeção à toa). O que este teste cobre é o oposto: OR gravada sem o
        # gatilho, portanto posterior à projeção.
        recalculado_em = await session.scalar(
            select(func.max(EstoqueVirtual.recalculado_em))
        )
        assert recalculado_em is not None
        session.add(
            OrdemReserva(
                nr_pedido=999_999_999,
                cd_prod_cor=_CD_TESTE,
                tipo="com",
                created_at=recalculado_em + timedelta(seconds=1),
                itens=[
                    {
                        "cd_prod_cor": _CD_TESTE,
                        "sg_tamanho": "M",
                        "qt_liquida": 3,
                        "canal": "Multimarca",
                    }
                ],
            )
        )
        await session.flush()

        estoque = await carregar_estoque(session)
        assert estoque["Multimarca"][chave] == 7
        # Isolamento de canal: a OR de Multimarca não tocou Franquia.
        assert chave not in estoque["Franquia"]

        await session.rollback()


@_sem_erp
async def test_carregar_estoque_reseta_quando_a_foto_vira_o_dia():
    """A virada da `dt_estoque` devolve a projeção ao valor da foto — o reset
    diário aceito do placebo, sem código de reset próprio."""
    chave = f"{_CD_TESTE}_M"
    async with async_session_factory() as session:
        await session.execute(delete(Estoque))
        session.add(
            Estoque(
                cd_prod_cor=_CD_TESTE,
                sg_tamanho="M",
                canal="Multimarca",
                qt_disponivel=10,
                dt_estoque=date.today(),
            )
        )
        session.add(
            OrdemReserva(
                nr_pedido=999_999_998,
                cd_prod_cor=_CD_TESTE,
                tipo="com",
                itens=[
                    {
                        "cd_prod_cor": _CD_TESTE,
                        "sg_tamanho": "M",
                        "qt_liquida": 4,
                        "canal": "Multimarca",
                    }
                ],
            )
        )
        await session.flush()

        assert (await carregar_estoque(session))["Multimarca"][chave] == 6

        await session.execute(
            update(Estoque).values(dt_estoque=date.today() + timedelta(days=1))
        )
        await session.flush()

        assert (await carregar_estoque(session))["Multimarca"][chave] == 10

        await session.rollback()


@_sem_erp
async def test_recalcular_grava_a_projecao_com_a_data_da_foto():
    async with async_session_factory() as session:
        await session.execute(delete(Estoque))
        session.add(
            Estoque(
                cd_prod_cor=_CD_TESTE,
                sg_tamanho="M",
                canal="Franquia",
                qt_disponivel=8,
                dt_estoque=date.today(),
            )
        )
        await session.flush()

        await recalcular_estoque_virtual(session)

        gravadas = (
            await session.execute(
                EstoqueVirtual.__table__.select().where(
                    EstoqueVirtual.cd_prod_cor == _CD_TESTE
                )
            )
        ).all()
        assert len(gravadas) == 1
        assert gravadas[0].qt_disponivel == 8
        assert gravadas[0].canal == "Franquia"
        assert gravadas[0].dt_estoque == date.today()

        await session.rollback()
