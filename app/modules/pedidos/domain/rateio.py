"""Utilitário de rateio de maior resto (método Hamilton)."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")


def ratear_hamilton(total: Decimal, sizes: dict[str, int]) -> dict[str, Decimal]:
    """Rateia um total monetário entre tamanhos pelo método do maior resto (Hamilton).

    Divide `total` proporcionalmente à quantidade de cada tamanho em `sizes`,
    garantindo que a soma das partes bate exatamente com o total em centavos
    (sem drift): cada tamanho recebe o piso proporcional e os centavos que
    sobram vão para os tamanhos com maior fração decimal, com desempate
    determinístico por ordem alfabética do nome do tamanho. O sinal do total
    é preservado em todas as partes.
    """
    total_cents_signed = int((total * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    sign = -1 if total_cents_signed < 0 else 1
    total_cents = abs(total_cents_signed)
    total_qty = sum(sizes.values())
    allocations: list[tuple[str, int, Decimal]] = []
    base_total = 0
    for size, qty in sorted(sizes.items()):
        exact = Decimal(total_cents) * Decimal(qty) / Decimal(total_qty)
        base = int(exact.quantize(Decimal(1), rounding=ROUND_DOWN))
        allocations.append((size, base, exact - base))
        base_total += base
    remainder = total_cents - base_total
    winners = {
        size
        for size, _base, _fraction in sorted(
            allocations,
            key=lambda item: (-item[2], item[0]),
        )[:remainder]
    }
    return {
        size: (Decimal(sign * (base + int(size in winners))) / 100).quantize(_CENT)
        for size, base, _fraction in allocations
    }


def ratear_inteiro(total: int, sizes: dict[str, int]) -> dict[str, int]:
    """Rateia um total de **peças inteiras** entre tamanhos pelo método do maior resto.

    Irmão inteiro de `ratear_hamilton`: mesma ideia (piso proporcional + resto
    para as maiores frações, desempate alfabético pelo nome do tamanho), mas
    a unidade é peça, não centavo, e o retorno é `int`, não `Decimal`. Existe
    como função própria — em vez de reaproveitar `ratear_hamilton` fingindo
    que peças são centavos — porque isso obrigaria todo chamador a converter
    peça↔centavo e devolveria `Decimal` num contexto onde a unidade correta é
    inteiro; a duplicação de ~15 linhas é o preço de manter a semântica certa
    no tipo de retorno. Não "simplifique" isto de volta a uma chamada de
    `ratear_hamilton`.

    Garante soma exata: `sum(ratear_inteiro(total, sizes).values()) == total`
    para qualquer entrada válida. Usa `Decimal` para a fração exata (não
    `float`), para não introduzir drift.
    """
    if total < 0:
        raise ValueError("total não pode ser negativo")
    if not sizes:
        raise ValueError("sizes não pode ser vazio")
    total_weight = sum(sizes.values())
    if total_weight == 0:
        raise ValueError("soma dos pesos em sizes não pode ser zero")

    allocations: list[tuple[str, int, Decimal]] = []
    base_total = 0
    for size, weight in sorted(sizes.items()):
        exact = Decimal(total) * Decimal(weight) / Decimal(total_weight)
        base = int(exact.quantize(Decimal(1), rounding=ROUND_DOWN))
        allocations.append((size, base, exact - base))
        base_total += base
    remainder = total - base_total
    winners = {
        size
        for size, _base, _fraction in sorted(
            allocations,
            key=lambda item: (-item[2], item[0]),
        )[:remainder]
    }
    return {
        size: base + int(size in winners) for size, base, _fraction in allocations
    }


__all__ = ["ratear_hamilton", "ratear_inteiro"]
