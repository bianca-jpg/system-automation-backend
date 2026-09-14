import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import (
    InterfaceError,
    OperationalError,
)
from sqlalchemy.exc import (
    TimeoutError as SQLAlchemyTimeoutError,
)

logger = logging.getLogger(__name__)

# Erros de CONEXÃO do SQLAlchemy (D-01/PD-04) — deliberadamente não o
# `DBAPIError` genérico, que também engloba `IntegrityError`/`ProgrammingError`
# (violação de unique não é banco instável e geraria aviso falso).
_ERROS_DE_CONEXAO_SQLALCHEMY = (
    OperationalError,
    InterfaceError,
    SQLAlchemyTimeoutError,
)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "Excecao nao tratada em %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        if isinstance(exc, _ERROS_DE_CONEXAO_SQLALCHEMY):
            # Import deferido para quebrar ciclo (padrão já usado em
            # repositorio_consultas.py:118 e ARCHITECTURE.md).
            from app.modules.health.service import (
                FonteIntegracao,
                registrar_falha_integracao,
            )

            await registrar_falha_integracao(
                FonteIntegracao.BANCO_DE_DADOS, detalhe_tecnico=type(exc).__name__
            )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "type": exc.__class__.__name__},
        )
