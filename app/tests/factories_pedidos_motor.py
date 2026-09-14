"""Factories COMPARTILHADAS de item de pedido e estoque por canal do motor de
adequação. Usadas pelos planos 14-03 a 14-07 desta fase, distintas da `_item`
privada em `test_pedidos_motor.py` (que continua existindo até o plano 14-07
a substituir)."""

from app.modules.pedidos.domain.value_objects import CANAIS, montar_chave_estoque


def item_pedido(
    sg_tamanho: str,
    qt_liquida: int,
    vl_liquido: float,
    *,
    nr_pedido: int = 1,
    cd_prod_cor: str = "PROD1",
    canal: str = "Franquia",
    status_credito: str = "Com Credito",
    indica_blacklist: bool = False,
    **extra,
) -> dict:
    """Monta um item de pedido válido para o motor de adequação.

    `sg_tamanho`, `qt_liquida` e `vl_liquido` são obrigatórios; os demais
    campos têm default e podem ser sobrescritos por nome ou via `**extra`
    (mesmo padrão de override pontual de `_item` em test_pedidos_motor.py).
    `indica_blacklist` (260825-jhv): default `False` preserva o comportamento
    pré-existente de todo teste que não passa este parâmetro.
    """
    base = {
        "nr_pedido": nr_pedido,
        "cd_prod_cor": cd_prod_cor,
        "sg_tamanho": sg_tamanho,
        "qt_liquida": qt_liquida,
        "vl_liquido": vl_liquido,
        "status_credito": status_credito,
        "canal": canal,
        "indica_blacklist": indica_blacklist,
    }
    base.update(extra)
    return base


def estoque_produto(
    cd_prod_cor: str, quantidades_por_tamanho: dict[str, int]
) -> dict[str, int]:
    """Monta o dict achatado de estoque `{chave: quantidade}` de um único
    produto, usando `montar_chave_estoque` para nunca reformatar a chave
    manualmente."""
    return {
        montar_chave_estoque(cd_prod_cor, tamanho): quantidade
        for tamanho, quantidade in quantidades_por_tamanho.items()
    }


def estoque_por_canal(**por_canal: dict[str, int]) -> dict[str, dict[str, int]]:
    """Monta o dict de estoque por canal `{canal: {chave: quantidade}}`
    esperado por `processar_pedidos`, validando cada nome de canal contra
    `CANAIS`."""
    for canal in por_canal:
        if canal not in CANAIS:
            raise ValueError(
                f"canal inválido: {canal!r} — canais aceitos: {', '.join(CANAIS)}"
            )
    return por_canal


__all__ = ["item_pedido", "estoque_produto", "estoque_por_canal"]
