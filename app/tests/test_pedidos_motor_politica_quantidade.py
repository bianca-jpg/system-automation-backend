"""Testes de integração da política de quantidade por modo (ADEQUAR vs
SEM_ADEQUAR) — ALOC-01/02/03. Domínio puro, sem I/O."""

from app.modules.pedidos.domain.motor_adequacao import processar_pedidos
from app.modules.pedidos.domain.politica_quantidade import ModoAdequacao
from app.modules.pedidos.processing.domain import ProcessingMode

# ---------------------------------------------------------------------------
# Paridade ModoAdequacao <-> ProcessingMode
# ---------------------------------------------------------------------------


def test_modo_adequacao_valores_espelham_processing_mode():
    """Import cruzando domain/ -> processing/ é OK aqui: é teste VERIFICANDO
    consistência, não produção acoplando a direção errada da dependência."""
    assert {m.value for m in ModoAdequacao} == {m.value for m in ProcessingMode}
    assert ModoAdequacao.ADEQUAR.value == "adequar"
    assert ModoAdequacao.SEM_ADEQUAR.value == "sem_adequar"


def _item(sg_tamanho, qt_liquida, vl_liquido, **extra):
    base = {
        "nr_pedido": 1,
        "cd_prod_cor": "PROD1",
        "sg_tamanho": sg_tamanho,
        "ds_grupo": "Camisas",
        "qt_liquida": qt_liquida,
        "vl_liquido": vl_liquido,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# processar_pedidos — despacho por modo (ALOC-01, ALOC-02, ALOC-03)
# ---------------------------------------------------------------------------


def test_processar_pedidos_disputa_prioridade_mantenedora_atende_maior_valor_nos_dois_modos():
    """Cenário nomeado "Disputa da mantenedora": A (R$50.000, pede 20 peças,
    estoque=20) leva as 20 peças; B (R$1.000) fica em stand-by — em AMBOS os
    modos."""
    dados = [
        _item(
            "M",
            20,
            50000.0,
            nr_pedido=1,
            cd_prod_cor="PRODX",
            canal="Franquia",
            status_credito="Com Credito",
        ),
        _item(
            "M",
            5,
            1000.0,
            nr_pedido=2,
            cd_prod_cor="PRODX",
            canal="Franquia",
            status_credito="Com Credito",
        ),
    ]
    estoque = {"Franquia": {"PRODX_M": 20}}

    for modo in (ModoAdequacao.ADEQUAR, ModoAdequacao.SEM_ADEQUAR):
        resultado = processar_pedidos(dados, estoque, set(), modo=modo)

        assert resultado["selecionados"] == [(1, "PRODX")]
        assert resultado["preteridos"] == [(2, "PRODX")]

        item_a = resultado["resultados"][(1, "PRODX")][0]
        assert item_a["status_item"] == "Gerar OR"
        assert item_a["qt_liquida"] == 20


def test_processar_pedidos_credito_bloqueia_e_preserva_estoque_nos_dois_modos():
    """Pedido sem crédito nunca aparece em selecionados/pares_processados; o
    estoque que ele teria consumido continua disponível para o próximo
    pagante, provado nos dois modos."""
    dados = [
        _item(
            "M",
            10,
            100.0,
            nr_pedido=1,
            cd_prod_cor="Z",
            canal="Franquia",
            status_credito="Sem Credito",
        ),
        _item(
            "M",
            10,
            200.0,
            nr_pedido=2,
            cd_prod_cor="Z",
            canal="Franquia",
            status_credito="Com Credito",
        ),
    ]
    estoque = {"Franquia": {"Z_M": 10}}

    for modo in (ModoAdequacao.ADEQUAR, ModoAdequacao.SEM_ADEQUAR):
        resultado = processar_pedidos(dados, estoque, set(), modo=modo)

        assert resultado["bloqueados_credito"] == [1]
        assert (1, "Z") not in resultado["pares_processados"]
        assert resultado["selecionados"] == [(2, "Z")]

        item_pagante = resultado["resultados"][(2, "Z")][0]
        # Grade CHEIA: os 10 do bloqueado nunca foram descontados do estoque
        # disponível para o próximo da fila.
        assert item_pagante["qt_liquida"] == 10


def test_processar_pedidos_sem_adequar_rejeita_falta_que_adequar_toleraria():
    """Mesma entrada (falta de 2 em 100, dentro dos 5% de tolerância que
    ADEQUAR aceitaria) produz resultado diferente por modo — a prova central
    de ALOC-02."""
    dados = [
        _item(
            "P",
            100,
            10000.0,
            nr_pedido=1,
            cd_prod_cor="PRODY",
            canal="Franquia",
            status_credito="Com Credito",
        ),
    ]

    resultado_adequar = processar_pedidos(
        dados,
        {"Franquia": {"PRODY_P": 98}},
        set(),
        modo=ModoAdequacao.ADEQUAR,
    )
    item_adequar = resultado_adequar["resultados"][(1, "PRODY")][0]
    assert item_adequar["status_item"] == "Gerar OR"
    assert item_adequar["qt_liquida"] == 98

    resultado_sem_adequar = processar_pedidos(
        dados,
        {"Franquia": {"PRODY_P": 98}},  # dict novo, não o já decrementado
        set(),
        modo=ModoAdequacao.SEM_ADEQUAR,
    )
    item_sem_adequar = resultado_sem_adequar["resultados"][(1, "PRODY")][0]
    assert item_sem_adequar["status_item"] == "Pedido em Stand By"
    assert item_sem_adequar["qt_liquida"] == 100
    assert (
        item_sem_adequar["motivo_stand_by"]
        == "Sem adequação: grade completa indisponível"
    )


def test_processar_pedidos_sem_adequar_rejeicao_e_por_par_nao_por_pedido():
    """A rejeição em SEM_ADEQUAR é por par (nr_pedido, cd_prod_cor), não por
    pedido inteiro: outros produtos do mesmo pedido seguem elegíveis."""
    dados = [
        _item(
            "M",
            5,
            500.0,
            nr_pedido=1,
            cd_prod_cor="A",
            canal="Franquia",
            status_credito="Com Credito",
        ),
        _item(
            "M",
            10,
            1000.0,
            nr_pedido=1,
            cd_prod_cor="B",
            canal="Franquia",
            status_credito="Com Credito",
        ),
    ]
    estoque = {"Franquia": {"A_M": 10, "B_M": 3}}

    resultado = processar_pedidos(dados, estoque, set(), modo=ModoAdequacao.SEM_ADEQUAR)

    assert (1, "A") in resultado["selecionados"]
    assert (1, "B") in resultado["preteridos"]
