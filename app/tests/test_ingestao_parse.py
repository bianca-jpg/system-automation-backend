"""Unit tests do parsing da ingestão — funções puras de tradução do vocabulário
Databricks (strings/None) para os tipos do domínio. Sem I/O (não dependem de
Postgres/Databricks)."""

import pytest

from app.modules.ingestao.domain.traducao_databricks import (
    _normalizar_canal,
    _parse_bool,
    _parse_float,
    _parse_int,
    _parse_posicao,
    _texto,
)


@pytest.mark.parametrize(
    "valor,esperado",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("True", True),
        ("t", True),
        ("1", True),
        ("sim", True),
        ("false", False),
        ("f", False),
        ("0", False),
        ("", False),
        (None, False),  # NULL do Databricks = não processado
        ("nao", False),
    ],
)
def test_parse_bool(valor, esperado):
    assert _parse_bool(valor) is esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        (True, 0),  # bool tratado como inválido, não como 0/1
        (False, 0),
        (5, 5),
        (5.7, 5),  # int() trunca
        ("10", 10),
        ("10,5", 10),  # vírgula decimal -> ponto, depois trunca
        ("", 0),
        ("   ", 0),
        (None, 0),
    ],
)
def test_parse_int(valor, esperado):
    assert _parse_int(valor) == esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        (5, 5.0),
        (5.5, 5.5),
        ("10.50", 10.5),
        ("1.234,56", 1234.56),  # milhar com ponto + decimal com vírgula
        ("R$ 10,00", 10.0),  # prefixo de moeda + espaço
        ("", 0.0),
        ("   ", 0.0),
        (None, 0.0),
        ("abc", 0.0),  # inparseável -> default seguro
    ],
)
def test_parse_float(valor, esperado):
    assert _parse_float(valor) == esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        (5, 5),  # dentro da faixa
        (1, 1),  # limite inferior inclusivo
        (48, 48),  # limite superior inclusivo
        (0, None),  # abaixo do minimo
        (49, None),  # acima do maximo
        ("abc", None),  # nao numerico -> _parse_int devolve 0, fora da faixa
        (None, None),
        ("5,0", 5),  # tolerancia a virgula decimal (via _parse_int), trunca
    ],
)
def test_parse_posicao(valor, esperado):
    assert _parse_posicao(valor) == esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("FRANQUIA", "Franquia"),
        ("FRQ", "Franquia"),
        ("frq", "Franquia"),
        ("MULTIMARCA", "Multimarca"),
        ("MM", "Multimarca"),
        ("outro_valor", None),
        ("", None),
        (None, None),
    ],
)
def test_normalizar_canal(valor, esperado):
    assert _normalizar_canal(valor) == esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("  hello  ", "hello"),
        ("", None),
        ("   ", None),
        (None, None),
        (123, "123"),
    ],
)
def test_texto(valor, esperado):
    assert _texto(valor) == esperado
