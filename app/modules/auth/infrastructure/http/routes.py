import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth import service
from app.modules.auth.application.schemas import (
    AdminUserResponse,
    AdminUsersPageResponse,
    AuthResponse,
    AuthUserResponse,
    MicrosoftSsoRequest,
    RefreshTokenRequest,
    SetUserRolesRequest,
    SignOutResponse,
)
from app.modules.auth.domain.exceptions import (
    AutoExclusaoNaoPermitidaError,
    ContaSsoNaoAutorizadaError,
    MicrosoftSsoNaoConfiguradoError,
    PapeisInvalidosError,
    RevogacaoIndisponivelError,
    SessaoInvalidaError,
    TokenMicrosoftInvalidoError,
    UsuarioNaoEncontradoError,
)
from app.shared.database.session import get_db
from app.shared.security import CurrentUser, get_current_user, require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/sso/microsoft",
    response_model=AuthResponse,
    responses={
        401: {
            "description": "ID token da Microsoft inválido, expirado ou de outro tenant/app."
        },
        403: {"description": "ID token da Microsoft sem e-mail utilizável."},
        503: {"description": "Login via Microsoft não configurado neste ambiente."},
    },
)
async def sso_microsoft(
    body: MicrosoftSsoRequest, db: AsyncSession = Depends(get_db)
) -> AuthResponse:
    # Sem rate limit dual-window aqui (SEC-07 era para segredos adivinháveis,
    # como senha/OTP): o ID token é assinado pela Microsoft, não é adivinhável.
    try:
        return await service.sign_in_microsoft(db, body)
    except MicrosoftSsoNaoConfiguradoError as exc:
        raise HTTPException(
            status_code=503, detail="Microsoft SSO not configured"
        ) from exc
    except TokenMicrosoftInvalidoError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Microsoft token"
        ) from exc
    except ContaSsoNaoAutorizadaError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account not authorized"
        ) from exc


@router.post("/token/refresh", response_model=AuthResponse)
async def refresh_token(
    body: RefreshTokenRequest, db: AsyncSession = Depends(get_db)
) -> AuthResponse:
    try:
        return await service.refresh_token(db, body)
    except SessaoInvalidaError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from exc
    except UsuarioNaoEncontradoError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        ) from exc


@router.post(
    "/sign-out",
    response_model=SignOutResponse,
    responses={
        401: {"description": "Access token ausente ou inválido."},
        503: {"description": "Revogação temporariamente indisponível."},
    },
)
async def sign_out(
    current_user: CurrentUser = Depends(get_current_user),
) -> SignOutResponse:
    try:
        return await service.sign_out(current_user)
    except RevogacaoIndisponivelError as exc:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível encerrar a sessão agora. Tente novamente.",
            headers={"Retry-After": "30"},
        ) from exc


@router.get(
    "/users/me",
    response_model=AuthUserResponse,
    responses={404: {"description": "Conta não existe mais."}},
)
async def get_current_profile(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AuthUserResponse:
    """Perfil da própria conta — qualquer nível autenticado vê o seu.

    Sem `require_*`: ver o próprio nome e papel não é privilégio, e exigir um
    nível mínimo esconderia o perfil justamente de quem tem menos acesso.
    """
    try:
        return await service.obter_perfil_atual(db, user_id=current.id)
    except UsuarioNaoEncontradoError as exc:
        raise HTTPException(
            status_code=404,
            detail="Conta não encontrada.",
        ) from exc


@router.get(
    "/users",
    response_model=AdminUsersPageResponse,
    dependencies=[Depends(require_admin)],
)
async def list_users(
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 25,
    search: Annotated[str, Query(max_length=120)] = "",
    sort: Literal["id", "email", "role", "confirmedAt"] = "id",
    order: Literal["asc", "desc"] = "asc",
    db: AsyncSession = Depends(get_db),
) -> AdminUsersPageResponse:
    users, total = await service.list_users_page(
        db,
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        order=order,
    )
    return AdminUsersPageResponse(
        rows=[AdminUserResponse.model_validate(user) for user in users],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.put(
    "/users/{user_id}/roles",
    response_model=AdminUserResponse,
    dependencies=[Depends(require_admin)],
)
async def set_user_roles(
    user_id: int, body: SetUserRolesRequest, db: AsyncSession = Depends(get_db)
) -> AdminUserResponse:
    try:
        user = await service.set_user_roles(db, user_id=user_id, roles=body.roles)
    except PapeisInvalidosError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Papéis inválidos: {exc.papeis_invalidos}",
        ) from exc
    except UsuarioNaoEncontradoError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc
    return AdminUserResponse.model_validate(user)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_user(
    user_id: int,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        await service.delete_user(db, admin_id=admin.id, user_id=user_id)
    except AutoExclusaoNaoPermitidaError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é possível excluir a própria conta",
        ) from exc
    except UsuarioNaoEncontradoError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc
