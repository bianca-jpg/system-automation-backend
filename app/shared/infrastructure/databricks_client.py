"""Cliente resiliente da Databricks SQL Statement Execution API.

O cliente continua expondo ``executar_consulta`` para os adaptadores de
ingestao, mas concentra aqui as garantias de transporte: deadline total,
timeout por request, retries limitados, coleta paginada com orcamento e
cancelamento remoto best-effort.

Nenhuma excecao ou log inclui SQL, token, URL pre-assinada ou corpo retornado
pelo provedor. Consumidores podem decidir se repetem a task consultando
``DatabricksError.retryable`` e ``DatabricksError.kind``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import random
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any, Final
from urllib.parse import parse_qsl, urlsplit

import httpx

from app.shared.config.settings import get_settings

logger = logging.getLogger(__name__)

_ESTADOS_OK: Final = {"SUCCEEDED"}
_ESTADOS_ERRO: Final = {"FAILED", "CANCELED", "CLOSED"}
_DISPOSITIONS: Final = {"INLINE", "EXTERNAL_LINKS"}
# Teto documentado do proprio Databricks para o campo byte_limit quando
# disposition=INLINE (a API rejeita com HTTP 400/INVALID_PARAMETER_VALUE
# qualquer valor fora de [1, 26_214_400] nesse modo). EXTERNAL_LINKS aceita
# ate 100 GiB, entao esse teto nunca deve ser aplicado a esse disposition.
_INLINE_BYTE_LIMIT_CEILING: Final = 26_214_400
_STATEMENT_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_INTERNAL_CHUNK_PATTERN: Final = re.compile(
    r"^/api/2\.0/sql/statements/([A-Za-z0-9-]{1,128})/result/chunks/[0-9]+$"
)
_INLINE_LIMIT_MARKERS: Final = (
    "INLINE_RESULT_TOO_LARGE",
    "INLINE_BYTE_LIMIT_EXCEEDED",
    "Inline byte limit exceeded",
)

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[float], float]


class DatabricksError(RuntimeError):
    """Falha sanitizada e classificavel ao consultar o Databricks."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code


class DatabricksConfigurationError(DatabricksError):
    def __init__(self, message: str) -> None:
        super().__init__(message, kind="configuration", retryable=False)


class DatabricksDeadlineExceeded(DatabricksError):
    def __init__(self) -> None:
        super().__init__(
            "A consulta ao Databricks excedeu o prazo total configurado.",
            kind="deadline",
            retryable=True,
        )


class DatabricksTimeoutError(DatabricksError):
    def __init__(self) -> None:
        super().__init__(
            "O Databricks nao respondeu dentro do timeout configurado.",
            kind="timeout",
            retryable=True,
        )


class DatabricksRateLimitError(DatabricksError):
    def __init__(self) -> None:
        super().__init__(
            "O Databricks limitou temporariamente as requisicoes.",
            kind="rate_limit",
            retryable=True,
            status_code=429,
        )


class DatabricksTransportError(DatabricksError):
    def __init__(self, *, status_code: int | None = None) -> None:
        super().__init__(
            "Falha transitoria na comunicacao com o Databricks.",
            kind="transport",
            retryable=True,
            status_code=status_code,
        )


class DatabricksAmbiguousSubmissionError(DatabricksError):
    """O submit pode ter chegado ao provider, mas nao retornou statement_id."""

    def __init__(self) -> None:
        super().__init__(
            "O envio da consulta teve resultado ambiguo e deve ser reconciliado pela task.",
            kind="ambiguous_submission",
            retryable=True,
        )


class DatabricksHTTPError(DatabricksError):
    def __init__(self, status_code: int) -> None:
        super().__init__(
            "O Databricks rejeitou a requisicao.",
            kind="http",
            retryable=False,
            status_code=status_code,
        )


class DatabricksProtocolError(DatabricksError):
    def __init__(
        self, message: str = "Resposta invalida recebida do Databricks."
    ) -> None:
        super().__init__(message, kind="protocol", retryable=False)


class DatabricksLimitError(DatabricksError):
    def __init__(self, resource: str) -> None:
        super().__init__(
            f"A resposta excedeu o limite configurado de {resource}.",
            kind="limit",
            retryable=False,
        )


class DatabricksStatementError(DatabricksError):
    def __init__(self, *, state: str, retryable: bool) -> None:
        super().__init__(
            f"A consulta terminou com estado {state}.",
            kind="statement",
            retryable=retryable,
        )
        self.state = state


@dataclass(frozen=True, slots=True)
class DatabricksClientConfig:
    """Configuracao imutavel e validada do transporte Databricks."""

    base_url: str
    token: str
    warehouse_id: str
    request_timeout_seconds: float = 30.0
    total_timeout_seconds: float = 900.0
    cancel_timeout_seconds: float = 3.0
    max_attempts: int = 4
    retry_base_seconds: float = 0.5
    retry_max_seconds: float = 8.0
    poll_interval_seconds: float = 2.0
    max_polls: int = 450
    max_chunks: int = 2_048
    max_rows: int = 1_500_000
    max_bytes: int = 536_870_912
    max_statement_bytes: int = 1_048_576
    external_host_suffixes: tuple[str, ...] = (
        ".blob.core.windows.net",
        ".dfs.core.windows.net",
    )

    def __post_init__(self) -> None:
        try:
            parsed_base = urlsplit(self.base_url)
            base_port = parsed_base.port
        except ValueError:
            raise ValueError("base_url do Databricks invalida") from None
        base_hostname = parsed_base.hostname.lower() if parsed_base.hostname else ""
        try:
            base_address = ipaddress.ip_address(base_hostname)
        except ValueError:
            base_address = None
        if (
            parsed_base.scheme != "https"
            or not base_hostname
            or parsed_base.username is not None
            or parsed_base.password is not None
            or parsed_base.query
            or parsed_base.fragment
            or parsed_base.path not in {"", "/"}
            or base_port not in {None, 443}
            or base_address is not None
            or base_hostname == "localhost"
            or base_hostname.endswith(".local")
            or len(base_hostname) > 253
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", base_hostname) is None
        ):
            raise ValueError("base_url do Databricks deve usar HTTPS")
        if not self.token.strip() or self.token != self.token.strip():
            raise ValueError("token do Databricks invalido")
        if re.fullmatch(r"[A-Za-z0-9-]{1,128}", self.warehouse_id) is None:
            raise ValueError("token e warehouse_id do Databricks sao obrigatorios")
        positive_values = (
            self.request_timeout_seconds,
            self.total_timeout_seconds,
            self.cancel_timeout_seconds,
            self.max_attempts,
            self.poll_interval_seconds,
            self.max_polls,
            self.max_chunks,
            self.max_rows,
            self.max_bytes,
            self.max_statement_bytes,
        )
        if any(value <= 0 for value in positive_values):
            raise ValueError("limites do cliente Databricks devem ser positivos")
        if self.retry_base_seconds < 0 or self.retry_max_seconds < 0:
            raise ValueError("intervalos de retry nao podem ser negativos")
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("retry_base_seconds nao pode exceder retry_max_seconds")
        if self.max_attempts > 1 and self.retry_max_seconds == 0:
            raise ValueError("retry_max_seconds deve ser positivo com retries ativos")
        if self.request_timeout_seconds > self.total_timeout_seconds:
            raise ValueError("timeout por request nao pode exceder o deadline total")
        bounded_values = (
            (self.request_timeout_seconds, 120),
            (self.total_timeout_seconds, 1_800),
            (self.cancel_timeout_seconds, 10),
            (self.max_attempts, 8),
            (self.retry_base_seconds, 30),
            (self.retry_max_seconds, 60),
            (self.poll_interval_seconds, 60),
            (self.max_polls, 3_600),
            (self.max_chunks, 10_000),
            (self.max_rows, 2_000_000),
            (self.max_bytes, 1_073_741_824),
            (self.max_statement_bytes, 16_777_216),
        )
        if any(value > maximum for value, maximum in bounded_values):
            raise ValueError("limite do cliente Databricks excede o teto seguro")
        if not self.external_host_suffixes or len(self.external_host_suffixes) > 16:
            raise ValueError("allowlist de hosts externos deve conter 1..16 sufixos")
        suffix_pattern = re.compile(r"^\.[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
        normalized_suffixes = tuple(
            suffix.lower() for suffix in self.external_host_suffixes
        )
        if len(set(normalized_suffixes)) != len(normalized_suffixes) or any(
            suffix_pattern.fullmatch(suffix) is None for suffix in normalized_suffixes
        ):
            raise ValueError("allowlist de hosts externos invalida")


@dataclass(slots=True)
class _ExecutionState:
    statement_id: str | None = None


@dataclass(slots=True)
class _ResponseBudget:
    max_chunks: int
    max_rows: int
    max_bytes: int
    chunks: int = 0
    rows: int = 0
    bytes: int = 0
    seen_chunks: set[str] = field(default_factory=set)
    seen_next_links: set[str] = field(default_factory=set)

    @property
    def remaining_bytes(self) -> int:
        return self.max_bytes - self.bytes

    def consume_bytes(self, amount: int) -> None:
        if amount < 0 or self.bytes + amount > self.max_bytes:
            raise DatabricksLimitError("bytes")
        self.bytes += amount

    def consume_rows(self, amount: int) -> None:
        if amount < 0 or self.rows + amount > self.max_rows:
            raise DatabricksLimitError("linhas")
        self.rows += amount

    def register_chunk(self, key: str) -> None:
        if key in self.seen_chunks:
            raise DatabricksProtocolError("Ciclo detectado nos chunks do Databricks.")
        if self.chunks >= self.max_chunks:
            raise DatabricksLimitError("chunks")
        self.seen_chunks.add(key)
        self.chunks += 1

    def register_next_link(self, link: str) -> None:
        if link in self.seen_next_links:
            raise DatabricksProtocolError("Ciclo detectado na paginacao do Databricks.")
        if len(self.seen_next_links) >= self.max_chunks:
            raise DatabricksLimitError("chunks")
        self.seen_next_links.add(link)


@dataclass(slots=True)
class _RetryableRequest(Exception):
    kind: str
    status_code: int | None = None
    retry_after_seconds: float | None = None


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _default_jitter(base_delay: float) -> float:
    return random.uniform(0.0, min(1.0, base_delay * 0.25))


def _statement_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("statement_id")
    if not isinstance(value, str) or _STATEMENT_ID_PATTERN.fullmatch(value) is None:
        raise DatabricksProtocolError("Resposta sem identificador de consulta valido.")
    return value


def _state(payload: Mapping[str, Any]) -> str:
    status = payload.get("status")
    value = status.get("state") if isinstance(status, Mapping) else None
    if not isinstance(value, str):
        raise DatabricksProtocolError("Resposta sem estado de consulta valido.")
    normalized = value.upper()
    if not re.fullmatch(r"[A-Z_]{2,32}", normalized):
        raise DatabricksProtocolError("Estado de consulta invalido.")
    return normalized


def _is_inline_limit(payload: Mapping[str, Any]) -> bool:
    status = payload.get("status")
    error = status.get("error") if isinstance(status, Mapping) else None
    if not isinstance(error, Mapping):
        return False
    code = error.get("error_code") or error.get("code")
    message = error.get("message")
    return any(
        marker == code or (isinstance(message, str) and marker in message)
        for marker in _INLINE_LIMIT_MARKERS
    )


def _manifest_is_truncated(payload: Mapping[str, Any]) -> bool:
    manifest = payload.get("manifest")
    return isinstance(manifest, Mapping) and manifest.get("truncated") is True


def _statement_error(state: str) -> DatabricksStatementError:
    return DatabricksStatementError(
        state=state,
        retryable=state in {"CANCELED", "CLOSED"},
    )


def _columns(payload: Mapping[str, Any]) -> list[str]:
    manifest = payload.get("manifest")
    schema = manifest.get("schema") if isinstance(manifest, Mapping) else None
    raw_columns = schema.get("columns") if isinstance(schema, Mapping) else None
    if not isinstance(raw_columns, list):
        raise DatabricksProtocolError("Manifesto sem colunas validas.")
    columns: list[str] = []
    for item in raw_columns:
        name = item.get("name") if isinstance(item, Mapping) else None
        if not isinstance(name, str) or not name or len(name) > 256:
            raise DatabricksProtocolError("Manifesto contem coluna invalida.")
        columns.append(name)
    if len(columns) != len(set(columns)):
        raise DatabricksProtocolError("Manifesto contem colunas duplicadas.")
    return columns


def _validate_manifest_budget(
    payload: Mapping[str, Any], budget: _ResponseBudget
) -> None:
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping):
        raise DatabricksProtocolError("Manifesto de resultado ausente.")
    if manifest.get("truncated") is True:
        raise DatabricksLimitError("dados; resultado truncado pelo provedor")
    total_rows = manifest.get("total_row_count")
    if isinstance(total_rows, int) and not isinstance(total_rows, bool):
        if total_rows < 0:
            raise DatabricksProtocolError("Manifesto contem total de linhas invalido.")
        if budget.rows + total_rows > budget.max_rows:
            raise DatabricksLimitError("linhas")


def _data_array(payload: object) -> object:
    if isinstance(payload, Mapping):
        if "data_array" in payload:
            return payload.get("data_array")
        result = payload.get("result")
        if isinstance(result, Mapping):
            return result.get("data_array")
    return payload


def _convert_rows(
    columns: Sequence[str], raw_rows: object, budget: _ResponseBudget
) -> list[dict[str, Any]]:
    if raw_rows is None:
        return []
    if not isinstance(raw_rows, list):
        raise DatabricksProtocolError("Chunk sem data_array valido.")
    budget.consume_rows(len(raw_rows))
    converted: list[dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise DatabricksProtocolError("Linha incompativel com o manifesto.")
        converted.append(dict(zip(columns, row, strict=True)))
    return converted


class DatabricksClient:
    """Executa statements SELECT com transporte bounded e cancelavel."""

    def __init__(
        self,
        config: DatabricksClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.monotonic,
        wall_clock: Clock = time.time,
        sleep: Sleep = asyncio.sleep,
        jitter: Jitter = _default_jitter,
    ) -> None:
        self._config = config
        self._transport = transport
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._jitter = jitter

    async def execute(
        self,
        statement: str,
        *,
        disposition: str = "INLINE",
        total_timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(statement, str) or not statement.strip():
            raise DatabricksConfigurationError(
                "A consulta Databricks nao pode ser vazia."
            )
        if len(statement.encode("utf-8")) > self._config.max_statement_bytes:
            raise DatabricksLimitError("bytes da consulta")
        normalized_disposition = disposition.upper()
        if normalized_disposition not in _DISPOSITIONS:
            raise DatabricksConfigurationError("Disposition Databricks invalida.")
        total_timeout = (
            self._config.total_timeout_seconds
            if total_timeout_seconds is None
            else total_timeout_seconds
        )
        if total_timeout <= 0:
            raise DatabricksConfigurationError(
                "O deadline Databricks deve ser positivo."
            )
        if total_timeout > self._config.total_timeout_seconds:
            raise DatabricksConfigurationError(
                "O deadline solicitado excede o limite configurado."
            )

        deadline = self._clock() + total_timeout
        state = _ExecutionState()
        budget = _ResponseBudget(
            max_chunks=self._config.max_chunks,
            max_rows=self._config.max_rows,
            max_bytes=self._config.max_bytes,
        )
        async with httpx.AsyncClient(
            base_url=self._config.base_url,
            headers={"Accept-Encoding": "identity"},
            timeout=None,
            # Redirects nunca sao seguidos automaticamente: isso impede que o
            # Bearer atravesse de host e que URLs externas burlem a allowlist.
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            try:
                async with asyncio.timeout(total_timeout):
                    rows = await self._execute_with_client(
                        client,
                        statement=statement,
                        disposition=normalized_disposition,
                        deadline=deadline,
                        state=state,
                        budget=budget,
                    )
            except asyncio.CancelledError:
                await self._cancel_shielded(client, state.statement_id)
                raise
            except TimeoutError:
                await self._cancel_shielded(client, state.statement_id)
                raise DatabricksDeadlineExceeded() from None
            except (DatabricksDeadlineExceeded, DatabricksTimeoutError):
                await self._cancel_shielded(client, state.statement_id)
                raise

        logger.info(
            "Databricks: %d linhas retornadas (disposition=%s).",
            len(rows),
            normalized_disposition,
        )
        return rows

    async def _execute_with_client(
        self,
        client: httpx.AsyncClient,
        *,
        statement: str,
        disposition: str,
        deadline: float,
        state: _ExecutionState,
        budget: _ResponseBudget,
    ) -> list[dict[str, Any]]:
        current_disposition = disposition
        while True:
            state.statement_id = None
            payload = await self._submit(
                client,
                statement=statement,
                disposition=current_disposition,
                deadline=deadline,
                budget=budget,
            )
            state.statement_id = _statement_id(payload)
            payload, terminal_state = await self._poll_until_terminal(
                client,
                payload=payload,
                statement_id=state.statement_id,
                deadline=deadline,
                budget=budget,
            )

            if terminal_state in _ESTADOS_ERRO:
                if current_disposition == "INLINE" and _is_inline_limit(payload):
                    logger.warning(
                        "Resultado INLINE excedeu o limite; repetindo em EXTERNAL_LINKS."
                    )
                    current_disposition = "EXTERNAL_LINKS"
                    continue
                raise _statement_error(terminal_state)

            if current_disposition == "INLINE" and _manifest_is_truncated(payload):
                logger.warning(
                    "Resultado INLINE truncado pelo provedor; "
                    "repetindo em EXTERNAL_LINKS."
                )
                current_disposition = "EXTERNAL_LINKS"
                continue

            _validate_manifest_budget(payload, budget)
            columns = _columns(payload)
            if current_disposition == "EXTERNAL_LINKS":
                return await self._collect_external_links(
                    client,
                    payload=payload,
                    statement_id=state.statement_id,
                    columns=columns,
                    deadline=deadline,
                    budget=budget,
                )
            return await self._collect_inline(
                client,
                payload=payload,
                statement_id=state.statement_id,
                columns=columns,
                deadline=deadline,
                budget=budget,
            )

    async def _submit(
        self,
        client: httpx.AsyncClient,
        *,
        statement: str,
        disposition: str,
        deadline: float,
        budget: _ResponseBudget,
    ) -> Mapping[str, Any]:
        # O provider tambem recebe o teto; se truncar, o manifesto e rejeitado
        # (ou, para INLINE, dispara o fallback para EXTERNAL_LINKS) e a
        # ingestao nunca publica um snapshot parcial. O teto de INLINE e
        # imposto pela propria API do Databricks (ver _INLINE_BYTE_LIMIT_CEILING)
        # e e independente do orcamento real de dados do cliente; nunca aplicar
        # esse teto a EXTERNAL_LINKS, que suporta resultados bem maiores.
        byte_limit = (
            min(self._config.max_bytes, _INLINE_BYTE_LIMIT_CEILING)
            if disposition == "INLINE"
            else self._config.max_bytes
        )
        body = {
            "warehouse_id": self._config.warehouse_id,
            "statement": statement,
            "format": "JSON_ARRAY",
            "disposition": disposition,
            "byte_limit": byte_limit,
            # Mantem a primeira resposta abaixo do timeout HTTP padrao e deixa
            # statements longos seguirem pelo polling explicitamente bounded.
            "wait_timeout": "10s",
        }
        payload = await self._request_json(
            client,
            "POST",
            "/api/2.0/sql/statements",
            deadline=deadline,
            budget=budget,
            authenticated=True,
            json_body=body,
            retry_ambiguous=False,
        )
        if not isinstance(payload, Mapping):
            raise DatabricksProtocolError()
        return payload

    async def _poll_until_terminal(
        self,
        client: httpx.AsyncClient,
        *,
        payload: Mapping[str, Any],
        statement_id: str,
        deadline: float,
        budget: _ResponseBudget,
    ) -> tuple[Mapping[str, Any], str]:
        current_state = _state(payload)
        polls = 0
        while current_state not in _ESTADOS_OK | _ESTADOS_ERRO:
            if polls >= self._config.max_polls:
                raise DatabricksDeadlineExceeded()
            await self._sleep_before_next_poll(deadline)
            polls += 1
            next_payload = await self._request_json(
                client,
                "GET",
                f"/api/2.0/sql/statements/{statement_id}",
                deadline=deadline,
                budget=budget,
                authenticated=True,
            )
            if not isinstance(next_payload, Mapping):
                raise DatabricksProtocolError()
            returned_id = next_payload.get("statement_id")
            if returned_id is not None and returned_id != statement_id:
                raise DatabricksProtocolError(
                    "A consulta mudou de identificador durante o poll."
                )
            payload = next_payload
            current_state = _state(payload)
        return payload, current_state

    async def _sleep_before_next_poll(self, deadline: float) -> None:
        delay = self._config.poll_interval_seconds
        if self._remaining(deadline) <= delay:
            raise DatabricksDeadlineExceeded()
        await self._sleep(delay)
        self._ensure_deadline(deadline)

    async def _collect_inline(
        self,
        client: httpx.AsyncClient,
        *,
        payload: Mapping[str, Any],
        statement_id: str,
        columns: Sequence[str],
        deadline: float,
        budget: _ResponseBudget,
    ) -> list[dict[str, Any]]:
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise DatabricksProtocolError("Resultado INLINE ausente.")
        budget.register_chunk("inline:initial")
        rows = _convert_rows(columns, result.get("data_array"), budget)
        next_link = result.get("next_chunk_internal_link")
        while next_link:
            next_link = self._validate_internal_link(next_link, statement_id)
            budget.register_next_link(next_link)
            budget.register_chunk(f"inline:{next_link}")
            chunk = await self._request_json(
                client,
                "GET",
                next_link,
                deadline=deadline,
                budget=budget,
                authenticated=True,
            )
            if not isinstance(chunk, Mapping):
                raise DatabricksProtocolError("Chunk INLINE invalido.")
            rows.extend(_convert_rows(columns, chunk.get("data_array"), budget))
            next_link = chunk.get("next_chunk_internal_link")
        return rows

    async def _collect_external_links(
        self,
        client: httpx.AsyncClient,
        *,
        payload: Mapping[str, Any],
        statement_id: str,
        columns: Sequence[str],
        deadline: float,
        budget: _ResponseBudget,
    ) -> list[dict[str, Any]]:
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise DatabricksProtocolError("Resultado EXTERNAL_LINKS ausente.")

        rows: list[dict[str, Any]] = []
        pages: deque[tuple[str, Mapping[str, Any]]] = deque(
            [("external:initial", result)]
        )
        scheduled_links: deque[str] = deque()

        while pages or scheduled_links:
            if scheduled_links:
                next_link = scheduled_links.popleft()
                page_payload = await self._request_json(
                    client,
                    "GET",
                    next_link,
                    deadline=deadline,
                    budget=budget,
                    authenticated=True,
                )
                if not isinstance(page_payload, Mapping):
                    raise DatabricksProtocolError("Pagina EXTERNAL_LINKS invalida.")
                page_result = page_payload.get("result", page_payload)
                if not isinstance(page_result, Mapping):
                    raise DatabricksProtocolError("Pagina EXTERNAL_LINKS invalida.")
                pages.append((f"external-page:{next_link}", page_result))

            page_key, page = pages.popleft()
            budget.register_chunk(page_key)
            raw_links = page.get("external_links")
            if raw_links is None:
                raw_links = []
            if not isinstance(raw_links, list):
                raise DatabricksProtocolError("Lista de chunks externos invalida.")
            if len(raw_links) > budget.max_chunks:
                raise DatabricksLimitError("chunks")

            for link in raw_links:
                if not isinstance(link, Mapping):
                    raise DatabricksProtocolError("Chunk externo invalido.")
                declared_bytes = link.get("byte_count")
                if (
                    isinstance(declared_bytes, int)
                    and declared_bytes > budget.remaining_bytes
                ):
                    raise DatabricksLimitError("bytes")
                declared_rows = link.get("row_count")
                if (
                    isinstance(declared_rows, int)
                    and budget.rows + declared_rows > budget.max_rows
                ):
                    raise DatabricksLimitError("linhas")

                external_url = link.get("external_link")
                if external_url is not None:
                    external_url = self._validate_external_url(external_url)
                    budget.register_chunk(f"external-data:{external_url}")
                    chunk_payload = await self._request_json(
                        client,
                        "GET",
                        external_url,
                        deadline=deadline,
                        budget=budget,
                        # URLs pre-assinadas nunca recebem o Bearer Databricks.
                        authenticated=False,
                    )
                    rows.extend(
                        _convert_rows(columns, _data_array(chunk_payload), budget)
                    )

                link_next = link.get("next_chunk_internal_link")
                if link_next is not None:
                    validated_link = self._validate_internal_link(
                        link_next, statement_id
                    )
                    budget.register_next_link(validated_link)
                    scheduled_links.append(validated_link)

            page_next = page.get("next_chunk_internal_link")
            if page_next is not None:
                validated_link = self._validate_internal_link(page_next, statement_id)
                budget.register_next_link(validated_link)
                scheduled_links.append(validated_link)

        return rows

    @staticmethod
    def _validate_internal_link(value: object, statement_id: str) -> str:
        if not isinstance(value, str) or len(value) > 2_048 or "\\" in value:
            raise DatabricksProtocolError("Link interno de paginacao invalido.")
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise DatabricksProtocolError(
                "Link interno de paginacao invalido."
            ) from None
        match = _INTERNAL_CHUNK_PATTERN.fullmatch(parsed.path)
        try:
            query_items = parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=2,
            )
        except ValueError:
            raise DatabricksProtocolError(
                "Link interno de paginacao invalido."
            ) from None
        query_is_valid = not query_items or (
            len(query_items) == 1
            and query_items[0][0] == "row_offset"
            and query_items[0][1].isdigit()
        )
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or match is None
            or match.group(1) != statement_id
            or not query_is_valid
        ):
            raise DatabricksProtocolError("Link interno de paginacao invalido.")
        return value

    def _validate_external_url(self, value: object) -> str:
        if not isinstance(value, str) or len(value) > 8_192 or "\\" in value:
            raise DatabricksProtocolError("URL de chunk externo invalida.")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise DatabricksProtocolError("URL de chunk externo invalida.") from None
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in {None, 443}
        ):
            raise DatabricksProtocolError("URL de chunk externo invalida.")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if (
            address is not None
            or hostname == "localhost"
            or hostname.endswith(".local")
        ):
            raise DatabricksProtocolError("Host de chunk externo nao permitido.")
        if not any(
            hostname.endswith(suffix.lower())
            and hostname != suffix.lower().removeprefix(".")
            for suffix in self._config.external_host_suffixes
        ):
            raise DatabricksProtocolError("Host de chunk externo nao permitido.")
        return value

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        deadline: float,
        budget: _ResponseBudget,
        authenticated: bool,
        json_body: Mapping[str, Any] | None = None,
        retry_ambiguous: bool = True,
    ) -> Any:
        last_failure: _RetryableRequest | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            self._ensure_deadline(deadline)
            try:
                return await self._request_once(
                    client,
                    method,
                    url,
                    deadline=deadline,
                    budget=budget,
                    authenticated=authenticated,
                    json_body=json_body,
                )
            except asyncio.CancelledError:
                raise
            except _RetryableRequest as failure:
                last_failure = failure
                # Sem statement_id, timeout/reset/5xx de um POST sao ambiguos:
                # o SELECT pode estar executando no provider. Nao criamos outro
                # statement cegamente; a task recebe uma classe retryable e
                # decide quando reconciliar/repetir. 429 e seguro para retry.
                if not retry_ambiguous and failure.status_code != 429:
                    raise DatabricksAmbiguousSubmissionError() from None
                if attempt >= self._config.max_attempts:
                    break
                delay = self._retry_delay(failure, attempt)
                if self._remaining(deadline) <= delay:
                    raise DatabricksDeadlineExceeded() from None
                logger.warning(
                    "Falha transitoria Databricks; retry %d/%d em %.2fs (kind=%s, status=%s).",
                    attempt,
                    self._config.max_attempts,
                    delay,
                    failure.kind,
                    failure.status_code,
                )
                await self._sleep(delay)

        if last_failure is None:
            raise DatabricksTransportError()
        if last_failure.kind == "timeout":
            raise DatabricksTimeoutError() from None
        if last_failure.status_code == 429:
            raise DatabricksRateLimitError() from None
        raise DatabricksTransportError(status_code=last_failure.status_code) from None

    async def _request_once(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        deadline: float,
        budget: _ResponseBudget,
        authenticated: bool,
        json_body: Mapping[str, Any] | None,
    ) -> Any:
        timeout_seconds = min(
            self._config.request_timeout_seconds,
            self._remaining(deadline),
        )
        if timeout_seconds <= 0:
            raise DatabricksDeadlineExceeded()
        headers = _headers(self._config.token) if authenticated else None
        try:
            async with asyncio.timeout(timeout_seconds):
                async with client.stream(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raise _RetryableRequest(
                            kind="rate_limit"
                            if response.status_code == 429
                            else "http_5xx",
                            status_code=response.status_code,
                            retry_after_seconds=self._parse_retry_after(
                                response.headers.get("Retry-After")
                            ),
                        )
                    if response.status_code < 200 or response.status_code >= 300:
                        raise DatabricksHTTPError(response.status_code)
                    content_encoding = response.headers.get(
                        "Content-Encoding", "identity"
                    )
                    if content_encoding.lower().strip() not in {"", "identity"}:
                        raise DatabricksProtocolError(
                            "Resposta comprimida nao permitida pelo contrato bounded."
                        )
                    content_length = response.headers.get("Content-Length")
                    if (
                        content_length
                        and content_length.isdigit()
                        and int(content_length) > budget.remaining_bytes
                    ):
                        raise DatabricksLimitError("bytes")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        budget.consume_bytes(len(chunk))
                        raw.extend(chunk)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise _RetryableRequest(kind="timeout") from None
        except httpx.TimeoutException:
            raise _RetryableRequest(kind="timeout") from None
        except httpx.TransportError:
            raise _RetryableRequest(kind="transport") from None

        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DatabricksProtocolError(
                "Resposta JSON invalida do Databricks."
            ) from None

    def _retry_delay(self, failure: _RetryableRequest, attempt: int) -> float:
        exponential = self._config.retry_base_seconds * (2 ** (attempt - 1))
        base = (
            failure.retry_after_seconds
            if failure.retry_after_seconds is not None
            else exponential
        )
        capped = min(self._config.retry_max_seconds, max(0.0, base))
        jitter = max(0.0, self._jitter(capped))
        return min(self._config.retry_max_seconds, capped + jitter)

    def _parse_retry_after(self, value: str | None) -> float | None:
        if not value:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                seconds = parsed.timestamp() - self._wall_clock()
            except (TypeError, ValueError, OverflowError):
                return None
        return min(self._config.retry_max_seconds, max(0.0, seconds))

    async def _cancel_best_effort(
        self, client: httpx.AsyncClient, statement_id: str | None
    ) -> None:
        if statement_id is None:
            return
        try:
            async with asyncio.timeout(self._config.cancel_timeout_seconds):
                async with client.stream(
                    "POST",
                    f"/api/2.0/sql/statements/{statement_id}/cancel",
                    headers=_headers(self._config.token),
                ):
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - compensacao nunca mascara erro original
            # O cancelamento e apenas uma compensacao: nunca mascara a falha
            # original e nunca inclui detalhes do provider no log.
            logger.warning("Falha no cancelamento remoto best-effort do Databricks.")

    async def _cancel_shielded(
        self, client: httpx.AsyncClient, statement_id: str | None
    ) -> None:
        """Da ao POST /cancel um budget proprio mesmo sob cancel do caller."""
        if statement_id is None:
            return
        cancel_task = asyncio.create_task(
            self._cancel_best_effort(client, statement_id)
        )
        try:
            await asyncio.shield(cancel_task)
        except asyncio.CancelledError:
            # Uma segunda solicitacao de cancelamento nao deixa uma task solta.
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
            raise

    def _remaining(self, deadline: float) -> float:
        return deadline - self._clock()

    def _ensure_deadline(self, deadline: float) -> None:
        if self._remaining(deadline) <= 0:
            raise DatabricksDeadlineExceeded()


def _config_from_settings() -> DatabricksClientConfig:
    settings = get_settings()
    if not settings.databricks_token:
        raise DatabricksConfigurationError(
            "DATABRICKS_TOKEN nao configurado; impossivel consultar o Databricks."
        )
    if not settings.databricks_server_hostname or not settings.databricks_http_path:
        raise DatabricksConfigurationError(
            "DATABRICKS_SERVER_HOSTNAME/HTTP_PATH nao configurados."
        )
    try:
        return DatabricksClientConfig(
            base_url=settings.databricks_base_url,
            token=settings.databricks_token,
            warehouse_id=settings.databricks_warehouse_id,
            request_timeout_seconds=settings.databricks_request_timeout_seconds,
            total_timeout_seconds=settings.databricks_total_timeout_seconds,
            cancel_timeout_seconds=settings.databricks_cancel_timeout_seconds,
            max_attempts=settings.databricks_max_attempts,
            retry_base_seconds=settings.databricks_retry_base_seconds,
            retry_max_seconds=settings.databricks_retry_max_seconds,
            poll_interval_seconds=settings.databricks_poll_interval_seconds,
            max_polls=settings.databricks_max_polls,
            max_chunks=settings.databricks_max_chunks,
            max_rows=settings.databricks_max_rows,
            max_bytes=settings.databricks_max_bytes,
            max_statement_bytes=settings.databricks_max_statement_bytes,
            external_host_suffixes=tuple(
                settings.databricks_external_host_suffixes_list
            ),
        )
    except ValueError:
        raise DatabricksConfigurationError(
            "Configuracao de transporte Databricks invalida."
        ) from None


async def executar_consulta(
    statement: str,
    *,
    disposition: str = "INLINE",
    timeout_s: float | None = None,
    total_timeout_s: float | None = None,
) -> list[dict[str, Any]]:
    """Executa um SELECT e devolve linhas JSON_ARRAY como dicionarios.

    ``timeout_s`` preserva o contrato legado de timeout por request. O deadline
    total independente vem de ``DATABRICKS_TOTAL_TIMEOUT_SECONDS`` e pode ser
    sobrescrito explicitamente por ``total_timeout_s``.
    """

    config = _config_from_settings()
    if timeout_s is not None:
        if timeout_s <= 0:
            raise DatabricksConfigurationError(
                "O timeout Databricks deve ser positivo."
            )
        config = replace(
            config,
            request_timeout_seconds=min(timeout_s, config.total_timeout_seconds),
        )
    client = DatabricksClient(config)
    return await client.execute(
        statement,
        disposition=disposition,
        total_timeout_seconds=total_timeout_s,
    )
