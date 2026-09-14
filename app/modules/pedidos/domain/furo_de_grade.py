"""Furo de grade (ALOC-04): verificação pura, chamada dentro do laço de
alocação por par, com o estoque já decrementado pelos pares de maior
prioridade processados antes dela nesta mesma passagem — por isso não pode
ser calculada fora do laço."""

from collections.abc import Mapping

from app.modules.pedidos.domain.value_objects import (
    get_tamanho_idx,
    montar_chave_estoque,
)


def tem_furo_de_grade(
    itens: list, estoque_local: Mapping[str, int], cd_prod_cor: str
) -> tuple[bool, str | None]:
    """True + motivo se algum tamanho estritamente interior à grade PEDIDA
    está com 0 reservável no estoque atual; caso contrário, (False, None).

    Considera só os itens com `qt_liquida > 0` — um item presente na lista com
    quantidade pedida zero (ou negativa) é tratado como se o tamanho não
    tivesse sido pedido, e nunca conta como interior. Entre os considerados,
    calcula `get_tamanho_idx(sg_tamanho)` e descarta os que derem 999
    (desconhecido): esse tamanho nunca define o menor/maior nem entra na
    checagem de interior, mesmo que "pareça" estar entre os extremos na lista
    de itens.

    Se sobrarem menos de 2 tamanhos reconhecidos, não existe posição
    "interior" possível e o resultado é sempre (False, None) — cobre tanto o
    caso de um único tamanho pedido quanto o de dois tamanhos pedidos
    (adjacentes ou não): com só 2 reconhecidos, ambos são extremos. Tamanho
    não pedido pelo cliente simplesmente não aparece em `itens`, então nunca
    é avaliado como interior nem como extremo — não precisa de tratamento
    especial.

    Com 2+ reconhecidos, um tamanho é "interior" apenas se seu idx for
    estritamente maior que o menor idx e estritamente menor que o maior idx
    (os extremos nunca contam, mesmo com estoque zerado). Os interiores são
    percorridos em ordem CRESCENTE de idx; o primeiro com
    `estoque_local.get(chave, 0) <= 0` decide o resultado — se houver mais de
    um interior zerado, o motivo relatado é sempre o do menor idx entre eles,
    de forma determinística entre execuções.
    """
    reconhecidos = [
        (get_tamanho_idx(item["sg_tamanho"]), item["sg_tamanho"])
        for item in itens
        if item["qt_liquida"] > 0
    ]
    reconhecidos = [(idx, tam) for idx, tam in reconhecidos if idx != 999]

    if len(reconhecidos) < 2:
        return False, None

    idx_min = min(idx for idx, _ in reconhecidos)
    idx_max = max(idx for idx, _ in reconhecidos)

    for idx, sg_tamanho in sorted(reconhecidos):
        if idx_min < idx < idx_max:
            chave = montar_chave_estoque(cd_prod_cor, sg_tamanho)
            if estoque_local.get(chave, 0) <= 0:
                return (
                    True,
                    f"Furo de grade: tamanho {sg_tamanho} sem estoque reservável",
                )

    return False, None


__all__ = ["tem_furo_de_grade"]
