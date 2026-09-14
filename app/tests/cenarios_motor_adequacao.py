"""Cenário obrigatório de 14-VALIDATION.md: "Disputa da mantenedora".

Válido hoje contra o motor ATUAL no modo adequação. O plano 14-05 (quando o
modo sem-adequação existir) deve reexecutar o mesmo cenário nesse segundo modo.
"""

from app.tests.factories_pedidos_motor import (
    estoque_por_canal,
    estoque_produto,
    item_pedido,
)

NR_PEDIDO_CLIENTE_A = 101
NR_PEDIDO_CLIENTE_B = 202
PRODUTO_DISPUTADO = "PRODX_DISPUTADO"
TAMANHO_DISPUTADO = "M"
CANAL_CENARIO = "Franquia"
ESTOQUE_DISPUTADO = 20


def cenario_disputa_mantenedora() -> tuple[list[dict], dict[str, dict[str, int]]]:
    """Cliente A (pedido global de 1.000 pçs / R$ 50.000) disputa 10 peças do
    produto disputado com o cliente B (pedido global de 50 pçs / R$ 1.000, que
    quer 15 peças do mesmo produto). Estoque do produto disputado = 20.

    Resultado esperado: A atendido, B em stand by — o valor total do pedido
    de A (R$ 50.000) prioriza sobre o de B (R$ 1.000), mesmo que B peça mais
    peças do produto disputado isoladamente.
    """
    dados = [
        # Cliente A — linha disputada.
        item_pedido(
            TAMANHO_DISPUTADO,
            10,
            500.0,
            nr_pedido=NR_PEDIDO_CLIENTE_A,
            cd_prod_cor=PRODUTO_DISPUTADO,
            canal=CANAL_CENARIO,
            status_credito="Com Credito",
        ),
        # Cliente A — linha de preenchimento (materializa o pedido global de
        # 1000 pçs / R$ 50.000 ditado pela mantenedora).
        item_pedido(
            TAMANHO_DISPUTADO,
            990,
            49500.0,
            nr_pedido=NR_PEDIDO_CLIENTE_A,
            cd_prod_cor="PRODX_FILLER_A",
            canal=CANAL_CENARIO,
            status_credito="Com Credito",
        ),
        # Cliente B — linha disputada.
        item_pedido(
            TAMANHO_DISPUTADO,
            15,
            300.0,
            nr_pedido=NR_PEDIDO_CLIENTE_B,
            cd_prod_cor=PRODUTO_DISPUTADO,
            canal=CANAL_CENARIO,
            status_credito="Com Credito",
        ),
        # Cliente B — linha de preenchimento (materializa o pedido global de
        # 50 pçs / R$ 1.000 ditado pela mantenedora).
        item_pedido(
            TAMANHO_DISPUTADO,
            35,
            700.0,
            nr_pedido=NR_PEDIDO_CLIENTE_B,
            cd_prod_cor="PRODX_FILLER_B",
            canal=CANAL_CENARIO,
            status_credito="Com Credito",
        ),
    ]

    # Cada produto de preenchimento recebe estoque exatamente igual à
    # quantidade pedida por seu cliente, para não competir pelo estoque
    # disputado nem ficar ele mesmo em stand-by (o que inflaria ou
    # desinflaria artificialmente o valor do pedido usado na priorização).
    estoque = estoque_por_canal(
        **{
            CANAL_CENARIO: (
                estoque_produto(
                    PRODUTO_DISPUTADO, {TAMANHO_DISPUTADO: ESTOQUE_DISPUTADO}
                )
                | estoque_produto("PRODX_FILLER_A", {TAMANHO_DISPUTADO: 990})
                | estoque_produto("PRODX_FILLER_B", {TAMANHO_DISPUTADO: 35})
            )
        }
    )

    return dados, estoque


__all__ = [
    "cenario_disputa_mantenedora",
    "NR_PEDIDO_CLIENTE_A",
    "NR_PEDIDO_CLIENTE_B",
    "PRODUTO_DISPUTADO",
    "TAMANHO_DISPUTADO",
    "CANAL_CENARIO",
    "ESTOQUE_DISPUTADO",
]
