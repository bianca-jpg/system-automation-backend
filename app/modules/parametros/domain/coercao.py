"""Regra pura de coerção de tipo: converte o valor armazenado (string
canônica) para o tipo declarado do parâmetro."""

import json
from typing import Any

from app.modules.parametros.infrastructure.models import ParametroTipo


def _coerce(valor: str, tipo: str) -> Any:
    """Converte o valor armazenado (string canônica) para o tipo declarado."""
    if tipo == ParametroTipo.FLOAT:
        return float(valor)
    if tipo == ParametroTipo.INT:
        return int(valor)
    if tipo == ParametroTipo.BOOL:
        return valor.strip().lower() in {"1", "true", "t", "yes", "sim"}
    if tipo == ParametroTipo.JSON:
        return json.loads(valor)
    return valor
