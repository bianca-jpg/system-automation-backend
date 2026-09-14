"""Conversão pura da grade interna de tamanhos para as colunas posicionais e1..e48 do layout Linx — sem I/O de banco/rede."""

import logging

logger = logging.getLogger(__name__)


def converter_grade_para_posicoes(
    grade: dict[str, int], referencia: dict[str, int], cd_prod_cor: str
) -> tuple[dict[str, int], list[str]]:
    """Converte a grade interna `{sg_tamanho: qtd}` de UM produto nas colunas
    posicionais `{"e1": qtd, ..., "e48": qtd}`, usando a referência
    `{sg_tamanho: nr_posicao}` já filtrada para aquele `cd_prod_cor`.

    Tamanho da grade sem posição na referência: nunca lança exceção, é
    ignorado e reportado no 2º elemento do retorno + aviso agregado por
    categoria (critério 5 do ROADMAP / D-04). Dois tamanhos mapeando pra mesma
    posição: o último processado (ordem de inserção do dict `grade`) vence,
    com aviso de conflito agregado por categoria (D-03).
    """
    posicoes = {f"e{n}": 0 for n in range(1, 49)}
    ignorados: list[str] = []
    quantidade_conflitos = 0
    ocupante_da_posicao: dict[int, str] = {}

    for sg_tamanho, qtd in grade.items():
        pos = referencia.get(sg_tamanho)
        if pos is None:
            ignorados.append(sg_tamanho)
            continue
        if pos in ocupante_da_posicao and ocupante_da_posicao[pos] != sg_tamanho:
            quantidade_conflitos += 1
        ocupante_da_posicao[pos] = sg_tamanho
        posicoes[f"e{pos}"] = qtd

    if ignorados:
        logger.warning(
            "grade_linx categoria=tamanho_sem_referencia quantidade=%d", len(ignorados)
        )
    if quantidade_conflitos:
        logger.warning(
            "grade_linx categoria=conflito_posicao quantidade=%d",
            quantidade_conflitos,
        )

    return posicoes, ignorados
