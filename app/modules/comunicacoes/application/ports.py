"""Portas da aplicação; nenhuma dependência de FastAPI, SQLAlchemy ou SMTP."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.modules.comunicacoes.domain.modelos import Comunicacao, EntregaEmail


class RepositorioComunicacoesPort(Protocol):
    async def listar(
        self,
        *,
        page_size: int,
        cursor: tuple[datetime, str] | None,
    ) -> tuple[list[Comunicacao], int, bool]: ...

    async def bloquear_idempotencia(self, actor_id: int, key: str) -> None: ...

    async def obter_por_idempotencia(
        self, actor_id: int, key: str
    ) -> Comunicacao | None: ...

    async def obter_por_id(self, communication_id: str) -> Comunicacao | None: ...

    async def criar_pendente(
        self,
        *,
        communication_id: str,
        delivery_id: str,
        content: str,
        recipient: str,
        subject: str,
        actor_id: int,
        idempotency_key: str,
        idempotency_fingerprint: str,
        message_id: str,
        max_attempts: int,
        created_at: datetime,
    ) -> Comunicacao: ...


class RepositorioEntregasPort(Protocol):
    async def reconciliar_processamentos_expirados(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[str]: ...

    async def reivindicar_proxima(
        self,
        *,
        claimed_by: str,
        now: datetime,
        lease_until: datetime,
    ) -> EntregaEmail | None: ...

    async def marcar_enviada(
        self,
        *,
        delivery_id: str,
        claimed_by: str,
        now: datetime,
    ) -> str | None: ...

    async def registrar_falha(
        self,
        *,
        delivery_id: str,
        claimed_by: str,
        now: datetime,
        error_code: str,
        terminal_status: str | None,
        next_attempt_at: datetime | None,
    ) -> tuple[str, str] | None: ...


class PublicadorRealtimePort(Protocol):
    async def registrar(
        self,
        *,
        event_type: str,
        communication_id: str,
    ) -> None: ...


class UnidadeTrabalhoPort(Protocol):
    async def commit(self) -> None: ...


class EntregadorEmailPort(Protocol):
    async def enviar(self, entrega: EntregaEmail) -> None: ...


class MetricasEntregaPort(Protocol):
    def reivindicada(self) -> None: ...

    def concluida(self, outcome: str, duration_seconds: float) -> None: ...


class FalhaEntregaEmail(RuntimeError):
    """Erro normalizado e seguro para persistência/log (sem texto do provider)."""

    def __init__(
        self,
        error_code: str,
        *,
        terminal_status: str | None,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code[:64]
        self.terminal_status = terminal_status


class FalhaTransitoriaEntrega(FalhaEntregaEmail):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code, terminal_status=None)


class FalhaTerminalEntrega(FalhaEntregaEmail):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code, terminal_status="failed")


class ResultadoIncertoEntrega(FalhaEntregaEmail):
    """O SMTP pode ter aceitado DATA; repetir automaticamente seria inseguro."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code, terminal_status="unknown")
