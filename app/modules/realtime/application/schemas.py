from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TicketResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ticket: str = Field(min_length=32, max_length=128)
    expires_in: int = Field(alias="expiresIn", ge=1)
    websocket_url: str = Field(alias="websocketUrl")


class TopicStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    latest_sequence: int = Field(alias="latestSequence", ge=0)
    last_read_sequence: int = Field(alias="lastReadSequence", ge=0)
    unseen: int = Field(ge=0)
    latest: dict[str, Any] | None = None


class RealtimeStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    last_sequence: int = Field(alias="lastSequence", ge=0)
    topics: dict[str, TopicStatusResponse]


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    topic: str = Field(min_length=1, max_length=64)
    through_sequence: int = Field(
        alias="throughSequence", ge=0, le=9_223_372_036_854_775_807
    )


class MarkReadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    topic: str
    last_read_sequence: int = Field(alias="lastReadSequence", ge=0)
    unseen: int = Field(ge=0)
