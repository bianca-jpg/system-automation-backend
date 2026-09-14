"""Expressão SQL de crédito bloqueado, equivalente a domain.is_sem_credito."""

from __future__ import annotations


def _credito_bloqueado_sql(column: str) -> str:
    """Expressão SQL equivalente a ``domain.is_sem_credito``.

    ``translate`` remove os acentos portugueses sem depender da extensão
    ``unaccent`` no banco.
    """

    normalized = (
        f"translate(upper(coalesce({column}, '')), 'ÁÀÂÃÉÊÍÓÔÕÚÇ', 'AAAAEEIOOOUC')"
    )
    return (
        f"({normalized} LIKE '%SEM%CRED%' "
        f"OR {normalized} LIKE '%BLOQUE%' "
        f"OR {normalized} LIKE '%REPROV%')"
    )
