"""Fachada best-effort dos avisos de integração crítica (composition root).

Nunca levanta: a observabilidade não pode derrubar o caminho principal
(D-01/D-02 exigem o log, que é a trilha histórica sempre escrita). Os três
pontos de instrumentação (Task 3) importam só esta fachada — nunca o
domínio/aplicação/infra diretamente.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from redis.exceptions import RedisError

from app.modules.health.application.casos_uso import (
    para_linha_de_alerta,
    registrar_falha,
    registrar_sucesso,
)
from app.modules.health.domain.avisos import CATALOGO, AvisoIntegracao, FonteIntegracao
from app.modules.health.infrastructure.repositorio_avisos_redis import (
    RepositorioAvisosRedis,
)
from app.shared.infrastructure.redis_client import get_redis

logger = logging.getLogger("app.avisos.integracao")

__all__ = [
    "FonteIntegracao",
    "listar_linhas_de_alerta",
    "registrar_falha_integracao",
    "registrar_sucesso_integracao",
]

_EXCECOES_DEGRADACAO = (RedisError, OSError, TimeoutError, ValueError)


def _log_por_severidade(fonte: FonteIntegracao) -> Callable[..., None]:
    severidade = CATALOGO[fonte].severidade
    if severidade == "error":
        return logger.error
    if severidade == "warning":
        return logger.warning
    return logger.info


async def registrar_falha_integracao(
    fonte: FonteIntegracao, *, detalhe_tecnico: str = ""
) -> None:
    """Abre/atualiza o aviso da fonte e registra o log estruturado (D-01/D-02).

    `detalhe_tecnico` (nome da classe da exceção, `kind` do Databricks, etc.)
    vai só para o log do servidor, nunca para o aviso.
    """

    try:
        repo = RepositorioAvisosRedis(get_redis())
        aviso, era_novo = await registrar_falha(
            repo, fonte=fonte, agora=datetime.now(UTC)
        )
    except _EXCECOES_DEGRADACAO as exc:
        logger.warning(
            "Falha ao registrar aviso de integração (fonte=%s): %s",
            fonte.value,
            type(exc).__name__,
        )
        return

    copy = CATALOGO[fonte]
    log_fn = _log_por_severidade(fonte)
    mensagem = "Aviso de integração aberto" if era_novo else "Aviso de integração recorrente"
    log_fn(
        "%s fonte=%s severidade=%s ocorrencias=%d ocorrido_em=%s detalhe_tecnico=%s",
        mensagem,
        aviso.fonte.value,
        copy.severidade,
        aviso.ocorrencias,
        aviso.ultima_ocorrencia_em.isoformat(),
        detalhe_tecnico,
        extra={
            "aviso_fonte": aviso.fonte.value,
            "aviso_severidade": copy.severidade,
            "aviso_ocorrencias": aviso.ocorrencias,
            "aviso_ocorrido_em": aviso.ultima_ocorrencia_em.isoformat(),
        },
    )


async def registrar_sucesso_integracao(
    fonte: FonteIntegracao,
) -> AvisoIntegracao | None:
    """Resolve o aviso aberto da fonte, se houver (D-05).

    Devolve o aviso resolvido, ou `None` quando não havia nada aberto para
    resolver (fonte já saudável) OU quando o Redis está indisponível
    (degradação silenciosa). O chamador que precisa distinguir "resolveu de
    verdade" de "não havia nada" (D-07, live-update só quando resolve algo)
    usa esse retorno — nunca chama `listar_linhas_de_alerta()` para inferir.
    """

    try:
        repo = RepositorioAvisosRedis(get_redis())
        aviso = await registrar_sucesso(repo, fonte=fonte, agora=datetime.now(UTC))
    except _EXCECOES_DEGRADACAO as exc:
        logger.warning(
            "Falha ao resolver aviso de integração (fonte=%s): %s",
            fonte.value,
            type(exc).__name__,
        )
        return None

    if aviso is None:
        return None

    copy = CATALOGO[fonte]
    logger.info(
        "Aviso de integração resolvido fonte=%s severidade=%s ocorrencias=%d "
        "ocorrido_em=%s",
        aviso.fonte.value,
        copy.severidade,
        aviso.ocorrencias,
        aviso.ultima_ocorrencia_em.isoformat(),
        extra={
            "aviso_fonte": aviso.fonte.value,
            "aviso_severidade": copy.severidade,
            "aviso_ocorrencias": aviso.ocorrencias,
            "aviso_ocorrido_em": aviso.ultima_ocorrencia_em.isoformat(),
        },
    )
    return aviso


async def listar_linhas_de_alerta() -> list[dict[str, object]]:
    """Devolve as linhas de alerta dos avisos ABERTOS, no formato de `AlertaOut`."""

    try:
        repo = RepositorioAvisosRedis(get_redis())
        avisos = await repo.listar()
    except _EXCECOES_DEGRADACAO as exc:
        logger.warning(
            "Falha ao listar avisos de integração: %s",
            type(exc).__name__,
        )
        return []

    return [
        para_linha_de_alerta(aviso) for aviso in avisos if aviso.resolvido_em is None
    ]
