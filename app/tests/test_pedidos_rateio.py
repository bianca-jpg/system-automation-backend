"""Unit tests do utilitário de rateio (Hamilton), isolados de edicao_grade.py.
Todas as funções aqui são puras (sem I/O), então os testes não tocam o banco."""

from decimal import Decimal

from app.modules.pedidos.domain.rateio import ratear_hamilton


def test_ratear_hamilton_soma_bate_com_total_mesmo_sem_divisao_exata():
    resultado = ratear_hamilton(Decimal("100"), {"P": 1, "M": 1, "G": 1})

    assert sum(resultado.values()) == Decimal("100.00")
    assert resultado["G"] == resultado["M"] + Decimal("0.01")
    assert resultado["M"] == resultado["P"]


def test_ratear_hamilton_total_negativo_propaga_sinal():
    resultado = ratear_hamilton(Decimal("-100"), {"P": 1, "M": 1, "G": 1})

    assert all(valor < 0 for valor in resultado.values())
    assert sum(resultado.values()) == Decimal("-100.00")
    assert resultado["G"] == resultado["M"] - Decimal("0.01")
    assert resultado["M"] == resultado["P"]


def test_ratear_hamilton_tamanho_com_quantidade_zero_nao_quebra():
    resultado = ratear_hamilton(Decimal("100"), {"P": 0, "M": 1, "G": 1})

    assert resultado["P"] == Decimal("0.00")
    assert resultado["M"] == Decimal("50.00")
    assert resultado["G"] == Decimal("50.00")
    assert sum(resultado.values()) == Decimal("100.00")


def test_ratear_hamilton_sem_remainder_bate_exato():
    resultado = ratear_hamilton(Decimal("100"), {"36": 1, "37": 4})

    assert resultado == {"36": Decimal("20.00"), "37": Decimal("80.00")}
