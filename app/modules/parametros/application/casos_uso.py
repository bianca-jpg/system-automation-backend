from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.domain.roles import automationRole
from app.modules.parametros.infrastructure.models import ParametroChangeRequest
from app.modules.parametros.infrastructure.repositorio_change_request import (
    list_change_requests_page,
)
from app.shared.security import CurrentUser


async def listar_solicitacoes_pagina_com_escopo(
    db: AsyncSession,
    *,
    user: CurrentUser,
    status_filter: str | None,
    page: int,
    page_size: int,
    search: str,
    sort: str,
    order: str,
) -> tuple[list[ParametroChangeRequest], int]:
    requested_by = None if user.has_level(automationRole.ADMINISTRADOR) else user.id
    return await list_change_requests_page(
        db,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        requested_by=requested_by,
        search=search,
        sort=sort,
        order=order,
    )
