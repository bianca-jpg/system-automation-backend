from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.shared.jobs.application.ports import (
    DurableJobRepository,
    DurableJobUnitOfWork,
)
from app.shared.jobs.application.service import reconcile_and_dispatch
from app.shared.jobs.domain import ReconcileResult
from app.workers import durable_job_dispatcher
from app.workers.durable_job_dispatcher import CeleryDurableJobDispatcher

_JOB_ID = UUID("11111111-1111-4111-8111-111111111111")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "task_name"),
    [
        (
            "ingestion.full_sync.v1",
            "app.workers.tasks.ingestao.sincronizar_databricks",
        ),
        (
            "orders.processing.v1",
            "app.workers.tasks.pedidos.processar_pedidos",
        ),
    ],
)
async def test_dispatcher_publica_registry_com_retry_desabilitado(
    kind: str,
    task_name: str,
) -> None:
    connection = object()
    with (
        patch.object(
            durable_job_dispatcher.celery_app,
            "connection_for_write",
            return_value=nullcontext(connection),
        ) as connection_for_write,
        patch.object(durable_job_dispatcher.celery_app, "send_task") as send_task,
    ):
        await CeleryDurableJobDispatcher().enqueue(job_id=_JOB_ID, kind=kind)

    connection_for_write.assert_called_once_with(
        connect_timeout=1.0,
        transport_options={
            "socket_connect_timeout": 1.0,
            "socket_timeout": 1.0,
            "retry_on_timeout": False,
        },
    )
    send_task.assert_called_once_with(
        task_name,
        args=[str(_JOB_ID)],
        connection=connection,
        ignore_result=True,
        retry=False,
    )


@pytest.mark.asyncio
async def test_dispatcher_rejeita_kind_desconhecido_sem_tocar_broker() -> None:
    with (
        patch.object(asyncio, "to_thread", new=AsyncMock()) as to_thread,
        pytest.raises(ValueError, match="job_kind_sem_dispatcher"),
    ):
        await CeleryDurableJobDispatcher().enqueue(
            job_id=_JOB_ID,
            kind="unknown.job.v1",
        )
    to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_aplica_deadline_total_ao_publish() -> None:
    publish_blocked = asyncio.Event()

    async def blocked_to_thread(*_args, **_kwargs):
        await publish_blocked.wait()

    with (
        patch.object(
            durable_job_dispatcher,
            "_PUBLISH_DEADLINE_SECONDS",
            0.01,
        ),
        patch.object(asyncio, "to_thread", side_effect=blocked_to_thread),
        pytest.raises(TimeoutError),
    ):
        await CeleryDurableJobDispatcher().enqueue(
            job_id=_JOB_ID,
            kind="orders.processing.v1",
        )


@pytest.mark.asyncio
async def test_reconciler_central_redespacha_job_de_pedidos_apos_broker_down() -> None:
    repository = SimpleNamespace(
        reconcile_expired=AsyncMock(return_value=ReconcileResult()),
        list_dispatchable=AsyncMock(
            return_value=(SimpleNamespace(id=_JOB_ID, kind="orders.processing.v1"),)
        ),
    )
    unit_of_work = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    with patch.object(durable_job_dispatcher, "_publish_sync") as publish:
        result = await reconcile_and_dispatch(
            cast(DurableJobRepository, repository),
            cast(DurableJobUnitOfWork, unit_of_work),
            CeleryDurableJobDispatcher(),
            now=datetime.now(UTC),
        )

    assert result == ReconcileResult()
    unit_of_work.commit.assert_awaited_once()
    unit_of_work.rollback.assert_not_awaited()
    publish.assert_called_once_with(
        task_name="app.workers.tasks.pedidos.processar_pedidos",
        job_id=_JOB_ID,
    )
