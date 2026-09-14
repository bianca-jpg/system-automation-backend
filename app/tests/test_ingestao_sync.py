"""Testes de caracterização das 3 sincronizações — Etapa 0 do plano de extração
DDD de Integração de Dados.

IMPORTANTE: sincronizar_pedidos/estoque/pedidos_processados_erp fazem
DELETE de toda a tabela antes de inserir (full refresh). Para não apagar dados
reais do Postgres do ambiente, estes testes NUNCA commitam — verificam dentro
da própria transação (a sessão enxerga suas próprias escritas não commitadas)
e descartam tudo ao sair do `async with` sem commit. `sincronizar_tudo` comita
internamente: sua orquestração é validada com as 3 sub-funções mockadas (sem
sessão real); sua garantia de "tudo ou nada" é validada com uma sessão real
que nunca chega a commitar (a falha simulada acontece antes do `db.commit()`).
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, func, select

from app.modules.ingestao import service
from app.modules.ingestao.application import casos_uso
from app.modules.ingestao.domain.agregacao import (
    CanalOrigemInvalidoError,
    FaturamentoOrigemInvalidaError,
    agregar_itens_pedidos,
    agregar_itens_processados_erp,
    agregar_referencia_tamanhos,
    processar_faturamento_colecao,
)
from app.modules.ingestao.infrastructure.adapters import (
    PostgresIngestionJobLock,
    SqlSnapshotRepository,
)
from app.modules.ingestao.infrastructure.databricks_reader import (
    ler_faturamento_colecao,
)
from app.modules.ingestao.infrastructure.models import (
    Estoque,
    Pedido,
    PedidoProcessadoErp,
    ProdutoTamanhoPosicao,
)
from app.shared.config.settings import get_settings
from app.shared.database.advisory_locks import (
    ORDER_STATE_MUTATION_LOCK as _LOCK_SINCRONIZACAO,
)
from app.shared.database.session import async_session_factory
from app.shared.infrastructure.databricks_client import DatabricksError

# Logger dos guards de estoque (snapshot vazio, frescor, granularidade).
_LOGGER_INGESTAO = "app.modules.ingestao.application.casos_uso"


def _mock_consulta(rows):
    return patch(
        "app.modules.ingestao.infrastructure.databricks_reader.executar_consulta",
        new=AsyncMock(return_value=rows),
    )


def _linha_financeira(*, valor: object, tamanho: str, quantidade: int) -> dict:
    return {
        "nr_pedido": "7001",
        "cd_prod_cor": "FIN1",
        "sg_tamanho": tamanho,
        "ds_grupo": "Calcas",
        "qt_entregar": quantidade,
        "qt_distribuida": quantidade,
        "vl_liquido": valor,
        "nm_cliente": "Cliente Financeiro",
        "ds_tp_canal": "FRANQUIA",
        "nm_prod": "Produto Financeiro",
        "status_credito": "Com Credito",
        "dt_emissao": "2026-01-10",
        "indica_reserva": True,
        "indica_embalado": True,
    }


def test_rateio_financeiro_falha_fechado_em_totais_divergentes():
    linhas = [
        _linha_financeira(valor="90,00", tamanho="36", quantidade=2),
        _linha_financeira(valor="100,00", tamanho="37", quantidade=3),
    ]

    pedidos, descartadas_pedidos = agregar_itens_pedidos(linhas)
    erp, descartadas_erp = agregar_itens_processados_erp(linhas)

    assert pedidos == {}
    assert erp == {}
    assert descartadas_pedidos == 2
    assert descartadas_erp == 2


def test_erp_preserva_quantidade_flags_e_grade_quando_valor_e_zero(caplog):
    linhas = [
        _linha_financeira(valor=0, tamanho="36", quantidade=2),
        _linha_financeira(valor=None, tamanho="37", quantidade=3),
    ]

    with caplog.at_level(logging.WARNING):
        erp, descartadas = agregar_itens_processados_erp(linhas)

    assert descartadas == 0
    assert [
        (chave[2], item["qt"], item["vl_liquido"]) for chave, item in erp.items()
    ] == [
        ("36", 2, Decimal(0)),
        ("37", 3, Decimal(0)),
    ]
    assert all(item["indica_embalado"] for item in erp.values())
    assert "pares sem valor contabil" in caplog.text


def test_erp_preserva_grade_e_flags_com_quantidade_zero_e_clampa_negativa():
    zero = {
        **_linha_financeira(valor=0, tamanho="36", quantidade=5),
        "qt_distribuida": 0,
        "indica_reserva": True,
        "indica_embalado": False,
    }
    negativa = {
        **_linha_financeira(valor=0, tamanho="37", quantidade=9),
        "qt_distribuida": -3,
        "indica_reserva": False,
        "indica_embalado": True,
    }

    erp, descartadas = agregar_itens_processados_erp([zero, negativa])

    assert descartadas == 0
    assert erp[(7001, "FIN1", "36")]["qt"] == 0
    assert erp[(7001, "FIN1", "36")]["indica_reserva"] is True
    assert erp[(7001, "FIN1", "37")]["qt"] == 0
    assert erp[(7001, "FIN1", "37")]["indica_embalado"] is True
    assert all(item["vl_liquido"] == 0 for item in erp.values())


@pytest.mark.parametrize(
    ("agregador", "source"),
    [
        (agregar_itens_pedidos, "pending"),
        (agregar_itens_processados_erp, "erp"),
    ],
)
def test_pedidos_e_erp_falham_fechado_em_canal_desconhecido(agregador, source):
    linha = _linha_financeira(valor="10,00", tamanho="M", quantidade=1)
    linha["ds_tp_canal"] = "automation DESCONHECIDO"

    with pytest.raises(
        CanalOrigemInvalidoError,
        match=rf"source={source}.*nr_pedido=7001.*cd_prod_cor=FIN1",
    ):
        agregador([linha])


@pytest.mark.parametrize(
    ("agregador", "source"),
    [
        (agregar_itens_pedidos, "pending"),
        (agregar_itens_processados_erp, "erp"),
    ],
)
def test_pedidos_e_erp_falham_fechado_em_canais_misturados_no_par(agregador, source):
    franquia = _linha_financeira(valor="10,00", tamanho="P", quantidade=1)
    multimarca = {
        **_linha_financeira(valor="10,00", tamanho="M", quantidade=1),
        "ds_tp_canal": "MM",
    }

    with pytest.raises(
        CanalOrigemInvalidoError,
        match=rf"canais misturados.*source={source}.*nr_pedido=7001.*FIN1",
    ):
        agregador([franquia, multimarca])


def test_erp_rateia_total_negativo_repetido_e_preserva_soma_assinada():
    linhas = [
        _linha_financeira(valor="-10,92", tamanho="G", quantidade=6),
        _linha_financeira(valor="-10,92", tamanho="GG", quantidade=2),
        _linha_financeira(valor="-10,92", tamanho="M", quantidade=7),
        _linha_financeira(valor="-10,92", tamanho="P", quantidade=3),
    ]

    erp, descartadas = agregar_itens_processados_erp(linhas)

    assert descartadas == 0
    assert sum(item["vl_liquido"] for item in erp.values()) == Decimal("-10.92")
    assert [(chave[2], item["vl_liquido"]) for chave, item in erp.items()] == [
        ("G", Decimal("-3.64")),
        ("GG", Decimal("-1.21")),
        ("M", Decimal("-4.25")),
        ("P", Decimal("-1.82")),
    ]


def test_pending_descarta_total_negativo_sem_inventar_zero():
    linhas = [_linha_financeira(valor="-10,92", tamanho="G", quantidade=6)]

    pedidos, descartadas = agregar_itens_pedidos(linhas)

    assert pedidos == {}
    assert descartadas == 1


def test_valor_br_e_limite_numeric_14_2_sao_parseados_sem_float():
    dentro = [
        _linha_financeira(
            valor="R$ 999.999.999.999,99",
            tamanho="UN",
            quantidade=1,
        )
    ]
    fora = [
        _linha_financeira(
            valor="1.000.000.000.000,00",
            tamanho="UN",
            quantidade=1,
        )
    ]

    agregado, descartadas = agregar_itens_pedidos(dentro)
    rejeitado, descartadas_fora = agregar_itens_pedidos(fora)

    assert agregado[(7001, "FIN1", "UN")]["vl_liquido"] == Decimal("999999999999.99")
    assert descartadas == 0
    assert rejeitado == {}
    assert descartadas_fora == 1


def test_faturamento_valida_decimal_e_deduplica_repeticao_identica():
    linha = {
        "colecao": "117",
        "canal": "FRANQUIA",
        "vl_planejado": "1.234,56",
        "vl_distribuido": "1000.005",
    }

    itens, diagnostico = processar_faturamento_colecao([linha, linha.copy()])

    assert itens == [
        {
            "colecao": 117,
            "canal": "Franquia",
            "vl_planejado": Decimal("1234.56"),
            "vl_distribuido": Decimal("1000.01"),
        }
    ]
    assert diagnostico.descartadas == 0


@pytest.mark.parametrize(
    ("campo", "valor", "categoria"),
    [
        ("colecao", "", "colecao_invalida"),
        ("canal", "LOJA INEXISTENTE", "canal_invalido"),
        ("vl_planejado", "NaN", "vl_planejado_invalido"),
        ("vl_distribuido", "1e99", "vl_distribuido_invalido"),
    ],
)
def test_faturamento_falha_fechado_sem_expor_valor_bruto(
    campo: str,
    valor: str,
    categoria: str,
):
    linha = {
        "colecao": "117",
        "canal": "FRANQUIA",
        "vl_planejado": "10.00",
        "vl_distribuido": "9.00",
    }
    linha[campo] = valor

    with pytest.raises(FaturamentoOrigemInvalidaError) as exc_info:
        processar_faturamento_colecao([linha])

    assert categoria in exc_info.value.diagnostico.razoes
    if valor:
        assert valor not in str(exc_info.value)


def test_faturamento_duplicata_conflitante_invalida_foto_inteira():
    with pytest.raises(FaturamentoOrigemInvalidaError) as exc_info:
        processar_faturamento_colecao(
            [
                {
                    "colecao": "117",
                    "canal": "FRANQUIA",
                    "vl_planejado": "10.00",
                    "vl_distribuido": "9.00",
                },
                {
                    "colecao": "117",
                    "canal": "FRANQUIA",
                    "vl_planejado": "11.00",
                    "vl_distribuido": "9.00",
                },
            ]
        )

    assert exc_info.value.diagnostico.razoes == ("duplicata_conflitante",)


async def test_faturamento_vazio_preserva_snapshot_anterior_sem_delete(caplog):
    source = MagicMock()
    source.faturamento_colecao = AsyncMock(return_value=[])
    repo = MagicMock()
    repo.count_faturamento = AsyncMock(return_value=12)
    repo.replace_faturamento = AsyncMock()

    with caplog.at_level(logging.WARNING, logger=_LOGGER_INGESTAO):
        total = await casos_uso.sincronizar_faturamento_colecao(source, repo)

    assert total == 12
    repo.replace_faturamento.assert_not_awaited()
    assert "mantendo snapshot anterior" in caplog.text


async def test_faturamento_invalido_aborta_e_loga_so_diagnostico(caplog):
    source = MagicMock()
    source.faturamento_colecao = AsyncMock(
        return_value=[
            {
                "colecao": "117",
                "canal": "CANAL DESCONHECIDO",
                "vl_planejado": "10.00",
                "vl_distribuido": "9.00",
            }
        ]
    )
    repo = MagicMock()
    repo.count_faturamento = AsyncMock()
    repo.replace_faturamento = AsyncMock()

    with (
        caplog.at_level(logging.ERROR, logger=_LOGGER_INGESTAO),
        pytest.raises(FaturamentoOrigemInvalidaError),
    ):
        await casos_uso.sincronizar_faturamento_colecao(source, repo)

    repo.count_faturamento.assert_not_awaited()
    repo.replace_faturamento.assert_not_awaited()
    assert "linhas=1 descartadas=1 categorias=canal_invalido" in caplog.text
    assert "CANAL DESCONHECIDO" not in caplog.text


async def test_reader_faturamento_usa_deadline_global_configurado_no_client():
    fake_settings = MagicMock(
        databricks_tabela_pedidos_processados="catalog.schema.erp"
    )
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as executar,
    ):
        await ler_faturamento_colecao()

    _args, kwargs = executar.call_args
    assert kwargs == {}


# ---------------------------------------------------------------------------
# sincronizar_pedidos
# ---------------------------------------------------------------------------


async def test_sincronizar_pedidos_tabela_nao_configurada_levanta_erro():
    fake_settings = MagicMock(databricks_tabela_pedidos="")
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as mock_consulta,
    ):
        async with async_session_factory() as session:
            with pytest.raises(DatabricksError):
                await service.sincronizar_pedidos(session)
        mock_consulta.assert_not_called()


async def test_sincronizar_pedidos_sql_exato():
    tabela = get_settings().databricks_tabela_pedidos or "tabela_fake_pedidos"
    fake_settings = MagicMock(databricks_tabela_pedidos=tabela)
    esperado = (
        "SELECT nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo, "
        "qt_entregar, vl_liquido, nm_cliente, ds_tp_canal, nm_prod, "
        "status_credito, dt_emissao, indica_blacklist "
        f"FROM {tabela} "
        "WHERE try_cast(qt_entregar AS DOUBLE) > 0"
    )
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as mock_consulta,
    ):
        async with async_session_factory() as session:
            await service.sincronizar_pedidos(session)
    args, kwargs = mock_consulta.call_args
    assert args[0] == esperado
    assert kwargs == {}


async def test_sincronizar_pedidos_agrega_duplicatas_e_descarta_invalidas():
    linhas = [
        {
            "nr_pedido": "9001",
            "cd_prod_cor": "ABC1",
            "sg_tamanho": "m",
            "ds_grupo": "Camisas",
            "qt_entregar": "2",
            "vl_liquido": "125,00",
            "nm_cliente": " Cliente Teste ",
            "ds_tp_canal": "FRANQUIA",
            "nm_prod": "Camisa X",
            "status_credito": "Com Credito",
            "dt_emissao": "2026-01-10",
        },
        {  # mesma chave: soma qt; o total do par repetido não pode ser somado
            "nr_pedido": "9001",
            "cd_prod_cor": "ABC1",
            "sg_tamanho": "M",
            "ds_grupo": "Camisas",
            "qt_entregar": "3",
            "vl_liquido": "125,00",
            "nm_cliente": "Cliente Teste",
            "ds_tp_canal": "FRANQUIA",
            "nm_prod": "Camisa X",
            "status_credito": "Com Credito",
            "dt_emissao": "2026-01-10",
        },
        {  # descartada: qt_entregar <= 0
            "nr_pedido": "9002",
            "cd_prod_cor": "XYZ9",
            "sg_tamanho": "G",
            "ds_grupo": "Calcas",
            "qt_entregar": "0",
            "vl_liquido": "10,00",
            "nm_cliente": "Outro",
            "ds_tp_canal": "MULTIMARCA",
            "nm_prod": "Calca Y",
            "status_credito": "Com Credito",
            "dt_emissao": "2026-01-11",
        },
        {  # descartada: nr_pedido invalido
            "nr_pedido": "0",
            "cd_prod_cor": "ZZZ1",
            "sg_tamanho": "P",
            "ds_grupo": "Camisas",
            "qt_entregar": "1",
            "vl_liquido": "10,00",
            "nm_cliente": "Outro2",
            "ds_tp_canal": "FRANQUIA",
            "nm_prod": "Camisa Z",
            "status_credito": "Com Credito",
            "dt_emissao": "2026-01-12",
        },
    ]
    with _mock_consulta(linhas):
        async with async_session_factory() as session:
            total = await service.sincronizar_pedidos(session)

            assert total == 1

            row = await session.scalar(select(Pedido).where(Pedido.nr_pedido == 9001))
            assert row is not None
            assert row.qt_entregar == 5
            assert float(row.vl_liquido) == 125.0
            assert row.canal == "Franquia"
            assert row.client == "Cliente Teste"

            assert (
                await session.scalar(select(Pedido).where(Pedido.nr_pedido == 9002))
                is None
            )
            assert (
                await session.scalar(select(Pedido).where(Pedido.nr_pedido == 0))
                is None
            )

            await (
                session.rollback()
            )  # nunca persistir: full refresh apagaria dados reais


async def test_sincronizar_pedidos_rateia_total_repetido_por_tamanhos_numericos():
    base = {
        "nr_pedido": "9003",
        "cd_prod_cor": "NUM1",
        "ds_grupo": "Calcas",
        "vl_liquido": "100,00",
        "nm_cliente": "Cliente Numerico",
        "ds_tp_canal": "MULTIMARCA",
        "nm_prod": "Calca Numerica",
        "status_credito": "Com Credito",
        "dt_emissao": "2026-01-10",
    }
    linhas = [
        {**base, "sg_tamanho": "36", "qt_entregar": "2"},
        {**base, "sg_tamanho": "37", "qt_entregar": "3"},
    ]

    with _mock_consulta(linhas):
        async with async_session_factory() as session:
            assert await service.sincronizar_pedidos(session) == 2
            rows = (
                (
                    await session.execute(
                        select(Pedido)
                        .where(Pedido.nr_pedido == 9003)
                        .order_by(Pedido.sg_tamanho)
                    )
                )
                .scalars()
                .all()
            )
            assert [
                (row.sg_tamanho, row.qt_entregar, float(row.vl_liquido)) for row in rows
            ] == [
                ("36", 2, 40.0),
                ("37", 3, 60.0),
            ]
            assert sum(row.vl_liquido for row in rows) == 100
            await session.rollback()


# ---------------------------------------------------------------------------
# sincronizar_estoque
# ---------------------------------------------------------------------------


async def test_sincronizar_estoque_tabela_nao_configurada_levanta_erro():
    fake_settings = MagicMock(databricks_tabela_estoque="")
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as mock_consulta,
    ):
        async with async_session_factory() as session:
            with pytest.raises(DatabricksError):
                await service.sincronizar_estoque(session)
        mock_consulta.assert_not_called()


async def test_sincronizar_estoque_sql_exato():
    tabela = get_settings().databricks_tabela_estoque or "tabela_fake_estoque"
    fake_settings = MagicMock(databricks_tabela_estoque=tabela)
    esperado = (
        "SELECT dt_estoque, canal, codigo_produto, tamanho, quantidade_disponivel "
        f"FROM {tabela}"
    )
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as mock_consulta,
    ):
        async with async_session_factory() as session:
            await service.sincronizar_estoque(session)
    args, kwargs = mock_consulta.call_args
    assert args[0] == esperado
    assert kwargs == {}


def _linha_estoque(canal, cd, tam, qt, dt=None):
    """Linha crua no vocabulário da view `system_automation_estoque_filtrado`."""
    return {
        "dt_estoque": (dt or date.today()).isoformat(),
        "canal": canal,
        "codigo_produto": cd,
        "tamanho": tam,
        "quantidade_disponivel": qt,
    }


async def test_sincronizar_estoque_agrega_por_chave_e_ignora_canal_invalido():
    hoje = date.today()
    linhas = [
        _linha_estoque("FRANQUIA", "ABC1", "m", "10", hoje),
        _linha_estoque("FRQ", "ABC1", "M", "5", hoje),
        _linha_estoque("CANAL_DESCONHECIDO", "XYZ9", "G", "100", hoje),
    ]
    with _mock_consulta(linhas):
        async with async_session_factory() as session:
            total = await service.sincronizar_estoque(session)

            assert total == 1

            row = await session.scalar(
                select(Estoque).where(
                    Estoque.cd_prod_cor == "ABC1", Estoque.canal == "Franquia"
                )
            )
            assert row is not None
            assert row.qt_disponivel == 15
            # A data da foto é persistida: é a âncora de reset do estoque virtual.
            assert row.dt_estoque == hoje

            assert (
                await session.scalar(
                    select(Estoque).where(Estoque.cd_prod_cor == "XYZ9")
                )
                is None
            )

            await session.rollback()


async def test_sincronizar_estoque_isola_canais_na_mesma_chave():
    """Mesmo produto/tamanho em canais diferentes são DUAS chaves distintas —
    é o que garante que pedido de Multimarca não consuma estoque de Franquia."""
    linhas = [
        _linha_estoque("FRANQUIA", "ABC1", "M", "10"),
        _linha_estoque("MULTIMARCA", "ABC1", "M", "7"),
    ]
    with _mock_consulta(linhas):
        async with async_session_factory() as session:
            assert await service.sincronizar_estoque(session) == 2

            por_canal = {
                r.canal: r.qt_disponivel
                for r in (
                    await session.execute(
                        select(Estoque).where(Estoque.cd_prod_cor == "ABC1")
                    )
                ).scalars()
            }
            assert por_canal == {"Franquia": 10, "Multimarca": 7}

            await session.rollback()


async def test_sincronizar_estoque_descarta_quantidade_nao_positiva():
    """Só é reservável quem tem disponível > 0: negativo e zero são descartados
    no domínio (antes o repositório clampava para 0 e gravava a linha)."""
    linhas = [
        _linha_estoque("FRANQUIA", "NEG1", "M", "-5"),
        _linha_estoque("FRANQUIA", "ZERO1", "M", "0"),
        _linha_estoque("FRANQUIA", "OK1", "M", "3"),
    ]
    with _mock_consulta(linhas):
        async with async_session_factory() as session:
            assert await service.sincronizar_estoque(session) == 1

            restantes = {
                r.cd_prod_cor
                for r in (await session.execute(select(Estoque))).scalars()
            }
            assert restantes == {"OK1"}

            await session.rollback()


async def test_sincronizar_estoque_vazio_mantem_foto_anterior(caplog):
    """Guard de snapshot vazio (D-02): a view volta vazia quando o job do dia
    ainda não rodou. Sem o guard, o full refresh zeraria `estoque` e a próxima
    adequação jogaria todos os pedidos em stand-by sem erro visível."""
    async with async_session_factory() as session:
        await session.execute(delete(Estoque))
        session.add(
            Estoque(
                cd_prod_cor="ANTERIOR1",
                sg_tamanho="M",
                canal="Franquia",
                qt_disponivel=42,
                dt_estoque=date.today() - timedelta(days=1),
            )
        )
        await session.flush()

        with (
            _mock_consulta([]),
            caplog.at_level(logging.WARNING, logger=_LOGGER_INGESTAO),
        ):
            total = await service.sincronizar_estoque(session)

        assert total == 1
        row = await session.scalar(
            select(Estoque).where(Estoque.cd_prod_cor == "ANTERIOR1")
        )
        assert row is not None
        assert row.qt_disponivel == 42
        assert "mantendo a foto anterior" in caplog.text

        await session.rollback()


async def test_sincronizar_estoque_avisa_foto_desatualizada(caplog):
    """Guard de frescor: a foto ainda é gravada (foto velha é melhor que
    nenhuma), mas o warning avisa que o job do Databricks atrasou."""
    ontem = date.today() - timedelta(days=1)
    with _mock_consulta([_linha_estoque("FRANQUIA", "ABC1", "M", "10", ontem)]):
        async with async_session_factory() as session:
            with caplog.at_level(logging.WARNING, logger=_LOGGER_INGESTAO):
                assert await service.sincronizar_estoque(session) == 1

            row = await session.scalar(
                select(Estoque).where(Estoque.cd_prod_cor == "ABC1")
            )
            assert row is not None
            assert row.dt_estoque == ontem
            assert "não de hoje" in caplog.text

            await session.rollback()


async def test_sincronizar_estoque_avisa_chave_duplicada(caplog):
    """Canário de granularidade: a view promete 1 linha por
    (canal, produto, tamanho). A soma segue como defesa, mas a colisão é avisada."""
    linhas = [
        _linha_estoque("FRANQUIA", "ABC1", "M", "4"),
        _linha_estoque("FRANQUIA", "ABC1", "M", "6"),
    ]
    with _mock_consulta(linhas):
        async with async_session_factory() as session:
            with caplog.at_level(logging.WARNING, logger=_LOGGER_INGESTAO):
                assert await service.sincronizar_estoque(session) == 1

            row = await session.scalar(
                select(Estoque).where(Estoque.cd_prod_cor == "ABC1")
            )
            assert row is not None
            assert row.qt_disponivel == 10
            assert "colisão" in caplog.text

            await session.rollback()


# ---------------------------------------------------------------------------
# sincronizar_pedidos_processados_erp
# ---------------------------------------------------------------------------


async def test_sincronizar_processados_erp_tabela_nao_configurada_levanta_erro():
    fake_settings = MagicMock(databricks_tabela_pedidos_processados="")
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as mock_consulta,
    ):
        async with async_session_factory() as session:
            with pytest.raises(DatabricksError):
                await service.sincronizar_pedidos_processados_erp(session)
        mock_consulta.assert_not_called()


async def test_sincronizar_processados_erp_sql_exato():
    settings_reais = get_settings()
    tabela = settings_reais.databricks_tabela_pedidos_processados or "tabela_fake_erp"
    ano = settings_reais.ingestao_ano_historico
    fake_settings = MagicMock(
        databricks_tabela_pedidos_processados=tabela, ingestao_ano_historico=ano
    )
    esperado = (
        "SELECT nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo, nm_prod, "
        "nm_cliente, ds_tp_canal, qt_entregar, qt_distribuida, vl_liquido, "
        "indica_reserva, indica_embalado, dt_emissao "
        f"FROM {tabela} "
        f"WHERE year(try_cast(dt_emissao AS DATE)) = {ano} "
        "AND (indica_reserva = true OR indica_embalado = true)"
    )
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as mock_consulta,
    ):
        async with async_session_factory() as session:
            await service.sincronizar_pedidos_processados_erp(session)
    args, kwargs = mock_consulta.call_args
    assert args[0] == esperado
    assert kwargs == {}


async def test_sincronizar_processados_erp_vazio_preserva_snapshot_anterior(caplog):
    anterior = PedidoProcessadoErp(
        nr_pedido=8_099,
        cd_prod_cor="ERP-ANTERIOR",
        sg_tamanho="M",
        ds_grupo="Camisas",
        ds_produto="Camisa anterior",
        client="Cliente anterior",
        canal="Franquia",
        qt=2,
        vl_liquido=20,
        indica_reserva=True,
        indica_embalado=False,
        data="2026-01-05",
    )
    async with async_session_factory() as session:
        session.add(anterior)
        await session.flush()
        try:
            with (
                _mock_consulta([]),
                caplog.at_level(logging.WARNING, logger=_LOGGER_INGESTAO),
            ):
                assert await service.sincronizar_pedidos_processados_erp(session) >= 1

            preservado = await session.scalar(
                select(PedidoProcessadoErp).where(
                    PedidoProcessadoErp.nr_pedido == 8_099,
                    PedidoProcessadoErp.cd_prod_cor == "ERP-ANTERIOR",
                )
            )
            assert preservado is not None
            assert "mantendo snapshot anterior" in caplog.text
        finally:
            await session.rollback()


@pytest.mark.parametrize("channel_case", ["unknown", "mixed"])
async def test_sincronizar_processados_erp_canal_invalido_preserva_snapshot_anterior(
    channel_case: str,
):
    anterior = PedidoProcessadoErp(
        nr_pedido=8_098,
        cd_prod_cor="ERP-ANTES-CANAL",
        sg_tamanho="M",
        ds_grupo="Camisas",
        ds_produto="Camisa anterior",
        client="Cliente anterior",
        canal="Franquia",
        qt=0,
        vl_liquido=0,
        indica_reserva=True,
        indica_embalado=False,
        data="2026-01-05",
    )
    linha_base = {
        **_linha_financeira(valor=0, tamanho="M", quantidade=1),
        "nr_pedido": "8008",
        "cd_prod_cor": "ERP-CANAL-UNKNOWN",
        "qt_distribuida": 0,
    }
    if channel_case == "unknown":
        linhas_invalidas = [{**linha_base, "ds_tp_canal": None}]
        error_match = r"source=erp.*nr_pedido=8008.*ERP-CANAL-UNKNOWN"
    else:
        linhas_invalidas = [
            {**linha_base, "sg_tamanho": "P", "ds_tp_canal": "Franquia"},
            {**linha_base, "sg_tamanho": "M", "ds_tp_canal": "MM"},
        ]
        error_match = r"canais misturados.*source=erp.*nr_pedido=8008"
    async with async_session_factory() as session:
        session.add(anterior)
        await session.flush()
        try:
            with (
                _mock_consulta(linhas_invalidas),
                pytest.raises(
                    CanalOrigemInvalidoError,
                    match=error_match,
                ),
            ):
                await service.sincronizar_pedidos_processados_erp(session)

            preservado = await session.scalar(
                select(PedidoProcessadoErp).where(
                    PedidoProcessadoErp.nr_pedido == 8_098,
                    PedidoProcessadoErp.cd_prod_cor == "ERP-ANTES-CANAL",
                )
            )
            assert preservado is not None
            assert preservado is anterior
            assert preservado.qt == 0
            assert preservado.indica_reserva is True
        finally:
            await session.rollback()


async def test_processados_erp_agrega_qt_e_faz_or_nos_flags():
    linhas = [
        {
            "nr_pedido": "8001",
            "cd_prod_cor": "AAA1",
            "sg_tamanho": "M",
            "ds_grupo": "Camisas",
            "nm_prod": "Camisa X",
            "nm_cliente": "Cliente A",
            "ds_tp_canal": "FRANQUIA",
            "qt_entregar": "5",
            "qt_distribuida": "4",
            "vl_liquido": "50,00",
            "indica_reserva": "true",
            "indica_embalado": "false",
            "dt_emissao": "2026-01-05",
        },
        {  # mesma chave: soma qt; total repetido não soma; flags fazem OR
            "nr_pedido": "8001",
            "cd_prod_cor": "AAA1",
            "sg_tamanho": "M",
            "ds_grupo": "Camisas",
            "nm_prod": "Camisa X",
            "nm_cliente": "Cliente A",
            "ds_tp_canal": "FRANQUIA",
            "qt_entregar": "1",
            "qt_distribuida": "1",
            "vl_liquido": "50,00",
            "indica_reserva": "false",
            "indica_embalado": "true",
            "dt_emissao": "2026-01-05",
        },
        {  # descartada: nr_pedido invalido
            "nr_pedido": "0",
            "cd_prod_cor": "BBB1",
            "sg_tamanho": "P",
            "ds_grupo": "Calcas",
            "nm_prod": "Calca Y",
            "nm_cliente": "Outro",
            "ds_tp_canal": "FRANQUIA",
            "qt_entregar": "1",
            "qt_distribuida": "1",
            "vl_liquido": "10,00",
            "indica_reserva": "true",
            "indica_embalado": "false",
            "dt_emissao": "2026-01-05",
        },
    ]
    with _mock_consulta(linhas):
        async with async_session_factory() as session:
            total = await service.sincronizar_pedidos_processados_erp(session)

            assert total == 1

            row = await session.scalar(
                select(PedidoProcessadoErp).where(PedidoProcessadoErp.nr_pedido == 8001)
            )
            assert row is not None
            assert (
                row.qt == 5
            )  # 4 + 1 (qt_distribuida tem prioridade sobre qt_entregar)
            assert float(row.vl_liquido) == 50.0
            assert row.indica_reserva is True  # OR: True | False
            assert row.indica_embalado is True  # OR: False | True

            assert (
                await session.scalar(
                    select(PedidoProcessadoErp).where(
                        PedidoProcessadoErp.nr_pedido == 0
                    )
                )
                is None
            )

            await session.rollback()


async def test_sincronizar_processados_erp_persiste_tamanho_com_qt_zero():
    linha = {
        **_linha_financeira(valor=0, tamanho="SEM-SALDO", quantidade=9),
        "nr_pedido": "8009",
        "cd_prod_cor": "ERP-ZERO-QTY",
        "qt_distribuida": 0,
        "indica_reserva": False,
        "indica_embalado": True,
    }

    with _mock_consulta([linha]):
        async with async_session_factory() as session:
            assert await service.sincronizar_pedidos_processados_erp(session) == 1
            row = await session.scalar(
                select(PedidoProcessadoErp).where(
                    PedidoProcessadoErp.nr_pedido == 8_009,
                    PedidoProcessadoErp.cd_prod_cor == "ERP-ZERO-QTY",
                    PedidoProcessadoErp.sg_tamanho == "SEM-SALDO",
                )
            )
            assert row is not None
            assert row.qt == 0
            assert row.vl_liquido == 0
            assert row.indica_reserva is False
            assert row.indica_embalado is True
            await session.rollback()


async def test_sincronizar_processados_erp_rateia_total_repetido_por_tamanho():
    base = {
        "nr_pedido": "8002",
        "cd_prod_cor": "ERP-NUM",
        "ds_grupo": "Calcas",
        "nm_prod": "Calca ERP",
        "nm_cliente": "Cliente ERP",
        "ds_tp_canal": "FRANQUIA",
        "vl_liquido": "100,00",
        "indica_reserva": "true",
        "indica_embalado": "false",
        "dt_emissao": "2026-01-05",
    }
    linhas = [
        {**base, "sg_tamanho": "36", "qt_entregar": "2", "qt_distribuida": "2"},
        {**base, "sg_tamanho": "37", "qt_entregar": "3", "qt_distribuida": "3"},
    ]

    with _mock_consulta(linhas):
        async with async_session_factory() as session:
            assert await service.sincronizar_pedidos_processados_erp(session) == 2
            rows = (
                (
                    await session.execute(
                        select(PedidoProcessadoErp)
                        .where(PedidoProcessadoErp.nr_pedido == 8002)
                        .order_by(PedidoProcessadoErp.sg_tamanho)
                    )
                )
                .scalars()
                .all()
            )
            assert [
                (row.sg_tamanho, row.qt, float(row.vl_liquido)) for row in rows
            ] == [
                ("36", 2, 40.0),
                ("37", 3, 60.0),
            ]
            assert sum(row.vl_liquido for row in rows) == 100
            await session.rollback()


# ---------------------------------------------------------------------------
# sincronizar_tudo — só orquestração, tudo mockado (comita internamente, então
# nunca deve ser exercitado contra o banco real neste nível de teste)
# ---------------------------------------------------------------------------


async def test_sincronizar_tudo_coleta_antes_do_lock_e_aplica_snapshot():
    source = MagicMock()
    repo = MagicMock()
    realtime = MagicMock()
    job_lock = MagicMock()

    @asynccontextmanager
    async def hold():
        yield True

    job_lock.hold = hold
    preparado = object()
    resultado_esperado = {
        "pedidos_inseridos": 3,
        "estoque_chaves": 7,
        "processados_erp": 2,
        "faturamento_colecoes": 6,
        "referencia_tamanho_posicao": 4,
    }
    with (
        patch.object(
            casos_uso,
            "_coletar_snapshot",
            new=AsyncMock(return_value=preparado),
        ) as coletar,
        patch.object(
            casos_uso,
            "_aplicar_snapshot",
            new=AsyncMock(return_value=resultado_esperado),
        ) as aplicar,
    ):
        resultado = await casos_uso.sincronizar_tudo(
            source,
            repo,
            realtime,
            job_lock,
        )

    coletar.assert_awaited_once_with(source)
    aplicar.assert_awaited_once_with(repo, realtime, preparado)
    assert resultado == {
        "pedidos_inseridos": 3,
        "estoque_chaves": 7,
        "processados_erp": 2,
        "faturamento_colecoes": 6,
        "referencia_tamanho_posicao": 4,
    }


async def test_coleta_falha_sem_abrir_swap_postgres():
    source = MagicMock()
    repo = MagicMock()
    repo.acquire_mutation_lock = AsyncMock()
    job_lock = MagicMock()

    @asynccontextmanager
    async def hold():
        yield True

    job_lock.hold = hold
    with (
        patch.object(
            casos_uso,
            "_coletar_snapshot",
            new=AsyncMock(side_effect=RuntimeError("fonte indisponível")),
        ),
        pytest.raises(RuntimeError, match="fonte indisponível"),
    ):
        await casos_uso.sincronizar_tudo(
            source,
            repo,
            MagicMock(),
            job_lock,
        )

    repo.acquire_mutation_lock.assert_not_awaited()


async def test_timeout_total_cancela_coleta_sem_entrar_no_swap():
    source = MagicMock()
    repo = MagicMock()
    repo.acquire_mutation_lock = AsyncMock()
    job_lock = MagicMock()

    @asynccontextmanager
    async def hold():
        yield True

    async def coleta_lenta(_source):
        await asyncio.sleep(1)

    job_lock.hold = hold
    with (
        patch.object(casos_uso, "_MIN_SWAP_BUDGET_SECONDS", 0.01),
        patch.object(casos_uso, "_coletar_snapshot", side_effect=coleta_lenta),
        pytest.raises(casos_uso.SincronizacaoTimeoutError),
    ):
        await casos_uso.sincronizar_tudo(
            source,
            repo,
            MagicMock(),
            job_lock,
            total_timeout_seconds=0.03,
        )

    repo.acquire_mutation_lock.assert_not_awaited()


async def test_falha_durante_swap_faz_rollback_sem_commit():
    repo = MagicMock()
    repo.acquire_mutation_lock = AsyncMock(return_value=True)
    repo.rollback = AsyncMock()
    repo.commit = AsyncMock()
    preparado = MagicMock()
    with (
        patch.object(
            casos_uso,
            "_persistir_pedidos",
            new=AsyncMock(return_value=3),
        ),
        patch.object(
            casos_uso,
            "_persistir_estoque",
            new=AsyncMock(side_effect=RuntimeError("swap falhou")),
        ),
        pytest.raises(RuntimeError, match="swap falhou"),
    ):
        await casos_uso._aplicar_snapshot(repo, MagicMock(), preparado)

    repo.rollback.assert_awaited_once()
    repo.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# agregar_referencia_tamanhos (D-01/D-03/D-04 — sem I/O, mesmo espírito de
# test_ingestao_parse.py)
# ---------------------------------------------------------------------------


def test_agregar_referencia_tamanhos_descarta_posicao_invalida_sem_lancar():
    linhas = [
        {"cd_prod_cor": "ABC1", "sg_tamanho": "M", "nr_posicao": "5"},
        {
            "cd_prod_cor": "ABC1",
            "sg_tamanho": "G",
            "nr_posicao": "0",
        },  # descartada: abaixo do minimo
        {
            "cd_prod_cor": "ABC1",
            "sg_tamanho": "P",
            "nr_posicao": "49",
        },  # descartada: acima do maximo
        {
            "cd_prod_cor": "ABC1",
            "sg_tamanho": "GG",
            "nr_posicao": "abc",
        },  # descartada: nao numerico
    ]
    agrupado, descartadas, conflitos_por_produto = agregar_referencia_tamanhos(linhas)

    assert descartadas == 3
    assert conflitos_por_produto == {}
    assert list(agrupado.keys()) == [("ABC1", "M")]
    assert agrupado[("ABC1", "M")] == {
        "cd_prod_cor": "ABC1",
        "sg_tamanho": "M",
        "nr_posicao": 5,
    }


def test_agregar_referencia_tamanhos_detecta_conflito_de_posicao_mantem_ambas_linhas():
    cd = "ABC1"
    linhas = [
        {"cd_prod_cor": cd, "sg_tamanho": "M", "nr_posicao": "5"},
        {
            "cd_prod_cor": cd,
            "sg_tamanho": "GG",
            "nr_posicao": "5",
        },  # mesma posicao, tamanho diferente
    ]
    agrupado, descartadas, conflitos_por_produto = agregar_referencia_tamanhos(linhas)

    assert descartadas == 0
    assert conflitos_por_produto == {cd: ["M/GG->pos5"]}
    assert (cd, "M") in agrupado
    assert (cd, "GG") in agrupado


def test_agregar_referencia_tamanhos_agrega_conflitos_por_produto_nao_por_linha():
    linhas = [
        {"cd_prod_cor": "PROD1", "sg_tamanho": "M", "nr_posicao": "5"},
        {"cd_prod_cor": "PROD1", "sg_tamanho": "GG", "nr_posicao": "5"},
        {"cd_prod_cor": "PROD2", "sg_tamanho": "P", "nr_posicao": "3"},
        {"cd_prod_cor": "PROD2", "sg_tamanho": "M", "nr_posicao": "3"},
    ]
    _, descartadas, conflitos_por_produto = agregar_referencia_tamanhos(linhas)

    assert descartadas == 0
    assert set(conflitos_por_produto.keys()) == {"PROD1", "PROD2"}
    assert conflitos_por_produto["PROD1"] == ["M/GG->pos5"]
    assert conflitos_por_produto["PROD2"] == ["P/M->pos3"]


# ---------------------------------------------------------------------------
# sincronizar_referencia_tamanhos (5ª fonte, ING-01 critérios 1-3, D-01, D-02)
# ---------------------------------------------------------------------------


async def test_sincronizar_referencia_tamanhos_tabela_nao_configurada_levanta_erro():
    fake_settings = MagicMock(databricks_tabela_tamanho_ref="")
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as mock_consulta,
    ):
        async with async_session_factory() as session:
            with pytest.raises(DatabricksError):
                await service.sincronizar_referencia_tamanhos(session)
        mock_consulta.assert_not_called()


async def test_sincronizar_referencia_tamanhos_sql_exato():
    tabela = get_settings().databricks_tabela_tamanho_ref or "tabela_fake_tamanho_ref"
    fake_settings = MagicMock(databricks_tabela_tamanho_ref=tabela)
    esperado = f"SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM {tabela}"
    with (
        patch(
            "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
            return_value=fake_settings,
        ),
        _mock_consulta([]) as mock_consulta,
    ):
        async with async_session_factory() as session:
            await service.sincronizar_referencia_tamanhos(session)
    args, kwargs = mock_consulta.call_args
    assert args[0] == esperado
    assert kwargs == {}


def _fake_settings_tamanho_ref():
    """A env local pode não ter DATABRICKS_TABELA_TAMANHO_REF configurada
    (só é obrigatório preencher ao testar contra o Databricks real) — os
    testes abaixo mockam settings com um nome de tabela fake, igual ao
    padrão usado para as 4 fontes irmãs quando necessário."""
    tabela = get_settings().databricks_tabela_tamanho_ref or "tabela_fake_tamanho_ref"
    return patch(
        "app.modules.ingestao.infrastructure.databricks_reader.get_settings",
        return_value=MagicMock(databricks_tabela_tamanho_ref=tabela),
    )


async def test_sincronizar_referencia_tamanhos_persiste_e_descarta_invalidas():
    linhas = [
        {"cd_prod_cor": "REF1|001", "sg_tamanho": "P", "nr_posicao": "5"},
        {"cd_prod_cor": "REF1|001", "sg_tamanho": "M", "nr_posicao": "12"},
        {
            "cd_prod_cor": "REF1|001",
            "sg_tamanho": "GG",
            "nr_posicao": "abc",
        },  # descartada: nao numerico
        {
            "cd_prod_cor": "REF1|001",
            "sg_tamanho": "G",
            "nr_posicao": "0",
        },  # descartada: fora de faixa
        {
            "cd_prod_cor": "REF1|001",
            "sg_tamanho": "XG",
            "nr_posicao": "49",
        },  # descartada: fora de faixa
    ]
    with _fake_settings_tamanho_ref(), _mock_consulta(linhas):
        async with async_session_factory() as session:
            total = await service.sincronizar_referencia_tamanhos(session)

            assert total == 2

            validas = await session.scalars(
                select(ProdutoTamanhoPosicao).where(
                    ProdutoTamanhoPosicao.cd_prod_cor == "REF1|001"
                )
            )
            tamanhos_persistidos = {row.sg_tamanho for row in validas}
            assert tamanhos_persistidos == {"P", "M"}

            await (
                session.rollback()
            )  # nunca persistir: full refresh apagaria dados reais


async def test_sincronizar_referencia_tamanhos_view_vazia_mantem_snapshot_anterior():
    async with async_session_factory() as session:
        # Contagem relativa: o banco de dev pode já ter a referência real
        # ingerida (centenas de milhares de linhas), então o esperado é
        # "o que havia + a linha inserida aqui", nunca um número fixo.
        antes = (
            await session.scalar(
                select(func.count()).select_from(ProdutoTamanhoPosicao)
            )
            or 0
        )

        # Simula um snapshot pré-existente (flush, sem commit).
        session.add(
            ProdutoTamanhoPosicao(cd_prod_cor="AAA1|001", sg_tamanho="M", nr_posicao=5)
        )
        await session.flush()

        with _fake_settings_tamanho_ref(), _mock_consulta([]):  # view devolve 0 linhas
            total = await service.sincronizar_referencia_tamanhos(session)

        # A linha pré-existente continua lá — substituir_referencia_tamanhos
        # nunca foi chamada (guard D-02).
        row = await session.scalar(
            select(ProdutoTamanhoPosicao).where(
                ProdutoTamanhoPosicao.cd_prod_cor == "AAA1|001"
            )
        )
        assert row is not None
        assert total == antes + 1  # contagem atual, não 0

        await session.rollback()


async def test_referencia_tamanhos_substitui_duas_vezes_sem_duplicar():
    async with async_session_factory() as session:
        linhas_produto_a = [
            {"cd_prod_cor": "PRODA|001", "sg_tamanho": "P", "nr_posicao": "1"},
            {"cd_prod_cor": "PRODA|001", "sg_tamanho": "M", "nr_posicao": "2"},
        ]
        with _fake_settings_tamanho_ref(), _mock_consulta(linhas_produto_a):
            total_1 = await service.sincronizar_referencia_tamanhos(session)
        await session.flush()

        assert total_1 == 2
        count_1 = await session.scalar(
            select(func.count()).select_from(ProdutoTamanhoPosicao)
        )
        assert count_1 == 2

        linhas_produto_b = [
            {"cd_prod_cor": "PRODB|001", "sg_tamanho": "G", "nr_posicao": "3"},
        ]
        with _fake_settings_tamanho_ref(), _mock_consulta(linhas_produto_b):
            total_2 = await service.sincronizar_referencia_tamanhos(session)
        await session.flush()

        assert total_2 == 1
        count_2 = await session.scalar(
            select(func.count()).select_from(ProdutoTamanhoPosicao)
        )
        assert count_2 == 1  # nunca 3 — substituição, não acumulação

        assert (
            await session.scalar(
                select(ProdutoTamanhoPosicao).where(
                    ProdutoTamanhoPosicao.cd_prod_cor == "PRODA|001"
                )
            )
            is None
        )

        await session.rollback()


# ---------------------------------------------------------------------------
# Guard de concorrência (advisory lock de transação)
#
# Duas sincronizações simultâneas colidiam no full refresh: o DELETE da segunda
# avalia um snapshot anterior ao COMMIT da primeira, então seus INSERTs batem em
# linhas recém-commitadas (violação de `uq_pedido_item`). Os dados ficavam
# íntegros (rollback da transação inteira), mas só após ~7 min perdidos. Agora a
# segunda é recusada de imediato.
# ---------------------------------------------------------------------------


async def test_job_lock_serializa_duas_sincronizacoes_e_libera_no_fim():
    """O lock do job usa conexão dedicada e falha rápido antes da coleta."""
    async with (
        async_session_factory() as primeira,
        async_session_factory() as segunda,
    ):
        lock_1 = PostgresIngestionJobLock(primeira)
        lock_2 = PostgresIngestionJobLock(segunda)
        async with lock_1.hold() as acquired_1:
            if not acquired_1:
                pytest.skip("job real de ingestão já está em andamento")
            async with lock_2.hold() as acquired_2:
                assert acquired_2 is False

        async with lock_2.hold() as acquired_after_release:
            assert acquired_after_release is True


async def test_lock_de_job_nao_bloqueia_write_e_mutation_lock_so_bloqueia_swap():
    """Duas sessões reais provam a separação entre coleta e swap."""

    async with (
        async_session_factory() as sincronizacao,
        async_session_factory() as write,
    ):
        job_lock = PostgresIngestionJobLock(sincronizacao)
        repo = SqlSnapshotRepository(sincronizacao)
        async with job_lock.hold() as acquired:
            if not acquired:
                pytest.skip("job real de ingestão já está em andamento")

            # Fase de coleta: o lock de job não interfere nos writes do domínio.
            assert (
                await write.scalar(
                    select(func.pg_try_advisory_xact_lock(_LOCK_SINCRONIZACAO))
                )
                is True
            )
            await write.rollback()

            # Início do swap: o sync toma o lock de mutação na própria transação.
            assert await repo.acquire_mutation_lock() is True
            assert (
                await write.scalar(
                    select(func.pg_try_advisory_xact_lock(_LOCK_SINCRONIZACAO))
                )
                is False
            )
            await write.rollback()

            # Commit encerra o swap e libera o write imediatamente.
            await sincronizacao.commit()
            assert (
                await write.scalar(
                    select(func.pg_try_advisory_xact_lock(_LOCK_SINCRONIZACAO))
                )
                is True
            )
            await write.rollback()


async def test_mutation_lock_espera_write_curto_e_aplica_sem_refazer_coleta():
    async with (
        async_session_factory() as write,
        async_session_factory() as sincronizacao,
    ):
        assert (
            await write.scalar(
                select(func.pg_try_advisory_xact_lock(_LOCK_SINCRONIZACAO))
            )
            is True
        )
        repo = SqlSnapshotRepository(sincronizacao)
        aguardando = asyncio.create_task(repo.acquire_mutation_lock())
        await asyncio.sleep(0.25)
        assert aguardando.done() is False
        await write.rollback()
        assert await asyncio.wait_for(aguardando, timeout=1) is True
        await sincronizacao.rollback()


async def test_mutation_lock_persistente_expira_bounded_sem_mutacao():
    async with (
        async_session_factory() as write,
        async_session_factory() as sincronizacao,
    ):
        assert (
            await write.scalar(
                select(func.pg_try_advisory_xact_lock(_LOCK_SINCRONIZACAO))
            )
            is True
        )
        repo = SqlSnapshotRepository(sincronizacao)
        inicio = time.monotonic()
        with patch(
            "app.modules.ingestao.infrastructure.adapters._MUTATION_LOCK_TIMEOUT_SECONDS",
            0.05,
        ):
            assert await repo.acquire_mutation_lock() is False
        assert time.monotonic() - inicio < 0.5
        await sincronizacao.rollback()
        await write.rollback()
