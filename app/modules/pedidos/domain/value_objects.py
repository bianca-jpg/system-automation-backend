"""Value Objects puros do motor de adequação: Canal, ChaveEstoque, Tamanho."""

CANAIS = ("Franquia", "Multimarca")


def normalizar_canal(canal: str | None) -> str | None:
    """Canal do domínio a partir de um rótulo qualquer.

    'FRANQUIA*'/'FRQ*' -> 'Franquia'; 'MULTIMARCA*'/'MM*' -> 'Multimarca';
    o que não bater (inclusive None) -> None. Definição ÚNICA da regra dentro do
    contexto de Adequação & Reserva — antes existia uma cópia em
    application/casos_uso.py. (A ingestão tem a sua própria, de propósito: é a
    anti-corruption layer que traduz o vocabulário do Databricks.)
    """
    if not canal:
        return None
    c = str(canal).strip().upper()
    if c.startswith("FRANQUIA") or c.startswith("FRQ"):
        return "Franquia"
    if c.startswith("MULTIMARCA") or c.startswith("MM"):
        return "Multimarca"
    return None


def canal_bucket(canal: str | None) -> str:
    """Canal ('Franquia'|'Multimarca') usado para casar pedido<->estoque.

    O estoque é isolado por canal, então errar aqui aloca estoque do canal
    errado. Reconhece as variações via `normalizar_canal` (antes comparava só
    com os literais exatos, e um 'MULTIMARCA VAREJO' caía silenciosamente no
    fallback 'Franquia'). Irreconhecível ou ausente -> 'Franquia'.
    """
    return normalizar_canal(canal) or "Franquia"


def montar_chave_estoque(cd_prod_cor: str, sg_tamanho: str) -> str:
    """Chave composta do dict plano de estoque: f'{cd_prod_cor}_{sg_tamanho}'."""
    return f"{cd_prod_cor}_{sg_tamanho}"


RANKINGS = {
    "ALFABETICO": ["XPP", "PP", "P", "M", "G", "GG", "XGG", "XXG", "5G"],
    "CAMISA_NUMERICA": [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "4A6",
        "6A8",
        "8A10",
        "10A12",
        "12A14",
        "14A16",
    ],
    "ALFAIATARIA": [
        "46",
        "46M",
        "46L",
        "48",
        "48M",
        "48L",
        "50",
        "50M",
        "50L",
        "52",
        "52M",
        "52L",
    ],
    "NUMERICO_CALCA": [
        "25/26",
        "26",
        "27",
        "28",
        "29",
        "30",
        "32",
        "34",
        "36",
        "38",
        "40",
        "42",
        "44",
        "46",
    ],
    "CALCADO": ["37", "38", "39", "40", "41", "42", "43", "44", "45", "46"],
    "CINTO_CM": ["85", "90", "95", "100", "105", "110", "115"],
    "UNICO": ["UN"],
}
RANKING_MAP = {
    tipo: {tam: idx for idx, tam in enumerate(tams)} for tipo, tams in RANKINGS.items()
}


def get_tamanho_idx(sg_tamanho: str) -> int:
    tam_limpo = sg_tamanho.replace(".", "").upper()
    # Tamanhos puramente numéricos (calça/terno 38-62, cinto 85-115) ordenam
    # pelo próprio número — cobre faixas não enumeradas nos RANKINGS. Dentro de
    # uma mesma grade (mesmo cd_prod_cor) os tamanhos são do mesmo tipo, então a
    # ordem relativa para detectar extremos (P/menor e maior) fica consistente.
    if tam_limpo.isdigit():
        return int(tam_limpo)
    for mapa in RANKING_MAP.values():
        if tam_limpo in mapa:
            return mapa[tam_limpo]
    return 999
