from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from typing import cast
from uuid import uuid4

import pytest

from app.modules.pedidos.processing.application import service as processing_service
from app.modules.pedidos.processing.application.ports import (
    ProcessingPlannerSource,
    ProcessingRepository,
    ProcessingWriter,
)
from app.modules.pedidos.processing.application.service import (
    claim_processing_job,
    run_claimed_processing_job,
    submit_processing_job,
)
from app.modules.pedidos.processing.domain import (
    PendingSnapshotStats,
    PlannedPair,
    PlanningState,
    ProcessingChannel,
    ProcessingLeaseLost,
    ProcessingLockUnavailable,
    ProcessingMode,
    ProcessingSpec,
    deterministic_alert_event_id,
    deterministic_completion_event_id,
    deterministic_progress_event_id,
)
from app.shared.jobs.application.ports import DurableJobRepository
from app.shared.jobs.domain import (
    DurableJob,
    JobReservation,
    JobReservationOutcome,
    JobStatus,
)


def _job(*, status: JobStatus = JobStatus.RUNNING) -> DurableJob:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    return DurableJob(
        id=uuid4(),
        kind="orders.processing.v1",
        owner_id=42,
        scope_key="sem_adequar:Franquia",
        idempotency_digest="a" * 64,
        fingerprint="b" * 64,
        status=status,
        attempts=1,
        max_attempts=3,
        progress_current=0,
        progress_total=None,
        result=None,
        error_code=None,
        retryable=False,
        requested_at=now,
        available_at=now,
        started_at=now,
        heartbeat_at=now,
        lease_owner="worker-024" if status is JobStatus.RUNNING else None,
        lease_expires_at=(now + timedelta(minutes=1))
        if status is JobStatus.RUNNING
        else None,
        deadline_at=now + timedelta(minutes=15),
        finished_at=None,
        updated_at=now,
    )


def _spec(job_id, *, applied: int) -> ProcessingSpec:
    return ProcessingSpec(
        job_id=job_id,
        mode=ProcessingMode.SEM_ADEQUAR,
        channel=ProcessingChannel.FRANQUIA,
        planning_state=PlanningState.PLANNED,
        next_ordinal=applied + 1,
        candidate_count=1,
        planned_count=1,
        applied_count=applied,
        deferred_count=0,
        blocked_credit_count=0,
        plan_hash="c" * 64,
        planned_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
    )


def _pending_spec(
    job_id, *, mode: ProcessingMode = ProcessingMode.SEM_ADEQUAR
) -> ProcessingSpec:
    return ProcessingSpec(
        job_id=job_id,
        mode=mode,
        channel=ProcessingChannel.FRANQUIA,
        planning_state=PlanningState.PENDING,
        next_ordinal=1,
        candidate_count=0,
        planned_count=0,
        applied_count=0,
        deferred_count=0,
        blocked_credit_count=0,
        plan_hash=None,
        planned_at=None,
    )


class _Lock:
    def __init__(self, events: list[str], *, error: Exception | None = None) -> None:
        self.events = events
        self.error = error

    async def ensure_held(self) -> None:
        self.events.append("lock")
        if self.error is not None:
            raise self.error


class _Lease:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start(self, **_kwargs) -> None:
        self.events.append("lease.start")

    async def update_progress(self, **_kwargs) -> None:
        self.events.append("lease.progress")

    async def ensure_valid(self) -> None:
        self.events.append("lease.valid")

    async def stop(self) -> None:
        self.events.append("lease.stop")


class _Uow:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


class _Processing:
    def __init__(self, job_id, events: list[str]) -> None:
        self.events = events
        self.incomplete = _spec(job_id, applied=0)
        self.complete = _spec(job_id, applied=1)
        self._loaded = False

    async def get_spec(self, _job_id, *, for_update: bool = False):
        self.events.append("spec.locked" if for_update else "spec")
        return self.complete if self._loaded else self.incomplete

    async def load_next_chunk(self, **_kwargs):
        self.events.append("plan.load")
        if self._loaded:
            return ()
        self._loaded = True
        return (
            PlannedPair(
                ordinal=1,
                nr_pedido=1,
                cd_prod_cor="PROD",
                payload={"x": 1},
            ),
        )

    async def checkpoint_chunk(self, **_kwargs):
        self.events.append("checkpoint")
        return self.complete


class _Writer:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def apply_pairs(self, _rows) -> None:
        self.events.append("writer")


class _Jobs:
    def __init__(self, claimed: DurableJob, events: list[str], *, succeed=True) -> None:
        self.claimed = claimed
        self.events = events
        self.should_succeed = succeed
        self.claim_called = False
        self.result = None
        self.succeed_now = None

    async def claim(self, **_kwargs):
        self.claim_called = True
        self.events.append("claim")
        return self.claimed

    async def heartbeat(self, **_kwargs):
        self.events.append("heartbeat")
        return self.claimed

    async def succeed(self, *, result, now, **_kwargs):
        self.events.append("succeed")
        self.result = result
        self.succeed_now = now
        if not self.should_succeed:
            return None
        if self.claimed.deadline_at is not None and now >= self.claimed.deadline_at:
            return None
        return replace(
            self.claimed,
            status=JobStatus.SUCCEEDED,
            result=result,
            lease_owner=None,
            lease_expires_at=None,
        )


class _Realtime:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.alert_event_id = None
        self.completion_event_id = None
        self.progress_events = []
        self.payload = None

    async def record_progress(self, *, event_id, payload, **_kwargs) -> None:
        self.events.append("progress")
        self.progress_events.append((event_id, payload))

    async def reconcile_alerts(self, *, event_id, **_kwargs) -> None:
        self.events.append("alerts")
        self.alert_event_id = event_id

    async def record_completion(self, *, event_id, payload, **_kwargs) -> None:
        self.events.append("orders")
        self.completion_event_id = event_id
        self.payload = payload


async def test_run_records_realtime_only_once_in_final_success_transaction() -> None:
    events: list[str] = []
    claimed = _job()
    jobs = _Jobs(claimed, events)
    processing = _Processing(claimed.id, events)
    realtime = _Realtime(events)
    now = datetime(2026, 8, 8, 12, 0, 10, tzinfo=UTC)

    completed = await run_claimed_processing_job(
        claimed=claimed,
        worker_id="worker-024",
        jobs=cast(DurableJobRepository, jobs),
        processing=cast(ProcessingRepository, processing),
        planner=cast(ProcessingPlannerSource, object()),  # plano já congelado
        writer=cast(ProcessingWriter, _Writer(events)),
        realtime=realtime,
        lease_keeper=_Lease(events),
        lock_guard=_Lock(events),
        unit_of_work=_Uow(events),
        now=lambda: now,
    )

    assert completed is not None and completed.status is JobStatus.SUCCEEDED
    assert events.count("alerts") == 1
    assert events.count("orders") == 1
    assert events.count("progress") == 1
    assert events.index("checkpoint") < events.index("progress")
    assert events.index("progress") < events.index("commit")
    assert events.index("checkpoint") < events.index("alerts")
    assert events.index("alerts") < events.index("succeed") < len(events) - 1
    assert realtime.alert_event_id == deterministic_alert_event_id(claimed.id)
    assert realtime.completion_event_id == deterministic_completion_event_id(claimed.id)
    assert realtime.progress_events == [
        (
            deterministic_progress_event_id(claimed.id, 1),
            {
                "jobId": str(claimed.id),
                "mode": "sem_adequar",
                "channel": "Franquia",
                "progressCurrent": 1,
                "progressTotal": 1,
            },
        )
    ]
    assert jobs.result is not None
    assert jobs.result == {
        "plannedCount": 1,
        "appliedCount": 1,
        "deferredCount": 0,
        "blockedCreditCount": 0,
    }
    assert realtime.payload == {
        "jobId": str(claimed.id),
        "mode": "sem_adequar",
        "channel": "Franquia",
        **jobs.result,
    }
    assert events[-2:] == ["commit", "lease.stop"]


async def test_final_cas_loss_rolls_back_realtime_and_success() -> None:
    events: list[str] = []
    claimed = _job()
    processing = _Processing(claimed.id, events)
    processing._loaded = True
    jobs = _Jobs(claimed, events, succeed=False)

    with pytest.raises(ProcessingLeaseLost, match="processing_job_lease_lost"):
        await run_claimed_processing_job(
            claimed=claimed,
            worker_id="worker-024",
            jobs=cast(DurableJobRepository, jobs),
            processing=cast(ProcessingRepository, processing),
            planner=cast(ProcessingPlannerSource, object()),
            writer=cast(ProcessingWriter, _Writer(events)),
            realtime=_Realtime(events),
            lease_keeper=_Lease(events),
            lock_guard=_Lock(events),
            unit_of_work=_Uow(events),
            now=lambda: datetime(2026, 8, 8, 12, 0, 10, tzinfo=UTC),
        )

    assert events.count("alerts") == 1
    assert events.count("orders") == 1
    assert events.count("commit") == 0
    assert events[-2:] == ["rollback", "lease.stop"]


async def test_final_cas_recaptures_clock_after_realtime_crosses_deadline() -> None:
    events: list[str] = []
    claimed = _job()
    processing = _Processing(claimed.id, events)
    processing._loaded = True
    jobs = _Jobs(claimed, events)
    assert claimed.deadline_at is not None
    instants = iter(
        (
            claimed.deadline_at - timedelta(milliseconds=2),
            claimed.deadline_at - timedelta(milliseconds=1),
            claimed.deadline_at,
        )
    )

    with pytest.raises(ProcessingLeaseLost, match="processing_job_lease_lost"):
        await run_claimed_processing_job(
            claimed=claimed,
            worker_id="worker-024",
            jobs=cast(DurableJobRepository, jobs),
            processing=cast(ProcessingRepository, processing),
            planner=cast(ProcessingPlannerSource, object()),
            writer=cast(ProcessingWriter, _Writer(events)),
            realtime=_Realtime(events),
            lease_keeper=_Lease(events),
            lock_guard=_Lock(events),
            unit_of_work=_Uow(events),
            now=lambda: next(instants),
        )

    assert jobs.succeed_now == claimed.deadline_at
    assert events.count("commit") == 0
    assert events[-2:] == ["rollback", "lease.stop"]


async def test_claim_checks_lock_before_touching_ledger() -> None:
    events: list[str] = []
    jobs = _Jobs(_job(), events)

    with pytest.raises(ProcessingLockUnavailable, match="busy"):
        await claim_processing_job(
            job_id=jobs.claimed.id,
            worker_id="worker-024",
            jobs=cast(DurableJobRepository, jobs),
            lock_guard=_Lock(
                events,
                error=ProcessingLockUnavailable("order_state_mutation_lock_busy"),
            ),
            unit_of_work=_Uow(events),
            now=lambda: datetime(2026, 8, 8, 12, tzinfo=UTC),
        )

    assert jobs.claim_called is False
    assert events == ["lock", "rollback"]


async def test_cpu_plan_monitor_signals_and_joins_cooperative_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        processing_service,
        "_CPU_MONITOR_INTERVAL_SECONDS",
        0.005,
    )
    entered = Event()
    exited = Event()
    events: list[str] = []

    def blocked_operation(cancel_token: Event) -> object:
        entered.set()
        cancel_token.wait(timeout=1)
        exited.set()
        raise RuntimeError("concurrent pure motor failure")

    class LostLease(_Lease):
        async def ensure_valid(self) -> None:
            self.events.append("lease.lost")
            raise ProcessingLeaseLost("processing_job_lease_lost")

    started = monotonic()
    with pytest.raises(ProcessingLeaseLost, match="processing_job_lease_lost"):
        await processing_service._run_cpu_bound_monitored(
            blocked_operation,
            lease_keeper=LostLease(events),
            lock_guard=_Lock(events),
        )

    assert entered.is_set()
    assert exited.is_set()
    assert monotonic() - started < 0.2
    assert events == ["lock", "lease.lost"]


@pytest.mark.parametrize(
    "outcome",
    [
        JobReservationOutcome.REPLAYED,
        JobReservationOutcome.COALESCED,
    ],
)
async def test_submit_does_not_dispatch_existing_job(outcome) -> None:
    events: list[str] = []
    existing = _job(status=JobStatus.QUEUED)

    class Jobs:
        async def reserve(self, _proposed):
            events.append("reserve")
            return JobReservation(job=existing, outcome=outcome)

    class Processing:
        async def create_spec(self, **_kwargs):
            events.append("header")

    class Dispatcher:
        async def enqueue(self, **_kwargs):
            events.append("dispatch")

    submission = await submit_processing_job(
        jobs=cast(DurableJobRepository, Jobs()),
        processing=cast(ProcessingRepository, Processing()),
        unit_of_work=_Uow(events),
        dispatcher=Dispatcher(),
        owner_id=42,
        idempotency_key="existing-key",
        request=processing_service.ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        requested_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
    )

    assert submission.broker_enqueued is False
    assert events == ["reserve", "commit"]


async def test_submit_commits_header_before_best_effort_dispatch_failure() -> None:
    events: list[str] = []
    created = _job(status=JobStatus.QUEUED)

    class Jobs:
        async def reserve(self, _proposed):
            events.append("reserve")
            return JobReservation(
                job=created,
                outcome=JobReservationOutcome.CREATED,
            )

    class Processing:
        async def create_spec(self, **_kwargs):
            events.append("header")

    class Dispatcher:
        async def enqueue(self, **_kwargs):
            events.append("dispatch")
            raise RuntimeError("broker unavailable")

    submission = await submit_processing_job(
        jobs=cast(DurableJobRepository, Jobs()),
        processing=cast(ProcessingRepository, Processing()),
        unit_of_work=_Uow(events),
        dispatcher=Dispatcher(),
        owner_id=42,
        idempotency_key="created-key",
        request=processing_service.ProcessingRequest(
            mode=ProcessingMode.ADEQUAR,
            channel=ProcessingChannel.TODOS,
        ),
        requested_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
    )

    assert submission.broker_enqueued is False
    assert events == ["reserve", "header", "commit", "dispatch"]


async def test_sem_adequar_planning_hydrates_globally_and_reports_real_counts() -> None:
    """Substitui o teste de paginação keyset: `sem_adequar` agora hidrata a
    foto inteira do canal (`load_pending_items`) e planeja pelo caminho
    global (`build_processing_plan`), com contadores reais (J1/J2/J3) — o
    Protocol de streaming (métodos de página keyset) deixou de existir."""

    events: list[str] = []
    claimed = _job()
    budget_calls: list[set[int]] = []

    items = (
        [
            {
                "nr_pedido": 1,
                "cd_prod_cor": "A",
                "sg_tamanho": size,
                "ds_grupo": "G",
                "qt_liquida": 1,
                "vl_liquido": 10.0,
                "status_credito": "Com Crédito",
                "client": "protected",
                "canal": "Franquia",
                "ds_produto": "A",
                "data": "2026-08-08",
            }
            for size in ("36", "37")
        ]
        + [
            {
                "nr_pedido": 2,
                "cd_prod_cor": "B",
                "sg_tamanho": "36",
                "ds_grupo": "G",
                "qt_liquida": 1,
                "vl_liquido": 10.0,
                "status_credito": "Com Crédito",
                "client": "protected",
                "canal": "Franquia",
                "ds_produto": "B",
                "data": "2026-08-08",
            }
        ]
        + [
            {
                "nr_pedido": 3,
                "cd_prod_cor": "C",
                "sg_tamanho": "36",
                "ds_grupo": "G",
                "qt_liquida": 1,
                "vl_liquido": 10.0,
                "status_credito": "Sem Crédito",
                "client": "protected",
                "canal": "Franquia",
                "ds_produto": "C",
                "data": "2026-08-08",
            }
        ]
    )

    class Planner:
        async def inspect_pending(self, _channel):
            events.append("inspect")
            return PendingSnapshotStats(
                pair_count=3,
                item_count=4,
                estimated_bytes=100,
                max_pair_item_count=2,
            )

        async def load_pending_items(self, _channel):
            events.append("hydrate")
            return list(items)

        async def load_size_reference(self, codes):
            events.append("reference")
            assert codes == {"A", "B", "C"}
            return {"A": {"36": 1, "37": 2}, "B": {"36": 1}, "C": {"36": 1}}

        async def load_stock(self, codes):
            events.append("stock")
            assert codes == {"A", "B", "C"}
            # A tem estoque suficiente (entra em rows); B não tem estoque
            # (cai em preteridos); C tem estoque, mas está sem crédito
            # (cai em bloqueados_credito).
            return {"Franquia": {"A_36": 1, "A_37": 1, "B_36": 0, "C_36": 1}}

        async def load_adequation_config(self):
            events.append("config")
            return "valor", 0.05

        async def load_pedido_budget(self, nr_pedidos):
            events.append("budget")
            budget_calls.append(set(nr_pedidos))
            scope = set(nr_pedidos)
            return (
                dict.fromkeys(scope, 0),
                dict.fromkeys(scope, 0),
                dict.fromkeys(scope, 0),
            )

    class Processing:
        def __init__(self) -> None:
            self.draft = None
            self.standby_calls: list[dict] = []

        async def get_spec(self, _job_id, *, for_update=False):
            assert for_update
            return _pending_spec(claimed.id)

        async def store_plan(self, *, job_id, draft, planned_at):
            events.append("store")
            self.draft = draft
            return ProcessingSpec(
                job_id=job_id,
                mode=ProcessingMode.SEM_ADEQUAR,
                channel=ProcessingChannel.FRANQUIA,
                planning_state=PlanningState.PLANNED,
                next_ordinal=1,
                candidate_count=draft.candidate_count,
                planned_count=draft.planned_count,
                applied_count=0,
                deferred_count=draft.deferred_count,
                blocked_credit_count=draft.blocked_credit_count,
                plan_hash=draft.plan_hash,
                planned_at=planned_at,
            )

        async def record_standby_reasons(
            self, *, job_id, deferred_pairs, blocked_credit_pairs, recorded_at
        ) -> None:
            events.append("standby")
            self.standby_calls.append(
                {
                    "job_id": job_id,
                    "deferred_pairs": deferred_pairs,
                    "blocked_credit_pairs": blocked_credit_pairs,
                    "recorded_at": recorded_at,
                }
            )

    processing = Processing()
    spec = await processing_service._plan_once(
        job=claimed,
        worker_id="worker-024",
        jobs=cast(DurableJobRepository, _Jobs(claimed, events)),
        processing=cast(ProcessingRepository, processing),
        planner=cast(ProcessingPlannerSource, Planner()),
        lease_keeper=_Lease(events),
        lock_guard=_Lock(events),
        unit_of_work=_Uow(events),
        now=lambda: datetime(2026, 8, 8, 12, 0, 10, tzinfo=UTC),
    )

    assert events.count("hydrate") == 1
    draft = processing.draft
    assert draft is not None
    assert draft.planned_count == 1
    assert draft.deferred_count == 1
    assert draft.blocked_credit_count == 1
    assert [(row.nr_pedido, row.cd_prod_cor) for row in draft.rows] == [(1, "A")]
    assert draft.rows[0].payload["type"] == "sem"
    assert spec.planned_count == 1
    assert budget_calls == [{1, 2, 3}]
    assert events.count("commit") == 1


async def test_plan_once_records_standby_reasons_between_store_plan_and_commit() -> (
    None
):
    """Reusa o cenário de `test_sem_adequar_planning_hydrates_globally_and_reports_real_counts`
    (que já produz `deferred_count == 1` e `blocked_credit_count == 1`), com um
    relógio INCREMENTAL — não um lambda de valor fixo — para provar que
    `record_standby_reasons` reusa o MESMO `planned_at` que `store_plan` recebeu:
    se a implementação chamasse `now()` de novo para o stand-by, o valor
    divergiria e este teste falharia (Pitfall 2 / risco 2 do 16-RESEARCH.md).
    """

    events: list[str] = []
    claimed = _job()
    budget_calls: list[set[int]] = []

    items = (
        [
            {
                "nr_pedido": 1,
                "cd_prod_cor": "A",
                "sg_tamanho": size,
                "ds_grupo": "G",
                "qt_liquida": 1,
                "vl_liquido": 10.0,
                "status_credito": "Com Crédito",
                "client": "protected",
                "canal": "Franquia",
                "ds_produto": "A",
                "data": "2026-08-08",
            }
            for size in ("36", "37")
        ]
        + [
            {
                "nr_pedido": 2,
                "cd_prod_cor": "B",
                "sg_tamanho": "36",
                "ds_grupo": "G",
                "qt_liquida": 1,
                "vl_liquido": 10.0,
                "status_credito": "Com Crédito",
                "client": "protected",
                "canal": "Franquia",
                "ds_produto": "B",
                "data": "2026-08-08",
            }
        ]
        + [
            {
                "nr_pedido": 3,
                "cd_prod_cor": "C",
                "sg_tamanho": "36",
                "ds_grupo": "G",
                "qt_liquida": 1,
                "vl_liquido": 10.0,
                "status_credito": "Sem Crédito",
                "client": "protected",
                "canal": "Franquia",
                "ds_produto": "C",
                "data": "2026-08-08",
            }
        ]
    )

    class Planner:
        async def inspect_pending(self, _channel):
            return PendingSnapshotStats(
                pair_count=3,
                item_count=4,
                estimated_bytes=100,
                max_pair_item_count=2,
            )

        async def load_pending_items(self, _channel):
            return list(items)

        async def load_size_reference(self, codes):
            return {"A": {"36": 1, "37": 2}, "B": {"36": 1}, "C": {"36": 1}}

        async def load_stock(self, codes):
            # A tem estoque suficiente (entra em rows); B não tem estoque
            # (cai em preteridos); C tem estoque, mas está sem crédito (cai
            # em bloqueados_credito) — mesmo cenário J1/J2/J3 do teste
            # irmão, que garante deferred_count == 1 e blocked_credit_count
            # == 1 não-vazios.
            return {"Franquia": {"A_36": 1, "A_37": 1, "B_36": 0, "C_36": 1}}

        async def load_adequation_config(self):
            return "valor", 0.05

        async def load_pedido_budget(self, nr_pedidos):
            budget_calls.append(set(nr_pedidos))
            scope = set(nr_pedidos)
            return (
                dict.fromkeys(scope, 0),
                dict.fromkeys(scope, 0),
                dict.fromkeys(scope, 0),
            )

    class Processing:
        def __init__(self) -> None:
            self.draft = None
            self.store_planned_at: datetime | None = None
            self.standby_calls: list[dict] = []

        async def get_spec(self, _job_id, *, for_update=False):
            return _pending_spec(claimed.id)

        async def store_plan(self, *, job_id, draft, planned_at):
            events.append("store")
            self.draft = draft
            self.store_planned_at = planned_at
            return ProcessingSpec(
                job_id=job_id,
                mode=ProcessingMode.SEM_ADEQUAR,
                channel=ProcessingChannel.FRANQUIA,
                planning_state=PlanningState.PLANNED,
                next_ordinal=1,
                candidate_count=draft.candidate_count,
                planned_count=draft.planned_count,
                applied_count=0,
                deferred_count=draft.deferred_count,
                blocked_credit_count=draft.blocked_credit_count,
                plan_hash=draft.plan_hash,
                planned_at=planned_at,
            )

        async def record_standby_reasons(
            self, *, job_id, deferred_pairs, blocked_credit_pairs, recorded_at
        ) -> None:
            events.append("standby")
            self.standby_calls.append(
                {
                    "job_id": job_id,
                    "deferred_pairs": deferred_pairs,
                    "blocked_credit_pairs": blocked_credit_pairs,
                    "recorded_at": recorded_at,
                }
            )

    processing = Processing()
    clock = iter(
        datetime(2026, 8, 8, 12, 0, second, tzinfo=UTC) for second in range(30)
    )

    spec = await processing_service._plan_once(
        job=claimed,
        worker_id="worker-024",
        jobs=cast(DurableJobRepository, _Jobs(claimed, events)),
        processing=cast(ProcessingRepository, processing),
        planner=cast(ProcessingPlannerSource, Planner()),
        lease_keeper=_Lease(events),
        lock_guard=_Lock(events),
        unit_of_work=_Uow(events),
        now=lambda: next(clock),
    )

    assert events.index("store") < events.index("standby") < events.index("commit")
    assert events.count("standby") == 1
    assert len(processing.standby_calls) == 1
    call = processing.standby_calls[0]
    draft = processing.draft
    assert draft is not None
    assert call["deferred_pairs"] == draft.deferred_pairs
    assert call["blocked_credit_pairs"] == draft.blocked_credit_pairs
    assert len(call["deferred_pairs"]) >= 1
    assert len(call["blocked_credit_pairs"]) >= 1
    assert call["recorded_at"] == processing.store_planned_at
    assert call["job_id"] == claimed.id
    assert spec.planned_count == 1


async def test_plan_once_feeds_real_budget_and_silences_the_aloc09_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """J6: com o orçamento real ligado pelo caminho de produção, o motor não
    emite o `logger.warning` de ALOC-09 — mesmo em `ADEQUAR`, onde o ledger
    de fato roda. O corte de 3 peças no tamanho 37 só é aceito porque o
    orçamento real (`total_original=100`) cobre o corte; o placeholder de
    execução única (soma do snapshot = 20 peças) daria `floor(20*0.05)=1`,
    insuficiente para os 3 que faltam — a mesma propriedade discriminante
    provada contra o banco real em `test_orcamento_persiste_entre_execucoes_sucessivas`."""

    events: list[str] = []
    claimed = _job()
    budget_calls: list[set[int]] = []

    items = [
        {
            "nr_pedido": 1,
            "cd_prod_cor": "A",
            "sg_tamanho": size,
            "ds_grupo": "G",
            "qt_liquida": 10,
            "vl_liquido": 100.0,
            "status_credito": "Com Crédito",
            "client": "protected",
            "canal": "Franquia",
            "ds_produto": "A",
            "data": "2026-08-08",
        }
        for size in ("36", "37")
    ]

    class Planner:
        async def inspect_pending(self, _channel):
            return PendingSnapshotStats(
                pair_count=1,
                item_count=2,
                estimated_bytes=100,
                max_pair_item_count=2,
            )

        async def load_pending_items(self, _channel):
            return list(items)

        async def load_size_reference(self, codes):
            assert codes == {"A"}
            return {"A": {"36": 1, "37": 2}}

        async def load_stock(self, codes):
            assert codes == {"A"}
            # 3 peças faltando no tamanho 37 (10 pedidas, 7 disponíveis).
            return {"Franquia": {"A_36": 10, "A_37": 7}}

        async def load_adequation_config(self):
            return "valor", 0.05

        async def load_pedido_budget(self, nr_pedidos):
            budget_calls.append(set(nr_pedidos))
            # Orçamento real: total_original=100 (bem maior que a soma do
            # snapshot, 20) prova que o valor usado não é o placeholder de
            # execução única — floor(100*0.05)=5 cobre o corte de 3.
            return {1: 100}, {1: 0}, {1: 0}

    class Processing:
        def __init__(self) -> None:
            self.draft = None
            self.standby_calls: list[dict] = []

        async def get_spec(self, _job_id, *, for_update=False):
            assert for_update
            return _pending_spec(claimed.id, mode=ProcessingMode.ADEQUAR)

        async def store_plan(self, *, job_id, draft, planned_at):
            self.draft = draft
            return ProcessingSpec(
                job_id=job_id,
                mode=ProcessingMode.ADEQUAR,
                channel=ProcessingChannel.FRANQUIA,
                planning_state=PlanningState.PLANNED,
                next_ordinal=1,
                candidate_count=draft.candidate_count,
                planned_count=draft.planned_count,
                applied_count=0,
                deferred_count=draft.deferred_count,
                blocked_credit_count=draft.blocked_credit_count,
                plan_hash=draft.plan_hash,
                planned_at=planned_at,
            )

        async def record_standby_reasons(
            self, *, job_id, deferred_pairs, blocked_credit_pairs, recorded_at
        ) -> None:
            self.standby_calls.append(
                {
                    "job_id": job_id,
                    "deferred_pairs": deferred_pairs,
                    "blocked_credit_pairs": blocked_credit_pairs,
                    "recorded_at": recorded_at,
                }
            )

    processing = Processing()
    with caplog.at_level(
        logging.WARNING, logger="app.modules.pedidos.domain.motor_adequacao"
    ):
        spec = await processing_service._plan_once(
            job=claimed,
            worker_id="worker-024",
            jobs=cast(DurableJobRepository, _Jobs(claimed, events)),
            processing=cast(ProcessingRepository, processing),
            planner=cast(ProcessingPlannerSource, Planner()),
            lease_keeper=_Lease(events),
            lock_guard=_Lock(events),
            unit_of_work=_Uow(events),
            now=lambda: datetime(2026, 8, 8, 12, 0, 10, tzinfo=UTC),
        )

    assert not any("ALOC-09" in record.message for record in caplog.records)
    assert budget_calls == [{1}]
    draft = processing.draft
    assert draft is not None
    assert spec.planned_count == 1
    assert [(row.nr_pedido, row.cd_prod_cor) for row in draft.rows] == [(1, "A")]
    row_items = {
        item["sg_tamanho"]: item["qt_liquida"]
        for item in draft.rows[0].payload["items"]
    }
    # 37 foi reduzido de 10 para 7 (o corte de 3 foi aceito pelo orçamento
    # real, não teria sido pelo placeholder de execução única).
    assert row_items == {"36": 10, "37": 7}
