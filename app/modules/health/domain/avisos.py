"""Domínio puro dos avisos de integração crítica (D-01/D-02).

Sem SQLAlchemy, sem Redis, sem FastAPI e sem `datetime.now`: o instante entra
sempre por parâmetro, para que este módulo permaneça testável sem infra.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal


class FonteIntegracao(StrEnum):
    """As duas integrações críticas travadas para este task (D-01/D-02)."""

    BANCO_DE_DADOS = "banco_de_dados"
    ESTOQUE = "estoque"


@dataclass(frozen=True, slots=True)
class AvisoIntegracao:
    """Estado atual de um aviso: uma linha por fonte, nunca histórico."""

    fonte: FonteIntegracao
    primeira_ocorrencia_em: datetime
    ultima_ocorrencia_em: datetime
    ocorrencias: int
    resolvido_em: datetime | None = None


@dataclass(frozen=True, slots=True)
class CopyAviso:
    """Texto não-técnico (D-04) associado a uma fonte de integração."""

    categoria: str
    severidade: Literal["error", "warning", "info"]
    titulo: str
    mensagem: str


CATALOGO: Mapping[FonteIntegracao, CopyAviso] = {
    FonteIntegracao.BANCO_DE_DADOS: CopyAviso(
        categoria="integracao_banco_de_dados",
        severidade="error",
        titulo="Instabilidade no banco de dados",
        mensagem=(
            "A conexão com o banco de dados está instável no momento — algumas "
            "informações podem não carregar corretamente."
        ),
    ),
    FonteIntegracao.ESTOQUE: CopyAviso(
        categoria="integracao_estoque",
        severidade="warning",
        titulo="Estoque indisponível no momento",
        mensagem=(
            "Não estamos conseguindo consultar o estoque no momento — o sistema "
            "de estoque pode estar em manutenção."
        ),
    ),
}

# Único ponto de manutenção do gate de linguagem não-técnica (D-04). Código de
# produção de propósito: consumido pelos testes de domínio (Task 1) e de
# endpoint (Task 4), no mesmo espírito de `_STRINGS_PROIBIDAS_NO_CORPO` em
# `app/tests/test_health.py`. Minúsculas, para comparação case-insensitive.
TERMOS_TECNICOS_PROIBIDOS: tuple[str, ...] = (
    "databricks",
    "sqlalchemy",
    "postgres",
    "asyncpg",
    "redis",
    "traceback",
    "exception",
    "system_automation_estoque_filtrado",
    "select",
)


def esta_aberto(aviso: AvisoIntegracao | None) -> bool:
    """`True` só quando existe aviso e ele ainda não foi resolvido."""

    return aviso is not None and aviso.resolvido_em is None


def abrir_ou_atualizar(
    atual: AvisoIntegracao | None,
    *,
    fonte: FonteIntegracao,
    agora: datetime,
) -> AvisoIntegracao:
    """Aplica a regra de dedupe/reabertura de D-05.

    - Sem aviso atual, ou aviso já resolvido: abre um novo (ocorrencias=1).
    - Aviso aberto da mesma fonte: atualiza última ocorrência e incrementa o
      contador, preservando a primeira ocorrência.
    """

    if not esta_aberto(atual):
        return AvisoIntegracao(
            fonte=fonte,
            primeira_ocorrencia_em=agora,
            ultima_ocorrencia_em=agora,
            ocorrencias=1,
            resolvido_em=None,
        )

    assert atual is not None  # esta_aberto garante não-nulo aqui
    return replace(
        atual,
        ultima_ocorrencia_em=agora,
        ocorrencias=atual.ocorrencias + 1,
    )


def resolver(atual: AvisoIntegracao, *, agora: datetime) -> AvisoIntegracao:
    """Marca o aviso como resolvido (D-05)."""

    return replace(atual, resolvido_em=agora)
