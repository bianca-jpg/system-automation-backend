"""Regressões de privacidade e volume dos logs da conversão Linx."""

import logging

from app.modules.pedidos.domain.grade_linx import converter_grade_para_posicoes


def test_grade_linx_loga_somente_categorias_e_contagens(caplog) -> None:
    codigo_produto_sensivel = "PEDIDO-778899|PROD-COR-SIGILOSO"
    tamanho_primeiro = "TAMANHO-SIGILOSO-ALFA"
    tamanho_conflitante = "TAMANHO-SIGILOSO-BETA"
    tamanho_sem_referencia = "TAMANHO-SIGILOSO-GAMA"

    with caplog.at_level(
        logging.WARNING, logger="app.modules.pedidos.domain.grade_linx"
    ):
        converter_grade_para_posicoes(
            grade={
                tamanho_primeiro: 2,
                tamanho_conflitante: 3,
                tamanho_sem_referencia: 5,
            },
            referencia={tamanho_primeiro: 47, tamanho_conflitante: 47},
            cd_prod_cor=codigo_produto_sensivel,
        )

    assert "categoria=tamanho_sem_referencia quantidade=1" in caplog.text
    assert "categoria=conflito_posicao quantidade=1" in caplog.text
    assert len(caplog.records) == 2

    valores_sensiveis = (
        codigo_produto_sensivel,
        tamanho_primeiro,
        tamanho_conflitante,
        tamanho_sem_referencia,
        "778899",
        "PROD-COR-SIGILOSO",
    )
    assert all(valor not in caplog.text for valor in valores_sensiveis)
