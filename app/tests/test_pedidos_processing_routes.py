"""Contrato HTTP bounded para o processamento durável de Pedidos."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.main import app
from app.modules.auth.infrastructure.http.dependencies import CurrentUser
from app.modules.pedidos.processing.domain import (
    PlanningState,
    ProcessingChannel,
    ProcessingMode,
    ProcessingSpec,
)
from app.shared.database.session import get_db
from app.shared.infrastructure.rate_limit import (
    FixedWindowDecision,
    RateLimitUnavailableError,
)
from app.shared.jobs.domain import (
    DurableJob,
    JobIdempotencyConflict,
    JobReservation,
    JobReservationOutcome,
    JobStatus,
    JobSubmission,
)
from app.shared.security import require_actor, require_viewer

_JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


@pytest.fixture
def processing_actor_sem_banco():
    actor = CurrentUser(id=77, roles=["operacional"])

    async def db_proibido():
        raise AssertionError("a rota durável não usa a sessão get_db da request")

    app.dependency_overrides[require_viewer] = lambda: actor
    app.dependency_overrides[require_actor] = lambda: actor
    app.dependency_overrides[get_db] = db_proibido
    with (
        patch(
            "app.modules.pedidos.service.buscar_replay_job_processamento",
            new=AsyncMock(return_value=None),
        ) as replay,
        patch(
            "app.modules.pedidos.infrastructure.http.routes.enforce_dual_fixed_window",
            new=AsyncMock(
                return_value=FixedWindowDecision(
                    allowed=True,
                    retry_after=30,
                    user_count=1,
                    ip_count=1,
                )
            ),
        ) as rate_limit,
    ):
        try:
            yield SimpleNamespace(
                actor=actor,
                replay=replay,
                rate_limit=rate_limit,
            )
        finally:
            app.dependency_overrides.pop(require_viewer, None)
            app.dependency_overrides.pop(require_actor, None)
            app.dependency_overrides.pop(get_db, None)


def _job(
    status: JobStatus = JobStatus.QUEUED,
    *,
    owner_id: int = 77,
    result: dict[str, object] | None = None,
    error_code: str | None = None,
    retryable: bool = False,
) -> DurableJob:
    now = datetime.now(UTC)
    terminal = status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.SKIPPED}
    return DurableJob(
        id=_JOB_ID,
        kind="orders.processing.v1",
        owner_id=owner_id,
        scope_key="adequar:Todos",
        idempotency_digest="a" * 64,
        fingerprint="b" * 64,
        status=status,
        attempts=1 if status is not JobStatus.QUEUED else 0,
        max_attempts=3,
        progress_current=4 if status is JobStatus.SUCCEEDED else 0,
        progress_total=4 if status is JobStatus.SUCCEEDED else None,
        result=result,
        error_code=error_code,
        retryable=retryable,
        requested_at=now,
        available_at=now,
        started_at=now if status is not JobStatus.QUEUED else None,
        heartbeat_at=now if status is JobStatus.RUNNING else None,
        lease_owner="orders:worker" if status is JobStatus.RUNNING else None,
        lease_expires_at=(now + timedelta(seconds=60))
        if status is JobStatus.RUNNING
        else None,
        deadline_at=now + timedelta(minutes=15),
        finished_at=now if terminal else None,
        updated_at=now,
    )


def _spec() -> ProcessingSpec:
    return ProcessingSpec(
        job_id=_JOB_ID,
        mode=ProcessingMode.ADEQUAR,
        channel=ProcessingChannel.TODOS,
        planning_state=PlanningState.PLANNED,
        next_ordinal=5,
        candidate_count=4,
        planned_count=4,
        applied_count=4,
        deferred_count=1,
        blocked_credit_count=2,
        plan_hash="c" * 64,
        planned_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("outcome", "broker_enqueued", "replayed", "coalesced"),
    [
        (JobReservationOutcome.CREATED, True, False, False),
        (JobReservationOutcome.CREATED, False, False, False),
        (JobReservationOutcome.REPLAYED, False, True, False),
        (JobReservationOutcome.COALESCED, False, False, True),
    ],
)
def test_post_retorna_202_location_e_replay_estavel_mesmo_sem_broker(
    client,
    processing_actor_sem_banco,
    outcome,
    broker_enqueued,
    replayed,
    coalesced,
):
    submission = JobSubmission(
        reservation=JobReservation(job=_job(), outcome=outcome),
        broker_enqueued=broker_enqueued,
    )
    with patch(
        "app.modules.pedidos.service.solicitar_job_processamento",
        new=AsyncMock(return_value=submission),
    ) as submit:
        response = client.post(
            "/api/v1/pedidos/processamentos",
            headers={"Idempotency-Key": "pedido-job-0001"},
            json={"mode": "adequar", "channel": "Todos"},
        )

    status_url = f"/api/v1/pedidos/processamentos/{_JOB_ID}"
    assert response.status_code == 202
    assert response.headers["location"] == status_url
    assert response.json() == {
        "jobId": str(_JOB_ID),
        "status": "queued",
        "replayed": replayed,
        "coalesced": coalesced,
        "statusUrl": status_url,
        "progressCurrent": 0,
        "progressTotal": None,
    }
    submit.assert_awaited_once_with(
        owner_id=77,
        idempotency_key="pedido-job-0001",
        mode="adequar",
        channel="Todos",
    )
    processing_actor_sem_banco.replay.assert_awaited_once_with(
        owner_id=77,
        idempotency_key="pedido-job-0001",
        mode="adequar",
        channel="Todos",
    )
    rate_kwargs = processing_actor_sem_banco.rate_limit.await_args.kwargs
    assert rate_kwargs["namespace"] == "orders_processing"
    assert rate_kwargs["user_identity"] == "77"
    assert rate_kwargs["user_limit"] == 3
    assert rate_kwargs["ip_limit"] == 10
    assert rate_kwargs["window_seconds"] == 60


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({}, {"mode": "adequar", "channel": "Todos"}),
        ({"Idempotency-Key": "curta"}, {"mode": "adequar", "channel": "Todos"}),
        (
            {"Idempotency-Key": "x" * 129},
            {"mode": "adequar", "channel": "Todos"},
        ),
        (
            {"Idempotency-Key": "pedido job 0001"},
            {"mode": "adequar", "channel": "Todos"},
        ),
        (
            {"Idempotency-Key": "pedido/job/0001"},
            {"mode": "adequar", "channel": "Todos"},
        ),
        (
            {"Idempotency-Key": b"pedid\xff-job-0001"},
            {"mode": "adequar", "channel": "Todos"},
        ),
        (
            {"Idempotency-Key": "pedido-job-0001"},
            {"mode": "invalido", "channel": "Todos"},
        ),
        (
            {"Idempotency-Key": "pedido-job-0001"},
            {"mode": "adequar", "channel": "Desconhecido"},
        ),
        (
            {"Idempotency-Key": "pedido-job-0001"},
            {"mode": "adequar", "channel": "Todos", "payload": "proibido"},
        ),
    ],
)
def test_post_rejeita_header_e_intencao_fora_do_contrato(
    client,
    processing_actor_sem_banco,
    headers,
    body,
):
    with patch(
        "app.modules.pedidos.service.solicitar_job_processamento",
        new=AsyncMock(),
    ) as submit:
        response = client.post(
            "/api/v1/pedidos/processamentos",
            headers=headers,
            json=body,
        )

    assert response.status_code == 422
    submit.assert_not_awaited()


def test_post_converte_conflito_e_indisponibilidade_sem_expor_detalhe(
    client,
    processing_actor_sem_banco,
):
    url = "/api/v1/pedidos/processamentos"
    request = {
        "headers": {"Idempotency-Key": "pedido-job-0001"},
        "json": {"mode": "sem_adequar", "channel": "Franquia"},
    }
    with patch(
        "app.modules.pedidos.service.solicitar_job_processamento",
        new=AsyncMock(side_effect=JobIdempotencyConflict(_JOB_ID)),
    ):
        conflict = client.post(url, **request)
    with patch(
        "app.modules.pedidos.service.solicitar_job_processamento",
        new=AsyncMock(side_effect=OperationalError("secret", {}, RuntimeError())),
    ):
        unavailable = client.post(url, **request)

    assert conflict.status_code == 409
    assert str(_JOB_ID) not in conflict.text
    assert unavailable.status_code == 503
    assert "secret" not in unavailable.text


def test_conflito_idempotente_no_preflight_nao_consome_rate_limit(
    client,
    processing_actor_sem_banco,
):
    with (
        patch(
            "app.modules.pedidos.service.buscar_replay_job_processamento",
            new=AsyncMock(side_effect=JobIdempotencyConflict(_JOB_ID)),
        ) as replay,
        patch(
            "app.modules.pedidos.infrastructure.http.routes.enforce_dual_fixed_window",
            new=AsyncMock(),
        ) as rate_limit,
        patch(
            "app.modules.pedidos.service.solicitar_job_processamento",
            new=AsyncMock(),
        ) as submit,
    ):
        response = client.post(
            "/api/v1/pedidos/processamentos",
            headers={"Idempotency-Key": "pedido-job-0001"},
            json={"mode": "sem_adequar", "channel": "Franquia"},
        )

    assert response.status_code == 409
    assert str(_JOB_ID) not in response.text
    replay.assert_awaited_once()
    rate_limit.assert_not_awaited()
    submit.assert_not_awaited()


def test_replay_persistido_ignora_rate_limit_e_redis_indisponivel(
    client,
    processing_actor_sem_banco,
):
    submission = JobSubmission(
        reservation=JobReservation(
            job=_job(),
            outcome=JobReservationOutcome.REPLAYED,
        ),
        broker_enqueued=False,
    )
    with (
        patch(
            "app.modules.pedidos.service.buscar_replay_job_processamento",
            new=AsyncMock(return_value=submission),
        ) as replay,
        patch(
            "app.modules.pedidos.infrastructure.http.routes.enforce_dual_fixed_window",
            new=AsyncMock(side_effect=RateLimitUnavailableError("redis down")),
        ) as rate_limit,
        patch(
            "app.modules.pedidos.service.solicitar_job_processamento",
            new=AsyncMock(),
        ) as submit,
    ):
        response = client.post(
            "/api/v1/pedidos/processamentos",
            headers={"Idempotency-Key": "pedido-job-0001"},
            json={"mode": "adequar", "channel": "Todos"},
        )

    assert response.status_code == 202
    assert response.json()["replayed"] is True
    replay.assert_awaited_once()
    rate_limit.assert_not_awaited()
    submit.assert_not_awaited()


def test_nova_intencao_falha_fechado_quando_rate_limit_indisponivel(
    client,
    processing_actor_sem_banco,
):
    with (
        patch(
            "app.modules.pedidos.infrastructure.http.routes.enforce_dual_fixed_window",
            new=AsyncMock(side_effect=RateLimitUnavailableError("redis down")),
        ),
        patch(
            "app.modules.pedidos.service.solicitar_job_processamento",
            new=AsyncMock(),
        ) as submit,
    ):
        response = client.post(
            "/api/v1/pedidos/processamentos",
            headers={"Idempotency-Key": "pedido-job-0002"},
            json={"mode": "adequar", "channel": "Todos"},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert "redis" not in response.text.lower()
    submit.assert_not_awaited()


def test_nova_intencao_acima_do_limite_retorna_429_sem_criar_job(
    client,
    processing_actor_sem_banco,
):
    with (
        patch(
            "app.modules.pedidos.infrastructure.http.routes.enforce_dual_fixed_window",
            new=AsyncMock(
                return_value=FixedWindowDecision(
                    allowed=False,
                    retry_after=41,
                    user_count=4,
                    ip_count=1,
                )
            ),
        ),
        patch(
            "app.modules.pedidos.service.solicitar_job_processamento",
            new=AsyncMock(),
        ) as submit,
    ):
        response = client.post(
            "/api/v1/pedidos/processamentos",
            headers={"Idempotency-Key": "pedido-job-0003"},
            json={"mode": "sem_adequar", "channel": "Multimarca"},
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "41"
    submit.assert_not_awaited()


def test_get_expoe_apenas_status_resultado_bounded_e_aliases_camelcase(
    client,
    processing_actor_sem_banco,
):
    job = _job(
        JobStatus.SUCCEEDED,
        owner_id=999,
        result={
            "plannedCount": 4,
            "appliedCount": 4,
            "deferredCount": 1,
            "blockedCreditCount": 2,
            "customerEmail": "nao-vazar@example.invalid",
            "exception": "segredo",
        },
    )
    with patch(
        "app.modules.pedidos.service.obter_job_processamento",
        new=AsyncMock(return_value=(job, _spec())),
    ):
        response = client.get(f"/api/v1/pedidos/processamentos/{_JOB_ID}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobId"] == str(_JOB_ID)
    assert payload["mode"] == "adequar"
    assert payload["channel"] == "Todos"
    assert payload["status"] == "succeeded"
    assert payload["progressCurrent"] == 4
    assert payload["progressTotal"] == 4
    assert payload["attempts"] == 1
    assert payload["maxAttempts"] == 3
    assert payload["retryable"] is False
    assert payload["requestedAt"]
    assert payload["deadlineAt"]
    assert payload["result"] == {
        "plannedCount": 4,
        "appliedCount": 4,
        "deferredCount": 1,
        "blockedCreditCount": 2,
    }
    forbidden = {
        "ownerId",
        "scopeKey",
        "idempotencyDigest",
        "fingerprint",
        "leaseOwner",
        "leaseExpiresAt",
        "customerEmail",
        "exception",
    }
    assert forbidden.isdisjoint(payload)
    assert forbidden.isdisjoint(payload["result"])


@pytest.mark.parametrize(
    ("job_status", "retryable", "error_code"),
    [
        (JobStatus.RETRYING, True, "processing_dependency_unavailable"),
        (JobStatus.FAILED, False, "job_attempts_exhausted"),
        (JobStatus.SKIPPED, False, "job_deadline_exceeded"),
    ],
)
def test_get_nao_oferece_retry_manual_para_estado_terminal(
    client,
    processing_actor_sem_banco,
    job_status,
    retryable,
    error_code,
):
    job = _job(
        job_status,
        retryable=retryable,
        error_code=error_code,
    )
    with patch(
        "app.modules.pedidos.service.obter_job_processamento",
        new=AsyncMock(return_value=(job, _spec())),
    ):
        response = client.get(f"/api/v1/pedidos/processamentos/{_JOB_ID}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == job_status.value
    assert payload["retryable"] is retryable
    assert payload["errorCode"] == error_code
    if job_status in {JobStatus.FAILED, JobStatus.SKIPPED}:
        assert payload["retryable"] is False


def test_get_autoriza_por_capability_mesmo_quando_job_foi_criado_por_outro_actor(
    client,
    processing_actor_sem_banco,
):
    with patch(
        "app.modules.pedidos.service.obter_job_processamento",
        new=AsyncMock(return_value=(_job(owner_id=123), _spec())),
    ):
        response = client.get(f"/api/v1/pedidos/processamentos/{_JOB_ID}")

    assert response.status_code == 200


def test_processamentos_exige_capability_actor(client):
    viewer = CurrentUser(id=10, roles=["basico"])

    def forbidden():
        raise HTTPException(status_code=403, detail="forbidden")

    app.dependency_overrides[require_viewer] = lambda: viewer
    app.dependency_overrides[require_actor] = forbidden
    try:
        with patch(
            "app.modules.pedidos.service.solicitar_job_processamento",
            new=AsyncMock(),
        ) as submit:
            post = client.post(
                "/api/v1/pedidos/processamentos",
                headers={"Idempotency-Key": "pedido-job-0001"},
                json={"mode": "adequar", "channel": "Todos"},
            )
        with patch(
            "app.modules.pedidos.service.obter_job_processamento",
            new=AsyncMock(),
        ) as get_job:
            get = client.get(f"/api/v1/pedidos/processamentos/{_JOB_ID}")
    finally:
        app.dependency_overrides.pop(require_viewer, None)
        app.dependency_overrides.pop(require_actor, None)

    assert post.status_code == 403
    assert get.status_code == 403
    submit.assert_not_awaited()
    get_job.assert_not_awaited()


def test_get_retorna_404_ou_503_sem_detalhes_internos(
    client,
    processing_actor_sem_banco,
):
    url = f"/api/v1/pedidos/processamentos/{_JOB_ID}"
    with patch(
        "app.modules.pedidos.service.obter_job_processamento",
        new=AsyncMock(return_value=None),
    ):
        missing = client.get(url)
    with patch(
        "app.modules.pedidos.service.obter_job_processamento",
        new=AsyncMock(side_effect=RuntimeError("customer@example.invalid")),
    ):
        unavailable = client.get(url)

    assert missing.status_code == 404
    assert unavailable.status_code == 503
    assert "customer@example.invalid" not in unavailable.text


def test_get_valida_uuid_e_nao_expoe_endpoint_de_retry(
    client,
    processing_actor_sem_banco,
):
    with patch(
        "app.modules.pedidos.service.obter_job_processamento",
        new=AsyncMock(),
    ) as get_job:
        invalid = client.get("/api/v1/pedidos/processamentos/nao-e-uuid")
        retry = client.post(f"/api/v1/pedidos/processamentos/{_JOB_ID}/retry")

    assert invalid.status_code == 422
    assert retry.status_code in {404, 405}
    get_job.assert_not_awaited()


def test_openapi_declara_header_bounded_e_nao_expoe_rotas_legadas():
    paths = app.openapi()["paths"]
    create = paths["/api/v1/pedidos/processamentos"]["post"]
    header = next(
        parameter
        for parameter in create["parameters"]
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    )
    assert header["required"] is True
    assert header["schema"]["minLength"] == 8
    assert header["schema"]["maxLength"] == 128
    assert header["schema"]["pattern"] == r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"
    assert {"202", "409", "422", "429", "503"}.issubset(create["responses"])
    assert "/api/v1/pedidos/adequar" not in paths
    assert "/api/v1/pedidos/sem_adequar" not in paths
    assert "/api/v1/pedidos/{nr_pedido}/alterar-grade" not in paths
    assert f"/api/v1/pedidos/processamentos/{_JOB_ID}/retry" not in paths
