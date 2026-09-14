"""Lock advisory de sessão e heartbeat independente do trabalho pesado."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Self
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.modules.pedidos.processing.domain import (
    PROCESSING_LEASE,
    ProcessingLeaseLost,
    ProcessingLockUnavailable,
)
from app.shared.database.advisory_locks import ORDER_STATE_MUTATION_LOCK
from app.shared.jobs.domain import JobStatus, utc_now
from app.shared.jobs.infrastructure.repository import (
    SqlAlchemyDurableJobRepository,
    SqlAlchemyDurableJobUnitOfWork,
)

logger = logging.getLogger(__name__)
_HEARTBEAT_INTERVAL_SECONDS = 20.0
_HEARTBEAT_STOP_TIMEOUT_SECONDS = 2.0


def _consume_task_result(task: asyncio.Task[None]) -> None:
    """Consome exceção tardia sem gerar ruído para task cancelada."""

    if task.cancelled():
        return
    task.exception()


class PostgresOrderMutationSessionLock:
    """Entrega uma AsyncSession presa à conexão que detém o lock global."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._connection: AsyncConnection | None = None
        self._session: AsyncSession | None = None
        self._held = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None or not self._held:
            raise RuntimeError("processing_lock_session_not_open")
        return self._session

    async def __aenter__(self) -> Self:
        connection = await self._engine.connect()
        try:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": ORDER_STATE_MUTATION_LOCK},
            )
            # Encerra a virtual transaction aberta pelo SELECT. O lock é de
            # sessão e permanece na conexão através de todos os commits seguintes.
            await connection.commit()
            if not acquired:
                raise ProcessingLockUnavailable("order_state_mutation_lock_busy")
        except BaseException:
            await connection.close()
            raise
        self._connection = connection
        self._session = AsyncSession(bind=connection, expire_on_commit=False)
        self._held = True
        return self

    async def ensure_held(self) -> None:
        if (
            not self._held
            or self._connection is None
            or self._connection.closed
            or self._connection.invalidated
            or self._session is None
        ):
            raise ProcessingLockUnavailable("order_state_mutation_lock_not_held")

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        session = self._session
        connection = self._connection
        try:
            if session is not None:
                await session.rollback()
                await session.close()
            if (
                connection is not None
                and not connection.closed
                and not connection.invalidated
                and self._held
            ):
                await connection.scalar(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": ORDER_STATE_MUTATION_LOCK},
                )
                await connection.commit()
        finally:
            self._held = False
            self._session = None
            self._connection = None
            if connection is not None and not connection.closed:
                await connection.close()


class SqlAlchemyProcessingLeaseKeeper:
    """Renova lease numa sessão separada, inclusive durante motor CPU-bound."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        now=utc_now,
        interval_seconds: float = _HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._factory = session_factory
        self._now = now
        self._interval = interval_seconds
        self._job_id: UUID | None = None
        self._worker_id: str | None = None
        self._progress_current = 0
        self._progress_total: int | None = None
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._lost = False

    async def start(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        progress_current: int,
        progress_total: int | None,
    ) -> None:
        if self._task is not None:
            raise RuntimeError("processing_lease_keeper_already_started")
        self._job_id = job_id
        self._worker_id = worker_id
        self._progress_current = progress_current
        self._progress_total = progress_total
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(),
            name=f"orders-processing-heartbeat-{job_id}",
        )

    async def update_progress(
        self, *, progress_current: int, progress_total: int | None
    ) -> None:
        if progress_current < self._progress_current:
            raise ValueError("processing_progress_cannot_decrease")
        if progress_total is not None and progress_current > progress_total:
            raise ValueError("processing_progress_exceeds_total")
        self._progress_current = progress_current
        self._progress_total = progress_total

    async def ensure_valid(self) -> None:
        if self._lost:
            raise ProcessingLeaseLost("processing_job_lease_lost")
        if self._task is None:
            raise ProcessingLeaseLost("processing_lease_keeper_not_started")

    async def stop(self) -> None:
        task = self._task
        event = self._stop_event
        self._task = None
        self._stop_event = None
        if task is None:
            return
        if event is not None:
            event.set()
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=_HEARTBEAT_STOP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            task.cancel()
            done, _pending = await asyncio.wait(
                {task},
                timeout=_HEARTBEAT_STOP_TIMEOUT_SECONDS,
            )
            if done:
                await asyncio.gather(task, return_exceptions=True)
            else:
                # Não bloqueia shutdown atrás do command timeout do driver. A
                # coroutine continua cancelada e consome sua exceção ao terminar.
                task.add_done_callback(_consume_task_result)

    async def _heartbeat(self, now: datetime) -> bool:
        if self._job_id is None or self._worker_id is None:
            return False
        async with self._factory() as db:
            repository = SqlAlchemyDurableJobRepository(db)
            unit_of_work = SqlAlchemyDurableJobUnitOfWork(db)
            job = await repository.heartbeat(
                job_id=self._job_id,
                worker_id=self._worker_id,
                now=now,
                lease_until=now + PROCESSING_LEASE,
                progress_current=self._progress_current,
                progress_total=self._progress_total,
            )
            if job is None:
                current = await repository.get(self._job_id)
                retry_after_foreground = (
                    current is not None
                    and current.status is JobStatus.RUNNING
                    and current.lease_owner == self._worker_id
                )
                if retry_after_foreground:
                    assert current is not None
                    # O foreground pode ter commitado um checkpoint maior entre
                    # o snapshot do keeper e este CAS. Sincroniza para frente e
                    # tenta novamente: somente o segundo CAS, protegido pelo
                    # clock do PostgreSQL, pode confirmar lease/deadline válidos.
                    self._progress_current = max(
                        self._progress_current,
                        current.progress_current,
                    )
                    if current.progress_total is not None:
                        self._progress_total = current.progress_total
                await unit_of_work.rollback()
                if retry_after_foreground:
                    job = await repository.heartbeat(
                        job_id=self._job_id,
                        worker_id=self._worker_id,
                        now=now,
                        lease_until=now + PROCESSING_LEASE,
                        progress_current=self._progress_current,
                        progress_total=self._progress_total,
                    )
                if job is None:
                    await unit_of_work.rollback()
                    return False
            await unit_of_work.commit()
            return True

    async def _run(self) -> None:
        stop_event = self._stop_event
        assert stop_event is not None
        while True:
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._interval,
                )
                return
            except TimeoutError:
                pass
            try:
                if not await self._heartbeat(self._now()):
                    self._lost = True
                    return
            except Exception as exc:  # noqa: BLE001 - fail closed on lease ambiguity
                self._lost = True
                logger.warning(
                    "orders processing heartbeat failed",
                    extra={
                        "job_id": str(self._job_id),
                        "heartbeat_error_type": type(exc).__name__,
                    },
                )
                return


__all__ = [
    "PostgresOrderMutationSessionLock",
    "SqlAlchemyProcessingLeaseKeeper",
]
