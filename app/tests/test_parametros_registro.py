"""Testes puros do registro de chaves conhecidas pelo motor de adequação.

Sem `client`, sem banco: `app/modules/parametros/domain/registro.py` é
domínio puro. A guarda de drift lê o fonte de `load_adequation_config` para
provar que o registro cobre exatamente as chaves que o motor lê de fato.
"""

import re
from pathlib import Path

import pytest

from app.modules.parametros.domain.exceptions import ValorDeParametroInvalidoError
from app.modules.parametros.domain.registro import (
    PARAMETROS_CONHECIDOS,
    ParametroConhecido,
    e_consumido_pelo_motor,
    parametro_conhecido,
    validar_valor_de_parametro,
)
from app.modules.parametros.infrastructure.models import ParametroTipo

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_e_consumido_pelo_motor_tolerancia_adequacao():
    assert e_consumido_pelo_motor("tolerancia_adequacao") is True


def test_e_consumido_pelo_motor_criterio_selecao():
    assert e_consumido_pelo_motor("criterio_selecao") is True


def test_e_consumido_pelo_motor_chave_inventada_e_falso():
    assert e_consumido_pelo_motor("qualquer_chave_inventada") is False


def test_parametro_conhecido_tolerancia_adequacao_tipo_e_faixa():
    conhecido = parametro_conhecido("tolerancia_adequacao")
    assert conhecido is not None
    assert conhecido.tipo == ParametroTipo.FLOAT
    assert conhecido.minimo == 0.0
    assert conhecido.maximo == 1.0


def test_parametro_conhecido_criterio_selecao_valores_aceitos():
    conhecido = parametro_conhecido("criterio_selecao")
    assert conhecido is not None
    assert conhecido.valores_aceitos == frozenset({"valor", "quantidade"})


def test_parametro_conhecido_chave_desconhecida_e_none():
    assert parametro_conhecido("nao_existe") is None


def test_toda_entrada_do_registro_tem_aplicado_em_nao_vazio():
    for chave, conhecido in PARAMETROS_CONHECIDOS.items():
        assert isinstance(conhecido, ParametroConhecido)
        assert conhecido.aplicado_em, f"'{chave}' sem aplicado_em"


def test_registro_cobre_todas_as_chaves_lidas_pelo_motor():
    """Guarda de drift: lê o fonte de `load_adequation_config` e extrai as
    chaves literais passadas como segundo argumento posicional de
    `get_param_value`. Se o adapter passar a citar uma chave que não está
    em `PARAMETROS_CONHECIDOS` (ou vice-versa), este teste falha."""
    fonte_adapter = (
        _BACKEND_ROOT
        / "app"
        / "modules"
        / "pedidos"
        / "processing"
        / "infrastructure"
        / "adapters.py"
    ).read_text(encoding="utf-8")

    match = re.search(
        r"async def load_adequation_config\(.*?\n(?P<corpo>.*?)\n    async def ",
        fonte_adapter,
        re.DOTALL,
    )
    assert match is not None, (
        "load_adequation_config não encontrado (ou não é mais seguida por outro "
        "'async def' no mesmo nível) — ajuste o regex desta guarda de drift."
    )
    corpo = match.group("corpo")

    chaves_lidas_pelo_motor = set(
        re.findall(r'get_param_value\(\s*self\._db,\s*"([^"]+)"', corpo)
    )
    assert chaves_lidas_pelo_motor, (
        "Nenhuma chave extraída do corpo de load_adequation_config — o regex "
        "da guarda de drift silenciosamente parou de medir algo."
    )

    chaves_registradas = set(PARAMETROS_CONHECIDOS)

    apenas_no_motor = chaves_lidas_pelo_motor - chaves_registradas
    apenas_no_registro = chaves_registradas - chaves_lidas_pelo_motor
    assert not apenas_no_motor and not apenas_no_registro, (
        f"Drift entre o registro e load_adequation_config: "
        f"lidas pelo motor e não registradas={sorted(apenas_no_motor)}; "
        f"registradas e não lidas pelo motor={sorted(apenas_no_registro)}"
    )


# --- validar_valor_de_parametro -------------------------------------------


@pytest.mark.parametrize(
    ("chave", "valor", "tipo"),
    [
        ("tolerancia_adequacao", "0.10", "float"),
        ("tolerancia_adequacao", "0", "float"),
        ("tolerancia_adequacao", "1", "float"),
        ("criterio_selecao", "valor", "string"),
        ("criterio_selecao", "quantidade", "string"),
        ("chave_livre_qualquer", "o que for", "string"),
    ],
)
def test_validar_valor_de_parametro_aceita_valores_validos(chave, valor, tipo):
    validar_valor_de_parametro(chave, valor, tipo)  # não deve levantar


@pytest.mark.parametrize(
    ("chave", "valor", "tipo"),
    [
        ("tolerancia_adequacao", "10", "float"),
        ("tolerancia_adequacao", "-0.01", "float"),
        ("tolerancia_adequacao", "abacaxi", "float"),
        ("tolerancia_adequacao", "0.05", "string"),
        ("criterio_selecao", "preco", "string"),
    ],
)
def test_validar_valor_de_parametro_rejeita_valores_invalidos(chave, valor, tipo):
    with pytest.raises(ValorDeParametroInvalidoError):
        validar_valor_de_parametro(chave, valor, tipo)


def test_validar_valor_de_parametro_excecao_carrega_chave_e_motivo():
    with pytest.raises(ValorDeParametroInvalidoError) as exc_info:
        validar_valor_de_parametro("tolerancia_adequacao", "10", "float")
    assert exc_info.value.chave == "tolerancia_adequacao"
    assert exc_info.value.motivo


def test_validacao_nao_e_alcancavel_pela_leitura_resiliente():
    """Guarda contra ligar a validação de escrita na leitura resiliente: o
    motor precisa continuar caindo no default de settings.py diante de
    parâmetro ausente ou corrompido, sem passar por validação nenhuma."""
    fonte_leitura = (
        _BACKEND_ROOT
        / "app"
        / "modules"
        / "parametros"
        / "domain"
        / "leitura_resiliente.py"
    ).read_text(encoding="utf-8")
    assert "validar_valor_de_parametro" not in fonte_leitura
    assert "registro" not in fonte_leitura
