from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal[
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "skipped",
]


class SincronizacaoResultado(BaseModel):
    pedidos_inseridos: int = Field(ge=0)
    estoque_chaves: int = Field(ge=0)
    processados_erp: int = Field(ge=0)
    faturamento_colecoes: int = Field(ge=0)
    referencia_tamanho_posicao: int = Field(ge=0)


class SincronizacaoJobAccepted(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(alias="jobId", min_length=36, max_length=36)
    status: JobStatus
    replayed: bool
    coalesced: bool
    status_url: str = Field(alias="statusUrl", max_length=256)


class SincronizacaoJobStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(alias="jobId", min_length=36, max_length=36)
    status: JobStatus
    requested_at: datetime = Field(alias="requestedAt")
    updated_at: datetime = Field(alias="updatedAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    deadline_at: datetime | None = Field(default=None, alias="deadlineAt")
    attempts: int = Field(ge=0, le=20)
    max_attempts: int = Field(alias="maxAttempts", ge=1, le=20)
    progress_current: int = Field(alias="progressCurrent", ge=0)
    progress_total: int | None = Field(default=None, alias="progressTotal", ge=0)
    retryable: bool
    result: SincronizacaoResultado | None = None
    error_code: str | None = Field(
        default=None,
        alias="errorCode",
        min_length=1,
        max_length=64,
    )


__all__ = [
    "SincronizacaoJobAccepted",
    "SincronizacaoJobStatus",
    "SincronizacaoResultado",
]
