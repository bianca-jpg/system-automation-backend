"""Portas da aplicação; nenhuma dependência de FastAPI, SQLAlchemy ou Redis."""

from __future__ import annotations

from typing import Protocol

from app.modules.health.domain.avisos import AvisoIntegracao, FonteIntegracao


class RepositorioAvisosIntegracaoPort(Protocol):
    async def obter(self, fonte: FonteIntegracao) -> AvisoIntegracao | None: ...

    async def salvar(self, aviso: AvisoIntegracao) -> None: ...

    async def listar(self) -> list[AvisoIntegracao]: ...
