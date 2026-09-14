"""Motor de adequação: casamento pedido x estoque, isolado por canal.

Orquestração sem I/O — `processar_pedidos` -> `_processar_pedidos_canal` ->
laço PEDIDO-EXTERNO/produto-interno -> `adequar_grade_produto` (passada 1,
mínimo viável) -> `conceder_adicao_pedido` (passada 2, extras) ->
recálculo financeiro via `ratear_hamilton`. Todas as funções aqui são puras
(sem banco/HTTP).
"""

import logging
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from threading import Event

from app.modules.pedidos.domain.furo_de_grade import tem_furo_de_grade
from app.modules.pedidos.domain.orcamento_pedido import OrcamentoPedido
from app.modules.pedidos.domain.politica_quantidade import ModoAdequacao
from app.modules.pedidos.domain.rateio import ratear_hamilton
from app.modules.pedidos.domain.standby_motivo import BLACKLIST, FURO_GRADE, SEM_ESTOQUE
from app.modules.pedidos.domain.value_objects import (
    CANAIS,
    get_tamanho_idx,
    montar_chave_estoque,
    normalizar_canal,
)

logger = logging.getLogger(__name__)

_MOTIVO_SEM_CANAL = (
    "Canal não identificado: o estoque é isolado por canal, então não há "
    "estoque contra o qual comparar este pedido"
)
_CENT = Decimal("0.01")


def _motivo_orcamento_corte(tolerancia: float) -> str:
    """Mensagem de stand-by por orçamento de corte, com o percentual
    interpolado a partir da tolerância real do ledger (`OrcamentoPedido`).

    `Decimal(str(tolerancia))` é o mesmo padrão que `OrcamentoPedido.limite_adicao`
    / `.limite_corte` já usam (evita o artefato de float `0.1 * 100 ==
    10.000000000000002`). `.normalize()` remove os zeros à direita que a
    multiplicação introduz, mas devolve notação científica para valores
    redondos (`Decimal("1E+1")`) — o format spec `f` força a forma decimal
    ("10", não "1E+1").
    """
    percentual = (Decimal(str(tolerancia)) * 100).normalize()
    return (
        "Orçamento do pedido: corte necessário excede o restante do "
        f"orçamento de {percentual:f}%"
    )


type CancellationToken = Event | Callable[[], bool]


class PlanningCancellationRequested(RuntimeError):
    """Sinal interno e sem dados de negócio para encerrar o motor CPU-bound."""

    def __init__(self) -> None:
        super().__init__("processing_planning_cancelled")


def _ensure_not_cancelled(cancel_token: CancellationToken | None) -> None:
    if cancel_token is None:
        return
    cancelled = cancel_token() if callable(cancel_token) else cancel_token.is_set()
    if cancelled:
        raise PlanningCancellationRequested


def is_sem_credito(status: str | None) -> bool:
    """True se o status de crédito indicar ausência de crédito.

    Robusto a acento/caixa: 'Sem Crédito', 'SEM CREDITO', 'sem credito' etc.
    Ausente ou 'Com Crédito' -> False (crédito OK).
    """
    if not status:
        return False
    norm = (
        unicodedata.normalize("NFKD", status).encode("ascii", "ignore").decode().lower()
    )
    return any(marker in norm for marker in ("sem cred", "bloque", "reprov"))


def agrupar_por_produto(
    dados: list,
    ja_processados: set,
    *,
    cancel_token: CancellationToken | None = None,
) -> dict:
    """Retorna { cd_prod_cor -> { nr_pedido -> [itens] } }.

    `ja_processados` é um set de PARES `(nr_pedido, cd_prod_cor)`. O skip é por
    par, não por pedido: um pedido que já gerou OR do produto A continua
    elegível para o produto B — é o que faz o faturamento parcial funcionar.
    """
    estrutura = defaultdict(lambda: defaultdict(list))
    ignorados = set()
    for item in dados:
        _ensure_not_cancelled(cancel_token)
        par = (item["nr_pedido"], item["cd_prod_cor"])
        if par in ja_processados:
            ignorados.add(par)
            continue
        estrutura[item["cd_prod_cor"]][item["nr_pedido"]].append(item)
    if ignorados:
        logger.info(
            "[Skip] %d par(es) (pedido, produto) já processados anteriormente.",
            len(ignorados),
        )
    return estrutura


def _indexar_estoque_por_produto(
    estoque: Mapping[str, int],
    produtos: set[str],
    *,
    cancel_token: CancellationToken | None = None,
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Particiona o estoque em uma passagem, preservando a regra de prefixo.

    A representação histórica usa ``f"{cd_prod_cor}_{sg_tamanho}"`` e pertence
    ao produto toda chave que começa por ``f"{cd_prod_cor}_"``. Uma chave pode,
    portanto, casar com mais de um código quando há prefixos sobrepostos (por
    exemplo ``A`` e ``A_B``). Examinar cada posição de ``_`` mantém exatamente
    essa semântica sem reler o mapa completo para cada produto.
    """
    por_produto = {produto: {} for produto in produtos}
    totais = dict.fromkeys(produtos, 0)

    for chave, quantidade in estoque.items():
        _ensure_not_cancelled(cancel_token)
        separador = chave.find("_")
        while separador >= 0:
            produto = chave[:separador]
            if produto in por_produto:
                por_produto[produto][chave] = quantidade
                totais[produto] += quantidade
            separador = chave.find("_", separador + 1)

    return por_produto, totais


def _falta_necessaria(
    itens: list, estoque_local: Mapping[str, int], cd_prod_cor: str
) -> int:
    """Soma, sobre os itens de um (pedido, produto), quanto falta de estoque
    para atender a quantidade pedida em cada tamanho. Usado ANTES de decidir
    a ordem da passada 1 (corte necessário crescente) — pré-computado uma
    única vez por produto, nunca recalculado dentro da chave de um `sorted`
    (T-14-06-01: recalcular a cada comparação seria superlinear)."""
    falta = 0
    for item in itens:
        chave = montar_chave_estoque(cd_prod_cor, item["sg_tamanho"])
        est_disp = estoque_local.get(chave, 0)
        falta += max(0, item["qt_liquida"] - est_disp)
    return falta


def _preco_unitario_medio(itens: list) -> Decimal:
    """Preço unitário médio de um produto (ainda não recalculado por
    Hamilton), usado para ordenar a passada 2 (preço unitário decrescente).
    Pré-computado uma única vez por produto, pelo mesmo motivo de
    `_falta_necessaria`."""
    qt_total = sum(item.get("qt_solicitada", item["qt_liquida"]) for item in itens)
    if qt_total <= 0:
        return Decimal(0)
    valor_total = sum((Decimal(str(item["vl_liquido"])) for item in itens), Decimal(0))
    return valor_total / qt_total


def adequar_grade_produto(
    itens: list,
    estoque: dict,
    cd_prod_cor: str,
    ledger: OrcamentoPedido,
    *,
    cancel_token: CancellationToken | None = None,
) -> list:
    """Passada 1 (mínimo viável, ALOC-10) de UM produto do pedido.

    Decide, usando o orçamento de CORTE do `ledger` do pedido inteiro
    (ALOC-07), se o corte necessário deste produto cabe no restante do
    pedido — `ledger.consumir_corte` é tudo-ou-nada, então não há redução
    parcial do orçamento: ou o produto inteiro é aprovado (reduzindo cada
    tamanho ao disponível, quando falta), ou vai inteiro para stand-by sem
    consumir nada do orçamento. Não concede peças extra (isso é
    `conceder_adicao_pedido`, passada 2) nem recalcula `vl_liquido` (isso é
    o recálculo financeiro via `ratear_hamilton`, aplicado depois que as
    duas passadas terminaram) — aqui só a decisão de corte.
    """
    _ensure_not_cancelled(cancel_token)
    itens = [dict(i) for i in itens]

    falta_total = _falta_necessaria(itens, estoque, cd_prod_cor)
    concedido = ledger.consumir_corte(falta_total)

    if not concedido:
        return marcar_stand_by(
            itens,
            motivo=_motivo_orcamento_corte(ledger.tolerancia),
            cancel_token=cancel_token,
        )

    for item in itens:
        _ensure_not_cancelled(cancel_token)
        chave = montar_chave_estoque(cd_prod_cor, item["sg_tamanho"])
        est_disp = estoque.get(chave, 0)
        qtd_antiga = item["qt_liquida"]
        nova_qtd = min(qtd_antiga, est_disp) if falta_total > 0 else qtd_antiga

        item["status_item"] = "Gerar OR"
        # Preserva a quantidade ORIGINALMENTE pedida antes de sobrescrever
        # qt_liquida com a adequada. Sem isto a informação "pediu 10,
        # reservou 8" se perde para sempre — só o delta de valor sobrevivia,
        # em diff_valor — e o ERP/auditoria precisa dos dois números.
        item["qt_solicitada"] = qtd_antiga
        item["qt_liquida"] = nova_qtd

    return itens


def conceder_adicao_pedido(
    itens: list,
    estoque: dict,
    cd_prod_cor: str,
    ledger: OrcamentoPedido,
    *,
    cancel_token: CancellationToken | None = None,
) -> list:
    """Passada 2 (extras, ALOC-10) de um produto ELEGÍVEL (sem corte na
    passada 1) do pedido.

    Consome o orçamento de ADIÇÃO do `ledger` do pedido inteiro para os
    tamanhos de fronteira (`is_ext`: o menor e o maior tamanho PEDIDOS
    naquele produto, com `get_tamanho_idx` já filtrando o índice 999 de
    tamanho desconhecido — nunca definindo extremo). `ledger.consumir_adicao`
    é parcial: concede até o espaço de estoque disponível, nunca mais do que
    isso, saturando no que resta do orçamento do pedido.

    PONTO DE EXTENSÃO (Phase 18, ALOC-11): esta função decide QUANTAS peças
    extras cada produto recebe do orçamento de adição do pedido. QUAL
    tamanho especificamente recebe cada peça extra continua usando o
    mecanismo de fronteira (`is_ext`) como placeholder documentado — a
    escolha por "tamanho com mais sobra" é da Phase 18, fora de escopo aqui.
    """
    _ensure_not_cancelled(cancel_token)
    itens = [dict(i) for i in itens]

    indices_validos = [
        get_tamanho_idx(item["sg_tamanho"])
        for item in itens
        if get_tamanho_idx(item["sg_tamanho"]) != 999
    ]
    if not indices_validos:
        return itens
    idx_min, idx_max = min(indices_validos), max(indices_validos)

    for item in itens:
        _ensure_not_cancelled(cancel_token)
        idx = get_tamanho_idx(item["sg_tamanho"])
        is_ext = idx != 999 and idx in (idx_min, idx_max)
        if not is_ext:
            continue
        chave = montar_chave_estoque(cd_prod_cor, item["sg_tamanho"])
        est_disp = estoque.get(chave, 0)
        espaco_disponivel = est_disp - item["qt_liquida"]
        if espaco_disponivel <= 0:
            continue
        concedido = ledger.consumir_adicao(espaco_disponivel)
        if concedido > 0:
            item["qt_liquida"] += concedido

    return itens


def _recalcular_financeiro_produto(itens: list) -> list:
    """FIX-02: recalcula `vl_liquido` de TODOS os itens `Gerar OR` de um
    produto de uma só vez, via `ratear_hamilton`, eliminando o drift de
    `round()` item a item.

    O preço unitário médio ORIGINAL (soma do `vl_liquido` original dividida
    pela soma da `qt_solicitada`) multiplicado pela soma das quantidades
    FINAIS dá o total a ratear; `ratear_hamilton` garante que a soma das
    partes bate exatamente com esse total, sem sobra nem falta de centavo.
    Itens em stand-by não são tocados (já vêm com `vl_liquido`/`diff_valor`
    corretos de `marcar_stand_by`).
    """
    itens_gerar_or = [i for i in itens if i.get("status_item") == "Gerar OR"]
    if not itens_gerar_or:
        return itens

    qt_solicitada_total = sum(i["qt_solicitada"] for i in itens_gerar_or)
    valor_original_total = sum(
        (Decimal(str(i["vl_liquido"])) for i in itens_gerar_or), Decimal(0)
    )
    preco_unit = (
        valor_original_total / qt_solicitada_total
        if qt_solicitada_total > 0
        else Decimal(0)
    )

    qt_final_total = sum(i["qt_liquida"] for i in itens_gerar_or)
    total_a_ratear = (preco_unit * qt_final_total).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )

    sizes = {i["sg_tamanho"]: i["qt_liquida"] for i in itens_gerar_or}
    rateado = ratear_hamilton(total_a_ratear, sizes)

    for item in itens_gerar_or:
        valor_original_item = Decimal(str(item["vl_liquido"]))
        novo_valor = rateado[item["sg_tamanho"]]
        item["vl_liquido"] = float(novo_valor)
        item["diff_valor"] = float((novo_valor - valor_original_item).quantize(_CENT))

    return itens


def aplicar_tudo_ou_nada(
    itens: list,
    estoque: dict,
    cd_prod_cor: str,
    *,
    cancel_token: CancellationToken | None = None,
) -> list:
    """Política SEM_ADEQUAR (ALOC-02): tudo-ou-nada por par.

    Reserva a grade EXATAMENTE como pedida em TODOS os tamanhos, ou o par
    inteiro fica em stand-by — nunca quantidade parcial. Diferente de
    `adequar_grade_produto`, não há ledger de orçamento: a falta em um único
    tamanho já reprova a grade inteira, mesmo que outros tamanhos tenham
    sobra de estoque. Nunca decrementa o dict `estoque` recebido; o commit
    continua sendo responsabilidade exclusiva de quem chama (mesmo contrato
    de `adequar_grade_produto`).
    """
    _ensure_not_cancelled(cancel_token)
    itens = [dict(i) for i in itens]

    falta_total = _falta_necessaria(itens, estoque, cd_prod_cor)

    if falta_total > 0:
        return marcar_stand_by(
            itens,
            motivo="Sem adequação: grade completa indisponível",
            cancel_token=cancel_token,
        )

    for item in itens:
        _ensure_not_cancelled(cancel_token)
        item["status_item"] = "Gerar OR"
        item["qt_solicitada"] = item["qt_liquida"]
        item["diff_valor"] = 0.0

    return itens


def marcar_stand_by(
    itens: list,
    motivo: str = "Preterido: estoque insuficiente para todos os pedidos do produto",
    *,
    cancel_token: CancellationToken | None = None,
) -> list:
    resultado = []
    for item in itens:
        _ensure_not_cancelled(cancel_token)
        copia = dict(item)
        copia["status_item"] = "Pedido em Stand By"
        copia["diff_valor"] = 0.0
        copia["motivo_stand_by"] = motivo
        copia.pop("is_ext", None)
        copia.pop("comentario", None)
        copia.pop("aceita_adequacao", None)
        resultado.append(copia)
    return resultado


def _resolver_orcamento_pedido(
    nr_pedido: int,
    itens_do_pedido: list,
    total_original_por_pedido: Mapping[int, int] | None,
    consumido_previo_adicao_por_pedido: Mapping[int, int] | None,
    consumido_previo_corte_por_pedido: Mapping[int, int] | None,
) -> tuple[int, int, int, bool]:
    """Resolve `(total_original, consumido_previo_adicao,
    consumido_previo_corte, usou_placeholder)` para um único pedido.

    Fronteira de I/O (ALOC-09): ler ORs anteriores é I/O e pertence à
    Phase 15, não a este domínio. `OrcamentoPedido` exige os 4 campos sem
    default (ver `orcamento_pedido.py`); ESTE orquestrador, por outro lado,
    recebe os totais/consumidos como Mapping OPCIONAL, por compatibilidade
    com todo chamador existente hoje (nenhum passa esses dados ainda). Sem
    a chave daquele `nr_pedido` específico (mapping ausente OU sem a
    chave), caímos no placeholder de execução única: `total_original` vira
    a soma de `qt_liquida` de todos os itens deste pedido presentes no
    snapshot desta execução, e `consumido_previo_* = 0`. Isso é correto
    para uma execução isolada, mas SUBESTIMA o total ao longo de execuções
    sucessivas (pares já processados saem do snapshot pendente) — esta
    função reporta `usou_placeholder=True` para o chamador poder emitir o
    aviso ALOC-09 sem inundar o log por pedido.
    """
    usou_placeholder = False

    if total_original_por_pedido is not None and nr_pedido in total_original_por_pedido:
        total_original = total_original_por_pedido[nr_pedido]
    else:
        total_original = sum(item["qt_liquida"] for item in itens_do_pedido)
        usou_placeholder = True

    if (
        consumido_previo_adicao_por_pedido is not None
        and nr_pedido in consumido_previo_adicao_por_pedido
    ):
        consumido_previo_adicao = consumido_previo_adicao_por_pedido[nr_pedido]
    else:
        consumido_previo_adicao = 0
        usou_placeholder = True

    if (
        consumido_previo_corte_por_pedido is not None
        and nr_pedido in consumido_previo_corte_por_pedido
    ):
        consumido_previo_corte = consumido_previo_corte_por_pedido[nr_pedido]
    else:
        consumido_previo_corte = 0
        usou_placeholder = True

    return (
        total_original,
        consumido_previo_adicao,
        consumido_previo_corte,
        usou_placeholder,
    )


def _commitar_par(
    par: tuple[int, str],
    itens_processados: list,
    estoque_local: dict[str, int],
    cd_prod_cor: str,
    selecionados: list,
    preteridos: list,
    preteridos_motivo: dict[tuple[int, str], str],
    resultados: dict,
    resultados_apenas_selecionados: bool,
    *,
    cancel_token: CancellationToken | None = None,
    motivo_sem_estoque: str = SEM_ESTOQUE,
) -> None:
    """Decremento de estoque só após confirmação (padrão de "commit"): só
    debita `estoque_local` para itens com `status_item == "Gerar OR"`, e só
    depois que TODAS as decisões de quantidade (passada 1 + passada 2, se
    elegível) já foram tomadas — nunca especulativamente.

    `motivo_sem_estoque` (quick task 260825-jhv): motivo canônico gravado em
    `preteridos_motivo` quando o par não gera OR. Default `SEM_ESTOQUE`
    preserva o comportamento pré-existente; o ramo tudo-ou-nada/blacklist
    passa `BLACKLIST` explicitamente para pares de cliente blacklist.
    """
    for item in itens_processados:
        _ensure_not_cancelled(cancel_token)
        if item.get("status_item") == "Gerar OR":
            chave = montar_chave_estoque(cd_prod_cor, item["sg_tamanho"])
            estoque_local[chave] = max(
                0, estoque_local.get(chave, 0) - item["qt_liquida"]
            )
    gerou_or = any(item.get("status_item") == "Gerar OR" for item in itens_processados)
    if gerou_or:
        selecionados.append(par)
        resultados[par].extend(itens_processados)
    else:
        preteridos.append(par)
        preteridos_motivo[par] = motivo_sem_estoque
        if not resultados_apenas_selecionados:
            resultados[par].extend(itens_processados)


def processar_pedidos(
    dados: list,
    estoque: dict,
    ja_processados: set,
    criterio: str = "valor",
    tolerancia: float = 0.05,
    *,
    modo: str = ModoAdequacao.ADEQUAR,
    resultados_apenas_selecionados: bool = False,
    cancel_token: CancellationToken | None = None,
    total_original_por_pedido: Mapping[int, int] | None = None,
    consumido_previo_adicao_por_pedido: Mapping[int, int] | None = None,
    consumido_previo_corte_por_pedido: Mapping[int, int] | None = None,
) -> dict:
    """Orquestração sem I/O. Isola o processamento por canal e mescla o resultado.

    GRÃO DE SAÍDA: o par `(nr_pedido, cd_prod_cor)`. Uma OR é um produto, e cada
    par é a reserva daquele produto para aquele cliente. `bloqueados_credito`
    é a exceção deliberada: crédito é do CLIENTE, então continua por nr_pedido.

    `estoque` é o dict por canal { canal -> { f'{cd}_{tam}': qt } } devolvido por
    `carregar_estoque`. Cada pedido pertence a um único canal (todos os seus itens
    compartilham o canal), então particionar por canal mantém cada pedido inteiro:
    não há colisão de nr_pedido entre canais.

    Item cujo canal não resolve para Franquia/Multimarca vai para STAND-BY, não
    para um canal-padrão: adivinhar o canal alocaria peça do canal errado, que é
    exatamente o que o isolamento existe para impedir. (`canal_bucket`, com seu
    fallback 'Franquia', continua servindo os caminhos de EXIBIÇÃO — ali um
    rótulo errado é cosmético, não move estoque.)

    `total_original_por_pedido`/`consumido_previo_adicao_por_pedido`/
    `consumido_previo_corte_por_pedido` (ALOC-09, opcionais/keyword-only):
    fronteira de I/O explícita. Ler ORs anteriores é I/O e pertence à
    Phase 15 — este orquestrador não lê banco. Quando ausentes (todo
    chamador hoje), cada pedido usa um placeholder documentado (ver
    `_resolver_orcamento_pedido`), e o motor emite `logger.warning` citando
    ALOC-09 sempre que o placeholder é usado, para o gap não ficar
    silencioso.

    Retorna:
        resultados         — { (nr_pedido, cd_prod_cor): [itens da grade] }
        selecionados       — lista de pares que geraram OR
        preteridos         — lista de pares em stand-by por falta de estoque ou
                             por canal não identificado
        preteridos_motivo  — { (nr_pedido, cd_prod_cor): motivo } com o motivo
                             canônico curto (FURO_GRADE, SEM_ESTOQUE ou
                             BLACKLIST, `standby_motivo.py`) de cada par em
                             `preteridos`. Campo ADITIVO (STANDBY-01): sempre
                             as mesmas chaves de `preteridos`, mesmo tamanho.
        bloqueados_credito — lista de nr_pedido sem crédito (por CLIENTE)
        pares_processados  — set dos pares que geraram OR
    """
    por_canal: dict[str, list] = defaultdict(list)
    sem_canal: list = []
    for item in dados:
        _ensure_not_cancelled(cancel_token)
        canal = normalizar_canal(item.get("canal"))
        if canal is None:
            sem_canal.append(item)
            continue
        por_canal[canal].append(item)

    merged: dict = {
        "resultados": {},
        "selecionados": [],
        "preteridos": [],
        "preteridos_motivo": {},
        "bloqueados_credito": [],
        "pares_processados": set(),
    }
    # Alias para o dict de dentro de `merged` — mesmo objeto, só evita repetir
    # `merged["preteridos_motivo"]` a cada par do ramo defensivo abaixo.
    preteridos_motivo = merged["preteridos_motivo"]

    # Stand-by por canal desconhecido. Não entra em `pares_processados`, então o
    # par reaparece na próxima tentativa — se a origem corrigir o rótulo do canal,
    # o pedido volta a ser elegível sozinho, sem intervenção.
    pares_sem_canal: dict[tuple, list] = defaultdict(list)
    for item in sem_canal:
        _ensure_not_cancelled(cancel_token)
        par = (item["nr_pedido"], item["cd_prod_cor"])
        if par not in ja_processados:
            pares_sem_canal[par].append(item)
    for par, itens_par in pares_sem_canal.items():
        _ensure_not_cancelled(cancel_token)
        if not resultados_apenas_selecionados:
            merged["resultados"][par] = marcar_stand_by(
                itens_par,
                motivo=_MOTIVO_SEM_CANAL,
                cancel_token=cancel_token,
            )
        merged["preteridos"].append(par)
        # Achado A1 (16-01-PLAN.md): ramo defensivo, código morto no caminho
        # real de build_processing_plan (_eligible_pairs já garante canal
        # canônico antes do motor rodar) — mantém a invariante universal
        # len(preteridos) == len(preteridos_motivo) mesmo assim.
        preteridos_motivo[par] = SEM_ESTOQUE
    if pares_sem_canal:
        logger.warning(
            "%d par(es) (pedido, produto) em stand-by por canal não identificado "
            "(%d item[ns]). Canais aceitos: %s.",
            len(pares_sem_canal),
            sum(len(i) for i in pares_sem_canal.values()),
            ", ".join(CANAIS),
        )

    for canal, itens_canal in por_canal.items():
        _ensure_not_cancelled(cancel_token)
        parcial = _processar_pedidos_canal(
            itens_canal,
            estoque.get(canal, {}),
            ja_processados,
            criterio,
            tolerancia,
            modo=modo,
            resultados_apenas_selecionados=resultados_apenas_selecionados,
            cancel_token=cancel_token,
            total_original_por_pedido=total_original_por_pedido,
            consumido_previo_adicao_por_pedido=consumido_previo_adicao_por_pedido,
            consumido_previo_corte_por_pedido=consumido_previo_corte_por_pedido,
        )
        merged["resultados"].update(parcial["resultados"])
        merged["selecionados"].extend(parcial["selecionados"])
        merged["preteridos"].extend(parcial["preteridos"])
        merged["bloqueados_credito"].extend(parcial["bloqueados_credito"])
        merged["pares_processados"] |= parcial["pares_processados"]
        merged["preteridos_motivo"].update(parcial["preteridos_motivo"])

    merged["selecionados"].sort()
    merged["preteridos"].sort()
    merged["bloqueados_credito"].sort()
    return merged


def _processar_pedidos_canal(
    dados: list,
    estoque: Mapping[str, int],
    ja_processados: set,
    criterio: str = "valor",
    tolerancia: float = 0.05,
    *,
    modo: str = ModoAdequacao.ADEQUAR,
    resultados_apenas_selecionados: bool = False,
    cancel_token: CancellationToken | None = None,
    total_original_por_pedido: Mapping[int, int] | None = None,
    consumido_previo_adicao_por_pedido: Mapping[int, int] | None = None,
    consumido_previo_corte_por_pedido: Mapping[int, int] | None = None,
) -> dict:
    """
    Orquestração de um único canal, sobre um estoque plano { f'{cd}_{tam}': qt }.

    Ver `processar_pedidos` para o contrato das chaves de retorno.
    """
    if cancel_token is None:
        estrutura = agrupar_por_produto(dados, ja_processados)
    else:
        estrutura = agrupar_por_produto(
            dados,
            ja_processados,
            cancel_token=cancel_token,
        )
    if not estrutura:
        logger.info("Nenhum par (pedido, produto) novo para processar.")
        return {
            "resultados": {},
            "selecionados": [],
            "preteridos": [],
            "preteridos_motivo": {},
            "bloqueados_credito": [],
            "pares_processados": set(),
        }

    resultados: dict = defaultdict(list)
    selecionados: list[tuple[int, str]] = []
    preteridos: list[tuple[int, str]] = []
    preteridos_motivo: dict[tuple[int, str], str] = {}

    todos_pedidos_flat: dict = {}
    for pedidos_cd in estrutura.values():
        _ensure_not_cancelled(cancel_token)
        for nr, itens in pedidos_cd.items():
            _ensure_not_cancelled(cancel_token)
            todos_pedidos_flat.setdefault(nr, []).extend(itens)

    # Pedidos sem crédito ficam em stand-by aguardando liberação: não consomem
    # estoque e não são marcados como processados (reaparecem na listagem).
    sem_credito_nrs = {
        nr
        for nr, itens in todos_pedidos_flat.items()
        if any(is_sem_credito(i.get("status_credito")) for i in itens)
    }
    if sem_credito_nrs:
        logger.info(
            "[Credito] %d pedido(s) em stand-by aguardando liberacao.",
            len(sem_credito_nrs),
        )

    # Exceção de blacklist (quick task 260825-jhv): cliente com
    # indica_blacklist="SIM" nunca pode ter a grade alterada pelo motor,
    # mesmo em modo=ADEQUAR — o pedido dele é sempre forçado para o caminho
    # tudo-ou-nada por produto (ver despacho por modo abaixo), com motivo
    # canônico BLACKLIST (nunca FURO_GRADE/SEM_ESTOQUE) quando preterido.
    blacklist_nrs = {
        nr
        for nr, itens in todos_pedidos_flat.items()
        if any(i.get("indica_blacklist") for i in itens)
    }

    # Faturamento PARCIAL: todo pedido com crédito gera OR dos produtos que tiverem
    # estoque; os demais produtos ficam em stand-by. O estoque é compartilhado e
    # alocado por PRIORIDADE — primeiro os pedidos que conseguem faturar mais
    # (maior valor disponível, depois mais produtos disponíveis e, como
    # desempate final e total, menor nr_pedido — ver `_prioridade` abaixo).
    produtos: set[str] = set()
    for itens in todos_pedidos_flat.values():
        _ensure_not_cancelled(cancel_token)
        for item in itens:
            _ensure_not_cancelled(cancel_token)
            produtos.add(item["cd_prod_cor"])
    if cancel_token is None:
        estoque_por_produto, disponivel_por_produto = _indexar_estoque_por_produto(
            estoque,
            produtos,
        )
    else:
        estoque_por_produto, disponivel_por_produto = _indexar_estoque_por_produto(
            estoque,
            produtos,
            cancel_token=cancel_token,
        )

    # `criterio` vem do parâmetro de negócio `criterio_selecao` (tabela parametros):
    # 'valor' prioriza quem fatura mais em R$; 'quantidade', quem escoa mais peças.
    # Desempate em três níveis, do menos ao mais específico: (1) score de negócio
    # (vl_liquido ou qt_liquida, conforme `criterio`); (2) mais produtos com
    # estoque disponível; (3) nr_pedido crescente — desempate FINAL e TOTAL
    # (ALOC-06). nr_pedido é único por pedido dentro do canal, então esta chave
    # nunca deixa um empate residual: a ordem de chegada do snapshot do banco
    # (não garantida pela origem) para de influenciar o vencedor, e o plan_hash
    # deixa de variar entre execuções "iguais" que empatavam nos dois primeiros
    # campos.
    campo_score = "qt_liquida" if criterio == "quantidade" else "vl_liquido"

    def _prioridade(nr: int) -> tuple:
        _ensure_not_cancelled(cancel_token)
        itens = todos_pedidos_flat[nr]
        prods_disp = {
            i["cd_prod_cor"]
            for i in itens
            if disponivel_por_produto.get(i["cd_prod_cor"], 0) > 0
        }
        score = sum(i[campo_score] for i in itens if i["cd_prod_cor"] in prods_disp)
        # `sorted(..., reverse=True)` ordena a tupla inteira em ordem
        # descendente. Um `nr` cru como último campo inverteria o desempate e
        # faria o MAIOR nr_pedido vencer — o oposto da decisão travada em
        # ALOC-06. Negativar `nr` é o que transforma "maior valor da tupla
        # vence" em "menor nr_pedido vence" no desempate final.
        return (score, len(prods_disp), -nr)

    ordens_credito = [nr for nr in todos_pedidos_flat if nr not in sem_credito_nrs]
    ordens_prioridade = sorted(ordens_credito, key=_prioridade, reverse=True)

    # Estoque local por produto: UMA cópia mutável por produto, criada uma
    # única vez antes do laço de pedidos e decrementada conforme cada
    # pedido (em ordem de prioridade global) consome — exatamente a mesma
    # semântica de estoque compartilhado do desenho produto-externo
    # anterior. A inversão de loop (pedido-externo, produto-interno) não
    # muda a alocação de estoque: a partição por produto já era
    # independente entre produtos, e a ordem de prioridade é calculada uma
    # única vez a partir do snapshot completo — processar cada pedido
    # inteiro em ordem de prioridade global, decrementando estes dicts
    # conforme avança, produz exatamente o mesmo resultado. A vantagem é
    # ergonômica: o ledger de orçamento do pedido passa a ser LOCAL à
    # iteração do pedido, sem precisar de um dict externo indexado por
    # nr_pedido — e, como a ordem das duas passadas depende do VALOR
    # computado (corte necessário, preço unitário) e não da ordem de
    # iteração de um dict, embaralhar a ordem de entrada dos produtos não
    # muda o resultado (invariante de permutação).
    estoque_locais: dict[str, dict[str, int]] = {
        cd_prod_cor: dict(estoque_por_produto[cd_prod_cor]) for cd_prod_cor in produtos
    }

    # Pedidos sem crédito: TODOS os produtos deles vão para stand-by, sem
    # tocar estoque nem ledger (ALOC-01, crédito é do CLIENTE). A ordem não
    # importa aqui — nenhum destes pedidos disputa estoque com ninguém, e
    # nenhum é marcado como processado (reaparece na próxima tentativa).
    for nr in sem_credito_nrs:
        _ensure_not_cancelled(cancel_token)
        produtos_do_pedido: dict[str, list] = defaultdict(list)
        for item in todos_pedidos_flat[nr]:
            produtos_do_pedido[item["cd_prod_cor"]].append(item)
        for cd_prod_cor, itens_par in produtos_do_pedido.items():
            par = (nr, cd_prod_cor)
            if not resultados_apenas_selecionados:
                resultados[par].extend(
                    marcar_stand_by(
                        itens_par,
                        motivo="Aguardando liberação de crédito",
                        cancel_token=cancel_token,
                    )
                )

    pedidos_com_orcamento_placeholder = 0

    for nr in ordens_prioridade:
        _ensure_not_cancelled(cancel_token)
        itens_do_pedido = todos_pedidos_flat[nr]
        produtos_do_pedido: dict[str, list] = defaultdict(list)
        for item in itens_do_pedido:
            _ensure_not_cancelled(cancel_token)
            produtos_do_pedido[item["cd_prod_cor"]].append(item)

        # Despacho por modo (ALOC-02): SEM_ADEQUAR usa tudo-ou-nada por par
        # e NÃO toca o ledger de orçamento — ALOC-02/03 é binário, sem
        # tolerância. Furo de grade continua rodando ANTES da política de
        # quantidade nos dois modos. Exceção de blacklist (260825-jhv): um
        # pedido em `blacklist_nrs` é forçado por este mesmo ramo tudo-ou-nada
        # mesmo quando `modo == ModoAdequacao.ADEQUAR` — a exceção vence o
        # parâmetro do request.
        if modo == ModoAdequacao.SEM_ADEQUAR or nr in blacklist_nrs:
            for cd_prod_cor, itens_par in produtos_do_pedido.items():
                _ensure_not_cancelled(cancel_token)
                par = (nr, cd_prod_cor)
                estoque_local = estoque_locais[cd_prod_cor]

                tem_furo, motivo_furo = tem_furo_de_grade(
                    itens_par, estoque_local, cd_prod_cor
                )
                if tem_furo:
                    assert motivo_furo is not None
                    preteridos.append(par)
                    preteridos_motivo[par] = (
                        BLACKLIST if nr in blacklist_nrs else FURO_GRADE
                    )
                    if not resultados_apenas_selecionados:
                        resultados[par].extend(
                            marcar_stand_by(
                                itens_par,
                                motivo=motivo_furo,
                                cancel_token=cancel_token,
                            )
                        )
                    continue

                itens_processados = aplicar_tudo_ou_nada(
                    itens_par,
                    estoque_local,
                    cd_prod_cor,
                    cancel_token=cancel_token,
                )
                _commitar_par(
                    par,
                    itens_processados,
                    estoque_local,
                    cd_prod_cor,
                    selecionados,
                    preteridos,
                    preteridos_motivo,
                    resultados,
                    resultados_apenas_selecionados,
                    cancel_token=cancel_token,
                    motivo_sem_estoque=BLACKLIST
                    if nr in blacklist_nrs
                    else SEM_ESTOQUE,
                )
            continue

        # --- Modo ADEQUAR: ledger de orçamento ±5% do pedido inteiro, duas
        # passadas (ALOC-07/08/09/10) ---
        (
            total_original,
            consumido_previo_adicao,
            consumido_previo_corte,
            usou_placeholder,
        ) = _resolver_orcamento_pedido(
            nr,
            itens_do_pedido,
            total_original_por_pedido,
            consumido_previo_adicao_por_pedido,
            consumido_previo_corte_por_pedido,
        )
        if usou_placeholder:
            pedidos_com_orcamento_placeholder += 1

        ledger = OrcamentoPedido(
            nr_pedido=nr,
            total_original=total_original,
            consumido_previo_adicao=consumido_previo_adicao,
            consumido_previo_corte=consumido_previo_corte,
            tolerancia=tolerancia,
        )

        # Furo de grade (ALOC-04) roda por produto ANTES de qualquer decisão
        # de quantidade, com o estoque já decrementado pelos pares de maior
        # prioridade processados antes deste (outros pedidos, ou outros
        # produtos do mesmo pedido não alteram o estoque um do outro).
        # Também pré-computa `falta_necessaria` (usado para ordenar a
        # passada 1) numa única passagem, nunca dentro da chave do `sorted`.
        produtos_elegiveis: dict[str, list] = {}
        falta_por_produto: dict[str, int] = {}
        for cd_prod_cor, itens_par in produtos_do_pedido.items():
            _ensure_not_cancelled(cancel_token)
            par = (nr, cd_prod_cor)
            estoque_local = estoque_locais[cd_prod_cor]

            tem_furo, motivo_furo = tem_furo_de_grade(
                itens_par, estoque_local, cd_prod_cor
            )
            if tem_furo:
                assert motivo_furo is not None
                preteridos.append(par)
                preteridos_motivo[par] = FURO_GRADE
                if not resultados_apenas_selecionados:
                    resultados[par].extend(
                        marcar_stand_by(
                            itens_par,
                            motivo=motivo_furo,
                            cancel_token=cancel_token,
                        )
                    )
                continue

            produtos_elegiveis[cd_prod_cor] = itens_par
            falta_por_produto[cd_prod_cor] = _falta_necessaria(
                itens_par, estoque_local, cd_prod_cor
            )

        # Passada 1 — mínimo viável (ALOC-10): corte necessário CRESCENTE,
        # desempate por cd_prod_cor ascendente (determinístico, independente
        # da ordem de entrada — invariante de permutação). Processar
        # primeiro quem precisa de MENOS corte maximiza quantos produtos são
        # atendidos dentro do orçamento de corte do pedido.
        ordem_passada_1 = sorted(
            produtos_elegiveis, key=lambda cd: (falta_por_produto[cd], cd)
        )
        itens_finais_por_produto: dict[str, list] = {}
        produtos_com_folga: list[str] = []
        for cd_prod_cor in ordem_passada_1:
            _ensure_not_cancelled(cancel_token)
            estoque_local = estoque_locais[cd_prod_cor]
            itens_processados = adequar_grade_produto(
                produtos_elegiveis[cd_prod_cor],
                estoque_local,
                cd_prod_cor,
                ledger,
                cancel_token=cancel_token,
            )
            itens_finais_por_produto[cd_prod_cor] = itens_processados
            gerou_or = any(
                item.get("status_item") == "Gerar OR" for item in itens_processados
            )
            # Elegível à passada 2 apenas se não precisou de corte nenhum
            # (folga total) — corresponde à condição `falta_total == 0` do
            # comportamento anterior.
            if gerou_or and falta_por_produto[cd_prod_cor] == 0:
                produtos_com_folga.append(cd_prod_cor)

        # Passada 2 — extras (ALOC-10): preço unitário médio DECRESCENTE,
        # desempate por cd_prod_cor ascendente. Só entre os produtos com
        # folga total da passada 1 (mínimo viável de TODOS já garantido
        # antes de qualquer extra ser distribuído).
        preco_por_produto = {
            cd: _preco_unitario_medio(itens_finais_por_produto[cd])
            for cd in produtos_com_folga
        }
        ordem_passada_2 = sorted(
            produtos_com_folga, key=lambda cd: (-preco_por_produto[cd], cd)
        )
        for cd_prod_cor in ordem_passada_2:
            _ensure_not_cancelled(cancel_token)
            estoque_local = estoque_locais[cd_prod_cor]
            itens_finais_por_produto[cd_prod_cor] = conceder_adicao_pedido(
                itens_finais_por_produto[cd_prod_cor],
                estoque_local,
                cd_prod_cor,
                ledger,
                cancel_token=cancel_token,
            )

        # Recálculo financeiro (FIX-02) + commit de estoque, por produto —
        # só depois que TODAS as quantidades finais deste pedido já foram
        # decididas (passada 1 e, se elegível, passada 2).
        for cd_prod_cor, itens_processados in itens_finais_por_produto.items():
            _ensure_not_cancelled(cancel_token)
            par = (nr, cd_prod_cor)
            estoque_local = estoque_locais[cd_prod_cor]
            itens_processados = _recalcular_financeiro_produto(itens_processados)
            _commitar_par(
                par,
                itens_processados,
                estoque_local,
                cd_prod_cor,
                selecionados,
                preteridos,
                preteridos_motivo,
                resultados,
                resultados_apenas_selecionados,
                cancel_token=cancel_token,
            )

    if pedidos_com_orcamento_placeholder:
        # ALOC-09: os parâmetros de orçamento são OPCIONAIS neste
        # orquestrador (compatibilidade com todo chamador existente — nenhum
        # ainda lê ORs anteriores do banco). Tornar isso silencioso
        # reintroduziria o double-spend sem erro, sem log e sem teste
        # vermelho assim que um chamador futuro esquecer de ligar os dados
        # reais (Phase 15). Emitido UMA VEZ por execução deste canal, não
        # por pedido, para não inundar o log em cenários de 100.000 pares.
        logger.warning(
            "ALOC-09: total_original_por_pedido/consumido_previo_* não "
            "fornecidos para %d pedido(s); usando placeholder de execução "
            "única — o orçamento acumulado entre execuções NÃO está sendo "
            "respeitado.",
            pedidos_com_orcamento_placeholder,
        )

    # Por PAR (pedido, produto): selecionado = aquele produto gerou OR para aquele
    # cliente; preterido = não havia estoque para ele. Pedidos sem crédito ficam
    # fora das duas listas (estão em `bloqueados_credito`).
    selecionados.sort()
    preteridos.sort()
    if preteridos:
        logger.info(
            "[Global] Sem estoque para gerar OR em %d par(es).", len(preteridos)
        )

    return {
        "resultados": dict(resultados),
        "selecionados": selecionados,
        "preteridos": preteridos,
        "preteridos_motivo": preteridos_motivo,
        "bloqueados_credito": sorted(sem_credito_nrs),
        # processados = só os pares que efetivamente geraram OR (saem da listagem).
        # Um par em stand-by reaparece para nova tentativa, e o pedido continua
        # elegível para os outros produtos dele.
        "pares_processados": set(selecionados),
    }
