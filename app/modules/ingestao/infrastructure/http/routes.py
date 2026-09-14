import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from sqlalchemy.exc import SQLAlchemyError

from app.modules.auth.infrastructure.http.dependencies import CurrentUser
from app.modules.ingestao import service
from app.modules.ingestao.application.jobs import public_result
from app.modules.ingestao.application.schemas import (
    SincronizacaoJobAccepted,
    SincronizacaoJobStatus,
    SincronizacaoResultado,
)
from app.shared.jobs.domain import JobIdempotencyConflict, JobReservationOutcome
from app.shared.security import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/ingestao",
    tags=["Ingestao"],
    dependencies=[Depends(require_admin)],
)


@router.post(
    "/sincronizar",
    response_model=SincronizacaoJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Job durável criado, reutilizado ou coalescido.",
            "headers": {
                "Location": {
                    "description": "Caminho canônico para consultar o job.",
                    "schema": {"type": "string"},
                }
            },
        }
    },
)
async def sincronizar(
    response: Response,
    actor: Annotated[CurrentUser, Depends(require_admin)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
        ),
    ],
) -> SincronizacaoJobAccepted:
    """Persiste o job sem manter a request aberta durante o full refresh."""

    try:
        submission = await service.solicitar_sincronizacao(
            owner_id=actor.id,
            idempotency_key=idempotency_key,
        )
    except JobIdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key já utilizada com outra operação.",
        ) from exc
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.error(
            "Registro de jobs de ingestão indisponível: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sincronização temporariamente indisponível.",
        ) from exc

    reservation = submission.reservation
    job = reservation.job
    status_url = f"/api/v1/ingestao/sincronizar/{job.id}"
    response.headers["Location"] = status_url
    return SincronizacaoJobAccepted.model_validate(
        {
            "job_id": str(job.id),
            "status": job.status.value,
            "replayed": reservation.outcome is JobReservationOutcome.REPLAYED,
            "coalesced": reservation.outcome is JobReservationOutcome.COALESCED,
            "status_url": status_url,
        }
    )


@router.get(
    "/sincronizar/{jobId}",
    response_model=SincronizacaoJobStatus,
)
async def status_sincronizacao(
    job_id: Annotated[
        str,
        Path(
            alias="jobId",
            min_length=36,
            max_length=36,
            pattern=(
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
                r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
            ),
        ),
    ],
) -> SincronizacaoJobStatus:
    try:
        job = await service.obter_job_sincronizacao(UUID(job_id))
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.error("Consulta de job de ingestão indisponível: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Status da sincronização temporariamente indisponível.",
        ) from exc
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job de sincronização não encontrado.",
        )
    safe_result = public_result(job)
    return SincronizacaoJobStatus.model_validate(
        {
            "job_id": str(job.id),
            "status": job.status.value,
            "requested_at": job.requested_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "deadline_at": job.deadline_at,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "retryable": job.retryable,
            "result": SincronizacaoResultado(**safe_result) if safe_result else None,
            "error_code": job.error_code,
        }
    )
