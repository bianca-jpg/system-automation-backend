from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.auth.infrastructure.models import AuthUser
from app.modules.comunicacoes.application import casos_uso
from app.modules.comunicacoes.application.entregas import (
    processar_entregas_pendentes,
)
from app.modules.comunicacoes.application.ports import EntregadorEmailPort
from app.modules.comunicacoes.domain.modelos import PoliticaEntrega
from app.modules.comunicacoes.infrastructure.adapters import (
    PublicadorComunicacoesRealtime,
    UnidadeTrabalhoSqlAlchemy,
)
from app.modules.comunicacoes.infrastructure.models import (
    Comunicacao,
    ComunicacaoEmailDelivery,
)
from app.modules.comunicacoes.infrastructure.repositorio_comunicacao import (
    RepositorioComunicacoesSqlAlchemy,
    RepositorioEntregasSqlAlchemy,
)
from app.modules.realtime.infrastructure.models import RealtimeOutbox
from app.shared.config.settings import get_settings

_ACTOR_ID = 999996


class _Sender:
    def __init__(self) -> None:
        self.calls = 0

    async def enviar(self, _entrega) -> None:
        self.calls += 1


class _Metrics:
    def reivindicada(self) -> None:
        pass

    def concluida(self, outcome: str, duration_seconds: float) -> None:
        assert outcome in {"sent", "retry", "failed", "unknown", "stale"}
        assert duration_seconds >= 0


def _policy() -> PoliticaEntrega:
    return PoliticaEntrega(
        max_attempts=3,
        lease_seconds=120,
        retry_base_seconds=10,
        retry_max_seconds=300,
        batch_size=5,
    )


@pytest.fixture
async def repository_database():
    database_url = get_settings().database_url
    assert database_url.rsplit("/", 1)[-1].endswith("_test")
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        if await session.get(AuthUser, _ACTOR_ID) is None:
            session.add(
                AuthUser(
                    id=_ACTOR_ID,
                    email="delivery-repository@project.com",
                    roles=["operacional"],
                    confirmed_at=datetime.now(UTC),
                )
            )
        await session.execute(delete(Comunicacao))
        await session.commit()
    try:
        yield factory
    finally:
        async with factory() as session:
            await session.execute(delete(Comunicacao))
            await session.commit()
        await engine.dispose()


async def _create_pending(factory, *, key: str | None = None) -> str:
    async with factory() as session:
        communication, created = await casos_uso.agendar_comunicacao(
            RepositorioComunicacoesSqlAlchemy(session),
            PublicadorComunicacoesRealtime(session),
            recipient="destino@project.com",
            content="conteudo reservado",
            order_ref="123",
            subject=None,
            actor_id=_ACTOR_ID,
            idempotency_key=key or f"repository-{uuid.uuid4()}",
            policy=_policy(),
        )
        assert created is True
        await session.commit()
        return communication.id


async def test_worker_aceito_atualiza_status_e_evento_na_mesma_transacao(
    repository_database,
):
    communication_id = await _create_pending(repository_database)
    sender = _Sender()
    async with repository_database() as session:
        result = await processar_entregas_pendentes(
            RepositorioEntregasSqlAlchemy(session),
            UnidadeTrabalhoSqlAlchemy(session),
            cast(EntregadorEmailPort, sender),
            PublicadorComunicacoesRealtime(session),
            _Metrics(),
            policy=_policy(),
            worker_id="worker-accepted",
        )
    assert result.sent == 1
    assert sender.calls == 1

    async with repository_database() as session:
        communication = await session.get(Comunicacao, communication_id)
        delivery = await session.scalar(
            select(ComunicacaoEmailDelivery).where(
                ComunicacaoEmailDelivery.communication_id == communication_id
            )
        )
        event_types = list(
            (
                await session.execute(
                    select(RealtimeOutbox.event_type)
                    .where(RealtimeOutbox.payload["id"].astext == communication_id)
                    .order_by(RealtimeOutbox.sequence)
                )
            ).scalars()
        )
    assert communication is not None and communication.status == "Enviado"
    assert delivery is not None and delivery.status == "sent"
    assert delivery.sent_at is not None and delivery.failed_at is None
    assert event_types == ["communication.created", "communication.sent"]


async def test_skip_locked_impede_claim_concorrente_da_mesma_entrega(
    repository_database,
):
    await _create_pending(repository_database)
    first = repository_database()
    second = repository_database()
    now = datetime.now(UTC)
    try:
        first_claim = await RepositorioEntregasSqlAlchemy(first).reivindicar_proxima(
            claimed_by="worker-one",
            now=now,
            lease_until=now + timedelta(seconds=120),
        )
        second_claim = await RepositorioEntregasSqlAlchemy(second).reivindicar_proxima(
            claimed_by="worker-two",
            now=now,
            lease_until=now + timedelta(seconds=120),
        )
        assert first_claim is not None
        assert second_claim is None
    finally:
        await first.rollback()
        await second.rollback()
        await first.close()
        await second.close()


async def test_idempotencia_concorrente_cria_um_unico_delivery_e_evento(
    repository_database,
):
    key = f"concurrent-{uuid.uuid4()}"
    first_ready = asyncio.Event()
    release_first = asyncio.Event()

    async def schedule_first():
        async with repository_database() as session:
            result = await casos_uso.agendar_comunicacao(
                RepositorioComunicacoesSqlAlchemy(session),
                PublicadorComunicacoesRealtime(session),
                recipient="destino@project.com",
                content="mesmo conteudo",
                order_ref=None,
                subject=None,
                actor_id=_ACTOR_ID,
                idempotency_key=key,
                policy=_policy(),
            )
            first_ready.set()
            await release_first.wait()
            await session.commit()
            return result

    async def schedule_second():
        await first_ready.wait()
        async with repository_database() as session:
            result = await casos_uso.agendar_comunicacao(
                RepositorioComunicacoesSqlAlchemy(session),
                PublicadorComunicacoesRealtime(session),
                recipient="destino@project.com",
                content="mesmo conteudo",
                order_ref=None,
                subject=None,
                actor_id=_ACTOR_ID,
                idempotency_key=key,
                policy=_policy(),
            )
            await session.commit()
            return result

    first_task = asyncio.create_task(schedule_first())
    await first_ready.wait()
    second_task = asyncio.create_task(schedule_second())
    await asyncio.sleep(0.05)
    assert not second_task.done()
    release_first.set()
    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert first_result[1] is True
    assert second_result[1] is False
    assert first_result[0].id == second_result[0].id
    async with repository_database() as session:
        deliveries = int(
            await session.scalar(
                select(func.count()).select_from(ComunicacaoEmailDelivery)
            )
            or 0
        )
        events = int(
            await session.scalar(
                select(func.count())
                .select_from(RealtimeOutbox)
                .where(
                    RealtimeOutbox.event_type == "communication.created",
                    RealtimeOutbox.payload["id"].astext == first_result[0].id,
                )
            )
            or 0
        )
    assert deliveries == 1
    assert events == 1


async def test_lease_expirado_vira_incerto_e_nao_e_reivindicado_novamente(
    repository_database,
):
    communication_id = await _create_pending(repository_database)
    now = datetime.now(UTC)
    async with repository_database() as session:
        delivery = await session.scalar(
            select(ComunicacaoEmailDelivery).where(
                ComunicacaoEmailDelivery.communication_id == communication_id
            )
        )
        assert delivery is not None
        delivery.status = "processing"
        delivery.attempt_count = 1
        delivery.claimed_by = "worker-crashed"
        delivery.claimed_until = now - timedelta(seconds=1)
        await session.commit()

    async with repository_database() as session:
        result = await processar_entregas_pendentes(
            RepositorioEntregasSqlAlchemy(session),
            UnidadeTrabalhoSqlAlchemy(session),
            cast(EntregadorEmailPort, _Sender()),
            PublicadorComunicacoesRealtime(session),
            _Metrics(),
            policy=_policy(),
            worker_id="worker-reconciler",
        )
    assert result.reconciled_unknown == 1
    assert result.claimed == 0

    async with repository_database() as session:
        communication = await session.get(Comunicacao, communication_id)
        delivery = await session.scalar(
            select(ComunicacaoEmailDelivery).where(
                ComunicacaoEmailDelivery.communication_id == communication_id
            )
        )
    assert communication is not None and communication.status == "Incerto"
    assert delivery is not None and delivery.status == "unknown"
    assert delivery.failed_at is not None
    assert delivery.claimed_by is None and delivery.claimed_until is None
