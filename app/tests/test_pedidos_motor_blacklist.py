"""Testes da exceção de blacklist no motor (quick task 260825-jhv).

Cliente com `indica_blacklist=True` nunca pode ter a grade alterada pelo
motor, mesmo em `modo=ADEQUAR` — o pedido dele é sempre tratado tudo-ou-nada
por produto (idêntico ao `modo=SEM_ADEQUAR`), com motivo canônico
`BLACKLIST` (nunca `FURO_GRADE`/`SEM_ESTOQUE`) quando faltar estoque de algum
tamanho pedido. Mesmo padrão de `test_pedidos_motor_furo_grade.py`."""

from app.modules.pedidos.domain.politica_quantidade import ModoAdequacao
from app.modules.pedidos.domain.standby_motivo import BLACKLIST, FURO_GRADE
from app.modules.pedidos.service import processar_pedidos
from app.tests.factories_pedidos_motor import (
    estoque_por_canal,
    estoque_produto,
    item_pedido,
)

# ---------------------------------------------------------------------------
# (a) estoque completo em todos os tamanhos pedidos -> OR EXATA, sem extras
# ---------------------------------------------------------------------------


def test_blacklist_estoque_completo_gera_or_exatamente_igual_ao_pedido():
    cd_prod_cor = "PROD1"
    originais = {"PP": 3, "M": 3, "G": 3}
    itens = [
        item_pedido(
            tamanho,
            qtd,
            300.0,
            nr_pedido=1,
            cd_prod_cor=cd_prod_cor,
            canal="Franquia",
            indica_blacklist=True,
        )
        for tamanho, qtd in originais.items()
    ]
    # Sobra de estoque generosa nos extremos: se a exceção não valesse, a
    # passada 2 (extras) do modo ADEQUAR concederia peças extras em PP/G.
    estoque = estoque_por_canal(
        Franquia=estoque_produto(cd_prod_cor, {"PP": 10, "M": 10, "G": 10})
    )

    resultado = processar_pedidos(
        itens, estoque, ja_processados=set(), modo=ModoAdequacao.ADEQUAR
    )

    assert resultado["selecionados"] == [(1, cd_prod_cor)]
    assert resultado["preteridos"] == []
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"]) == 0

    itens_par = resultado["resultados"][(1, cd_prod_cor)]
    assert len(itens_par) == 3
    for item in itens_par:
        assert item["status_item"] == "Gerar OR"
        assert item["qt_liquida"] == originais[item["sg_tamanho"]]  # nenhum extra


# ---------------------------------------------------------------------------
# (b) furo de grade (tamanho INTERIOR sem estoque) -> BLACKLIST, nunca
#     FURO_GRADE, e nenhuma peça reservada (nem os tamanhos com estoque)
# ---------------------------------------------------------------------------


def test_blacklist_furo_de_grade_grava_motivo_blacklist_sem_reservar_nada():
    cd_prod_cor = "PROD1"
    originais = {"PP": 2, "M": 2, "G": 2}
    itens = [
        item_pedido(
            tamanho,
            qtd,
            200.0,
            nr_pedido=1,
            cd_prod_cor=cd_prod_cor,
            canal="Franquia",
            indica_blacklist=True,
        )
        for tamanho, qtd in originais.items()
    ]
    # M (interior) sem estoque -> furo de grade se não fosse blacklist.
    estoque = estoque_por_canal(
        Franquia=estoque_produto(cd_prod_cor, {"PP": 10, "M": 0, "G": 10})
    )

    resultado = processar_pedidos(
        itens, estoque, ja_processados=set(), modo=ModoAdequacao.ADEQUAR
    )

    assert resultado["selecionados"] == []
    assert resultado["preteridos"] == [(1, cd_prod_cor)]
    assert resultado["preteridos_motivo"][(1, cd_prod_cor)] == BLACKLIST
    assert resultado["preteridos_motivo"][(1, cd_prod_cor)] != FURO_GRADE
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"]) == 1

    itens_par = resultado["resultados"][(1, cd_prod_cor)]
    for item in itens_par:
        assert item["status_item"] == "Pedido em Stand By"
        # Nenhuma peça reservada, nem nos tamanhos que tinham estoque (PP/G).
        assert item["qt_liquida"] == originais[item["sg_tamanho"]]


# ---------------------------------------------------------------------------
# (c) exceção vence o parâmetro do request: ADEQUAR == SEM_ADEQUAR p/ blacklist
# ---------------------------------------------------------------------------


def test_blacklist_resultado_identico_em_adequar_e_sem_adequar():
    cd_prod_cor = "PROD1"
    originais = {"PP": 2, "M": 2, "G": 2}

    def _itens():
        return [
            item_pedido(
                tamanho,
                qtd,
                200.0,
                nr_pedido=1,
                cd_prod_cor=cd_prod_cor,
                canal="Franquia",
                indica_blacklist=True,
            )
            for tamanho, qtd in originais.items()
        ]

    estoque = estoque_por_canal(
        Franquia=estoque_produto(cd_prod_cor, {"PP": 10, "M": 0, "G": 10})
    )

    resultado_adequar = processar_pedidos(
        _itens(), estoque, ja_processados=set(), modo=ModoAdequacao.ADEQUAR
    )
    resultado_sem_adequar = processar_pedidos(
        _itens(), estoque, ja_processados=set(), modo=ModoAdequacao.SEM_ADEQUAR
    )

    assert resultado_adequar["selecionados"] == resultado_sem_adequar["selecionados"]
    assert resultado_adequar["preteridos"] == resultado_sem_adequar["preteridos"]
    assert (
        resultado_adequar["preteridos_motivo"]
        == resultado_sem_adequar["preteridos_motivo"]
    )
    resultados_adequar_sizes = {
        item["sg_tamanho"]: item["qt_liquida"]
        for item in resultado_adequar["resultados"][(1, cd_prod_cor)]
    }
    resultados_sem_adequar_sizes = {
        item["sg_tamanho"]: item["qt_liquida"]
        for item in resultado_sem_adequar["resultados"][(1, cd_prod_cor)]
    }
    assert resultados_adequar_sizes == resultados_sem_adequar_sizes


# ---------------------------------------------------------------------------
# (d) 2 produtos do MESMO pedido blacklist: tudo-ou-nada é POR PRODUTO
# ---------------------------------------------------------------------------


def test_blacklist_vale_para_o_pedido_mas_tudo_ou_nada_e_por_produto():
    itens = [
        item_pedido(
            tamanho,
            qtd,
            100.0,
            nr_pedido=1,
            cd_prod_cor="COMPLETO",
            canal="Franquia",
            indica_blacklist=True,
        )
        for tamanho, qtd in {"PP": 2, "M": 2, "G": 2}.items()
    ] + [
        item_pedido(
            tamanho,
            qtd,
            100.0,
            nr_pedido=1,
            cd_prod_cor="FURO",
            canal="Franquia",
            indica_blacklist=True,
        )
        for tamanho, qtd in {"PP": 2, "M": 2, "G": 2}.items()
    ]
    estoque = estoque_por_canal(
        Franquia={
            **estoque_produto("COMPLETO", {"PP": 10, "M": 10, "G": 10}),
            **estoque_produto("FURO", {"PP": 10, "M": 0, "G": 10}),
        }
    )

    resultado = processar_pedidos(
        itens, estoque, ja_processados=set(), modo=ModoAdequacao.ADEQUAR
    )

    assert resultado["selecionados"] == [(1, "COMPLETO")]
    assert resultado["preteridos"] == [(1, "FURO")]
    assert resultado["preteridos_motivo"][(1, "FURO")] == BLACKLIST
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"]) == 1


# ---------------------------------------------------------------------------
# (e) dois nr_pedido no mesmo lote: só o blacklist é desviado
# ---------------------------------------------------------------------------


def test_blacklist_so_desvia_o_pedido_blacklist_normal_segue_regra_padrao():
    itens_blacklist = [
        item_pedido(
            tamanho,
            qtd,
            200.0,
            nr_pedido=1,
            cd_prod_cor="BL",
            canal="Franquia",
            indica_blacklist=True,
        )
        for tamanho, qtd in {"PP": 2, "M": 2, "G": 2}.items()
    ]
    itens_normal = [
        item_pedido(
            tamanho,
            qtd,
            100.0,
            nr_pedido=2,
            cd_prod_cor="NORMAL",
            canal="Franquia",
            indica_blacklist=False,
        )
        for tamanho, qtd in {"PP": 1, "M": 1, "G": 1}.items()
    ]
    estoque = estoque_por_canal(
        Franquia={
            **estoque_produto("BL", {"PP": 10, "M": 0, "G": 10}),
            **estoque_produto("NORMAL", {"PP": 5, "M": 0, "G": 5}),
        }
    )

    resultado = processar_pedidos(
        itens_blacklist + itens_normal,
        estoque,
        ja_processados=set(),
        modo=ModoAdequacao.ADEQUAR,
    )

    assert resultado["preteridos"] == [(1, "BL"), (2, "NORMAL")]
    assert resultado["preteridos_motivo"][(1, "BL")] == BLACKLIST
    # Pedido normal continua rotulando furo de grade como FURO_GRADE, nunca
    # BLACKLIST -- só o pedido 1 (blacklist) foi desviado.
    assert resultado["preteridos_motivo"][(2, "NORMAL")] == FURO_GRADE
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"]) == 2


# ---------------------------------------------------------------------------
# Regressão: indica_blacklist=False (default/ausente) não muda nada
# ---------------------------------------------------------------------------


def test_regressao_cliente_nao_blacklist_continua_gravando_furo_grade():
    cd_prod_cor = "PROD1"
    itens = [
        item_pedido(
            tamanho,
            qtd,
            200.0,
            nr_pedido=1,
            cd_prod_cor=cd_prod_cor,
            canal="Franquia",
            indica_blacklist=False,
        )
        for tamanho, qtd in {"PP": 2, "M": 2, "G": 2}.items()
    ]
    estoque = estoque_por_canal(
        Franquia=estoque_produto(cd_prod_cor, {"PP": 10, "M": 0, "G": 10})
    )

    resultado = processar_pedidos(
        itens, estoque, ja_processados=set(), modo=ModoAdequacao.ADEQUAR
    )

    assert resultado["preteridos"] == [(1, cd_prod_cor)]
    assert resultado["preteridos_motivo"][(1, cd_prod_cor)] == FURO_GRADE
    assert resultado["preteridos_motivo"][(1, cd_prod_cor)] != BLACKLIST
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"]) == 1
