"""Composition adapter that maps durable job kinds to Celery tasks.

PostgreSQL is the authority for job state. Publishing is therefore a short,
best-effort hint: callers commit first and the periodic reconciler republishes
rows that remain dispatchable.
"""

from __future__ import annotations

import asyncio
from typing import Final
from uuid import UUID

from app.workers.celery_app import celery_app

_TASK_BY_KIND: Final = {
    "ingestion.full_sync.v1": "app.workers.tasks.ingestao.sincronizar_databricks",
    "orders.processing.v1": "app.workers.tasks.pedidos.processar_pedidos",
}
_BROKER_CONNECT_TIMEOUT_SECONDS: Final = 1.0
_PUBLISH_DEADLINE_SECONDS: Final = 2.0
_TRANSPORT_OPTIONS: Final = {
    "socket_connect_timeout": _BROKER_CONNECT_TIMEOUT_SECONDS,
    "socket_timeout": _BROKER_CONNECT_TIMEOUT_SECONDS,
    "retry_on_timeout": False,
}


def _publish_sync(*, task_name: str, job_id: UUID) -> None:
    """Publish once with broker retries disabled and no result-backend write."""

    with celery_app.connection_for_write(
        connect_timeout=_BROKER_CONNECT_TIMEOUT_SECONDS,
        transport_options=_TRANSPORT_OPTIONS,
    ) as connection:
        celery_app.send_task(
            task_name,
            args=[str(job_id)],
            connection=connection,
            ignore_result=True,
            retry=False,
        )


class CeleryDurableJobDispatcher:
    """Bounded implementation of the shared durable-job dispatcher port."""

    async def enqueue(self, *, job_id: UUID, kind: str) -> None:
        task_name = _TASK_BY_KIND.get(kind)
        if task_name is None:
            raise ValueError("job_kind_sem_dispatcher")
        async with asyncio.timeout(_PUBLISH_DEADLINE_SECONDS):
            await asyncio.to_thread(
                _publish_sync,
                task_name=task_name,
                job_id=job_id,
            )


__all__ = ["CeleryDurableJobDispatcher"]
