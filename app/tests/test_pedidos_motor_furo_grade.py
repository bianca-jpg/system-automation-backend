"""Testes de furo de grade (ALOC-04): unitários do predicado puro
`tem_furo_de_grade` e de integração provando a sensibilidade à ordem dentro
do laço de `_processar_pedidos_canal`."""

from app.modules.pedidos.domain.furo_de_grade import tem_furo_de_grade
from app.modules.pedidos.domain.politica_quantidade import ModoAdequacao
from app.modules.pedidos.domain.standby_motivo import FURO_GRADE, SEM_ESTOQUE
from app.modules.pedidos.service import processar_pedidos
from app.tests.factories_pedidos_motor import (
    estoque_por_canal,
    estoque_produto,
    item_pedido,
)


def _item(sg_tamanho, qt_liquida, **extra):
    base = {
        "sg_tamanho": sg_tamanho,
        "qt_liquida": qt_liquida,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# testes unitários de tem_furo_de_grade
# ---------------------------------------------------------------------------


def test_tem_furo_de_grade_furo_no_meio_bloqueia_par():
    itens = [_item("PP", 1), _item("M", 1), _item("G", 1)]
    estoque_local = {"PROD1_PP": 5, "PROD1_M": 0, "PROD1_G": 5}

    resultado = tem_furo_de_grade(itens, estoque_local, "PROD1")

    assert resultado == (True, "Furo de grade: tamanho M sem estoque reservável")


def test_tem_furo_de_grade_tamanho_nao_pedido_nunca_conta_como_interior():
    itens = [_item("PP", 1), _item("GG", 1)]
    estoque_local = {"PROD1_PP": 5, "PROD1_GG": 5, "PROD1_M": 0}

    resultado = tem_furo_de_grade(itens, estoque_local, "PROD1")

    assert resultado == (False, None)


def test_tem_furo_de_grade_tamanho_unico_nunca_e_furo():
    itens = [_item("M", 1)]
    estoque_local = {"PROD1_M": 0}

    resultado = tem_furo_de_grade(itens, estoque_local, "PROD1")

    assert resultado == (False, None)


def test_tem_furo_de_grade_dois_tamanhos_adjacentes_sempre_integra():
    itens = [_item("PP", 1), _item("P", 1)]
    estoque_local = {"PROD1_PP": 0, "PROD1_P": 0}

    resultado = tem_furo_de_grade(itens, estoque_local, "PROD1")

    assert resultado == (False, None)


def test_tem_furo_de_grade_tamanho_desconhecido_nunca_e_interior():
    itens = [_item("PP", 1), _item("XPTO", 1), _item("G", 1)]
    estoque_local = {"PROD1_PP": 5, "PROD1_XPTO": 0, "PROD1_G": 5}

    resultado = tem_furo_de_grade(itens, estoque_local, "PROD1")

    assert resultado == (False, None)


def test_tem_furo_de_grade_tamanho_desconhecido_nao_define_extremo():
    itens = [_item("PP", 1), _item("M", 1), _item("XPTO", 1)]
    estoque_local = {"PROD1_PP": 5, "PROD1_M": 0, "PROD1_XPTO": 5}

    resultado = tem_furo_de_grade(itens, estoque_local, "PROD1")

    assert resultado == (False, None)


def test_tem_furo_de_grade_qt_liquida_zero_nao_conta_como_pedido():
    itens = [_item("PP", 1), _item("M", 0), _item("G", 1)]
    estoque_local = {"PROD1_PP": 5, "PROD1_M": 0, "PROD1_G": 5}

    resultado = tem_furo_de_grade(itens, estoque_local, "PROD1")

    assert resultado == (False, None)


def test_tem_furo_de_grade_reporta_primeiro_furo_em_ordem_crescente_de_idx():
    itens = [_item("PP", 1), _item("M", 1), _item("G", 1), _item("GG", 1)]
    estoque_local = {"PROD1_PP": 5, "PROD1_M": 0, "PROD1_G": 0, "PROD1_GG": 5}

    resultado = tem_furo_de_grade(itens, estoque_local, "PROD1")

    assert resultado == (True, "Furo de grade: tamanho M sem estoque reservável")


# ---------------------------------------------------------------------------
# testes de integração — processar_pedidos
# ---------------------------------------------------------------------------


def test_processar_pedidos_furo_de_grade_isolado_bloqueia_par_unico():
    cd_prod_cor = "PROD1"
    originais = {"PP": 5, "M": 5, "G": 5}
    itens = [
        item_pedido(
            tamanho, qtd, 500.0, nr_pedido=1, cd_prod_cor=cd_prod_cor, canal="Franquia"
        )
        for tamanho, qtd in originais.items()
    ]
    estoque = estoque_por_canal(
        Franquia=estoque_produto(cd_prod_cor, {"PP": 10, "M": 0, "G": 10})
    )

    resultado = processar_pedidos(itens, estoque, ja_processados=set())

    assert resultado["selecionados"] == []
    assert resultado["preteridos"] == [(1, cd_prod_cor)]
    assert resultado["pares_processados"] == set()

    itens_par = resultado["resultados"][(1, cd_prod_cor)]
    assert len(itens_par) == 3
    for item in itens_par:
        assert item["status_item"] == "Pedido em Stand By"
        assert "Furo de grade" in item["motivo_stand_by"]
        assert "qt_solicitada" not in item
        assert item["qt_liquida"] == originais[item["sg_tamanho"]]


def test_processar_pedidos_furo_sensivel_a_ordem_prova_checagem_dentro_do_laco():
    cd_prod_cor = "PROD1"
    estoque = estoque_por_canal(
        Franquia=estoque_produto(cd_prod_cor, {"M": 5, "PP": 10, "G": 10})
    )

    # Cliente A (nr=1): maior valor -> maior prioridade. Pede só M, na
    # quantidade exata do estoque inicial, esgotando-o dentro do laço.
    itens_a = [
        item_pedido(
            "M", 5, 1000.0, nr_pedido=1, cd_prod_cor=cd_prod_cor, canal="Franquia"
        )
    ]
    # Cliente B (nr=2): menor valor -> menor prioridade. Pede PP/M/G do mesmo
    # produto; se a checagem de furo lesse o estoque ANTES do laço (foto
    # inicial, M=5), B não seria furo. Só é furo porque A já esgotou o M.
    itens_b = [
        item_pedido(
            "PP", 3, 100.0, nr_pedido=2, cd_prod_cor=cd_prod_cor, canal="Franquia"
        ),
        item_pedido(
            "M", 2, 100.0, nr_pedido=2, cd_prod_cor=cd_prod_cor, canal="Franquia"
        ),
        item_pedido(
            "G", 3, 100.0, nr_pedido=2, cd_prod_cor=cd_prod_cor, canal="Franquia"
        ),
    ]

    resultado = processar_pedidos(itens_a + itens_b, estoque, ja_processados=set())

    assert resultado["selecionados"] == [(1, cd_prod_cor)]
    assert resultado["preteridos"] == [(2, cd_prod_cor)]
    assert resultado["pares_processados"] == {(1, cd_prod_cor)}

    itens_par_b = resultado["resultados"][(2, cd_prod_cor)]
    for item in itens_par_b:
        assert "Furo de grade" in item["motivo_stand_by"]
        assert "M" in item["motivo_stand_by"]
        assert "qt_solicitada" not in item

    item_m_b = next(item for item in itens_par_b if item["sg_tamanho"] == "M")
    assert item_m_b["qt_liquida"] == 2  # preservado, não decrementado


def test_processar_pedidos_preteridos_motivo_distingue_furo_de_sem_estoque_modo_adequar():
    """K1 (16-VALIDATION.md): no mesmo teste, um par furo de grade e um par sem
    estoque genérico (aqui, orçamento de corte excedido) gravam motivos
    DIFERENTES em `preteridos_motivo`, em modo ADEQUAR (default)."""
    itens = [
        item_pedido(
            tamanho, qtd, 100.0, nr_pedido=1, cd_prod_cor="FURO", canal="Franquia"
        )
        for tamanho, qtd in {"PP": 1, "M": 1, "G": 1}.items()
    ] + [
        item_pedido(
            "M", 100, 1000.0, nr_pedido=1, cd_prod_cor="CORTE", canal="Franquia"
        ),
    ]
    estoque = estoque_por_canal(
        Franquia={
            **estoque_produto("FURO", {"PP": 5, "M": 0, "G": 5}),
            **estoque_produto("CORTE", {"M": 0}),
        }
    )

    resultado = processar_pedidos(itens, estoque, ja_processados=set())

    assert resultado["preteridos"] == [(1, "CORTE"), (1, "FURO")]
    assert resultado["preteridos_motivo"][(1, "FURO")] == FURO_GRADE
    assert resultado["preteridos_motivo"][(1, "CORTE")] == SEM_ESTOQUE
    assert (
        resultado["preteridos_motivo"][(1, "FURO")]
        != resultado["preteridos_motivo"][(1, "CORTE")]
    )
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"]) == 2


def test_processar_pedidos_preteridos_motivo_distingue_furo_de_sem_estoque_modo_sem_adequar():
    """Mesma distinção do teste acima, agora em modo SEM_ADEQUAR explícito:
    furo de grade e falta de estoque simples (tamanho único, nunca é furo)
    continuam gravando motivos DIFERENTES."""
    itens = [
        item_pedido(
            tamanho, qtd, 100.0, nr_pedido=1, cd_prod_cor="FURO", canal="Franquia"
        )
        for tamanho, qtd in {"PP": 1, "M": 1, "G": 1}.items()
    ] + [
        item_pedido(
            "M", 5, 500.0, nr_pedido=1, cd_prod_cor="SEMESTOQUE", canal="Franquia"
        ),
    ]
    estoque = estoque_por_canal(
        Franquia={
            **estoque_produto("FURO", {"PP": 5, "M": 0, "G": 5}),
            **estoque_produto("SEMESTOQUE", {"M": 0}),
        }
    )

    resultado = processar_pedidos(
        itens, estoque, ja_processados=set(), modo=ModoAdequacao.SEM_ADEQUAR
    )

    assert resultado["preteridos"] == [(1, "FURO"), (1, "SEMESTOQUE")]
    assert resultado["preteridos_motivo"][(1, "FURO")] == FURO_GRADE
    assert resultado["preteridos_motivo"][(1, "SEMESTOQUE")] == SEM_ESTOQUE
    assert (
        resultado["preteridos_motivo"][(1, "FURO")]
        != resultado["preteridos_motivo"][(1, "SEMESTOQUE")]
    )
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"]) == 2
