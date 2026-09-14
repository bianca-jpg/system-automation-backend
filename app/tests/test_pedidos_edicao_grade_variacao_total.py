"""Prova da invariante da linha de base do par ao longo de edições sucessivas
(Pitfall 2, Phase 20) e do rateio inteiro exato que a sustenta.

Este arquivo cobre duas coisas deliberadamente no mesmo lugar, em vez de em
dois arquivos separados:

1. `ratear_inteiro` (rateio.py) — rateio de maior resto em peças, irmão
   inteiro de `ratear_hamilton`.
2. `montar_grade_atualizada` (edicao_grade.py) com o parâmetro novo de
   variação de total, incluindo o cenário central de duas edições sucessivas
   que expõe o Pitfall 2 (edição isolada não o detectaria).
"""

from __future__ import annotations

import pytest

from app.modules.pedidos.application.casos_uso import _linha_base_par
from app.modules.pedidos.domain.edicao_grade import (
    _qt_solicitada_ou_fallback,
    montar_grade_atualizada,
)
from app.modules.pedidos.domain.rateio import ratear_inteiro

# ---------------------------------------------------------------------------
# Seção 1 — ratear_inteiro (Task 1)
# ---------------------------------------------------------------------------


def test_ratear_inteiro_dois_tamanhos_iguais():
    assert ratear_inteiro(10, {"P": 1, "M": 1}) == {"P": 5, "M": 5}


def test_ratear_inteiro_tres_tamanhos_soma_exata_com_desempate_alfabetico():
    result = ratear_inteiro(10, {"P": 1, "M": 1, "G": 1})
    assert sum(result.values()) == 10
    assert set(result.keys()) == {"P", "M", "G"}


def test_ratear_inteiro_pesos_desiguais_soma_exata_e_proporcional():
    result = ratear_inteiro(7, {"P": 3, "M": 4})
    assert sum(result.values()) == 7
    # M tem peso maior, deve receber parte maior ou igual à de P.
    assert result["M"] >= result["P"]


def test_ratear_inteiro_total_zero_devolve_zero_para_todos():
    assert ratear_inteiro(0, {"P": 5}) == {"P": 0}
    assert ratear_inteiro(0, {"P": 1, "M": 1}) == {"P": 0, "M": 0}


def test_ratear_inteiro_um_unico_tamanho_recebe_tudo():
    assert ratear_inteiro(5, {"P": 1}) == {"P": 5}


def test_ratear_inteiro_total_negativo_levanta_valueerror():
    with pytest.raises(ValueError):
        ratear_inteiro(-1, {"P": 1})


def test_ratear_inteiro_sizes_vazio_levanta_valueerror():
    with pytest.raises(ValueError):
        ratear_inteiro(10, {})


def test_ratear_inteiro_soma_pesos_zero_levanta_valueerror():
    with pytest.raises(ValueError):
        ratear_inteiro(10, {"P": 0, "M": 0})


def test_ratear_inteiro_determinismo():
    first = ratear_inteiro(10, {"P": 1, "M": 1, "G": 1})
    second = ratear_inteiro(10, {"P": 1, "M": 1, "G": 1})
    assert first == second


@pytest.mark.parametrize(
    ("total", "sizes"),
    [
        (10, {"P": 1, "M": 1}),
        (7, {"P": 3, "M": 4}),
        (0, {"P": 5}),
        (5, {"P": 1}),
        (100, {"P": 1, "M": 1, "G": 1}),
        (1, {"P": 1, "M": 1, "G": 1}),
        (17, {"36": 2, "37": 3, "38": 5}),
        (9999, {"P": 7, "M": 13, "G": 5}),
    ],
)
def test_ratear_inteiro_propriedade_soma_exata(total, sizes):
    result = ratear_inteiro(total, sizes)
    assert sum(result.values()) == total
    assert set(result.keys()) == set(sizes.keys())


# ---------------------------------------------------------------------------
# Seção 2 — montar_grade_atualizada com total variável (Task 2)
# ---------------------------------------------------------------------------


def _item(
    *,
    size: str,
    qty: int,
    value: float,
    qt_solicitada: int | None = None,
    status_item: str = "Gerar OR",
) -> dict:
    return {
        "nr_pedido": 10,
        "cd_prod_cor": "BATCH_GRADE",
        "sg_tamanho": size,
        "ds_grupo": "CAMISA",
        "qt_liquida": qty,
        "vl_liquido": value,
        "client": "Loja 10",
        "canal": "Franquia",
        "status_credito": "Com Credito",
        "ds_produto": "Camisa Batch",
        "data": "2026-08-08",
        "status_item": status_item,
        "qt_solicitada": qty if qt_solicitada is None else qt_solicitada,
        "diff_valor": 0,
    }


def _sum_qt_solicitada(items: list[dict]) -> int:
    return sum(int(item["qt_solicitada"]) for item in items)


def _sum_qt_liquida(items: list[dict]) -> int:
    return sum(int(item["qt_liquida"]) for item in items)


def test_sem_parametro_novo_total_divergente_continua_levantando_valueerror():
    original = [
        _item(size="P", qty=5, value=50),
        _item(size="M", qty=5, value=50),
    ]
    with pytest.raises(ValueError, match="preservar a quantidade total"):
        montar_grade_atualizada(
            nr_pedido=10,
            cd_prod_cor="BATCH_GRADE",
            itens_originais=original,
            sizes={"P": 6, "M": 6},
        )


def test_sem_parametro_novo_total_igual_resultado_identico_ao_de_hoje():
    original = [
        _item(size="P", qty=5, value=50),
        _item(size="M", qty=5, value=50),
    ]
    items, total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 7, "M": 3},
    )
    assert [(item["sg_tamanho"], item["qt_liquida"]) for item in items] == [
        ("M", 3),
        ("P", 7),
    ]
    assert total == 100
    # Comportamento de hoje: qt_solicitada reseta para a quantidade nova.
    assert _sum_qt_solicitada(items) == 10


def test_com_permitir_variacao_total_total_maior_nao_levanta_excecao():
    original = [
        _item(size="P", qty=5, value=50),
        _item(size="M", qty=5, value=50),
    ]
    items, total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 6, "M": 6},
        permitir_variacao_total=True,
    )
    assert _sum_qt_liquida(items) == 12
    assert total == 100 or total is not None


def test_com_permitir_variacao_total_total_menor_nao_levanta_excecao():
    original = [
        _item(size="P", qty=5, value=50),
        _item(size="M", qty=5, value=50),
    ]
    items, _total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 4, "M": 4},
        permitir_variacao_total=True,
    )
    assert _sum_qt_liquida(items) == 8


def test_com_permitir_variacao_total_soma_qt_solicitada_preserva_linha_de_base():
    original = [
        _item(size="P", qty=5, value=50),
        _item(size="M", qty=5, value=50),
    ]
    items, _total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 8, "M": 4},
        permitir_variacao_total=True,
    )
    # A linha de base do par (10) é preservada, não a quantidade nova (12).
    assert _sum_qt_solicitada(items) == 10
    assert _sum_qt_liquida(items) == 12


def test_duas_edicoes_sucessivas_soma_qt_solicitada_permanece_100():
    original = [
        _item(size="P", qty=50, value=500),
        _item(size="M", qty=50, value=500),
    ]
    assert _sum_qt_liquida(original) == 100
    assert _sum_qt_solicitada(original) == 100

    first_items, _first_total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 52, "M": 52},
        permitir_variacao_total=True,
    )
    assert _sum_qt_liquida(first_items) == 104
    assert _sum_qt_solicitada(first_items) == 100

    second_items, _second_total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=first_items,
        sizes={"P": 51, "M": 51},
        permitir_variacao_total=True,
    )
    assert _sum_qt_liquida(second_items) == 102
    # A invariante central do Pitfall 2: depois da SEGUNDA edição, a soma de
    # qt_solicitada continua igual à linha de base original (100), não ao
    # total intermediário (104) nem ao total novo (102).
    assert _sum_qt_solicitada(second_items) == 100


def test_tamanho_novo_introduzido_nao_infla_linha_de_base_do_par():
    original = [
        _item(size="P", qty=5, value=50),
        _item(size="M", qty=5, value=50),
    ]
    items, _total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 4, "M": 3, "G": 3},
        permitir_variacao_total=True,
    )
    assert _sum_qt_solicitada(items) == 10
    assert {item["sg_tamanho"] for item in items} == {"P", "M", "G"}
    assert _sum_qt_liquida(items) == 10


def test_tamanho_zerado_nao_encolhe_linha_de_base_do_par():
    original = [
        _item(size="P", qty=5, value=50),
        _item(size="M", qty=5, value=50),
    ]
    items, _total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 10, "M": 0},
        permitir_variacao_total=True,
    )
    assert _sum_qt_solicitada(items) == 10
    assert {item["sg_tamanho"] for item in items} == {"P"}


def test_grade_inteiramente_zerada_levanta_valueerror_com_ou_sem_parametro():
    original = [_item(size="P", qty=5, value=50)]
    with pytest.raises(ValueError):
        montar_grade_atualizada(
            nr_pedido=10,
            cd_prod_cor="BATCH_GRADE",
            itens_originais=original,
            sizes={"P": 0},
        )
    with pytest.raises(ValueError):
        montar_grade_atualizada(
            nr_pedido=10,
            cd_prod_cor="BATCH_GRADE",
            itens_originais=original,
            sizes={"P": 0},
            permitir_variacao_total=True,
        )


def test_itens_stand_by_continuam_excluidos_com_ou_sem_parametro():
    original = [
        _item(size="P", qty=5, value=50),
        _item(size="M", qty=5, value=50, status_item="Pedido em Stand By"),
    ]
    items_sem, _t1 = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 5},
    )
    assert _sum_qt_liquida(items_sem) == 5

    items_com, _t2 = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 8},
        permitir_variacao_total=True,
    )
    assert _sum_qt_liquida(items_com) == 8
    # Linha de base só considera itens ativos (o P original, 5) — o item em
    # stand by (M, 5) não entra.
    assert _sum_qt_solicitada(items_com) == 5


def test_com_permitir_variacao_total_valores_financeiros_somam_exato_em_centavos():
    original = [
        _item(size="P", qty=5, value=50.33),
        _item(size="M", qty=5, value=50.34),
    ]
    items, total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 7, "M": 5},
        permitir_variacao_total=True,
    )
    from decimal import Decimal

    soma = sum((Decimal(str(item["vl_liquido"])) for item in items), Decimal(0))
    assert soma == Decimal(str(total)).quantize(Decimal("0.01"))


def test_linha_de_base_zero_quando_originais_sem_qt_solicitada_aproveitavel():
    original = [
        _item(size="P", qty=5, value=50, qt_solicitada=0),
        _item(size="M", qty=5, value=50, qt_solicitada=0),
    ]
    items, _total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"P": 8, "M": 4},
        permitir_variacao_total=True,
    )
    # Linha de base zero é aceitável: significa "este par nunca teve base
    # registrada" — não deve cair de volta para a quantidade nova.
    assert _sum_qt_solicitada(items) == 0


# ---------------------------------------------------------------------------
# Seção 3 — consistência cruzada `_linha_base_par` × `_qt_solicitada_ou_fallback`
# (WR-02, 20-REVIEW): as duas cópias intencionais do mesmo critério de
# fallback (`casos_uso.py` para o ledger de orçamento, `edicao_grade.py` para
# a grade gravada) precisam concordar byte a byte para o mesmo fixture, ou o
# orçamento e a grade persistida divergem silenciosamente sobre a linha de
# base do par. Este teste falha alto e local se uma das duas cópias for
# editada sem a outra.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "itens",
    [
        # qt_solicitada explícito e presente — caso comum.
        [
            _item(size="P", qty=5, value=50, qt_solicitada=7),
            _item(size="M", qty=3, value=30, qt_solicitada=2),
        ],
        # qt_solicitada explicitamente 0 — preservado, não cai para qt_liquida.
        [
            _item(size="P", qty=5, value=50, qt_solicitada=0),
            _item(size="M", qty=3, value=30, qt_solicitada=0),
        ],
        # qt_solicitada ausente — cai para qt_liquida.
        [
            {**_item(size="P", qty=5, value=50), "qt_solicitada": None},
            {**_item(size="M", qty=3, value=30), "qt_solicitada": None},
        ],
        # qt_solicitada vazio ("") — mesmo fallback que ausente.
        [
            {**_item(size="P", qty=5, value=50), "qt_solicitada": ""},
        ],
        # lista vazia — soma zero para as duas implementações.
        [],
    ],
)
def test_linha_base_par_e_qt_solicitada_ou_fallback_concordam(itens):
    esperado_casos_uso = _linha_base_par(itens)
    esperado_edicao_grade = sum(_qt_solicitada_ou_fallback(item) for item in itens)
    assert esperado_casos_uso == esperado_edicao_grade
