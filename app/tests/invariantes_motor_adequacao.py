"""Helpers de asserção para as invariantes I1-I9 de 14-VALIDATION.md.

Reutilizados pelos testes de propriedade da Phase 19. Cada função levanta
`AssertionError` com mensagem prefixada "I{n} violada: " quando a invariante
correspondente não vale, e não retorna nada quando vale.
"""

from decimal import Decimal

from app.modules.pedidos.domain.motor_adequacao import is_sem_credito
from app.modules.pedidos.domain.value_objects import (
    get_tamanho_idx,
    montar_chave_estoque,
)

_CHAVES_CONTRATO_RESULTADO = {
    "resultados",
    "selecionados",
    "preteridos",
    "bloqueados_credito",
    "pares_processados",
}


def assert_estoque_nunca_excedido(
    estoque_inicial: dict[str, int], resultado: dict
) -> None:
    """I1: a soma reservada de um (produto, tamanho) nunca excede o estoque
    disponível daquele canal (ALOC-02, ALOC-03)."""
    reservado_por_chave: dict[str, int] = {}
    for itens in resultado["resultados"].values():
        for item in itens:
            if item.get("status_item") != "Gerar OR":
                continue
            chave = montar_chave_estoque(item["cd_prod_cor"], item["sg_tamanho"])
            reservado_por_chave[chave] = (
                reservado_por_chave.get(chave, 0) + item["qt_liquida"]
            )

    for chave, total_reservado in reservado_por_chave.items():
        if total_reservado <= 0:
            continue
        disponivel = estoque_inicial.get(chave, 0)
        if total_reservado > disponivel:
            raise AssertionError(
                f"I1 violada: chave {chave!r} reservou {total_reservado} mas o "
                f"estoque inicial era {disponivel}"
            )


def assert_sem_credito_nunca_alocado(dados: list[dict], resultado: dict) -> None:
    """I2: pedido sem crédito nunca aparece em selecionados nem em
    pares_processados, e não decrementa estoque (ALOC-01)."""
    nrs_sem_credito: set[int] = set()
    for item in dados:
        if is_sem_credito(item.get("status_credito")):
            nrs_sem_credito.add(item["nr_pedido"])

    for par in resultado["selecionados"]:
        if par[0] in nrs_sem_credito:
            raise AssertionError(
                f"I2 violada: par {par!r} pertence ao pedido sem crédito "
                f"{par[0]} mas aparece em selecionados"
            )
    for par in resultado["pares_processados"]:
        if par[0] in nrs_sem_credito:
            raise AssertionError(
                f"I2 violada: par {par!r} pertence ao pedido sem crédito "
                f"{par[0]} mas aparece em pares_processados"
            )
    for par, itens in resultado["resultados"].items():
        if par[0] not in nrs_sem_credito:
            continue
        for item in itens:
            if item.get("status_item") == "Gerar OR":
                raise AssertionError(
                    f"I2 violada: item do par {par!r} (pedido sem crédito) "
                    'tem status_item == "Gerar OR"'
                )


def assert_tudo_ou_nada_preserva_quantidade(itens_par: list[dict]) -> None:
    """I3: no modo sem adequação, todo par selecionado tem qt_liquida ==
    qt_solicitada em todos os seus tamanhos (ALOC-02)."""
    if not any(item.get("status_item") == "Gerar OR" for item in itens_par):
        return
    for item in itens_par:
        pedida = item.get("qt_solicitada", item["qt_liquida"])
        if item["qt_liquida"] != pedida:
            raise AssertionError(
                f"I3 violada: item {item.get('sg_tamanho')!r} do par selecionado "
                f"reservou {item['qt_liquida']} mas foi pedido {pedida} "
                "(tudo-ou-nada não preservou a quantidade)"
            )


def calcular_furo_de_grade(
    itens_pedido_produto: list[dict],
    estoque_local: dict[str, int],
    cd_prod_cor: str,
) -> tuple[bool, str | None]:
    """Oráculo de referência para I4 (ALOC-04), independente da futura
    implementação de produção do plano 14-04.

    Considera apenas os tamanhos que o cliente PEDIU naquele produto.
    Tamanho desconhecido (`get_tamanho_idx` -> 999) é descartado: nunca
    define extremo nem conta como interior.
    """
    tamanhos_pedidos = {item["sg_tamanho"] for item in itens_pedido_produto}
    idx_por_tamanho = {
        tamanho: get_tamanho_idx(tamanho) for tamanho in tamanhos_pedidos
    }
    idx_validos = {
        tamanho: idx for tamanho, idx in idx_por_tamanho.items() if idx != 999
    }

    if len(set(idx_validos.values())) < 2:
        return False, None

    idx_min = min(idx_validos.values())
    idx_max = max(idx_validos.values())

    for tamanho, idx in idx_validos.items():
        if idx_min < idx < idx_max:
            chave = montar_chave_estoque(cd_prod_cor, tamanho)
            if estoque_local.get(chave, 0) <= 0:
                return True, f"Furo de grade: tamanho {tamanho} sem estoque reservável"

    return False, None


def assert_furo_de_grade_nao_selecionado(
    par: tuple[int, str],
    itens_pedido_produto: list[dict],
    estoque_no_momento: dict[str, int],
    resultado: dict,
) -> None:
    """I4: par com furo de grade nunca aparece em selecionados (ALOC-04)."""
    tem_furo, motivo = calcular_furo_de_grade(
        itens_pedido_produto, estoque_no_momento, par[1]
    )
    if tem_furo and par in resultado["selecionados"]:
        raise AssertionError(f"I4 violada: par {par!r} selecionado com furo. {motivo}")


def assert_orcamento_nao_excedido(
    total_original: int,
    adicionado_execucoes: list[int],
    cortado_execucoes: list[int],
    *,
    tolerancia: float,
) -> None:
    """I5: total adicionado e total cortado, contados SEPARADAMENTE, nunca
    excedem floor(total_original × tolerancia) (ALOC-07, ALOC-08, ALOC-09).

    `tolerancia` é keyword-only e obrigatório de propósito: um default
    reintroduziria em silêncio o 5% fixo que este oráculo precisa medir
    contra a tolerância realmente usada em cada execução. Independente da
    implementação de produção: não importa nada de `orcamento_pedido.py`
    nem de `motor_adequacao.py` para calcular o limite."""
    limite = int(Decimal(str(tolerancia)) * total_original)
    total_adicionado = sum(adicionado_execucoes)
    total_cortado = sum(cortado_execucoes)
    if total_adicionado > limite:
        raise AssertionError(
            f"I5 violada: total adicionado {total_adicionado} excede o limite "
            f"{limite} (tolerancia {tolerancia!r} de {total_original})"
        )
    if total_cortado > limite:
        raise AssertionError(
            f"I5 violada: total cortado {total_cortado} excede o limite "
            f"{limite} (tolerancia {tolerancia!r} de {total_original})"
        )


def assert_execucoes_identicas(resultado_1: dict, resultado_2: dict) -> None:
    """I6: rodar o mesmo cenário duas vezes produz plano idêntico, inclusive
    com empate exato de prioridade (ALOC-06)."""
    for campo in (
        "selecionados",
        "preteridos",
        "bloqueados_credito",
        "pares_processados",
    ):
        if resultado_1[campo] != resultado_2[campo]:
            raise AssertionError(
                f"I6 violada: campo {campo!r} difere entre as duas execuções: "
                f"{resultado_1[campo]!r} != {resultado_2[campo]!r}"
            )
    chaves_1 = set(resultado_1["resultados"].keys())
    chaves_2 = set(resultado_2["resultados"].keys())
    if chaves_1 != chaves_2:
        raise AssertionError(
            f"I6 violada: chaves de resultados diferem entre execuções: "
            f"{chaves_1!r} != {chaves_2!r}"
        )
    for par in chaves_1:
        if resultado_1["resultados"][par] != resultado_2["resultados"][par]:
            raise AssertionError(
                f"I6 violada: itens do par {par!r} diferem entre execuções"
            )


def assert_rateio_sem_drift(itens: list[dict], valor_total_esperado: Decimal) -> None:
    """I7 / FIX-02: sum(vl_liquido dos itens) == valor total rateado, sem
    sobra nem falta de centavo."""
    soma = sum((Decimal(str(item["vl_liquido"])) for item in itens), Decimal("0"))
    if soma != valor_total_esperado:
        raise AssertionError(
            f"I7 violada: soma dos itens {soma} difere do valor total esperado "
            f"{valor_total_esperado} (drift de rateio)"
        )


def assert_minimo_viavel_antes_de_extra(
    itens_por_produto: dict[str, list[dict]],
) -> None:
    """I8: todo produto do pedido que tinha alocação viável recebe o mínimo
    antes de qualquer extra ser distribuído (ALOC-10)."""
    tem_zerado = False
    tem_extra = False
    for itens in itens_por_produto.values():
        zerado = not any(item.get("status_item") == "Gerar OR" for item in itens)
        extra = any(
            item["qt_liquida"] > item.get("qt_solicitada", item["qt_liquida"])
            for item in itens
        )
        tem_zerado = tem_zerado or zerado
        tem_extra = tem_extra or extra

    if tem_zerado and tem_extra:
        raise AssertionError(
            "I8 violada: existe produto zerado no pedido enquanto outro produto "
            "do mesmo pedido já recebeu peça extra"
        )


def assert_contrato_chaves_resultado(resultado: dict) -> None:
    """I9: as chaves de retorno de processar_pedidos continuam sendo
    exatamente resultados, selecionados, preteridos, bloqueados_credito,
    pares_processados (contrato)."""
    chaves = set(resultado.keys())
    if chaves != _CHAVES_CONTRATO_RESULTADO:
        raise AssertionError(
            f"I9 violada: chaves de resultado {chaves!r} != contrato esperado "
            f"{_CHAVES_CONTRATO_RESULTADO!r}"
        )


__all__ = [
    "assert_estoque_nunca_excedido",
    "assert_sem_credito_nunca_alocado",
    "assert_tudo_ou_nada_preserva_quantidade",
    "calcular_furo_de_grade",
    "assert_furo_de_grade_nao_selecionado",
    "assert_orcamento_nao_excedido",
    "assert_execucoes_identicas",
    "assert_rateio_sem_drift",
    "assert_minimo_viavel_antes_de_extra",
    "assert_contrato_chaves_resultado",
]
