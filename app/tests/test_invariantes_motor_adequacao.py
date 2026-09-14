"""Self-teste dos helpers de invariante de invariantes_motor_adequacao.py.

Sem hypothesis (a dependência só entra na Phase 19): aqui cada invariante
vira um caso de exemplo que passa e um caso de exemplo que falha.
"""

from decimal import Decimal

import pytest

from app.modules.pedidos.domain.value_objects import montar_chave_estoque
from app.tests.factories_pedidos_motor import item_pedido
from app.tests.invariantes_motor_adequacao import (
    assert_contrato_chaves_resultado,
    assert_estoque_nunca_excedido,
    assert_execucoes_identicas,
    assert_furo_de_grade_nao_selecionado,
    assert_minimo_viavel_antes_de_extra,
    assert_orcamento_nao_excedido,
    assert_rateio_sem_drift,
    assert_sem_credito_nunca_alocado,
    assert_tudo_ou_nada_preserva_quantidade,
    calcular_furo_de_grade,
)

# ---------------------------------------------------------------------------
# I1 — assert_estoque_nunca_excedido
# ---------------------------------------------------------------------------


def test_assert_estoque_nunca_excedido_passa_quando_reserva_dentro_do_estoque():
    chave = montar_chave_estoque("PROD1", "M")
    item = item_pedido("M", 5, 250.0, cd_prod_cor="PROD1", status_item="Gerar OR")
    resultado = {"resultados": {(1, "PROD1"): [item]}}

    assert assert_estoque_nunca_excedido({chave: 10}, resultado) is None


def test_assert_estoque_nunca_excedido_falha_quando_reserva_excede_estoque():
    chave = montar_chave_estoque("PROD1", "M")
    item = item_pedido("M", 15, 750.0, cd_prod_cor="PROD1", status_item="Gerar OR")
    resultado = {"resultados": {(1, "PROD1"): [item]}}

    with pytest.raises(AssertionError, match="I1 violada"):
        assert_estoque_nunca_excedido({chave: 10}, resultado)


# ---------------------------------------------------------------------------
# I2 — assert_sem_credito_nunca_alocado
# ---------------------------------------------------------------------------


def test_assert_sem_credito_nunca_alocado_passa_quando_bloqueado_fica_fora():
    dados = [
        item_pedido("M", 10, 500.0, nr_pedido=1, status_credito="Sem Credito"),
    ]
    resultado = {
        "resultados": {
            (1, "PROD1"): [
                item_pedido(
                    "M", 10, 500.0, nr_pedido=1, status_item="Pedido em Stand By"
                )
            ]
        },
        "selecionados": [],
        "preteridos": [],
        "bloqueados_credito": [1],
        "pares_processados": set(),
    }

    assert assert_sem_credito_nunca_alocado(dados, resultado) is None


def test_assert_sem_credito_nunca_alocado_falha_quando_selecionado_mesmo_sem_credito():
    dados = [
        item_pedido("M", 10, 500.0, nr_pedido=1, status_credito="Sem Credito"),
    ]
    resultado = {
        "resultados": {
            (1, "PROD1"): [
                item_pedido("M", 10, 500.0, nr_pedido=1, status_item="Gerar OR")
            ]
        },
        "selecionados": [(1, "PROD1")],
        "preteridos": [],
        "bloqueados_credito": [],
        "pares_processados": {(1, "PROD1")},
    }

    with pytest.raises(AssertionError, match="I2 violada"):
        assert_sem_credito_nunca_alocado(dados, resultado)


# ---------------------------------------------------------------------------
# I3 — assert_tudo_ou_nada_preserva_quantidade
# ---------------------------------------------------------------------------


def test_assert_tudo_ou_nada_preserva_quantidade_passa_quando_reserva_integra():
    itens_par = [
        item_pedido("P", 10, 500.0, status_item="Gerar OR", qt_solicitada=10),
        item_pedido("M", 8, 400.0, status_item="Gerar OR", qt_solicitada=8),
    ]

    assert assert_tudo_ou_nada_preserva_quantidade(itens_par) is None


def test_assert_tudo_ou_nada_preserva_quantidade_passa_quando_par_nao_selecionado():
    itens_par = [
        item_pedido("P", 10, 500.0, status_item="Pedido em Stand By", qt_solicitada=10),
    ]

    assert assert_tudo_ou_nada_preserva_quantidade(itens_par) is None


def test_assert_tudo_ou_nada_preserva_quantidade_falha_quando_reserva_parcial():
    itens_par = [
        item_pedido("P", 10, 500.0, status_item="Gerar OR", qt_solicitada=10),
        item_pedido("M", 6, 300.0, status_item="Gerar OR", qt_solicitada=8),
    ]

    with pytest.raises(AssertionError, match="I3 violada"):
        assert_tudo_ou_nada_preserva_quantidade(itens_par)


# ---------------------------------------------------------------------------
# calcular_furo_de_grade (oráculo de I4) — casos de borda do 14-CONTEXT.md
# ---------------------------------------------------------------------------


def test_calcular_furo_de_grade_tamanho_unico_nunca_e_furo():
    itens = [item_pedido("M", 5, 250.0)]
    estoque_local = {}

    tem_furo, motivo = calcular_furo_de_grade(itens, estoque_local, "PROD1")

    assert tem_furo is False
    assert motivo is None


def test_calcular_furo_de_grade_dois_tamanhos_adjacentes_nunca_e_furo():
    itens = [
        item_pedido("P", 5, 250.0, cd_prod_cor="PROD1"),
        item_pedido("M", 3, 150.0, cd_prod_cor="PROD1"),
    ]
    estoque_local = {}

    tem_furo, motivo = calcular_furo_de_grade(itens, estoque_local, "PROD1")

    assert tem_furo is False
    assert motivo is None


def test_calcular_furo_de_grade_pp_e_gg_sem_pedir_m_nunca_e_furo():
    itens = [
        item_pedido("PP", 5, 250.0, cd_prod_cor="PROD1"),
        item_pedido("GG", 4, 200.0, cd_prod_cor="PROD1"),
    ]
    # M está zerado, mas não foi pedido — não pode contar como furo.
    estoque_local = {montar_chave_estoque("PROD1", "M"): 0}

    tem_furo, motivo = calcular_furo_de_grade(itens, estoque_local, "PROD1")

    assert tem_furo is False
    assert motivo is None


def test_calcular_furo_de_grade_tamanho_desconhecido_nao_conta_como_interior():
    # Sem o desconhecido ("TAMX", idx 999) sendo descartado, M(idx 3) e
    # G(idx 4) seriam vistos como "adjacentes" a um extremo em 999, o que
    # faria G (interior "aparente") ser cobrado por estoque que não é seu.
    itens = [
        item_pedido("M", 5, 250.0, cd_prod_cor="PROD1"),
        item_pedido("G", 4, 200.0, cd_prod_cor="PROD1"),
        item_pedido("TAMX", 1, 50.0, cd_prod_cor="PROD1"),
    ]
    estoque_local = {montar_chave_estoque("PROD1", "G"): 0}

    tem_furo, motivo = calcular_furo_de_grade(itens, estoque_local, "PROD1")

    assert tem_furo is False
    assert motivo is None


def test_calcular_furo_de_grade_tamanho_do_meio_zerado_e_furo():
    itens = [
        item_pedido("PP", 5, 250.0, cd_prod_cor="PROD1"),
        item_pedido("M", 3, 150.0, cd_prod_cor="PROD1"),
        item_pedido("GG", 2, 100.0, cd_prod_cor="PROD1"),
    ]
    estoque_local = {montar_chave_estoque("PROD1", "M"): 0}

    tem_furo, motivo = calcular_furo_de_grade(itens, estoque_local, "PROD1")

    assert tem_furo is True
    assert motivo is not None
    assert "M" in motivo


# ---------------------------------------------------------------------------
# I4 — assert_furo_de_grade_nao_selecionado
# ---------------------------------------------------------------------------


def test_assert_furo_de_grade_nao_selecionado_passa_quando_furo_fica_fora():
    itens = [
        item_pedido("PP", 5, 250.0, cd_prod_cor="PROD1", nr_pedido=1),
        item_pedido("M", 3, 150.0, cd_prod_cor="PROD1", nr_pedido=1),
        item_pedido("GG", 2, 100.0, cd_prod_cor="PROD1", nr_pedido=1),
    ]
    estoque_local = {montar_chave_estoque("PROD1", "M"): 0}
    resultado = {"selecionados": []}

    assert (
        assert_furo_de_grade_nao_selecionado(
            (1, "PROD1"), itens, estoque_local, resultado
        )
        is None
    )


def test_assert_furo_de_grade_nao_selecionado_falha_quando_furo_e_selecionado():
    itens = [
        item_pedido("PP", 5, 250.0, cd_prod_cor="PROD1", nr_pedido=1),
        item_pedido("M", 3, 150.0, cd_prod_cor="PROD1", nr_pedido=1),
        item_pedido("GG", 2, 100.0, cd_prod_cor="PROD1", nr_pedido=1),
    ]
    estoque_local = {montar_chave_estoque("PROD1", "M"): 0}
    resultado = {"selecionados": [(1, "PROD1")]}

    with pytest.raises(AssertionError, match="I4 violada"):
        assert_furo_de_grade_nao_selecionado(
            (1, "PROD1"), itens, estoque_local, resultado
        )


# ---------------------------------------------------------------------------
# I5 — assert_orcamento_nao_excedido
# ---------------------------------------------------------------------------


def test_assert_orcamento_nao_excedido_passa_dentro_do_limite():
    # total_original=100 -> limite = floor(100*5/100) = 5
    assert assert_orcamento_nao_excedido(100, [3, 2], [1, 1], tolerancia=0.05) is None


def test_assert_orcamento_nao_excedido_falha_quando_adicionado_excede():
    with pytest.raises(AssertionError, match="I5 violada"):
        assert_orcamento_nao_excedido(100, [4, 4], [0], tolerancia=0.05)


def test_assert_orcamento_nao_excedido_falha_quando_cortado_excede_mesmo_sem_adicao():
    # Prova que os dois orçamentos não se compensam: adicionado=0 não libera
    # o teto de corte.
    with pytest.raises(AssertionError, match="I5 violada"):
        assert_orcamento_nao_excedido(100, [0], [6], tolerancia=0.05)


def test_assert_orcamento_nao_excedido_segue_a_tolerancia_recebida():
    # I5 mede contra a tolerância que a execução realmente usou, não um 5%
    # fixo: com tolerancia=0.10 o limite é 10 (cabe); com tolerancia=0.05,
    # a MESMA chamada excede o limite de 5.
    assert assert_orcamento_nao_excedido(100, [8], [0], tolerancia=0.10) is None
    with pytest.raises(AssertionError, match="I5 violada"):
        assert_orcamento_nao_excedido(100, [8], [0], tolerancia=0.05)


# ---------------------------------------------------------------------------
# I6 — assert_execucoes_identicas
# ---------------------------------------------------------------------------


def _resultado_base():
    return {
        "resultados": {(1, "PROD1"): [item_pedido("M", 10, 500.0, nr_pedido=1)]},
        "selecionados": [(1, "PROD1")],
        "preteridos": [],
        "bloqueados_credito": [],
        "pares_processados": {(1, "PROD1")},
    }


def test_assert_execucoes_identicas_passa_quando_planos_batem():
    resultado_1 = _resultado_base()
    resultado_2 = _resultado_base()

    assert assert_execucoes_identicas(resultado_1, resultado_2) is None


def test_assert_execucoes_identicas_falha_quando_selecionados_diferem():
    resultado_1 = _resultado_base()
    resultado_2 = _resultado_base()
    resultado_2["selecionados"] = []

    with pytest.raises(AssertionError, match="I6 violada"):
        assert_execucoes_identicas(resultado_1, resultado_2)


# ---------------------------------------------------------------------------
# I7 — assert_rateio_sem_drift
# ---------------------------------------------------------------------------


def test_assert_rateio_sem_drift_passa_quando_soma_bate_exatamente():
    itens = [
        item_pedido("P", 3, 33.33),
        item_pedido("M", 3, 33.34),
        item_pedido("G", 3, 33.33),
    ]

    assert assert_rateio_sem_drift(itens, Decimal("100.00")) is None


def test_assert_rateio_sem_drift_falha_quando_ha_drift_de_centavo():
    itens = [
        item_pedido("P", 3, 33.33),
        item_pedido("M", 3, 33.33),
        item_pedido("G", 3, 33.33),
    ]

    with pytest.raises(AssertionError, match="I7 violada"):
        assert_rateio_sem_drift(itens, Decimal("100.00"))


# ---------------------------------------------------------------------------
# I8 — assert_minimo_viavel_antes_de_extra
# ---------------------------------------------------------------------------


def test_assert_minimo_viavel_antes_de_extra_passa_sem_zerado_e_extra_simultaneos():
    itens_por_produto = {
        "PROD1": [
            item_pedido("M", 10, 500.0, status_item="Gerar OR", qt_solicitada=10)
        ],
        "PROD2": [item_pedido("M", 5, 250.0, status_item="Gerar OR", qt_solicitada=5)],
    }

    assert assert_minimo_viavel_antes_de_extra(itens_por_produto) is None


def test_assert_minimo_viavel_antes_de_extra_falha_quando_zerado_convive_com_extra():
    itens_por_produto = {
        "PROD1": [
            item_pedido("M", 0, 0.0, status_item="Pedido em Stand By", qt_solicitada=10)
        ],
        "PROD2": [item_pedido("M", 8, 400.0, status_item="Gerar OR", qt_solicitada=5)],
    }

    with pytest.raises(AssertionError, match="I8 violada"):
        assert_minimo_viavel_antes_de_extra(itens_por_produto)


# ---------------------------------------------------------------------------
# I9 — assert_contrato_chaves_resultado
# ---------------------------------------------------------------------------


def test_assert_contrato_chaves_resultado_passa_com_as_cinco_chaves_exatas():
    resultado = {
        "resultados": {},
        "selecionados": [],
        "preteridos": [],
        "bloqueados_credito": [],
        "pares_processados": set(),
    }

    assert assert_contrato_chaves_resultado(resultado) is None


def test_assert_contrato_chaves_resultado_falha_com_chave_faltando():
    resultado = {
        "resultados": {},
        "selecionados": [],
        "preteridos": [],
        "bloqueados_credito": [],
        # pares_processados ausente.
    }

    with pytest.raises(AssertionError, match="I9 violada"):
        assert_contrato_chaves_resultado(resultado)


def test_assert_contrato_chaves_resultado_falha_com_chave_extra():
    resultado = {
        "resultados": {},
        "selecionados": [],
        "preteridos": [],
        "bloqueados_credito": [],
        "pares_processados": set(),
        "chave_inesperada": None,
    }

    with pytest.raises(AssertionError, match="I9 violada"):
        assert_contrato_chaves_resultado(resultado)
