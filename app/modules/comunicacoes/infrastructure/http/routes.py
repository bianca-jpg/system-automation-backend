import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.comunicacoes import service
from app.modules.comunicacoes.application.casos_uso import IdempotencyConflictError
from app.modules.comunicacoes.application.schemas import (
    ComunicacaoResponse,
    ComunicacoesPageResponse,
    EnviarComunicacaoRequest,
)
from app.shared.config.settings import get_settings
from app.shared.database.session import get_db
from app.shared.infrastructure.rate_limit import (
    RateLimitUnavailableError,
    enforce_dual_fixed_window,
)
from app.shared.infrastructure.redis_client import get_redis
from app.shared.pagination.cursor import (
    CursorInvalidoError,
    decode_cursor,
    encode_cursor,
)
from app.shared.security import CurrentUser, require_actor, require_viewer

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/comunicacoes",
    tags=["Comunicacoes"],
    dependencies=[Depends(require_viewer)],
)

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100
_MAX_CURSOR_LENGTH = 512
_CURSOR_KIND = "communications"
_CURSOR_SCOPE = "created_at:desc,id:desc"
_SMTP_USER_RATE_PER_MINUTE = 5
_SMTP_IP_RATE_PER_MINUTE = 20
_RATE_LIMIT_UNAVAILABLE_RETRY_AFTER_SECONDS = 30


def _valid_cursor_key(key: tuple) -> bool:
    if (
        len(key) != 2
        or not isinstance(key[0], str)
        or not isinstance(key[1], str)
        or not 1 <= len(key[1]) <= 32
    ):
        return False
    try:
        return datetime.fromisoformat(key[0]).tzinfo is not None
    except ValueError:
        return False


@router.get(
    "",
    summary="Lista as comunicações recentes",
    response_model=ComunicacoesPageResponse,
)
async def listar_comunicacoes(
    db: Annotated[AsyncSession, Depends(get_db)],
    page_size: Annotated[
        int,
        Query(alias="pageSize", ge=1, le=_MAX_PAGE_SIZE),
    ] = _DEFAULT_PAGE_SIZE,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=_MAX_CURSOR_LENGTH),
    ] = None,
) -> ComunicacoesPageResponse:
    """Feed keyset recente, com desempate determinístico pelo identificador."""
    settings = get_settings()
    try:
        raw_cursor = decode_cursor(
            cursor,
            kind=_CURSOR_KIND,
            scope=_CURSOR_SCOPE,
            secret=settings.jwt_secret,
            key_size=2,
            key_validator=_valid_cursor_key,
        )
    except CursorInvalidoError as exc:
        raise HTTPException(status_code=422, detail="Cursor inválido.") from exc

    decoded_cursor = (
        (datetime.fromisoformat(raw_cursor[0]).astimezone(UTC), raw_cursor[1])
        if raw_cursor is not None
        else None
    )

    comunicacoes, total, has_more = await service.listar_comunicacoes(
        db,
        page_size=page_size,
        cursor=decoded_cursor,
    )
    next_cursor = (
        encode_cursor(
            kind=_CURSOR_KIND,
            scope=_CURSOR_SCOPE,
            key=[
                comunicacoes[-1].created_at.astimezone(UTC).isoformat(),
                comunicacoes[-1].id,
            ],
            secret=settings.jwt_secret,
        )
        if has_more and comunicacoes
        else None
    )
    return ComunicacoesPageResponse(
        rows=[ComunicacaoResponse.model_validate(item) for item in comunicacoes],
        total=total,
        page_size=page_size,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/{communication_id}",
    summary="Consulta uma comunicação pelo identificador",
    response_model=ComunicacaoResponse,
    response_model_exclude_none=True,
)
async def obter_comunicacao(
    communication_id: Annotated[str, Path(min_length=1, max_length=32)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ComunicacaoResponse:
    comunicacao = await service.obter_comunicacao(db, communication_id)
    if comunicacao is None:
        raise HTTPException(status_code=404, detail="Comunicação não encontrada.")
    return ComunicacaoResponse.model_validate(comunicacao)


@router.post(
    "",
    response_model=ComunicacaoResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Agenda um e-mail idempotente ao time comercial",
    responses={
        409: {"description": "Chave idempotente vinculada a outro payload."},
        429: {"description": "Limite de novas intenções excedido."},
        503: {"description": "Rate limit temporariamente indisponível."},
    },
)
async def enviar_comunicacao(
    body: EnviarComunicacaoRequest,
    request: Request,
    response: Response,
    actor: Annotated[CurrentUser, Depends(require_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
        ),
    ],
) -> ComunicacaoResponse:
    """
    Persiste a comunicação como pendente. O delivery outbox executa SMTP
    somente após o commit; replay da mesma chave devolve o mesmo registro.
    """
    try:
        replay_persistido = await service.idempotency_key_persistida(
            db, actor.id, idempotency_key
        )
        if not replay_persistido:
            client_ip = request.client.host if request.client else "unknown"
            settings = get_settings()
            try:
                rate = await enforce_dual_fixed_window(
                    get_redis(),
                    namespace="smtp",
                    user_identity=str(actor.id),
                    ip_identity=client_ip,
                    secret=settings.jwt_secret,
                    user_limit=_SMTP_USER_RATE_PER_MINUTE,
                    ip_limit=_SMTP_IP_RATE_PER_MINUTE,
                )
            except RateLimitUnavailableError as exc:
                cause = (
                    type(exc.__cause__).__name__
                    if exc.__cause__
                    else type(exc).__name__
                )
                logger.warning("Rate limit SMTP indisponível: %s", cause)
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Serviço de comunicação temporariamente indisponível. "
                        "Tente novamente mais tarde."
                    ),
                    headers={
                        "Retry-After": str(_RATE_LIMIT_UNAVAILABLE_RETRY_AFTER_SECONDS)
                    },
                ) from exc
            else:
                if not rate.allowed:
                    raise HTTPException(
                        status_code=429,
                        detail="Limite de envios excedido. Tente novamente mais tarde.",
                        headers={"Retry-After": str(rate.retry_after)},
                    )

        comunicacao, _created = await service.agendar_comunicacao(
            db,
            recipient=str(body.recipient),
            content=body.content,
            order_ref=body.order_ref,
            subject=body.subject,
            actor_id=actor.id,
            idempotency_key=idempotency_key,
        )
        await db.commit()
    except IdempotencyConflictError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    response.headers["Location"] = f"/api/v1/comunicacoes/{comunicacao.id}"
    return ComunicacaoResponse.model_validate(comunicacao)
