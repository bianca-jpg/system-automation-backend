"""Composition root do bounded context de comunicações."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.comunicacoes.application import casos_uso
from app.modules.comunicacoes.application.entregas import (
    ResultadoProcessamento,
    processar_entregas_pendentes,
)
from app.modules.comunicacoes.domain.modelos import Comunicacao, PoliticaEntrega
from app.modules.comunicacoes.infrastructure.adapters import (
    PublicadorComunicacoesRealtime,
    UnidadeTrabalhoSqlAlchemy,
)
from app.modules.comunicacoes.infrastructure.email_sender import SmtpEmailSender
from app.modules.comunicacoes.infrastructure.metrics import (
    PrometheusEmailDeliveryMetrics,
)
from app.modules.comunicacoes.infrastructure.repositorio_comunicacao import (
    RepositorioComunicacoesSqlAlchemy,
    RepositorioEntregasSqlAlchemy,
)
from app.shared.config.settings import Settings, get_settings


def politica_entrega(settings: Settings | None = None) -> PoliticaEntrega:
    config = settings or get_settings()
    return PoliticaEntrega(
        max_attempts=config.communication_delivery_max_attempts,
        lease_seconds=config.communication_delivery_lease_seconds,
        retry_base_seconds=config.communication_delivery_retry_base_seconds,
        retry_max_seconds=config.communication_delivery_retry_max_seconds,
        batch_size=config.communication_delivery_batch_size,
    )


async def listar_comunicacoes(
    db: AsyncSession,
    *,
    page_size: int,
    cursor: tuple[datetime, str] | None,
) -> tuple[list[Comunicacao], int, bool]:
    return await casos_uso.listar_comunicacoes(
        RepositorioComunicacoesSqlAlchemy(db),
        page_size=page_size,
        cursor=cursor,
    )


async def idempotency_key_persistida(
    db: AsyncSession,
    actor_id: int,
    idempotency_key: str,
) -> bool:
    return await casos_uso.idempotency_key_persistida(
        RepositorioComunicacoesSqlAlchemy(db),
        actor_id,
        idempotency_key,
    )


async def obter_comunicacao(
    db: AsyncSession,
    communication_id: str,
) -> Comunicacao | None:
    return await casos_uso.obter_comunicacao(
        RepositorioComunicacoesSqlAlchemy(db),
        communication_id,
    )


async def agendar_comunicacao(
    db: AsyncSession,
    *,
    recipient: str,
    content: str,
    order_ref: str | None,
    subject: str | None,
    actor_id: int,
    idempotency_key: str,
) -> tuple[Comunicacao, bool]:
    return await casos_uso.agendar_comunicacao(
        RepositorioComunicacoesSqlAlchemy(db),
        PublicadorComunicacoesRealtime(db),
        recipient=recipient,
        content=content,
        order_ref=order_ref,
        subject=subject,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        policy=politica_entrega(),
    )


async def processar_entregas(db: AsyncSession) -> ResultadoProcessamento:
    settings = get_settings()
    return await processar_entregas_pendentes(
        RepositorioEntregasSqlAlchemy(db),
        UnidadeTrabalhoSqlAlchemy(db),
        SmtpEmailSender(settings),
        PublicadorComunicacoesRealtime(db),
        PrometheusEmailDeliveryMetrics(),
        policy=politica_entrega(settings),
    )


__all__ = [
    "agendar_comunicacao",
    "idempotency_key_persistida",
    "listar_comunicacoes",
    "obter_comunicacao",
    "politica_entrega",
    "processar_entregas",
]
