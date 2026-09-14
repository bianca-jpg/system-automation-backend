import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.modules.health.service import (
    FonteIntegracao,
    registrar_falha_integracao,
    registrar_sucesso_integracao,
)
from app.shared.database.session import check_database, check_database_schema
from app.shared.infrastructure.redis_client import check_redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CheckStatus = Literal["ok", "fail", "unavailable"]


@router.get("/health")
def health_liveness() -> dict[str, str]:
    return {"status": "ok"}


async def _run_check(
    nome: str,
    check: Callable[[], Awaitable[bool]],
    *,
    fonte: FonteIntegracao | None = None,
) -> CheckStatus:
    """Executa um check de dependência e devolve só o status resumido.

    O endpoint de readiness é público (probe do ECS / healthcheck do compose), então
    a identidade da exceção nunca entra na resposta: ela entregaria de graça a
    topologia interna das dependências para qualquer cliente que alcance a porta.
    O nome da classe fica registrado no log do servidor, no mesmo padrão adotado em
    ingestao/infrastructure/http/routes.py.

    `fonte` liga este check a um aviso de integração (D-01): só o check
    `database` passa uma fonte hoje — `databaseSchema` (migration atrasada,
    não conexão instável) e `redis` ficam fora do escopo travado.
    """
    try:
        await check()
    except Exception as exc:  # noqa: BLE001
        logger.error("Readiness check %s falhou: %s", nome, type(exc).__name__)
        if fonte is not None:
            await registrar_falha_integracao(fonte, detalhe_tecnico=type(exc).__name__)
        return "fail"
    if fonte is not None:
        await registrar_sucesso_integracao(fonte)
    return "ok"


@router.get("/health/ready")
async def health_readiness() -> JSONResponse:
    checks: dict[str, CheckStatus] = {}

    checks["database"] = await _run_check(
        "database", check_database, fonte=FonteIntegracao.BANCO_DE_DADOS
    )

    if checks["database"] == "ok":
        checks["databaseSchema"] = await _run_check(
            "databaseSchema", check_database_schema
        )
    else:
        checks["databaseSchema"] = "unavailable"

    checks["redis"] = await _run_check("redis", check_redis)

    todos_ok = all(valor == "ok" for valor in checks.values())
    body = {"status": "ok" if todos_ok else "degraded", "checks": checks}
    return JSONResponse(
        status_code=status.HTTP_200_OK
        if todos_ok
        else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=body,
    )
