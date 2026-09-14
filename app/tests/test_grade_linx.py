"""Testes puros (sem banco/HTTP) de converter_grade_para_posicoes."""

from app.modules.pedidos.service import converter_grade_para_posicoes

# capturado via psql no Plano 02-04, sync real de 2026-08-05
_CD_PROD_COR_REAL = "AC.02.0002|163"
# capturado via psql no Plano 02-04, sync real de 2026-08-05
_REFERENCIA_REAL = {"17": 1, "19": 2, "21": 3, "23": 4, "25": 5, "27": 6}


def test_converter_grade_para_posicoes_preenche_e1_a_e48_com_zero_por_padrao():
    grade = {}
    referencia = {"P": 3}

    posicoes, ignorados = converter_grade_para_posicoes(
        grade, referencia, cd_prod_cor="PROD1|001"
    )

    assert len(posicoes) == 48
    assert all(posicoes[f"e{n}"] == 0 for n in range(1, 49))
    assert ignorados == []


def test_converter_grade_para_posicoes_mapeia_quantidade_para_posicao_certa():
    grade = {"P": 2, "M": 10}
    referencia = {"P": 3, "M": 5}

    posicoes, ignorados = converter_grade_para_posicoes(
        grade, referencia, cd_prod_cor="PROD1|001"
    )

    assert posicoes["e3"] == 2
    assert posicoes["e5"] == 10
    assert ignorados == []


def test_converter_grade_para_posicoes_tamanho_sem_posicao_nao_lanca_excecao():
    """Critério 5 do ROADMAP: tamanho sem posição não lança exceção, conversão
    devolve o restante convertido, com aviso agregado por produto."""
    grade = {"M": 10, "XG": 3}
    referencia = {"M": 5}

    posicoes, ignorados = converter_grade_para_posicoes(
        grade, referencia, cd_prod_cor="PROD1|001"
    )

    assert posicoes["e5"] == 10
    assert ignorados == ["XG"]


def test_converter_grade_para_posicoes_conflito_de_posicao_ultimo_processado_vence():
    """D-03: dois tamanhos mapeando pra mesma posição — o último processado vence.
    Depende da ordem de inserção do dict `grade` (Python 3.7+ preserva ordem):
    "M" é processado primeiro (posicoes["e5"] = 10), depois "GG" sobrescreve
    a mesma posição e5 (posicoes["e5"] = 3), pois a referência de teste
    mapeia deliberadamente M e GG para a posição 5."""
    referencia = {"M": 5, "GG": 5}
    grade = {"M": 10, "GG": 3}

    posicoes, ignorados = converter_grade_para_posicoes(
        grade, referencia, cd_prod_cor="PROD1|001"
    )

    assert posicoes["e5"] == 3
    assert ignorados == []


def test_converter_grade_para_posicoes_com_dados_reais_da_referencia():
    """Critério 4 do ROADMAP: função pura testada sem acesso a banco, usando
    dados reais já ingeridos nesta fase (amostra capturada via psql no
    checkpoint do Plano 02-04 — ver 02-04-SUMMARY.md)."""
    grade = {"17": 2, "23": 10}

    posicoes, ignorados = converter_grade_para_posicoes(
        grade, _REFERENCIA_REAL, _CD_PROD_COR_REAL
    )

    for tamanho in grade:
        assert posicoes[f"e{_REFERENCIA_REAL[tamanho]}"] == grade[tamanho]
    assert ignorados == []
