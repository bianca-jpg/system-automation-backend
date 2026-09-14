from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.parametros.application.schemas import ParametroCreate, ParametroUpdate
from app.modules.parametros.domain.exceptions import (
    ParametroJaExisteError,
    ParametroNaoEncontradoError,
)
from app.modules.parametros.infrastructure.models import Parametro


async def list_parametros_page(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    search: str,
    sort: str,
    order: str,
) -> tuple[list[Parametro], int]:
    normalized_search = search.strip()
    filters = []
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            or_(
                Parametro.chave.ilike(pattern),
                Parametro.valor.ilike(pattern),
                cast(Parametro.tipo, String).ilike(pattern),
                Parametro.descricao.ilike(pattern),
            )
        )

    total_stmt = select(func.count()).select_from(Parametro)
    rows_stmt = select(Parametro)
    if filters:
        total_stmt = total_stmt.where(*filters)
        rows_stmt = rows_stmt.where(*filters)

    sort_columns = {
        "id": Parametro.id,
        "name": Parametro.chave,
        "type": cast(Parametro.tipo, String),
        "value": Parametro.valor,
        "updatedAt": Parametro.updated_at,
    }
    sort_column = sort_columns.get(sort, Parametro.chave)
    direction = sort_column.desc() if order == "desc" else sort_column.asc()
    rows_stmt = (
        rows_stmt.order_by(direction, Parametro.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    total = int(await db.scalar(total_stmt) or 0)
    result = await db.execute(rows_stmt)
    return list(result.scalars().all()), total


async def get_parametro(db: AsyncSession, chave: str) -> Parametro | None:
    return await db.scalar(select(Parametro).where(Parametro.chave == chave))


async def create_parametro(db: AsyncSession, data: ParametroCreate) -> Parametro:
    existing = await get_parametro(db, data.chave)
    if existing is not None:
        raise ParametroJaExisteError(data.chave)
    param = Parametro(
        chave=data.chave,
        valor=data.valor,
        tipo=str(data.tipo),
        descricao=data.descricao,
    )
    db.add(param)
    await db.commit()
    await db.refresh(param)
    return param


async def update_parametro(
    db: AsyncSession, chave: str, data: ParametroUpdate
) -> Parametro:
    param = await get_parametro(db, chave)
    if param is None:
        raise ParametroNaoEncontradoError()
    if data.valor is not None:
        param.valor = data.valor
    if data.tipo is not None:
        param.tipo = str(data.tipo)
    if data.descricao is not None:
        param.descricao = data.descricao
    await db.commit()
    await db.refresh(param)
    return param


async def delete_parametro(db: AsyncSession, chave: str) -> None:
    param = await get_parametro(db, chave)
    if param is None:
        raise ParametroNaoEncontradoError()
    await db.delete(param)
    await db.commit()
