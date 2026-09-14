from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.parametros import service
from app.modules.parametros.application import casos_uso
from app.modules.parametros.application.schemas import (
    ChangeRequestCreate,
    ChangeRequestOut,
    ChangeRequestReview,
    ChangeRequestsPageOut,
    ParametroCreate,
    ParametroOut,
    ParametrosPageOut,
    ParametroUpdate,
)
from app.modules.parametros.domain.exceptions import (
    ChangeRequestJaRevisadoError,
    ChangeRequestNaoEncontradoError,
    ParametroJaExisteError,
    ParametroNaoEncontradoError,
)
from app.shared.database.session import get_db
from app.shared.security import (
    CurrentUser,
    require_admin,
    require_gestor,
    require_viewer,
)

router = APIRouter(
    prefix="/api/v1/parametros",
    tags=["Parametros"],
    dependencies=[Depends(require_viewer)],
)


# --------------------------------------------------------------------------
# CRUD direto de parâmetros (administrador+)
# --------------------------------------------------------------------------


@router.get("", response_model=ParametrosPageOut)
async def listar_parametros(
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 25,
    search: Annotated[str, Query(max_length=120)] = "",
    sort: Literal["id", "name", "type", "value", "updatedAt"] = "name",
    order: Literal["asc", "desc"] = "asc",
    db: AsyncSession = Depends(get_db),
) -> ParametrosPageOut:
    params, total = await service.list_parametros_page(
        db,
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        order=order,
    )
    return ParametrosPageOut(
        rows=[ParametroOut.model_validate(param) for param in params],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.post(
    "",
    response_model=ParametroOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def criar_parametro(
    body: ParametroCreate, db: AsyncSession = Depends(get_db)
) -> ParametroOut:
    try:
        param = await service.create_parametro(db, body)
    except ParametroJaExisteError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Parâmetro '{exc.chave}' já existe.",
        ) from exc
    return ParametroOut.model_validate(param)


@router.put(
    "/{chave}",
    response_model=ParametroOut,
    dependencies=[Depends(require_admin)],
)
async def atualizar_parametro(
    chave: str, body: ParametroUpdate, db: AsyncSession = Depends(get_db)
) -> ParametroOut:
    try:
        param = await service.update_parametro(db, chave, body)
    except ParametroNaoEncontradoError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parâmetro não encontrado."
        ) from exc
    return ParametroOut.model_validate(param)


@router.delete(
    "/{chave}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def excluir_parametro(chave: str, db: AsyncSession = Depends(get_db)) -> None:
    try:
        await service.delete_parametro(db, chave)
    except ParametroNaoEncontradoError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parâmetro não encontrado."
        ) from exc


# --------------------------------------------------------------------------
# Solicitações de alteração (gestor cria, administrador aprova/rejeita)
# --------------------------------------------------------------------------


@router.post(
    "/change-requests",
    response_model=ChangeRequestOut,
    status_code=status.HTTP_201_CREATED,
)
async def criar_solicitacao(
    body: ChangeRequestCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_gestor),
) -> ChangeRequestOut:
    request = await service.create_change_request(db, requested_by=user.id, data=body)
    return ChangeRequestOut.model_validate(request)


@router.get("/change-requests", response_model=ChangeRequestsPageOut)
async def listar_solicitacoes(
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 25,
    search: Annotated[str, Query(max_length=120)] = "",
    sort: Literal["id", "createdAt", "updatedAt", "parameter", "status"] = "id",
    order: Literal["asc", "desc"] = "desc",
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_viewer),
    status_filter: Annotated[
        Literal["pending", "approved", "rejected", "resolved"] | None,
        Query(alias="statusFilter"),
    ] = None,
) -> ChangeRequestsPageOut:
    requests, total = await casos_uso.listar_solicitacoes_pagina_com_escopo(
        db,
        user=user,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        order=order,
    )
    return ChangeRequestsPageOut(
        rows=[ChangeRequestOut.model_validate(request) for request in requests],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.post(
    "/change-requests/{request_id}/approve",
    response_model=ChangeRequestOut,
)
async def aprovar_solicitacao(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    _body: ChangeRequestReview | None = None,
) -> ChangeRequestOut:
    try:
        request = await service.review_change_request(
            db, request_id=request_id, reviewer_id=user.id, approve=True
        )
    except ChangeRequestNaoEncontradoError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Solicitação não encontrada."
        ) from exc
    except ChangeRequestJaRevisadoError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solicitação já está '{exc.status_atual}'.",
        ) from exc
    return ChangeRequestOut.model_validate(request)


@router.post(
    "/change-requests/{request_id}/reject",
    response_model=ChangeRequestOut,
)
async def rejeitar_solicitacao(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
    _body: ChangeRequestReview | None = None,
) -> ChangeRequestOut:
    try:
        request = await service.review_change_request(
            db, request_id=request_id, reviewer_id=user.id, approve=False
        )
    except ChangeRequestNaoEncontradoError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Solicitação não encontrada."
        ) from exc
    except ChangeRequestJaRevisadoError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solicitação já está '{exc.status_atual}'.",
        ) from exc
    return ChangeRequestOut.model_validate(request)
