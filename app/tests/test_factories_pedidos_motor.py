"""Self-teste das factories compartilhadas do motor de adequação (sem I/O)."""

import pytest

from app.modules.pedidos.domain.value_objects import CANAIS, montar_chave_estoque
from app.tests.factories_pedidos_motor import (
    estoque_por_canal,
    estoque_produto,
    item_pedido,
)


def test_item_pedido_defaults():
    item = item_pedido("M", 10, 500.0)

    assert item == {
        "nr_pedido": 1,
        "cd_prod_cor": "PROD1",
        "sg_tamanho": "M",
        "qt_liquida": 10,
        "vl_liquido": 500.0,
        "status_credito": "Com Credito",
        "canal": "Franquia",
        "indica_blacklist": False,
    }


def test_item_pedido_overrides_nomeados_e_via_extra():
    item = item_pedido(
        "M",
        10,
        500.0,
        nr_pedido=42,
        canal="Multimarca",
        status_credito="Sem Credito",
        ds_grupo="Camisas",
    )

    assert item["nr_pedido"] == 42
    assert item["canal"] == "Multimarca"
    assert item["status_credito"] == "Sem Credito"
    assert item["ds_grupo"] == "Camisas"
    # Os três posicionais não são afetados pelos overrides.
    assert item["sg_tamanho"] == "M"
    assert item["qt_liquida"] == 10
    assert item["vl_liquido"] == 500.0


def test_estoque_produto_chaves_e_validacao_de_canal():
    resultado = estoque_produto("PROD1", {"P": 5, "M": 10})

    assert resultado == {
        montar_chave_estoque("PROD1", "P"): 5,
        montar_chave_estoque("PROD1", "M"): 10,
    }

    with pytest.raises(ValueError):
        estoque_por_canal(**{"Canal Inexistente": {"PROD1_M": 10}})

    canal_valido = CANAIS[0]
    assert estoque_por_canal(**{canal_valido: {"PROD1_M": 10}}) == {
        canal_valido: {"PROD1_M": 10}
    }
