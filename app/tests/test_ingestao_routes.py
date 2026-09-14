from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.main import app
from app.modules.auth.infrastructure.http.dependencies import CurrentUser
from app.shared.database.session import get_db
from app.shared.jobs.domain import (
    DurableJob,
    JobReservation,
    JobReservationOutcome,
    JobStatus,
    JobSubmission,
)
from app.shared.security import require_admin

_JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


@pytest.fixture
def admin_sem_banco():
    async def db_proibido():
        raise AssertionError("a rota de enqueue não deve abrir sessão PostgreSQL")

    app.dependency_overrides[require_admin] = lambda: CurrentUser(
        id=77,
        roles=["administrador"],
    )
    app.dependency_overrides[get_db] = db_proibido
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_admin, None)
        app.dependency_overrides.pop(get_db, None)


def _job(
    status: JobStatus = JobStatus.QUEUED,
    *,
    owner_id: int | None = 1,
    result: dict[str, object] | None = None,
    error_code: str | None = None,
    retryable: bool = False,
) -> DurableJob:
    now = datetime.now(UTC)
    terminal = status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.SKIPPED}
    return DurableJob(
        id=_JOB_ID,
        kind="ingestion.full_sync.v1",
        owner_id=owner_id,
        scope_key="full-refresh",
        idempotency_digest=None,
        fingerprint="a" * 64,
        status=status,
        attempts=1 if status is not JobStatus.QUEUED else 0,
        max_attempts=3,
        progress_current=5 if status is JobStatus.SUCCEEDED else 0,
        progress_total=5,
        result=result,
        error_code=error_code,
        retryable=retryable,
        requested_at=now,
        available_at=now,
        started_at=now if status is not JobStatus.QUEUED else None,
        heartbeat_at=now if status is JobStatus.RUNNING else None,
        lease_owner="celery:worker" if status is JobStatus.RUNNING else None,
        lease_expires_at=(now + timedelta(seconds=120))
        if status is JobStatus.RUNNING
        else None,
        deadline_at=now + timedelta(minutes=30),
        finished_at=now if terminal else None,
        updated_at=now,
    )


@pytest.mark.parametrize(
    ("outcome", "replayed", "coalesced", "broker_enqueued"),
    [
        (JobReservationOutcome.CREATED, False, False, True),
        (JobReservationOutcome.CREATED, False, False, False),
        (JobReservationOutcome.REPLAYED, True, False, False),
        (JobReservationOutcome.COALESCED, False, True, False),
    ],
)
def test_post_retorna_202_e_reutiliza_job_sem_abrir_db_da_request(
    client,
    admin_sem_banco,
    outcome,
    replayed,
    coalesced,
    broker_enqueued,
):
    submission = JobSubmission(
        reservation=JobReservation(job=_job(), outcome=outcome),
        broker_enqueued=broker_enqueued,
    )
    with patch(
        "app.modules.ingestao.service.solicitar_sincronizacao",
        new=AsyncMock(return_value=submission),
    ) as request_sync:
        response = client.post(
            "/api/v1/ingestao/sincronizar",
            headers={"Idempotency-Key": "request-12345678"},
        )

    status_url = f"/api/v1/ingestao/sincronizar/{_JOB_ID}"
    assert response.status_code == 202
    assert response.headers["location"] == status_url
    assert response.json() == {
        "jobId": str(_JOB_ID),
        "status": "queued",
        "replayed": replayed,
        "coalesced": coalesced,
        "statusUrl": status_url,
    }
    request_sync.assert_awaited_once_with(
        owner_id=77,
        idempotency_key="request-12345678",
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Idempotency-Key": "curta"},
        {"Idempotency-Key": "x" * 129},
        {"Idempotency-Key": "invalid key!"},
    ],
)
def test_post_exige_idempotency_key_ascii_bounded(client, admin_sem_banco, headers):
    with patch(
        "app.modules.ingestao.service.solicitar_sincronizacao",
        new=AsyncMock(),
    ) as request_sync:
        response = client.post("/api/v1/ingestao/sincronizar", headers=headers)

    assert response.status_code == 422
    request_sync.assert_not_awaited()


def test_openapi_declara_idempotency_key_obrigatoria_e_bounded():
    operation = app.openapi()["paths"]["/api/v1/ingestao/sincronizar"]["post"]
    header = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    )
    assert header["required"] is True
    assert header["schema"]["minLength"] == 8
    assert header["schema"]["maxLength"] == 128


def test_get_status_sucesso_expoe_progresso_e_resultado_bounded(
    client,
    admin_sem_banco,
):
    job = _job(
        status=JobStatus.SUCCEEDED,
        result={
            "pedidos_inseridos": 1,
            "estoque_chaves": 2,
            "processados_erp": 3,
            "faturamento_colecoes": 4,
            "referencia_tamanho_posicao": 5,
            "customer_email": "nao-pode-vazar@example.invalid",
        },
    )
    with patch(
        "app.modules.ingestao.service.obter_job_sincronizacao",
        new=AsyncMock(return_value=job),
    ):
        response = client.get(f"/api/v1/ingestao/sincronizar/{_JOB_ID}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobId"] == str(_JOB_ID)
    assert payload["status"] == "succeeded"
    assert payload["result"]["pedidos_inseridos"] == 1
    assert payload["progressCurrent"] == 5
    assert payload["progressTotal"] == 5
    assert payload["attempts"] == 1
    assert payload["maxAttempts"] == 3
    assert payload["retryable"] is False
    assert payload["requestedAt"]
    assert payload["deadlineAt"]
    assert "customer_email" not in payload["result"]
    assert "detail" not in payload


def test_get_status_global_autoriza_por_capability_nao_por_owner(
    client,
    admin_sem_banco,
):
    job_de_outro_ator = _job(owner_id=123)
    with patch(
        "app.modules.ingestao.service.obter_job_sincronizacao",
        new=AsyncMock(return_value=job_de_outro_ator),
    ):
        response = client.get(f"/api/v1/ingestao/sincronizar/{_JOB_ID}")

    assert response.status_code == 200


def test_get_status_sem_capability_retorna_403(client):
    def forbidden():
        raise HTTPException(status_code=403, detail="forbidden")

    app.dependency_overrides[require_admin] = forbidden
    try:
        with patch(
            "app.modules.ingestao.service.obter_job_sincronizacao",
            new=AsyncMock(),
        ) as get_job:
            response = client.get(f"/api/v1/ingestao/sincronizar/{_JOB_ID}")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert response.status_code == 403
    get_job.assert_not_awaited()


def test_get_status_falha_nao_expoe_excecao_interna(client, admin_sem_banco):
    with patch(
        "app.modules.ingestao.service.obter_job_sincronizacao",
        new=AsyncMock(
            return_value=_job(
                status=JobStatus.FAILED,
                error_code="internal_error",
            )
        ),
    ):
        response = client.get(f"/api/v1/ingestao/sincronizar/{_JOB_ID}")

    assert response.status_code == 200
    assert response.json()["errorCode"] == "internal_error"
    assert response.json()["result"] is None


def test_get_status_job_inexistente(client, admin_sem_banco):
    with patch(
        "app.modules.ingestao.service.obter_job_sincronizacao",
        new=AsyncMock(return_value=None),
    ):
        response = client.get(f"/api/v1/ingestao/sincronizar/{_JOB_ID}")

    assert response.status_code == 404


def test_get_status_valida_job_id_antes_do_adapter(client, admin_sem_banco):
    with patch(
        "app.modules.ingestao.service.obter_job_sincronizacao",
        new=AsyncMock(),
    ) as get_job:
        response = client.get("/api/v1/ingestao/sincronizar/../../segredo")

    assert response.status_code in {404, 422}
    get_job.assert_not_awaited()
