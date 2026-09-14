"""Monta a linha de saída no layout Linx (ordens_reserva_linx) de UM par (nr_pedido, cd_prod_cor) — função pura, sem I/O de banco/rede."""

from app.modules.pedidos.domain.grade_linx import converter_grade_para_posicoes


def montar_linha_linx(
    nr_pedido: int,
    cd_prod_cor: str,
    itens: list[dict],
    referencia_produto: dict[str, int],
    tipo: str,
) -> dict | None:
    """Monta o dict completo da linha Linx de UM par (nr_pedido, cd_prod_cor).

    Produto sem nenhuma posição cadastrada na referência (`referencia_produto`
    vazio): devolve `None` ANTES de chamar `converter_grade_para_posicoes` —
    nunca inferido a partir do 2º retorno (`ignorados`) daquela função (D-01).
    Grade parcialmente convertida (ao menos um tamanho COM posição) NÃO cai
    neste caso — segue normalmente, distinto de D-04 da Fase 2.

    Os totais contam SÓ os tamanhos que entraram na grade posicional, não a
    grade inteira (CR-01): `qtde_embalada` vem de `posicoes`, então a invariante
    `sum(e1..e48) == qtde_embalada` vale por construção — inclusive quando um
    tamanho é ignorado (sem posição na referência) ou perde um conflito de
    posição (é sobrescrito). `valor_embalado` soma o `vl_liquido` só dos tamanhos
    COM posição (não ignorados). `preco1` é `valor_embalado / qtde_embalada`
    arredondado a 2 casas, aceitando divergência de centavos na multiplicação
    inversa (D-03).

    `cd_prod_cor` sem `"|"` não lança exceção: o produto recebe o código
    inteiro e `cor_produto` vira `None` (split defensivo).

    Colunas sem fonte conhecida hoje (FILIAL, ROMANEIO, CAIXA, REPRESENTANTE,
    ENTREGA, PACKS, ITEM, ITEM_PEDIDO, ORDEM_PRODUCAO etc.) não aparecem no
    dict devolvido — nada é inventado (critério 5 do ROADMAP).
    """
    if not referencia_produto:
        return None

    grade: dict[str, int] = {}
    for item in itens:
        sg_tamanho = item["sg_tamanho"]
        grade[sg_tamanho] = grade.get(sg_tamanho, 0) + item["qt_liquida"]

    posicoes, ignorados = converter_grade_para_posicoes(
        grade, referencia_produto, cd_prod_cor
    )

    # CR-01: totais SÓ dos tamanhos que entraram na grade posicional. qtde vem de
    # `posicoes` (invariante sum(e1..e48) == qtde_embalada, robusto a ignorados e
    # a conflito de posição); valor soma o vl_liquido só dos tamanhos não
    # ignorados. Assim a linha nunca vai ao Linx com a grade divergindo do total.
    # (No caso patológico de conflito de posição — dado inválido de referência, já
    # avisado na ingestão — o valor pode super-contar o tamanho sobrescrito.)
    ignorados_set = set(ignorados)
    qtde_embalada = sum(posicoes.values())
    valor_embalado = round(
        sum(
            item["vl_liquido"]
            for item in itens
            if item["sg_tamanho"] not in ignorados_set
        ),
        2,
    )
    preco1 = round(valor_embalado / qtde_embalada, 2) if qtde_embalada else None

    produto, sep, cor_produto = cd_prod_cor.partition("|")
    if not sep:
        produto, cor_produto = cd_prod_cor, None

    meta = itens[0]
    nome_clifor = meta.get("client") or f"Cliente {nr_pedido}"

    return {
        "nr_pedido": nr_pedido,
        "cd_prod_cor": cd_prod_cor,
        "tipo": tipo,
        "nome_clifor": nome_clifor,
        "produto": produto,
        "cor_produto": cor_produto,
        "pedido": nr_pedido,
        "preco1": preco1,
        "valor_embalado": valor_embalado,
        "qtde_embalada": qtde_embalada,
        **posicoes,
    }
