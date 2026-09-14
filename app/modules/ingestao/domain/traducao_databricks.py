"""Anti-corruption layer: traduz o vocabulário bruto do Databricks (strings,
None) para os tipos e o vocabulário do domínio (Canal, números, texto)."""

from datetime import date, datetime


def _parse_int(v) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v or "").strip()
    if not s:
        return 0
    try:
        return int(float(s.replace(",", ".")))
    except ValueError:
        return 0


def _parse_posicao(v) -> int | None:
    """Valida nr_posicao na faixa 1..48. Devolve None (nunca 0) para descarte:
    0 seria uma posição real inválida e mascararia o descarte — o chamador
    (agregacao.py) usa `is None` para decidir se a linha é descartada (D-01)."""
    n = _parse_int(v)
    return n if 1 <= n <= 48 else None


def _parse_data(v) -> date | None:
    """dt_estoque -> date. Chega como string via JSON_ARRAY ('2026-08-05' ou ISO
    com hora); o que não parsear vira None e o chamador decide o que fazer.
    `datetime` é testado antes de `date` porque é subclasse dele."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _parse_float(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return 0.0
    s = s.replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _normalizar_canal(canal: str | None) -> str | None:
    """ds_tp_canal -> 'Franquia' | 'Multimarca' (o que não bater vira None)."""
    if not canal:
        return None
    c = str(canal).strip().upper()
    if c.startswith("FRANQUIA") or c.startswith("FRQ"):
        return "Franquia"
    if c.startswith("MULTIMARCA") or c.startswith("MM"):
        return "Multimarca"
    return None


def _texto(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _parse_bool(v) -> bool:
    """Flags do Databricks chegam como string ('true'/'false') ou None."""
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("true", "t", "1", "sim", "s")
