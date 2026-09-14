"""Regras puras de agregação/dedup das linhas cruas do Databricks antes da
persistência (full refresh)."""

import logging
from collections import defaultdict
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import NamedTuple

from app.modules.ingestao.domain.traducao_databricks import (
    _normalizar_canal,
    _parse_bool,
    _parse_data,
    _parse_int,
    _parse_posicao,
    _texto,
)

_CENTAVO = Decimal("0.01")
_MAX_NUMERIC_14_2 = Decimal("999999999999.99")
logger = logging.getLogger(__name__)


class CanalOrigemInvalidoError(ValueError):
    """Canal externo é desconhecido ou conflita dentro do mesmo par."""


class FaturamentoOrigemInvalidaError(ValueError):
    """A foto de faturamento contém dados inválidos e não pode ser aplicada."""

    def __init__(self, diagnostico: "FaturamentoDiagnostico") -> None:
        self.diagnostico = diagnostico
        categorias = ",".join(diagnostico.razoes) or "desconhecida"
        super().__init__(
            "foto de faturamento inválida "
            f"(linhas={diagnostico.linhas}, descartadas={diagnostico.descartadas}, "
            f"categorias={categorias})"
        )


class FaturamentoDiagnostico(NamedTuple):
    linhas: int
    descartadas: int
    razoes: tuple[str, ...]


def _canal_obrigatorio(
    valor: object,
    *,
    source: str,
    nr_pedido: int,
    cd_prod_cor: str,
    sg_tamanho: str,
) -> str:
    valor_texto = None if valor is None else str(valor)
    canal = _normalizar_canal(valor_texto)
    if canal is not None:
        return canal
    bruto = str(valor).strip()[:64] if valor is not None else "<NULL>"
    raise CanalOrigemInvalidoError(
        "canal desconhecido na origem "
        f"source={source} nr_pedido={nr_pedido} "
        f"cd_prod_cor={cd_prod_cor} sg_tamanho={sg_tamanho} "
        f"canal={bruto!r}"
    )


def _registrar_canal_do_par(
    canais_por_par: dict[tuple[int, str], str],
    par: tuple[int, str],
    canal: str,
    *,
    source: str,
    sg_tamanho: str,
) -> None:
    canal_anterior = canais_por_par.setdefault(par, canal)
    if canal_anterior == canal:
        return
    canais = ",".join(sorted((canal_anterior, canal)))
    raise CanalOrigemInvalidoError(
        "canais misturados na origem "
        f"source={source} nr_pedido={par[0]} cd_prod_cor={par[1]} "
        f"sg_tamanho={sg_tamanho} canais={canais!r}"
    )


def _valor_monetario(v: object, *, permitir_negativo: bool = False) -> Decimal:
    """Converte moeda externa direto para Decimal dentro de Numeric(14, 2).

    Pedidos em aberto nunca aceitam valor negativo. O snapshot ERP, porém,
    também contém devoluções/ajustes contábeis e precisa preservar o sinal. A
    conversão continua exata em centavos e nunca passa por ``float``.
    """
    if isinstance(v, bool) or v is None:
        return Decimal(0)
    if isinstance(v, Decimal):
        valor = v
    else:
        bruto = str(v).strip().replace("R$", "").replace(" ", "")
        if not bruto:
            return Decimal(0)
        if "," in bruto:
            bruto = bruto.replace(".", "").replace(",", ".")
        try:
            valor = Decimal(bruto)
        except (InvalidOperation, ValueError):
            return Decimal(0)
    if not valor.is_finite() or (valor < 0 and not permitir_negativo):
        return Decimal(0)
    try:
        valor = valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return Decimal(0)
    return valor if abs(valor) <= _MAX_NUMERIC_14_2 else Decimal(0)


def _ratear_valor_repetido_por_tamanho(
    agrupado: dict[tuple, dict],
    totais_por_par: dict[tuple[int, str], Decimal],
    *,
    campo_quantidade: str,
) -> None:
    """Rateia o total comercial do par pelas quantidades de seus tamanhos.

    O Databricks repete o valor total de ``(pedido, produto)`` em cada linha de
    tamanho. Persistir esse valor como se fosse da linha multiplica o financeiro
    pelo número de tamanhos. O rateio usa Hamilton em centavos: parte inteira
    proporcional e, depois, um centavo para os maiores restos. Empates usam o
    tamanho em ordem lexical, tornando refresh e migration reprodutíveis.
    """
    chaves_por_par: dict[tuple[int, str], list[tuple]] = defaultdict(list)
    for chave in agrupado:
        chaves_por_par[(chave[0], chave[1])].append(chave)

    for par, chaves in chaves_por_par.items():
        if par not in totais_por_par:
            continue
        total = totais_por_par[par]
        total_centavos_assinado = int(
            (total * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )
        sinal = -1 if total_centavos_assinado < 0 else 1
        total_centavos = abs(total_centavos_assinado)
        quantidade_total = sum(
            int(agrupado[chave][campo_quantidade]) for chave in chaves
        )
        if quantidade_total <= 0:
            continue

        rateios: list[tuple[tuple, int, Decimal]] = []
        centavos_base = 0
        for chave in chaves:
            exato = (
                Decimal(total_centavos)
                * Decimal(int(agrupado[chave][campo_quantidade]))
                / Decimal(quantidade_total)
            )
            base = int(exato.quantize(Decimal(1), rounding=ROUND_DOWN))
            rateios.append((chave, base, exato - Decimal(base)))
            centavos_base += base

        residuo = total_centavos - centavos_base
        rateios.sort(key=lambda item: (-item[2], str(item[0][2])))
        contempladas = {item[0] for item in rateios[:residuo]}
        for chave, base, _resto in rateios:
            centavos = base + int(chave in contempladas)
            agrupado[chave]["vl_liquido"] = (Decimal(sinal * centavos) / 100).quantize(
                _CENTAVO
            )


def agregar_itens_pedidos(linhas: list[dict]) -> tuple[dict[tuple, dict], int]:
    """Agrega itens de pedidos duplicados pela chave (nr_pedido, cd_prod_cor,
    sg_tamanho), somando quantidades. ``vl_liquido`` é o total do par repetido
    pela dimensão tamanho e, por isso, é rateado proporcionalmente. Descarta
    linhas com identidade/quantidade inválida e pares sem total positivo.
    Retorna (agrupado, contagem de descartadas)."""
    agrupado: dict[tuple, dict] = {}
    valores_por_par: dict[tuple[int, str], set[Decimal]] = defaultdict(set)
    linhas_por_par: dict[tuple[int, str], int] = defaultdict(int)
    canais_por_par: dict[tuple[int, str], str] = {}
    descartadas = 0
    for linha in linhas:
        nr = _parse_int(linha.get("nr_pedido"))
        cd = str(linha.get("cd_prod_cor") or "").strip()
        tam = str(linha.get("sg_tamanho") or "").strip().upper()
        grupo = str(linha.get("ds_grupo") or "").strip()
        qt = _parse_int(linha.get("qt_entregar"))
        vl = _valor_monetario(linha.get("vl_liquido"))

        if nr <= 0 or qt <= 0 or not cd or not tam or not grupo:
            descartadas += 1
            continue
        canal = _canal_obrigatorio(
            linha.get("ds_tp_canal"),
            source="pending",
            nr_pedido=nr,
            cd_prod_cor=cd,
            sg_tamanho=tam,
        )

        par = (nr, cd)
        _registrar_canal_do_par(
            canais_por_par,
            par,
            canal,
            source="pending",
            sg_tamanho=tam,
        )
        linhas_por_par[par] += 1
        if vl > 0:
            valores_por_par[par].add(vl)

        chave = (nr, cd, tam)
        if chave in agrupado:
            agrupado[chave]["qt_entregar"] += qt
            continue

        agrupado[chave] = {
            "nr_pedido": nr,
            "cd_prod_cor": cd,
            "sg_tamanho": tam,
            "ds_grupo": grupo,
            "qt_entregar": qt,
            "vl_liquido": Decimal(0),
            "client": _texto(linha.get("nm_cliente")),
            "canal": canal,
            "status_credito": _texto(linha.get("status_credito")),
            "ds_produto": _texto(linha.get("nm_prod")),
            "data": _texto(linha.get("dt_emissao")),
            "indica_blacklist": _parse_bool(linha.get("indica_blacklist")),
        }

    pares_invalidos = {par for par in linhas_por_par if len(valores_por_par[par]) != 1}
    if pares_invalidos:
        descartadas += sum(linhas_por_par[par] for par in pares_invalidos)
        agrupado = {
            chave: item
            for chave, item in agrupado.items()
            if (chave[0], chave[1]) not in pares_invalidos
        }
    totais_por_par = {
        par: next(iter(valores))
        for par, valores in valores_por_par.items()
        if len(valores) == 1
    }
    _ratear_valor_repetido_por_tamanho(
        agrupado,
        totais_por_par,
        campo_quantidade="qt_entregar",
    )
    return agrupado, descartadas


class EstoqueDiagnostico(NamedTuple):
    """Contadores da agregação de estoque. Alimentam os logs e os guards do caso
    de uso (snapshot vazio, frescor da foto, canário de granularidade)."""

    ignoradas: int  # produto/tamanho vazio ou canal fora de Franquia/Multimarca
    sem_disponivel: int  # quantidade <= 0: não é estoque reservável
    duplicadas: int  # colisão de chave — a origem deveria dar 1 linha por chave
    datas: frozenset[date]  # dt_estoque distintas vistas nas linhas válidas


def agregar_estoque(linhas: list[dict]) -> tuple[dict[tuple, int], EstoqueDiagnostico]:
    """Traduz o estoque cru para a chave do domínio (cd_prod_cor, sg_tamanho, canal).

    Vocabulário externo da view `system_automation_estoque_filtrado`: `codigo_produto`,
    `tamanho`, `canal`, `quantidade_disponivel`, `dt_estoque`. O vocabulário
    interno é preservado de propósito — é o mesmo de `pedidos` e da chave de
    matching `f'{cd}_{tam}'`; traduzir aqui é o papel desta camada.

    A view entrega 1 linha por (canal, produto, tamanho) e já filtra o dia
    corrente na origem. A soma fica como defesa, mas colisão de chave passa a ser
    CONTADA (`duplicadas`): se aparecer, a granularidade da origem mudou sem aviso.

    Descarta linha com produto/tamanho vazio ou canal que não resolve
    (`ignoradas`) e linha com quantidade <= 0 (`sem_disponivel`) — só é reservável
    quem tem disponível > 0, e quantidade negativa é ruído da origem.
    """
    agregado: dict[tuple, int] = defaultdict(int)
    ignoradas = 0
    sem_disponivel = 0
    duplicadas = 0
    datas: set[date] = set()
    for linha in linhas:
        cd = str(linha.get("codigo_produto") or "").strip()
        tam = str(linha.get("tamanho") or "").strip().upper()
        canal = _normalizar_canal(linha.get("canal"))
        qt = _parse_int(linha.get("quantidade_disponivel"))
        if not cd or not tam or not canal:
            ignoradas += 1
            continue
        if qt <= 0:
            sem_disponivel += 1
            continue
        chave = (cd, tam, canal)
        if chave in agregado:
            duplicadas += 1
        agregado[chave] += qt
        data = _parse_data(linha.get("dt_estoque"))
        if data is not None:
            datas.add(data)
    diagnostico = EstoqueDiagnostico(
        ignoradas=ignoradas,
        sem_disponivel=sem_disponivel,
        duplicadas=duplicadas,
        datas=frozenset(datas),
    )
    return dict(agregado), diagnostico


def agregar_itens_processados_erp(linhas: list[dict]) -> tuple[dict[tuple, dict], int]:
    """Agrega itens já processados no ERP pela chave (nr_pedido, cd_prod_cor,
    sg_tamanho): soma qt, rateia o total repetido do par e faz OR nos flags
    indica_reserva/indica_embalado. Quantidade zero é uma posição/flag
    ERP legítima e permanece na grade; negativa tem peso zero. Canal fora do
    vocabulário F/MM aborta antes de substituir o snapshot. Retorna (agrupado,
    contagem de descartadas)."""
    agrupado: dict[tuple, dict] = {}
    valores_por_par: dict[tuple[int, str], set[Decimal]] = defaultdict(set)
    linhas_por_par: dict[tuple[int, str], int] = defaultdict(int)
    canais_por_par: dict[tuple[int, str], str] = {}
    descartadas = 0
    for linha in linhas:
        nr = _parse_int(linha.get("nr_pedido"))
        cd = str(linha.get("cd_prod_cor") or "").strip()
        tam = str(linha.get("sg_tamanho") or "").strip().upper()
        qt_distribuida = linha.get("qt_distribuida")
        qt = (
            _parse_int(qt_distribuida)
            if qt_distribuida is not None
            else _parse_int(linha.get("qt_entregar"))
        )
        if nr <= 0 or not cd or not tam:
            descartadas += 1
            continue
        qt = max(qt, 0)
        canal = _canal_obrigatorio(
            linha.get("ds_tp_canal"),
            source="erp",
            nr_pedido=nr,
            cd_prod_cor=cd,
            sg_tamanho=tam,
        )
        vl = _valor_monetario(
            linha.get("vl_liquido"),
            permitir_negativo=True,
        )

        par = (nr, cd)
        _registrar_canal_do_par(
            canais_por_par,
            par,
            canal,
            source="erp",
            sg_tamanho=tam,
        )
        linhas_por_par[par] += 1
        if vl != 0:
            valores_por_par[par].add(vl)

        chave = (nr, cd, tam)
        if chave in agrupado:
            agrupado[chave]["qt"] += qt
            agrupado[chave]["indica_reserva"] |= _parse_bool(
                linha.get("indica_reserva")
            )
            agrupado[chave]["indica_embalado"] |= _parse_bool(
                linha.get("indica_embalado")
            )
            continue

        agrupado[chave] = {
            "nr_pedido": nr,
            "cd_prod_cor": cd,
            "sg_tamanho": tam,
            "ds_grupo": _texto(linha.get("ds_grupo")),
            "ds_produto": _texto(linha.get("nm_prod")),
            "client": _texto(linha.get("nm_cliente")),
            "canal": canal,
            "data": _texto(linha.get("dt_emissao")),
            "qt": qt,
            "vl_liquido": Decimal(0),
            "indica_reserva": _parse_bool(linha.get("indica_reserva")),
            "indica_embalado": _parse_bool(linha.get("indica_embalado")),
        }

    pares_invalidos = {par for par in linhas_por_par if len(valores_por_par[par]) > 1}
    pares_sem_total = {par for par in linhas_por_par if not valores_por_par[par]}
    if pares_invalidos:
        descartadas += sum(linhas_por_par[par] for par in pares_invalidos)
        agrupado = {
            chave: item
            for chave, item in agrupado.items()
            if (chave[0], chave[1]) not in pares_invalidos
        }
    if pares_sem_total:
        logger.warning(
            "ERP preservou %d pares sem valor contabil; qty/flags permanecem "
            "com valor zero",
            len(pares_sem_total),
        )
    totais_por_par = {
        par: next(iter(valores))
        for par, valores in valores_por_par.items()
        if len(valores) == 1
    }
    _ratear_valor_repetido_por_tamanho(
        agrupado,
        totais_por_par,
        campo_quantidade="qt",
    )
    return agrupado, descartadas


def agregar_referencia_tamanhos(
    linhas: list[dict],
) -> tuple[dict[tuple, dict], int, dict[str, list[str]]]:
    """Agrega a referência tamanho->posição por (cd_prod_cor, sg_tamanho).
    Descarta linhas com cd/tam vazio ou nr_posicao fora de 1..48 (D-01).
    Detecta conflito quando dois tamanhos diferentes do mesmo produto apontam
    para a mesma posição: último vence, e o aviso é agregado por produto
    (nunca por linha) — D-03/D-04. O conflito NÃO remove nenhuma das linhas
    conflitantes de `agrupado`: ambas permanecem, cada uma na sua própria
    chave (cd, tam). Retorna (agrupado, descartadas, conflitos_por_produto)."""
    agrupado: dict[tuple, dict] = {}
    descartadas = 0
    posicao_por_produto: dict[str, dict[int, str]] = defaultdict(dict)
    conflitos_por_produto: dict[str, list[str]] = defaultdict(list)

    for linha in linhas:
        cd = str(linha.get("cd_prod_cor") or "").strip()
        tam = str(linha.get("sg_tamanho") or "").strip().upper()
        pos = _parse_posicao(linha.get("nr_posicao"))

        if not cd or not tam or pos is None:
            descartadas += 1
            continue

        tamanho_anterior = posicao_por_produto[cd].get(pos)
        if tamanho_anterior is not None and tamanho_anterior != tam:
            conflitos_por_produto[cd].append(f"{tamanho_anterior}/{tam}->pos{pos}")
        posicao_por_produto[cd][pos] = tam

        agrupado[(cd, tam)] = {"cd_prod_cor": cd, "sg_tamanho": tam, "nr_posicao": pos}

    return agrupado, descartadas, dict(conflitos_por_produto)


def _valor_faturamento(valor: object) -> Decimal | None:
    """Converte moeda externa sem passar por float e valida Numeric(14, 2)."""

    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, Decimal):
        convertido = valor
    else:
        bruto = str(valor).strip().replace("R$", "").replace(" ", "")
        if not bruto:
            return None
        if "," in bruto:
            bruto = bruto.replace(".", "").replace(",", ".")
        try:
            convertido = Decimal(bruto)
        except (InvalidOperation, ValueError):
            return None
    if not convertido.is_finite():
        return None
    try:
        convertido = convertido.quantize(_CENTAVO, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    if abs(convertido) > _MAX_NUMERIC_14_2:
        return None
    return convertido


def processar_faturamento_colecao(
    linhas: list[dict],
) -> tuple[list[dict], FaturamentoDiagnostico]:
    """Valida e deduplica a foto de faturamento de forma fail-closed.

    Uma linha inválida ou uma duplicata com valores conflitantes invalida a
    foto inteira. O diagnóstico contém apenas categorias bounded, nunca valores
    brutos da origem.
    """

    por_chave: dict[tuple[int, str], dict] = {}
    razoes: set[str] = set()
    descartadas = 0
    for linha in linhas:
        colecao = _parse_int(linha.get("colecao"))
        canal = _normalizar_canal(linha.get("canal"))
        vl_plan = _valor_faturamento(linha.get("vl_planejado"))
        vl_dist = _valor_faturamento(linha.get("vl_distribuido"))

        linha_invalida = False
        if colecao is None or colecao <= 0:
            razoes.add("colecao_invalida")
            linha_invalida = True
        if canal is None:
            razoes.add("canal_invalido")
            linha_invalida = True
        if vl_plan is None:
            razoes.add("vl_planejado_invalido")
            linha_invalida = True
        if vl_dist is None:
            razoes.add("vl_distribuido_invalido")
            linha_invalida = True
        if (
            linha_invalida
            or colecao is None
            or canal is None
            or vl_plan is None
            or vl_dist is None
        ):
            descartadas += 1
            continue

        item = {
            "colecao": colecao,
            "canal": canal,
            "vl_planejado": vl_plan,
            "vl_distribuido": vl_dist,
        }
        chave = (colecao, canal)
        anterior = por_chave.get(chave)
        if anterior is not None and anterior != item:
            razoes.add("duplicata_conflitante")
            descartadas += 1
            continue
        por_chave[chave] = item

    diagnostico = FaturamentoDiagnostico(
        linhas=len(linhas),
        descartadas=descartadas,
        razoes=tuple(sorted(razoes)),
    )
    if diagnostico.descartadas:
        raise FaturamentoOrigemInvalidaError(diagnostico)
    return list(por_chave.values()), diagnostico
