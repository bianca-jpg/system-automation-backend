from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from aiosmtplib import errors

from app.modules.comunicacoes.application.entregas import (
    processar_entregas_pendentes,
)
from app.modules.comunicacoes.application.ports import (
    FalhaTerminalEntrega,
    FalhaTransitoriaEntrega,
    ResultadoIncertoEntrega,
)
from app.modules.comunicacoes.domain.modelos import EntregaEmail, PoliticaEntrega
from app.modules.comunicacoes.infrastructure.email_sender import SmtpEmailSender
from app.shared.config.settings import Settings


def _delivery(*, attempt_count: int = 1, max_attempts: int = 5) -> EntregaEmail:
    return EntregaEmail(
        id="mail-123",
        recipient="destino@project.com",
        subject="Assunto",
        content="Conteúdo reservado",
        message_id="<stable@system-automation.project.com.br>",
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    )


@dataclass
class _Repository:
    deliveries: list[EntregaEmail] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    async def reconciliar_processamentos_expirados(
        self, *, now: datetime, limit: int
    ) -> list[str]:
        del now
        return self.expired[:limit]

    async def reivindicar_proxima(
        self, *, claimed_by: str, now: datetime, lease_until: datetime
    ) -> EntregaEmail | None:
        del claimed_by, now, lease_until
        return self.deliveries.pop(0) if self.deliveries else None

    async def marcar_enviada(
        self, *, delivery_id: str, claimed_by: str, now: datetime
    ) -> str | None:
        del claimed_by, now
        self.sent.append(delivery_id)
        return "comm-123"

    async def registrar_falha(
        self,
        *,
        delivery_id: str,
        claimed_by: str,
        now: datetime,
        error_code: str,
        terminal_status: str | None,
        next_attempt_at: datetime | None,
    ) -> tuple[str, str] | None:
        self.failures.append(
            {
                "delivery_id": delivery_id,
                "claimed_by": claimed_by,
                "now": now,
                "error_code": error_code,
                "terminal_status": terminal_status,
                "next_attempt_at": next_attempt_at,
            }
        )
        return "comm-123", terminal_status or "retry"


@dataclass
class _Uow:
    fail_on_commit: int | None = None
    commits: int = 0

    async def commit(self) -> None:
        self.commits += 1
        if self.commits == self.fail_on_commit:
            raise RuntimeError("commit failure")


@dataclass
class _Sender:
    error: Exception | None = None
    calls: list[EntregaEmail] = field(default_factory=list)

    async def enviar(self, entrega: EntregaEmail) -> None:
        self.calls.append(entrega)
        if self.error is not None:
            raise self.error


@dataclass
class _Realtime:
    events: list[tuple[str, str]] = field(default_factory=list)

    async def registrar(self, *, event_type: str, communication_id: str) -> None:
        self.events.append((event_type, communication_id))


@dataclass
class _Metrics:
    claims: int = 0
    outcomes: list[str] = field(default_factory=list)

    def reivindicada(self) -> None:
        self.claims += 1

    def concluida(self, outcome: str, duration_seconds: float) -> None:
        assert duration_seconds >= 0
        self.outcomes.append(outcome)


def _policy(max_attempts: int = 5) -> PoliticaEntrega:
    return PoliticaEntrega(
        max_attempts=max_attempts,
        lease_seconds=120,
        retry_base_seconds=10,
        retry_max_seconds=300,
        batch_size=5,
    )


async def test_claim_commit_falha_antes_de_qualquer_smtp():
    repository = _Repository(deliveries=[_delivery()])
    uow = _Uow(fail_on_commit=2)
    sender = _Sender()
    with pytest.raises(RuntimeError, match="commit failure"):
        await processar_entregas_pendentes(
            repository,
            uow,
            sender,
            _Realtime(),
            _Metrics(),
            policy=_policy(),
            worker_id="worker-1",
        )
    assert sender.calls == []
    assert repository.sent == []


async def test_smtp_aceito_transiciona_sent_e_publica_realtime():
    repository = _Repository(deliveries=[_delivery()])
    realtime = _Realtime()
    metrics = _Metrics()
    result = await processar_entregas_pendentes(
        repository,
        _Uow(),
        _Sender(),
        realtime,
        metrics,
        policy=_policy(),
        worker_id="worker-1",
    )
    assert result.sent == 1
    assert repository.sent == ["mail-123"]
    assert realtime.events == [("communication.sent", "comm-123")]
    assert metrics.outcomes == ["sent"]


async def test_falha_transitoria_agenda_backoff_sem_evento():
    repository = _Repository(deliveries=[_delivery(attempt_count=1)])
    realtime = _Realtime()
    result = await processar_entregas_pendentes(
        repository,
        _Uow(),
        _Sender(FalhaTransitoriaEntrega("smtp_response_4xx")),
        realtime,
        _Metrics(),
        policy=_policy(),
        worker_id="worker-1",
    )
    assert result.retried == 1
    assert repository.failures[0]["terminal_status"] is None
    assert repository.failures[0]["next_attempt_at"] > repository.failures[0]["now"]
    assert realtime.events == []


async def test_maximo_de_tentativas_vira_failed_e_evento_terminal():
    repository = _Repository(deliveries=[_delivery(attempt_count=5, max_attempts=5)])
    realtime = _Realtime()
    result = await processar_entregas_pendentes(
        repository,
        _Uow(),
        _Sender(FalhaTransitoriaEntrega("smtp_connect_timeout")),
        realtime,
        _Metrics(),
        policy=_policy(),
        worker_id="worker-1",
    )
    assert result.failed == 1
    assert repository.failures[0]["terminal_status"] == "failed"
    assert realtime.events == [("communication.failed", "comm-123")]


@pytest.mark.parametrize(
    ("error", "terminal_status", "event_type", "counter"),
    [
        (
            FalhaTerminalEntrega("smtp_response_5xx"),
            "failed",
            "communication.failed",
            "failed",
        ),
        (
            ResultadoIncertoEntrega("smtp_outcome_unknown"),
            "unknown",
            "communication.unknown",
            "unknown",
        ),
    ],
)
async def test_falhas_terminal_e_incerta_nao_sao_retentadas(
    error: Exception,
    terminal_status: str,
    event_type: str,
    counter: str,
):
    repository = _Repository(deliveries=[_delivery()])
    realtime = _Realtime()
    result = await processar_entregas_pendentes(
        repository,
        _Uow(),
        _Sender(error),
        realtime,
        _Metrics(),
        policy=_policy(),
        worker_id="worker-1",
    )
    assert getattr(result, counter) == 1
    assert repository.failures[0]["terminal_status"] == terminal_status
    assert repository.failures[0]["next_attempt_at"] is None
    assert realtime.events == [(event_type, "comm-123")]


async def test_exception_nao_classificada_vira_incerta_sem_vazar_pii(caplog):
    repository = _Repository(deliveries=[_delivery()])
    secret = "destino@project.com conteudo ultra secreto"
    result = await processar_entregas_pendentes(
        repository,
        _Uow(),
        _Sender(RuntimeError(secret)),
        _Realtime(),
        _Metrics(),
        policy=_policy(),
        worker_id="worker-1",
    )
    assert result.unknown == 1
    assert repository.failures[0]["error_code"] == "smtp_outcome_unknown"
    assert secret not in caplog.text


async def test_lease_expirado_vira_incerto_sem_novo_claim():
    repository = _Repository(expired=["comm-expired"])
    realtime = _Realtime()
    result = await processar_entregas_pendentes(
        repository,
        _Uow(),
        _Sender(),
        realtime,
        _Metrics(),
        policy=_policy(),
        worker_id="worker-1",
    )
    assert result.reconciled_unknown == 1
    assert realtime.events == [("communication.unknown", "comm-expired")]


def _smtp_settings(**overrides):
    values = {
        "smtp_configured": True,
        "effective_smtp_from": "origem@project.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "usuario",
        "smtp_password": "senha",
        "smtp_starttls": True,
        "smtp_timeout_seconds": 7,
    }
    values.update(overrides)
    return cast(Settings, SimpleNamespace(**values))


def _settings(**overrides: object) -> Settings:
    # pyright sintetiza o __init__ de Settings a partir dos campos do
    # modelo e não enxerga o __init__ real de BaseSettings (que aceita
    # _env_file); o cast do construtor para o Callable real resolve isso
    # sem afetar o comportamento em runtime.
    ctor = cast(Callable[..., Settings], Settings)
    return ctor(_env_file=None, **overrides)


async def test_smtp_adapter_envia_message_id_deterministico_e_timeout():
    with patch(
        "app.modules.comunicacoes.infrastructure.email_sender.aiosmtplib.send",
        new_callable=AsyncMock,
    ) as send:
        await SmtpEmailSender(_smtp_settings()).enviar(_delivery())
    assert send.await_args is not None
    message = send.await_args.args[0]
    assert message["Message-ID"] == "<stable@system-automation.project.com.br>"
    assert send.await_args.kwargs["timeout"] == 7


async def test_smtp_deadline_global_bloqueado_vira_incerto_sem_retry():
    async def blocked_send(*args, **kwargs):
        del args, kwargs
        await asyncio.Event().wait()

    repository = _Repository(deliveries=[_delivery()])
    realtime = _Realtime()
    started = perf_counter()
    with patch(
        "app.modules.comunicacoes.infrastructure.email_sender.aiosmtplib.send",
        new=blocked_send,
    ):
        result = await processar_entregas_pendentes(
            repository,
            _Uow(),
            SmtpEmailSender(_smtp_settings(smtp_timeout_seconds=0.01)),
            realtime,
            _Metrics(),
            policy=_policy(),
            worker_id="worker-1",
        )

    assert perf_counter() - started < 0.5
    assert result.unknown == 1
    assert result.retried == 0
    assert result.stale == 0
    assert repository.sent == []
    assert repository.failures[0]["terminal_status"] == "unknown"
    assert repository.failures[0]["next_attempt_at"] is None
    assert realtime.events == [("communication.unknown", "comm-123")]


def test_config_extrema_mantem_lease_acima_do_deadline_smtp_total():
    settings = _settings(
        SMTP_TIMEOUT_SECONDS=60,
        COMMUNICATION_DELIVERY_LEASE_SECONDS=90,
    )
    assert settings.smtp_timeout_seconds == 60
    assert settings.communication_delivery_lease_seconds == 90
    assert (
        settings.communication_delivery_lease_seconds
        >= settings.smtp_timeout_seconds + 30
    )


@pytest.mark.parametrize(
    ("provider_error", "expected_error"),
    [
        (errors.SMTPResponseException(451, "temporario"), FalhaTransitoriaEntrega),
        (errors.SMTPResponseException(550, "rejeitado"), FalhaTerminalEntrega),
        (errors.SMTPReadTimeoutError("incerto"), ResultadoIncertoEntrega),
        (errors.SMTPServerDisconnected("incerto"), ResultadoIncertoEntrega),
    ],
)
async def test_smtp_adapter_classifica_sem_expor_texto_do_provider(
    provider_error: Exception,
    expected_error: type[Exception],
):
    with (
        patch(
            "app.modules.comunicacoes.infrastructure.email_sender.aiosmtplib.send",
            new=AsyncMock(side_effect=provider_error),
        ),
        pytest.raises(expected_error) as raised,
    ):
        await SmtpEmailSender(_smtp_settings()).enviar(_delivery())
    assert "temporario" not in str(raised.value)
    assert "rejeitado" not in str(raised.value)


def test_application_nao_importa_framework_banco_smtp_ou_infrastructure():
    application_dir = (
        Path(__file__).resolve().parents[1] / "modules" / "comunicacoes" / "application"
    )
    forbidden = (
        "sqlalchemy",
        "fastapi",
        "aiosmtplib",
        "app.modules.comunicacoes.infrastructure",
        "app.modules.comunicacoes.email_service",
    )
    for path in application_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        imports.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            imported.startswith(prefix) for imported in imports for prefix in forbidden
        ), f"{path.name} viola a direção de dependência: {imports}"


def test_celery_inclui_worker_e_reconciliacao_periodica():
    from app.workers.celery_app import celery_app

    assert "app.workers.tasks.comunicacoes" in celery_app.conf.include
    schedule = celery_app.conf.beat_schedule["reconciliar-entregas-comunicacoes"]
    assert schedule["task"] == "app.workers.tasks.comunicacoes.processar_entregas"
    assert 5 <= float(schedule["schedule"]) <= 3_600
