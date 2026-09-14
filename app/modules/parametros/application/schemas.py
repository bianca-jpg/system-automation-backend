import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.parametros.infrastructure.models import (
    ChangeRequestType,
    ParametroTipo,
)

# valor/descricao/justification sao colunas Text (sem limite no banco); estes
# sao tetos de aplicacao deliberados, nao espelhos de coluna.
_VALOR_MAX_LENGTH = 4_000
_JUSTIFICATION_MAX_LENGTH = 2_000
_PROPOSED_PAYLOAD_MAX_BYTES = 16_384


class ParametroOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chave: str
    valor: str
    tipo: str
    descricao: str | None = None
    created_at: datetime
    updated_at: datetime


class ParametrosPageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rows: list[ParametroOut]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, serialization_alias="pageSize")
    total_pages: int = Field(ge=1, serialization_alias="totalPages")


class ParametroCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chave: str = Field(min_length=1, max_length=128)
    valor: str = Field(min_length=1, max_length=_VALOR_MAX_LENGTH)
    tipo: ParametroTipo = ParametroTipo.STRING
    descricao: str | None = Field(default=None, max_length=_VALOR_MAX_LENGTH)


class ParametroUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None = "nao alterar" nesta rota de update; os defaults None sao
    # obrigatorios, senao a rota de update quebra.
    valor: str | None = Field(default=None, max_length=_VALOR_MAX_LENGTH)
    tipo: ParametroTipo | None = None
    descricao: str | None = Field(default=None, max_length=_VALOR_MAX_LENGTH)


class ChangeRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_chave: str | None = Field(default=None, max_length=128)
    change_type: ChangeRequestType
    proposed_payload: dict[str, Any] = Field(default_factory=dict)
    justification: str | None = Field(
        default=None, max_length=_JUSTIFICATION_MAX_LENGTH
    )

    @field_validator("proposed_payload")
    @classmethod
    def validar_tamanho_do_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        serializado = json.dumps(value, ensure_ascii=False, default=str)
        if len(serializado.encode("utf-8")) > _PROPOSED_PAYLOAD_MAX_BYTES:
            raise ValueError("proposed_payload excede 16 KiB serializado")
        return value


class ChangeRequestReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None


class ChangeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requested_by: int | None = None
    target_chave: str | None = None
    change_type: str
    proposed_payload: dict[str, Any]
    justification: str | None = None
    status: str
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChangeRequestsPageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rows: list[ChangeRequestOut]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, serialization_alias="pageSize")
    total_pages: int = Field(ge=1, serialization_alias="totalPages")
