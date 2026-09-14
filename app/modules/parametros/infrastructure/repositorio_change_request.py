from datetime import UTC, datetime

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.parametros.application.schemas import ChangeRequestCreate
from app.modules.parametros.domain.exceptions import (
    ChangeRequestJaRevisadoError,
    ChangeRequestNaoEncontradoError,
)
from app.modules.parametros.infrastructure.models import (
    ChangeRequestStatus,
    ParametroChangeRequest,
)


async def create_change_request(
    db: AsyncSession, *, requested_by: int, data: ChangeRequestCreate
) -> ParametroChangeRequest:
    request = ParametroChangeRequest(
        requested_by=requested_by,
        target_chave=data.target_chave,
        change_type=str(data.change_type),
        proposed_payload=data.proposed_payload,
        justification=data.justification,
        status=ChangeRequestStatus.PENDING,
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return request


async def list_change_requests_page(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    status_filter: str | None,
    requested_by: int | None,
    search: str,
    sort: str,
    order: str,
) -> tuple[list[ParametroChangeRequest], int]:
    filters = []
    if status_filter == "resolved":
        filters.append(ParametroChangeRequest.status != ChangeRequestStatus.PENDING)
    elif status_filter is not None:
        filters.append(ParametroChangeRequest.status == status_filter)
    if requested_by is not None:
        filters.append(ParametroChangeRequest.requested_by == requested_by)
    normalized_search = search.strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            or_(
                ParametroChangeRequest.target_chave.ilike(pattern),
                ParametroChangeRequest.justification.ilike(pattern),
                cast(ParametroChangeRequest.status, String).ilike(pattern),
                cast(ParametroChangeRequest.requested_by, String).ilike(pattern),
            )
        )

    total_stmt = select(func.count()).select_from(ParametroChangeRequest)
    rows_stmt = select(ParametroChangeRequest)
    if filters:
        total_stmt = total_stmt.where(*filters)
        rows_stmt = rows_stmt.where(*filters)

    sort_columns = {
        "id": ParametroChangeRequest.id,
        "createdAt": ParametroChangeRequest.created_at,
        "updatedAt": ParametroChangeRequest.updated_at,
        "parameter": ParametroChangeRequest.target_chave,
        "status": cast(ParametroChangeRequest.status, String),
    }
    sort_column = sort_columns.get(sort, ParametroChangeRequest.id)
    direction = sort_column.desc() if order == "desc" else sort_column.asc()
    rows_stmt = (
        rows_stmt.order_by(direction, ParametroChangeRequest.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    total = int(await db.scalar(total_stmt) or 0)
    result = await db.execute(rows_stmt)
    return list(result.scalars().all()), total


async def review_change_request(
    db: AsyncSession, *, request_id: int, reviewer_id: int, approve: bool
) -> ParametroChangeRequest:
    """Aprova/rejeita uma solicitação — apenas muda o status, nunca aplica o valor."""
    request = await db.get(ParametroChangeRequest, request_id)
    if request is None:
        raise ChangeRequestNaoEncontradoError()
    if request.status != ChangeRequestStatus.PENDING:
        raise ChangeRequestJaRevisadoError(request.status)
    request.status = (
        ChangeRequestStatus.APPROVED if approve else ChangeRequestStatus.REJECTED
    )
    request.reviewed_by = reviewer_id
    request.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(request)
    return request
