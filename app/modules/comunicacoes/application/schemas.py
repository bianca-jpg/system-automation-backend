from datetime import datetime
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)


class EnviarComunicacaoRequest(BaseModel):
    """Payload para disparar um e-mail ao time comercial."""

    model_config = ConfigDict(populate_by_name=True)

    recipient: EmailStr = Field(max_length=254)
    content: str = Field(min_length=1, max_length=10000)
    order_ref: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("order_ref", "orderRef"),
    )
    subject: str | None = Field(default=None, max_length=255)

    @field_validator("order_ref", "subject")
    @classmethod
    def reject_header_control_chars(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 for char in value):
            raise ValueError("Cabeçalhos não aceitam caracteres de controle.")
        return value.strip() if value is not None else None

    @field_validator("content")
    @classmethod
    def reject_empty_or_nul_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A mensagem não pode conter apenas espaços.")
        if "\x00" in value:
            raise ValueError("A mensagem contém caractere não permitido.")
        return value


class ComunicacaoResponse(BaseModel):
    """Espelha o tipo Communication consumido pelo frontend."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    status: Literal["Pendente", "Enviado", "Falhou", "Incerto"]
    time: str
    content: str
    recipient: str
    attempt_count: int | None = Field(
        default=None,
        ge=0,
        serialization_alias="attemptCount",
    )
    max_attempts: int | None = Field(
        default=None,
        ge=1,
        serialization_alias="maxAttempts",
    )
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime | None = Field(
        default=None,
        serialization_alias="updatedAt",
    )
    sent_at: datetime | None = Field(default=None, serialization_alias="sentAt")
    next_attempt_at: datetime | None = Field(
        default=None,
        serialization_alias="nextAttemptAt",
    )


class ComunicacoesPageResponse(BaseModel):
    """Envelope estável do feed paginado, serializado em camelCase."""

    model_config = ConfigDict(populate_by_name=True)

    rows: list[ComunicacaoResponse]
    total: int = Field(ge=0)
    page_size: int = Field(
        ge=1,
        validation_alias=AliasChoices("page_size", "pageSize"),
        serialization_alias="pageSize",
    )
    next_cursor: str | None = Field(
        validation_alias=AliasChoices("next_cursor", "nextCursor"),
        serialization_alias="nextCursor",
    )
    has_more: bool = Field(
        validation_alias=AliasChoices("has_more", "hasMore"),
        serialization_alias="hasMore",
    )
