"""Domínio puro dos avisos de integração (D-01/D-02/D-04/D-05) — sem DB, sem Redis."""

from datetime import UTC, datetime, timedelta

from app.modules.health.application.casos_uso import para_linha_de_alerta
from app.modules.health.domain.avisos import (
    CATALOGO,
    TERMOS_TECNICOS_PROIBIDOS,
    AvisoIntegracao,
    FonteIntegracao,
    abrir_ou_atualizar,
    esta_aberto,
    resolver,
)

_T0 = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=5)
_T2 = _T0 + timedelta(minutes=10)


def test_b1_primeira_falha_abre_aviso_com_uma_ocorrencia():
    aviso = abrir_ou_atualizar(None, fonte=FonteIntegracao.BANCO_DE_DADOS, agora=_T0)

    assert aviso.ocorrencias == 1
    assert aviso.primeira_ocorrencia_em == _T0
    assert aviso.ultima_ocorrencia_em == _T0
    assert aviso.resolvido_em is None
    assert esta_aberto(aviso) is True


def test_b2_segunda_falha_da_mesma_fonte_nao_duplica():
    primeiro = abrir_ou_atualizar(None, fonte=FonteIntegracao.BANCO_DE_DADOS, agora=_T0)
    segundo = abrir_ou_atualizar(
        primeiro, fonte=FonteIntegracao.BANCO_DE_DADOS, agora=_T1
    )

    assert segundo.ocorrencias == 2
    assert segundo.primeira_ocorrencia_em == _T0
    assert segundo.ultima_ocorrencia_em == _T1
    assert segundo.resolvido_em is None


def test_b3_falha_apos_resolvido_reabre():
    primeiro = abrir_ou_atualizar(None, fonte=FonteIntegracao.ESTOQUE, agora=_T0)
    resolvido = resolver(primeiro, agora=_T1)
    reaberto = abrir_ou_atualizar(resolvido, fonte=FonteIntegracao.ESTOQUE, agora=_T2)

    assert reaberto.ocorrencias == 1
    assert reaberto.primeira_ocorrencia_em == _T2
    assert reaberto.ultima_ocorrencia_em == _T2
    assert reaberto.resolvido_em is None


def test_b4_resolver_preenche_resolvido_em_e_fecha_aviso():
    aberto = abrir_ou_atualizar(None, fonte=FonteIntegracao.BANCO_DE_DADOS, agora=_T0)
    resolvido = resolver(aberto, agora=_T1)

    assert resolvido.resolvido_em == _T1
    assert esta_aberto(resolvido) is False


def test_b5_para_linha_de_alerta_traduz_contrato_de_alerta():
    aviso_banco = AvisoIntegracao(
        fonte=FonteIntegracao.BANCO_DE_DADOS,
        primeira_ocorrencia_em=_T0,
        ultima_ocorrencia_em=_T1,
        ocorrencias=3,
    )
    linha_banco = para_linha_de_alerta(aviso_banco)

    assert linha_banco["id"] == "aviso-integracao-banco_de_dados"
    assert linha_banco["kind"] == "integracao"
    assert linha_banco["order_id"] is None
    assert linha_banco["affected_count"] == 3
    assert linha_banco["category"] == "integracao_banco_de_dados"
    assert linha_banco["type"] == "error"
    assert linha_banco["title"] == CATALOGO[FonteIntegracao.BANCO_DE_DADOS].titulo
    assert linha_banco["message"] == CATALOGO[FonteIntegracao.BANCO_DE_DADOS].mensagem

    aviso_estoque = AvisoIntegracao(
        fonte=FonteIntegracao.ESTOQUE,
        primeira_ocorrencia_em=_T0,
        ultima_ocorrencia_em=_T0,
        ocorrencias=1,
    )
    linha_estoque = para_linha_de_alerta(aviso_estoque)

    assert linha_estoque["type"] == "warning"
    assert linha_estoque["category"] == "integracao_estoque"


def test_b6_catalogo_nao_vaza_termo_tecnico():
    for copy in CATALOGO.values():
        texto = f"{copy.titulo} {copy.mensagem}".lower()
        for termo in TERMOS_TECNICOS_PROIBIDOS:
            assert termo not in texto
