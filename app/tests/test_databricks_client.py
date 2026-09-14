from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from app.shared.config.settings import Settings
from app.shared.infrastructure import databricks_client as databricks_module
from app.shared.infrastructure.databricks_client import (
    DatabricksAmbiguousSubmissionError,
    DatabricksClient,
    DatabricksClientConfig,
    DatabricksDeadlineExceeded,
    DatabricksHTTPError,
    DatabricksLimitError,
    DatabricksProtocolError,
    DatabricksTimeoutError,
    DatabricksTransportError,
    _ResponseBudget,
)

STATEMENT_ID = "abc-123"
SUBMIT_PATH = "/api/2.0/sql/statements"
STATEMENT_PATH = f"/api/2.0/sql/statements/{STATEMENT_ID}"
CANCEL_PATH = f"{STATEMENT_PATH}/cancel"
CHUNK_1 = f"{STATEMENT_PATH}/result/chunks/1"
CHUNK_2 = f"{STATEMENT_PATH}/result/chunks/2"


def _settings(**overrides: object) -> Settings:
    # pyright sintetiza o __init__ de Settings a partir dos campos do
    # modelo e não enxerga o __init__ real de BaseSettings (que aceita
    # _env_file); o cast do construtor para o Callable real resolve isso
    # sem afetar o comportamento em runtime.
    ctor = cast(Callable[..., Settings], Settings)
    return ctor(_env_file=None, **overrides)


@dataclass
class FakeTime:
    value: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def clock(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.value += delay


def config(**overrides: Any) -> DatabricksClientConfig:
    base = DatabricksClientConfig(
        base_url="https://workspace.azuredatabricks.net",
        token="token-super-secreto",
        warehouse_id="warehouse-1",
        request_timeout_seconds=1,
        total_timeout_seconds=30,
        cancel_timeout_seconds=0.2,
        max_attempts=3,
        retry_base_seconds=0.25,
        retry_max_seconds=2,
        poll_interval_seconds=0.1,
        max_polls=5,
        max_chunks=10,
        max_rows=100,
        max_bytes=200_000,
    )
    return replace(base, **overrides)


def pending(statement_id: str = STATEMENT_ID) -> dict[str, Any]:
    return {"statement_id": statement_id, "status": {"state": "PENDING"}}


def succeeded(
    rows: list[list[Any]] | None = None,
    *,
    statement_id: str = STATEMENT_ID,
    next_link: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"data_array": rows or []}
    if next_link is not None:
        result["next_chunk_internal_link"] = next_link
    return {
        "statement_id": statement_id,
        "status": {"state": "SUCCEEDED"},
        "manifest": {"schema": {"columns": [{"name": "value"}]}},
        "result": result,
    }


def external_succeeded(
    links: list[dict[str, Any]],
    *,
    statement_id: str = STATEMENT_ID,
    next_link: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"external_links": links}
    if next_link is not None:
        result["next_chunk_internal_link"] = next_link
    return {
        "statement_id": statement_id,
        "status": {"state": "SUCCEEDED"},
        "manifest": {"schema": {"columns": [{"name": "value"}]}},
        "result": result,
    }


def make_client(
    handler,
    *,
    fake_time: FakeTime | None = None,
    client_config: DatabricksClientConfig | None = None,
) -> DatabricksClient:
    fake_time = fake_time or FakeTime()
    return DatabricksClient(
        client_config or config(),
        transport=httpx.MockTransport(handler),
        clock=fake_time.clock,
        wall_clock=lambda: 0,
        sleep=fake_time.sleep,
        jitter=lambda _delay: 0,
    )


@pytest.mark.asyncio
async def test_retry_429_honra_retry_after_e_nao_vaza_payload(caplog) -> None:
    calls = 0
    fake_time = FakeTime()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "1.5"},
                json={"message": "SQL-SECRETO token-super-secreto"},
            )
        return httpx.Response(200, json=succeeded([["ok"]]))

    rows = await make_client(handler, fake_time=fake_time).execute("SELECT SEGREDO")

    assert rows == [{"value": "ok"}]
    assert calls == 2
    assert fake_time.sleeps == [1.5]
    assert "SEGREDO" not in caplog.text
    assert "token-super-secreto" not in caplog.text


@pytest.mark.asyncio
async def test_retry_after_e_jitter_nunca_excedem_teto() -> None:
    calls = 0
    fake_time = FakeTime()

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "99"})
        return httpx.Response(200, json=succeeded([["ok"]]))

    client = DatabricksClient(
        config(retry_max_seconds=2),
        transport=httpx.MockTransport(handler),
        clock=fake_time.clock,
        sleep=fake_time.sleep,
        jitter=lambda _delay: 1,
    )

    assert await client.execute("SELECT 1") == [{"value": "ok"}]
    assert fake_time.sleeps == [2]


@pytest.mark.asyncio
async def test_get_retry_transport_e_5xx_com_backoff_bounded() -> None:
    get_calls = 0
    fake_time = FakeTime()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_calls
        if request.method == "POST":
            return httpx.Response(200, json=pending())
        get_calls += 1
        if get_calls == 1:
            raise httpx.ReadError("connection reset", request=request)
        if get_calls == 2:
            return httpx.Response(503, json={"provider": "indisponivel"})
        return httpx.Response(200, json=succeeded([["ok"]]))

    rows = await make_client(handler, fake_time=fake_time).execute("SELECT 1")

    assert rows == [{"value": "ok"}]
    assert get_calls == 3
    # Poll + retry exponencial de 0.25 e 0.5 segundo.
    assert fake_time.sleeps == [0.1, 0.25, 0.5]


@pytest.mark.asyncio
async def test_submit_ambiguo_nao_cria_statement_duplicado() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("reset depois do submit", request=request)

    with pytest.raises(DatabricksAmbiguousSubmissionError) as captured:
        await make_client(handler).execute("SELECT 1")

    assert calls == 1
    assert captured.value.kind == "ambiguous_submission"
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_statement_tem_max_length_e_nunca_chega_ao_transporte() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=succeeded())

    with pytest.raises(DatabricksLimitError, match="consulta"):
        await make_client(
            handler,
            client_config=config(max_statement_bytes=8),
        ).execute("SELECT 123456789")

    assert calls == 0


@pytest.mark.asyncio
async def test_4xx_permanente_nao_faz_retry_e_sanitiza_erro(caplog) -> None:
    calls = 0
    secret = "cliente-fulano SQL SELECT * FROM segredo token-super-secreto"

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"message": secret})

    with pytest.raises(DatabricksHTTPError) as captured:
        await make_client(handler).execute("SELECT * FROM segredo")

    assert calls == 1
    assert captured.value.status_code == 400
    assert captured.value.retryable is False
    assert secret not in str(captured.value)
    assert "token-super-secreto" not in caplog.text


@pytest.mark.asyncio
async def test_get_lento_esgota_retries_e_cancela_statement_remoto() -> None:
    get_calls = 0
    cancel_seen = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_calls
        if request.url.path == CANCEL_PATH:
            cancel_seen.set()
            return httpx.Response(204)
        if request.method == "POST":
            return httpx.Response(200, json=pending())
        get_calls += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=succeeded([["tarde"]]))

    cfg = config(
        request_timeout_seconds=0.005,
        max_attempts=2,
        retry_base_seconds=0.001,
        retry_max_seconds=0.001,
        poll_interval_seconds=0.001,
    )
    with pytest.raises(DatabricksTimeoutError) as captured:
        await make_client(handler, client_config=cfg).execute("SELECT 1")

    assert captured.value.retryable is True
    assert get_calls == 2
    assert cancel_seen.is_set()


@pytest.mark.asyncio
async def test_deadline_total_cancela_sem_iniciar_poll_fora_do_budget() -> None:
    requests: list[str] = []
    fake_time = FakeTime()

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == CANCEL_PATH:
            return httpx.Response(204)
        return httpx.Response(200, json=pending())

    cfg = config(total_timeout_seconds=1, poll_interval_seconds=2)
    with pytest.raises(DatabricksDeadlineExceeded):
        await make_client(handler, fake_time=fake_time, client_config=cfg).execute(
            "SELECT 1"
        )

    assert requests == [SUBMIT_PATH, CANCEL_PATH]


@pytest.mark.asyncio
async def test_retry_after_nao_ultrapassa_deadline_total() -> None:
    requests: list[str] = []
    fake_time = FakeTime()

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == CANCEL_PATH:
            return httpx.Response(204)
        if request.method == "POST":
            return httpx.Response(200, json=pending())
        return httpx.Response(429, headers={"Retry-After": "10"})

    cfg = config(total_timeout_seconds=1, retry_max_seconds=2)
    with pytest.raises(DatabricksDeadlineExceeded):
        await make_client(handler, fake_time=fake_time, client_config=cfg).execute(
            "SELECT 1"
        )

    assert requests == [SUBMIT_PATH, STATEMENT_PATH, CANCEL_PATH]
    assert fake_time.sleeps == [0.1]


@pytest.mark.asyncio
async def test_max_polls_e_bounded_e_cancela_statement() -> None:
    requests: list[str] = []
    fake_time = FakeTime()

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == CANCEL_PATH:
            return httpx.Response(204)
        return httpx.Response(200, json=pending())

    cfg = config(max_polls=1)
    with pytest.raises(DatabricksDeadlineExceeded):
        await make_client(handler, fake_time=fake_time, client_config=cfg).execute(
            "SELECT 1"
        )

    assert requests.count(STATEMENT_PATH) == 1
    assert requests[-1] == CANCEL_PATH


@pytest.mark.asyncio
async def test_cancel_do_caller_aguarda_cancel_remoto_antes_de_propagar() -> None:
    poll_started = asyncio.Event()
    cancel_seen = asyncio.Event()
    never = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CANCEL_PATH:
            cancel_seen.set()
            return httpx.Response(204)
        if request.method == "POST":
            return httpx.Response(200, json=pending())
        poll_started.set()
        await never.wait()
        raise AssertionError("poll deveria ser cancelado")

    task = asyncio.create_task(make_client(handler).execute("SELECT 1"))
    await asyncio.wait_for(poll_started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel_seen.is_set()


@pytest.mark.asyncio
async def test_falha_do_cancel_nao_mascara_timeout_original() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CANCEL_PATH:
            raise httpx.ConnectError("cancel indisponivel", request=request)
        if request.method == "POST":
            return httpx.Response(200, json=pending())
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=succeeded([["tarde"]]))

    cfg = config(
        request_timeout_seconds=0.005,
        max_attempts=1,
        poll_interval_seconds=0.001,
    )
    with pytest.raises(DatabricksTimeoutError):
        await make_client(handler, client_config=cfg).execute("SELECT 1")


@pytest.mark.asyncio
async def test_detecta_ciclo_em_next_chunk_inline() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=succeeded([["a"]], next_link=CHUNK_1))
        return httpx.Response(
            200,
            json={"data_array": [["b"]], "next_chunk_internal_link": CHUNK_1},
        )

    with pytest.raises(DatabricksProtocolError, match="Ciclo"):
        await make_client(handler).execute("SELECT 1")


@pytest.mark.asyncio
async def test_limita_quantidade_de_chunks_unicos() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=succeeded([["a"]], next_link=CHUNK_1))
        return httpx.Response(
            200,
            json={"data_array": [["b"]], "next_chunk_internal_link": CHUNK_2},
        )

    with pytest.raises(DatabricksLimitError, match="chunks"):
        await make_client(handler, client_config=config(max_chunks=2)).execute(
            "SELECT 1"
        )


@pytest.mark.asyncio
async def test_limita_linhas_antes_de_materializar_dicionarios() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=succeeded([["a"], ["b"], ["c"]]))

    with pytest.raises(DatabricksLimitError, match="linhas"):
        await make_client(handler, client_config=config(max_rows=2)).execute("SELECT 1")


@pytest.mark.asyncio
async def test_manifesto_truncado_inline_repete_em_external_links() -> None:
    submits = 0
    external_url = "https://conta.blob.core.windows.net/chunk"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submits
        if request.method == "POST":
            submits += 1
            body = json.loads(request.content)
            if body["disposition"] == "INLINE":
                payload = succeeded([["a"]])
                payload["manifest"]["truncated"] = True
                return httpx.Response(200, json=payload)
            return httpx.Response(
                200,
                json=external_succeeded(
                    [{"external_link": external_url}], statement_id="external-1"
                ),
            )
        return httpx.Response(200, json=[["ok"]])

    rows = await make_client(handler).execute("SELECT 1")

    assert rows == [{"value": "ok"}]
    assert submits == 2


@pytest.mark.asyncio
async def test_manifesto_ainda_truncado_apos_external_links_levanta_erro() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            payload = (
                succeeded([["a"]])
                if body["disposition"] == "INLINE"
                else external_succeeded([])
            )
            payload["manifest"]["truncated"] = True
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=[["ok"]])

    with pytest.raises(DatabricksLimitError, match="truncado"):
        await make_client(handler).execute("SELECT 1")


@pytest.mark.asyncio
async def test_rejeita_total_de_linhas_oversize_no_manifesto() -> None:
    payload_oversize = succeeded([])
    payload_oversize["manifest"]["total_row_count"] = 101

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload_oversize)

    with pytest.raises(DatabricksLimitError, match="linhas"):
        await make_client(handler).execute("SELECT 1")


@pytest.mark.asyncio
async def test_streaming_interrompe_resposta_que_excede_max_bytes() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b'"x":"' + (b"a" * 500) + b'"}')

    with pytest.raises(DatabricksLimitError, match="bytes"):
        await make_client(handler, client_config=config(max_bytes=100)).execute(
            "SELECT 1"
        )


@pytest.mark.asyncio
async def test_rejeita_compressao_para_evitar_bomba_de_descompressao() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            content=gzip.compress(b"payload-nao-deve-ser-decodificado"),
        )

    with pytest.raises(DatabricksProtocolError, match="comprimida"):
        await make_client(handler).execute("SELECT 1")


@pytest.mark.asyncio
async def test_byte_count_externo_oversize_falha_antes_do_download() -> None:
    requests: list[httpx.Request] = []
    url = "https://conta.blob.core.windows.net/chunk"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=external_succeeded([{"external_link": url, "byte_count": 20_000}]),
        )

    with pytest.raises(DatabricksLimitError, match="bytes"):
        await make_client(handler, client_config=config(max_bytes=10_000)).execute(
            "SELECT 1", disposition="EXTERNAL_LINKS"
        )

    assert len(requests) == 1


@pytest.mark.parametrize(
    "malicious_link",
    [
        "//evil.example/api/2.0/sql/statements/x/result/chunks/1",
        "/api/2.0/token/list",
        "/api/2.0/sql/statements/outro/result/chunks/1",
        "/api/2.0/sql/statements/abc-123/result/chunks\\1",
        "/api/2.0/sql/statements/abc-123/result/chunks/1?evil=1",
        "/api/2.0/sql/statements/abc-123/result/chunks/1?row_offset=-1",
        "https://evil.example/api/2.0/sql/statements/abc-123/result/chunks/1",
    ],
)
@pytest.mark.asyncio
async def test_link_interno_malicioso_e_rejeitado_sem_request(
    malicious_link: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=succeeded(next_link=malicious_link))

    with pytest.raises(DatabricksProtocolError):
        await make_client(handler).execute("SELECT 1")

    assert len(requests) == 1
    assert requests[0].url.host == "workspace.azuredatabricks.net"


@pytest.mark.asyncio
async def test_link_interno_oficial_com_row_offset_e_aceito() -> None:
    next_link = f"{CHUNK_1}?row_offset=1"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["byte_limit"] == 200_000
            return httpx.Response(200, json=succeeded([["a"]], next_link=next_link))
        return httpx.Response(200, json={"data_array": [["b"]]})

    assert await make_client(handler).execute("SELECT 1") == [
        {"value": "a"},
        {"value": "b"},
    ]


@pytest.mark.asyncio
async def test_byte_limit_inline_e_clampado_ao_teto_da_api_mas_external_links_nao() -> (
    None
):
    submitted_byte_limits: dict[str, int] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            submitted_byte_limits[body["disposition"]] = body["byte_limit"]
            if body["disposition"] == "INLINE":
                payload = succeeded([["a"]])
                payload["manifest"]["truncated"] = True
                return httpx.Response(200, json=payload)
            return httpx.Response(
                200,
                json=external_succeeded(
                    [{"external_link": "https://conta.blob.core.windows.net/chunk"}],
                    statement_id="external-1",
                ),
            )
        return httpx.Response(200, json=[["ok"]])

    rows = await make_client(
        handler, client_config=config(max_bytes=200_000_000)
    ).execute("SELECT 1")

    assert rows == [{"value": "ok"}]
    # INLINE nunca pode exceder o teto documentado da API (26_214_400), mesmo
    # que o orcamento local configurado seja maior.
    assert submitted_byte_limits["INLINE"] == 26_214_400
    # EXTERNAL_LINKS recebe o orcamento real configurado, sem o clamp de INLINE.
    assert submitted_byte_limits["EXTERNAL_LINKS"] == 200_000_000


@pytest.mark.parametrize(
    "malicious_url",
    [
        "http://conta.blob.core.windows.net/chunk",
        "https://127.0.0.1/chunk",
        "https://localhost/chunk",
        "https://evil.example/chunk",
        "https://conta.blob.core.windows.net.evil.example/chunk",
        "https://user@conta.blob.core.windows.net/chunk",
        "https://conta.blob.core.windows.net/chunk#fragment",
    ],
)
@pytest.mark.asyncio
async def test_external_link_fora_da_allowlist_e_rejeitado_sem_request(
    malicious_url: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=external_succeeded([{"external_link": malicious_url}]),
        )

    with pytest.raises(DatabricksProtocolError):
        await make_client(handler).execute("SELECT 1", disposition="EXTERNAL_LINKS")

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_external_link_nao_recebe_bearer_e_redirect_nao_e_seguido() -> None:
    requests: list[httpx.Request] = []
    url = "https://conta.blob.core.windows.net/chunk?sig=segredo"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json=external_succeeded([{"external_link": url}]),
            )
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})

    with pytest.raises(DatabricksHTTPError) as captured:
        await make_client(handler).execute("SELECT 1", disposition="EXTERNAL_LINKS")

    assert captured.value.status_code == 302
    assert len(requests) == 2
    assert "authorization" in requests[0].headers
    assert "authorization" not in requests[1].headers


@pytest.mark.asyncio
async def test_external_link_valido_e_coletado_sem_bearer() -> None:
    url = "https://conta.dfs.core.windows.net/chunk?sig=segredo"
    external_authorization: str | None = "nao-observado"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal external_authorization
        if request.method == "POST":
            return httpx.Response(
                200,
                json=external_succeeded([{"external_link": url}]),
            )
        external_authorization = request.headers.get("Authorization")
        return httpx.Response(200, json=[["ok"]])

    rows = await make_client(handler).execute("SELECT 1", disposition="EXTERNAL_LINKS")

    assert rows == [{"value": "ok"}]
    assert external_authorization is None


@pytest.mark.asyncio
async def test_inline_limit_faz_fallback_uma_vez_no_mesmo_deadline() -> None:
    submits = 0
    external_url = "https://conta.blob.core.windows.net/chunk"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submits
        if request.method == "POST":
            submits += 1
            body = json.loads(request.content)
            if body["disposition"] == "INLINE":
                return httpx.Response(
                    200,
                    json={
                        "statement_id": "inline-1",
                        "status": {
                            "state": "FAILED",
                            "error": {"error_code": "INLINE_RESULT_TOO_LARGE"},
                        },
                    },
                )
            return httpx.Response(
                200,
                json=external_succeeded(
                    [{"external_link": external_url}], statement_id="external-1"
                ),
            )
        return httpx.Response(200, json=[["ok"]])

    rows = await make_client(handler).execute("SELECT 1")

    assert rows == [{"value": "ok"}]
    assert submits == 2


def test_default_comporta_fontes_reais_sem_alocar_milhoes_de_linhas() -> None:
    cfg = DatabricksClientConfig(
        base_url="https://workspace.azuredatabricks.net",
        token="token",
        warehouse_id="warehouse",
    )
    budget = _ResponseBudget(
        max_chunks=cfg.max_chunks,
        max_rows=cfg.max_rows,
        max_bytes=cfg.max_bytes,
    )

    # ERP observado: 675.717; referencia observada: 569.726.
    budget.consume_rows(675_717)
    assert cfg.max_rows == 1_500_000
    assert budget.rows == 675_717


def test_settings_validam_relacoes_e_allowlist_databricks() -> None:
    settings = _settings()
    assert settings.databricks_total_timeout_seconds == 900
    assert settings.databricks_max_rows == 1_500_000
    assert settings.databricks_max_statement_bytes == 1_048_576
    assert settings.databricks_external_host_suffixes_list == [
        ".blob.core.windows.net",
        ".dfs.core.windows.net",
    ]

    with pytest.raises(ValidationError, match="REQUEST_TIMEOUT"):
        _settings(
            DATABRICKS_REQUEST_TIMEOUT_SECONDS=10,
            DATABRICKS_TOTAL_TIMEOUT_SECONDS=5,
        )
    with pytest.raises(ValidationError, match="EXTERNAL_HOST_SUFFIXES"):
        _settings(
            DATABRICKS_EXTERNAL_HOST_SUFFIXES=".blob.core.windows.net,localhost",
        )


@pytest.mark.asyncio
async def test_wrapper_preserva_timeout_legado_e_expoe_deadline_explicito(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    base_config = config(total_timeout_seconds=900)

    class FakeClient:
        def __init__(self, client_config: DatabricksClientConfig) -> None:
            captured["request_timeout"] = client_config.request_timeout_seconds

        async def execute(self, statement: str, **kwargs: Any) -> list[dict[str, Any]]:
            captured["statement"] = statement
            captured.update(kwargs)
            return []

    monkeypatch.setattr(databricks_module, "_config_from_settings", lambda: base_config)
    monkeypatch.setattr(databricks_module, "DatabricksClient", FakeClient)

    assert (
        await databricks_module.executar_consulta(
            "SELECT 1",
            timeout_s=120,
            total_timeout_s=600,
        )
        == []
    )
    assert captured == {
        "request_timeout": 120,
        "statement": "SELECT 1",
        "disposition": "INLINE",
        "total_timeout_seconds": 600,
    }


@pytest.mark.asyncio
async def test_5xx_get_esgotado_expoe_classificacao_retryable_e_cancela() -> None:
    cancel_seen = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cancel_seen
        if request.url.path == CANCEL_PATH:
            cancel_seen = True
            return httpx.Response(204)
        if request.method == "POST":
            return httpx.Response(200, json=pending())
        return httpx.Response(502, json={"body": "nao deve aparecer"})

    with pytest.raises(DatabricksTransportError) as captured:
        await make_client(handler, client_config=config(max_attempts=2)).execute(
            "SELECT 1"
        )

    assert captured.value.retryable is True
    assert captured.value.status_code == 502
    # 5xx nao e timeout/cancel do caller; o statement segue reconciliavel pela task.
    assert cancel_seen is False
