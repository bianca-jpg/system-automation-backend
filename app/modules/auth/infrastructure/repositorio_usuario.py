from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import AuthUser


async def buscar_por_email(session: AsyncSession, email: str) -> AuthUser | None:
    return await session.scalar(select(AuthUser).where(AuthUser.email == email))


async def buscar_por_id(session: AsyncSession, user_id: int) -> AuthUser | None:
    return await session.get(AuthUser, user_id)


async def listar_pagina(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    search: str,
    sort: str,
    order: str,
) -> tuple[list[AuthUser], int]:
    filters = []
    normalized_search = search.strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            or_(
                AuthUser.email.ilike(pattern),
                cast(AuthUser.roles, String).ilike(pattern),
            )
        )

    total_stmt = select(func.count()).select_from(AuthUser)
    rows_stmt = select(AuthUser)
    if filters:
        total_stmt = total_stmt.where(*filters)
        rows_stmt = rows_stmt.where(*filters)

    sort_columns = {
        "id": AuthUser.id,
        "email": AuthUser.email,
        "role": cast(AuthUser.roles, String),
        "confirmedAt": AuthUser.confirmed_at,
    }
    sort_column = sort_columns.get(sort, AuthUser.id)
    direction = sort_column.desc() if order == "desc" else sort_column.asc()
    rows_stmt = (
        rows_stmt.order_by(direction, AuthUser.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    total = int(await session.scalar(total_stmt) or 0)
    result = await session.execute(rows_stmt)
    return list(result.scalars().all()), total


async def atualizar_papeis(
    session: AsyncSession, user: AuthUser, roles: list[str]
) -> AuthUser:
    user.roles = roles
    await session.commit()
    await session.refresh(user)
    return user


async def excluir(session: AsyncSession, user: AuthUser) -> None:
    """Hard delete: revoga o acesso do usuário imediatamente (sem soft-delete)."""
    await session.delete(user)
    await session.commit()
