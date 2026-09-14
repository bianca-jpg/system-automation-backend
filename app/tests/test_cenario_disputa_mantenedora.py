"""Prova de que a fixture de disputa da mantenedora produz A atendido / B em
stand by contra o motor de adequação, nos dois modos (ADEQUAR/SEM_ADEQUAR)."""

from app.modules.pedidos.domain.politica_quantidade import ModoAdequacao
from app.modules.pedidos.service import processar_pedidos
from app.tests.cenarios_motor_adequacao import (
    NR_PEDIDO_CLIENTE_A,
    NR_PEDIDO_CLIENTE_B,
    PRODUTO_DISPUTADO,
    TAMANHO_DISPUTADO,
    cenario_disputa_mantenedora,
)


def test_cenario_disputa_mantenedora_cliente_a_atendido_cliente_b_stand_by():
    """Modo ADEQUAR — atualizado no plano 14-06 (ALOC-07): a base do
    orçamento de adição passou a ser o PEDIDO COMPLETO do cliente A (1000
    peças, filler + disputado), não mais a grade do produto disputado
    isolado (10 peças). Com estoque de 20 disponíveis no produto disputado
    e apenas 10 pedidos, o produto tem folga total (elegível à passada 2) e
    o orçamento de adição do pedido inteiro (floor(1000 * 5%) = 50) cobre as
    10 peças extra de espaço — resultado correto e esperado da regra nova,
    não uma tolerância residual do produto isolado (que antes era
    floor(10 * 5%) = 0 e por isso não concedia extra nenhum)."""
    dados, estoque = cenario_disputa_mantenedora()

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    par_a = (NR_PEDIDO_CLIENTE_A, PRODUTO_DISPUTADO)
    par_b = (NR_PEDIDO_CLIENTE_B, PRODUTO_DISPUTADO)

    assert par_a in resultado["selecionados"]
    assert par_b in resultado["preteridos"]
    assert par_b not in resultado["selecionados"]

    item_a = next(
        item
        for item in resultado["resultados"][par_a]
        if item["sg_tamanho"] == TAMANHO_DISPUTADO
    )
    assert item_a["status_item"] == "Gerar OR"
    # ALOC-07: orçamento de adição é do pedido completo (1000 pçs), não do
    # produto isolado (10 pçs) — o produto disputado tem folga (20
    # disponíveis, 10 pedidos) e recebe as 10 peças de espaço extra do
    # orçamento de adição do pedido inteiro (floor(1000 * 5%) = 50).
    assert item_a["qt_solicitada"] == 10
    assert item_a["qt_liquida"] == 20

    item_b = next(
        item
        for item in resultado["resultados"][par_b]
        if item["sg_tamanho"] == TAMANHO_DISPUTADO
    )
    assert item_b["status_item"] == "Pedido em Stand By"


def test_cenario_disputa_mantenedora_cliente_a_atendido_cliente_b_stand_by_sem_adequar():
    """Modo SEM_ADEQUAR (ALOC-02, plano 14-05): mesma prioridade decide quem
    consome o estoque disputado; a diferença de regra é que B, sem couber a
    grade completa, vai a stand-by com a quantidade PEDIDA preservada (15),
    não reduzida ao que sobrou."""
    dados, estoque = cenario_disputa_mantenedora()

    resultado = processar_pedidos(
        dados,
        estoque,
        ja_processados=set(),
        criterio="valor",
        tolerancia=0.05,
        modo=ModoAdequacao.SEM_ADEQUAR,
    )

    par_a = (NR_PEDIDO_CLIENTE_A, PRODUTO_DISPUTADO)
    par_b = (NR_PEDIDO_CLIENTE_B, PRODUTO_DISPUTADO)

    assert par_a in resultado["selecionados"]
    assert par_b in resultado["preteridos"]
    assert par_b not in resultado["selecionados"]

    item_a = next(
        item
        for item in resultado["resultados"][par_a]
        if item["sg_tamanho"] == TAMANHO_DISPUTADO
    )
    assert item_a["status_item"] == "Gerar OR"
    assert item_a["qt_liquida"] == 10
    assert item_a["qt_solicitada"] == 10

    item_b = next(
        item
        for item in resultado["resultados"][par_b]
        if item["sg_tamanho"] == TAMANHO_DISPUTADO
    )
    assert item_b["status_item"] == "Pedido em Stand By"
    assert item_b["qt_liquida"] == 15  # preservada, não reduzida ao que sobrou
    assert item_b["motivo_stand_by"] == "Sem adequação: grade completa indisponível"
