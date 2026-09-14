def montar_assunto(subject: str | None, order_ref: str | None) -> str:
    """Porte fiel da regra que hoje vivia em routes.py: subject explícito >
    order_ref > default."""
    if subject:
        return subject
    if order_ref:
        return f"[automation OR] Comunicação Time Comercial — Pedido {order_ref}"
    return "[automation OR] Comunicação Time Comercial"
