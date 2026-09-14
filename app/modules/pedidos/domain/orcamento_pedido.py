"""Ledger de orçamento ±5% do PEDIDO COMPLETO (ALOC-07, ALOC-08, ALOC-09).

Regra de negócio: a base do orçamento é a quantidade total do pedido inteiro
do cliente (todos os produtos), nunca a grade de um único produto isolado.
Existem DOIS orçamentos independentes — adição e corte, cada um
`floor(total_original × tolerancia)`, aritmética inteira (nunca
`float`/`ceil`) — e eles NÃO SE COMPENSAM: esgotar o orçamento de corte não
afeta o de adição, e vice-versa. `tolerancia` é o parâmetro de negócio
`tolerancia_adequacao`: obrigatório e sem default, pelo mesmo motivo dos
`consumido_previo_*` abaixo — um default aqui reintroduziria em silêncio o
teto fixo de 5% que este campo existe para eliminar. O acumulado consumido
em execuções anteriores (`consumido_previo_*`) é recebido como parâmetro
obrigatório, sem default algum: ler ORs anteriores no banco é I/O e pertence
à Phase 15, não a este domínio. Um valor default aqui (ex. `= 0`)
reintroduziria o double-spend em silêncio sempre que um chamador futuro
esquecesse de passar o consumido real — motivo pelo qual os 5 campos de
entrada são todos posicionais, sem default, e sua ausência resulta em
`TypeError` do próprio Python, não em um comportamento "aparentemente
correto" que esconde o esquecimento.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ["OrcamentoPedido"]


@dataclass(slots=True)
class OrcamentoPedido:
    """Ledger mutável do orçamento ±5% de um pedido completo.

    NÃO é `frozen`: `consumir_corte`/`consumir_adicao` mutam o restante
    incrementalmente ao longo do processamento do pedido (estado mutável por
    desenho, per 14-PATTERNS.md). O resto do código só enxerga o estado via
    `@property` (`restante_adicao`/`restante_corte`/`consumido_*_total`) —
    nunca escreve diretamente nos campos internos.
    """

    nr_pedido: int
    total_original: int
    consumido_previo_adicao: int
    consumido_previo_corte: int
    tolerancia: float
    _restante_adicao: int = field(init=False, repr=False)
    _restante_corte: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.total_original < 0:
            raise ValueError(
                f"total_original não pode ser negativo: {self.total_original!r}"
            )
        if self.consumido_previo_adicao < 0:
            raise ValueError(
                "consumido_previo_adicao não pode ser negativo: "
                f"{self.consumido_previo_adicao!r}"
            )
        if self.consumido_previo_corte < 0:
            raise ValueError(
                "consumido_previo_corte não pode ser negativo: "
                f"{self.consumido_previo_corte!r}"
            )
        if not (0.0 <= self.tolerancia <= 1.0):
            raise ValueError(
                f"tolerancia deve estar entre 0.0 e 1.0: {self.tolerancia!r}"
            )
        # floor inteiro, nunca ceil/float — ALOC-08.
        self._restante_adicao = max(
            0, self.limite_adicao - self.consumido_previo_adicao
        )
        self._restante_corte = max(0, self.limite_corte - self.consumido_previo_corte)

    @property
    def limite_adicao(self) -> int:
        """Teto de adição do pedido: floor(total_original × tolerancia),
        sempre `int` por floor (nunca ceil, nunca float) — ALOC-08."""
        return int(Decimal(str(self.tolerancia)) * self.total_original)

    @property
    def limite_corte(self) -> int:
        """Teto de corte do pedido: floor(total_original × tolerancia),
        sempre `int` por floor (nunca ceil, nunca float) — ALOC-08.

        Mesmo cálculo de `limite_adicao` — os dois orçamentos têm o mesmo
        teto nominal, mas são consumidos e rastreados de forma totalmente
        independente (ver `restante_adicao`/`restante_corte`)."""
        return int(Decimal(str(self.tolerancia)) * self.total_original)

    @property
    def restante_adicao(self) -> int:
        """Quanto ainda pode ser adicionado nesta execução, já descontado o
        `consumido_previo_adicao` e qualquer `consumir_adicao` já aplicado."""
        return self._restante_adicao

    @property
    def restante_corte(self) -> int:
        """Quanto ainda pode ser cortado nesta execução, já descontado o
        `consumido_previo_corte` e qualquer `consumir_corte` já aplicado."""
        return self._restante_corte

    @property
    def consumido_adicao_total(self) -> int:
        """Total efetivamente consumido de adição até agora (previo + nesta
        execução) — é o valor que uma PRÓXIMA construção do ledger (a
        próxima execução) deve receber como `consumido_previo_adicao`, para
        que o acumulado nunca reset (ALOC-09)."""
        return self.limite_adicao - self._restante_adicao

    @property
    def consumido_corte_total(self) -> int:
        """Total efetivamente consumido de corte até agora — mesma semântica
        de `consumido_adicao_total`, mas para o orçamento de corte."""
        return self.limite_corte - self._restante_corte

    def consumir_corte(self, quantidade_necessaria: int) -> bool:
        """Tudo-ou-nada: só decrementa se `quantidade_necessaria` couber
        inteiramente no restante. Se não couber, devolve `False` e NÃO
        decrementa nada — o chamador decide marcar o produto inteiro em
        stand-by, sem consumo parcial do orçamento de corte."""
        if quantidade_necessaria < 0:
            raise ValueError(
                f"quantidade_necessaria não pode ser negativa: {quantidade_necessaria!r}"
            )
        if quantidade_necessaria > self._restante_corte:
            return False
        self._restante_corte -= quantidade_necessaria
        return True

    def consumir_adicao(self, quantidade_desejada: int) -> int:
        """Parcial: devolve `min(quantidade_desejada, restante_adicao)`,
        decrementando exatamente esse tanto. Nunca levanta por exceder —
        satura em 0."""
        if quantidade_desejada < 0:
            raise ValueError(
                f"quantidade_desejada não pode ser negativa: {quantidade_desejada!r}"
            )
        usado = min(quantidade_desejada, self._restante_adicao)
        self._restante_adicao -= usado
        return usado
