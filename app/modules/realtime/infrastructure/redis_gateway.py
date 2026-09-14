"""Redis como transporte efêmero: tickets one-use e Stream fanout."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.modules.realtime.domain import RealtimeUnavailableError
from app.shared.config.settings import Settings

_TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class TicketRateLimitedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TicketClaims:
    user_id: int
    access_expires_at: datetime
    topics: frozenset[str]


class RedisRealtimeGateway:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    @staticmethod
    def _ticket_key(ticket: str) -> str:
        digest = hashlib.sha256(ticket.encode("ascii")).hexdigest()
        return f"automation:realtime:ticket:{digest}"

    @staticmethod
    def _rate_key(scope: str, identity: str) -> str:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        minute = int(datetime.now(UTC).timestamp() // 60)
        return f"automation:realtime:rate:{scope}:{minute}:{digest}"

    async def _enforce_rate(self, *, scope: str, identity: str, limit: int) -> None:
        raw_count = await cast(
            Awaitable[object],
            self._redis.eval(
                _RATE_LIMIT_SCRIPT,
                1,
                self._rate_key(scope, identity),
                "70",
            ),
        )
        if not isinstance(raw_count, (int, str)):
            raise RealtimeUnavailableError("resposta inválida do rate limit realtime")
        if int(raw_count) > limit:
            raise TicketRateLimitedError(f"limite de tickets por {scope} excedido")

    async def issue_ticket(
        self,
        *,
        user_id: int,
        client_ip: str,
        access_expires_at: datetime,
        topics: Sequence[str],
    ) -> str:
        try:
            await self._enforce_rate(
                scope="user",
                identity=str(user_id),
                limit=self._settings.realtime_ticket_user_rate_per_minute,
            )
            await self._enforce_rate(
                scope="ip",
                identity=client_ip[:128],
                limit=self._settings.realtime_ticket_ip_rate_per_minute,
            )
            ticket = secrets.token_urlsafe(32)
            value = json.dumps(
                {
                    "userId": user_id,
                    "nonce": secrets.token_hex(8),
                    "issuedAt": datetime.now(UTC).isoformat(),
                    "accessExpiresAt": access_expires_at.astimezone(UTC).isoformat(),
                    "topics": list(topics),
                },
                separators=(",", ":"),
            )
            stored = await self._redis.set(
                self._ticket_key(ticket),
                value,
                ex=self._settings.realtime_ticket_ttl_seconds,
                nx=True,
            )
            if not stored:  # colisão criptograficamente improvável
                raise RealtimeUnavailableError("não foi possível emitir ticket")
            return ticket
        except TicketRateLimitedError:
            raise
        except (RedisError, OSError, TimeoutError) as exc:
            raise RealtimeUnavailableError("transporte realtime indisponível") from exc

    async def consume_ticket(self, ticket: str) -> TicketClaims | None:
        if not _TICKET_RE.fullmatch(ticket):
            return None
        try:
            raw = await self._redis.getdel(self._ticket_key(ticket))
        except (RedisError, OSError, TimeoutError) as exc:
            raise RealtimeUnavailableError("transporte realtime indisponível") from exc
        if raw is None:
            return None
        if not isinstance(raw, str) or len(raw) > 2_048:
            return None
        try:
            data = json.loads(raw)
            topics_raw = data["topics"]
            if (
                not isinstance(topics_raw, list)
                or not topics_raw
                or len(topics_raw) > 16
                or any(not isinstance(topic, str) for topic in topics_raw)
            ):
                return None
            access_expires_at = datetime.fromisoformat(data["accessExpiresAt"])
            issued_at = datetime.fromisoformat(data["issuedAt"])
            if access_expires_at.tzinfo is None or issued_at.tzinfo is None:
                return None
            return TicketClaims(
                user_id=int(data["userId"]),
                access_expires_at=access_expires_at,
                topics=frozenset(topics_raw),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    async def publish_envelope(self, envelope: dict[str, Any]) -> str:
        try:
            return str(
                await self._redis.xadd(
                    self._settings.realtime_stream_key,
                    {"envelope": json.dumps(envelope, separators=(",", ":"))},
                    maxlen=self._settings.realtime_stream_maxlen,
                    approximate=True,
                )
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise RealtimeUnavailableError("Redis Stream indisponível") from exc

    async def read_stream(
        self, *, after_id: str, block_ms: int = 1_000, count: int = 100
    ) -> tuple[str, list[dict[str, Any]]]:
        try:
            batches = await self._redis.xread(
                {self._settings.realtime_stream_key: after_id},
                block=block_ms,
                count=count,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise RealtimeUnavailableError("Redis Stream indisponível") from exc

        last_id = after_id
        envelopes: list[dict[str, Any]] = []
        for _stream, messages in batches:
            for message_id, fields in messages:
                last_id = str(message_id)
                raw = fields.get("envelope")
                if not isinstance(raw, str):
                    continue
                if (
                    len(raw.encode("utf-8"))
                    > self._settings.realtime_payload_max_bytes + 2_048
                ):
                    continue
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    envelopes.append(decoded)
        return last_id, envelopes
