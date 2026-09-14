"""Política de leitura resiliente de parâmetros: nunca propaga erro — cai
para um default seguro se o parâmetro não existir ou a coerção falhar. É o
que garante que o motor de adequação (pedidos) nunca quebra por causa de um
parâmetro ausente ou corrompido."""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.parametros.domain.coercao import _coerce

logger = logging.getLogger(__name__)


async def get_param_value[T](
    db: AsyncSession, chave: str, *, default: T, cast: Callable[[Any], T] = lambda v: v
) -> T:
    """Lê o valor de um parâmetro com fallback ao default; nunca propaga erro."""
    # Import tardio: get_parametro ainda mora em service.py até a Etapa 3 mover
    # o repositório para infrastructure/ — evita import circular (service.py
    # reexporta deste módulo).
    from app.modules.parametros.service import get_parametro

    try:
        param = await get_parametro(db, chave)
        if param is None:
            return default
        return cast(_coerce(param.valor, param.tipo))
    except Exception:
        logger.warning(
            "Falha ao ler parâmetro '%s'; usando default.", chave, exc_info=True
        )
        return default
