"""Testes puros (sem banco/HTTP) de montar_linha_linx."""

from app.modules.pedidos.domain.ordem_reserva_linx import montar_linha_linx


def test_montar_linha_linx_referencia_vazia_retorna_none():
    """D-01: produto sem nenhuma posição na referência não gera linha Linx."""
    itens = [{"sg_tamanho": "M", "qt_liquida": 5, "vl_liquido": 500.0}]

    linha = montar_linha_linx(1, "X|1", itens, referencia_produto={}, tipo="com")

    assert linha is None


def test_montar_linha_linx_grade_parcialmente_convertida_continua_gerando_linha():
    """Distinção de D-01 vs. D-04 da Fase 2: referência não vazia mas com
    tamanho faltando (aqui "XG") continua gerando a linha normalmente.

    CR-01: o tamanho ignorado ("XG") NÃO entra nos totais — qtde_embalada e
    valor_embalado contam só o tamanho que virou posição ("M"), então a soma
    das posições bate com o total declarado ao Linx."""
    itens = [
        {"sg_tamanho": "M", "qt_liquida": 5, "vl_liquido": 500.0},
        {"sg_tamanho": "XG", "qt_liquida": 2, "vl_liquido": 200.0},
    ]
    referencia_produto = {"M": 5}

    linha = montar_linha_linx(1, "X|1", itens, referencia_produto, tipo="com")

    assert linha is not None
    assert linha["e5"] == 5
    # XG (2 peças, R$200) ficou de fora dos totais — só M conta.
    assert linha["qtde_embalada"] == 5
    assert linha["valor_embalado"] == 500.0
    # Invariante CR-01: soma das 48 posições == qtde_embalada.
    soma_posicoes = sum(linha[f"e{n}"] for n in range(1, 49))
    assert soma_posicoes == linha["qtde_embalada"]


def test_montar_linha_linx_nenhum_tamanho_converte_gera_linha_zerada_consistente():
    """CR-01 (pior caso): referência não vazia, mas nenhum tamanho do pedido tem
    posição. A linha é gerada (referencia_produto não é vazio -> não é o caso
    D-01), porém 100% consistente: todas as posições em zero, qtde_embalada 0,
    valor_embalado 0 e preco1 None. Nunca sai uma grade divergindo do total."""
    itens = [
        {"sg_tamanho": "M", "qt_liquida": 5, "vl_liquido": 500.0},
        {"sg_tamanho": "G", "qt_liquida": 3, "vl_liquido": 300.0},
    ]
    referencia_produto = {"P": 1, "XG": 2}  # não cobre nenhum tamanho do pedido

    linha = montar_linha_linx(1, "X|1", itens, referencia_produto, tipo="com")

    assert linha is not None
    assert linha["qtde_embalada"] == 0
    assert linha["valor_embalado"] == 0
    assert linha["preco1"] is None
    soma_posicoes = sum(linha[f"e{n}"] for n in range(1, 49))
    assert soma_posicoes == linha["qtde_embalada"] == 0


def test_montar_linha_linx_preenche_campos_e_grade_posicional():
    itens = [
        {
            "sg_tamanho": "M",
            "qt_liquida": 5,
            "vl_liquido": 500.0,
            "client": "Cliente XYZ",
        },
        {"sg_tamanho": "G", "qt_liquida": 3, "vl_liquido": 300.0},
    ]
    referencia_produto = {"M": 1, "G": 2}

    linha = montar_linha_linx(
        42, "ML.18.0315|001", itens, referencia_produto, tipo="sem"
    )

    assert linha is not None
    assert linha["produto"] == "ML.18.0315"
    assert linha["cor_produto"] == "001"
    assert linha["pedido"] == 42
    assert linha["nr_pedido"] == 42
    assert linha["nome_clifor"] == "Cliente XYZ"
    assert linha["tipo"] == "sem"
    assert linha["e1"] == 5
    assert linha["e2"] == 3


def test_montar_linha_linx_coerencia_aritmetica_soma_exata_do_csv_real():
    """D-03: amostra real do Linx — 311,24 x 3 = 933,72."""
    itens = [{"sg_tamanho": "M", "qt_liquida": 3, "vl_liquido": 933.72}]
    referencia_produto = {"M": 1}

    linha = montar_linha_linx(1, "X|1", itens, referencia_produto, tipo="com")

    assert linha is not None
    assert linha["valor_embalado"] == 933.72
    assert linha["qtde_embalada"] == 3
    assert linha["preco1"] == 311.24


def test_montar_linha_linx_coerencia_aritmetica_aceita_divergencia_de_centavos():
    """D-03: valor_embalado é a fonte de verdade; preco1 * qtde_embalada pode
    divergir em centavos do valor_embalado original (33.33 * 3 == 99.99,
    != 100.00) — isso é aceito, nunca recalculamos valor_embalado a partir
    de preco1 * qtde_embalada."""
    itens = [
        {"sg_tamanho": "M", "qt_liquida": 1, "vl_liquido": 40.00},
        {"sg_tamanho": "G", "qt_liquida": 2, "vl_liquido": 60.00},
    ]
    referencia_produto = {"M": 1, "G": 2}

    linha = montar_linha_linx(1, "X|1", itens, referencia_produto, tipo="com")

    assert linha is not None
    assert linha["valor_embalado"] == 100.00
    assert linha["qtde_embalada"] == 3
    assert linha["preco1"] == 33.33


def test_montar_linha_linx_cd_prod_cor_sem_separador_e_defensivo():
    itens = [{"sg_tamanho": "M", "qt_liquida": 1, "vl_liquido": 10.0}]
    referencia_produto = {"M": 1}

    linha = montar_linha_linx(1, "PRODSEMPIPE", itens, referencia_produto, tipo="com")

    assert linha is not None
    assert linha["produto"] == "PRODSEMPIPE"
    assert linha["cor_produto"] is None


def test_montar_linha_linx_colunas_sem_fonte_nao_aparecem_no_dict():
    """Critério 5 do ROADMAP: colunas sem fonte conhecida hoje nunca são
    inventadas — não aparecem no dict devolvido."""
    itens = [{"sg_tamanho": "M", "qt_liquida": 1, "vl_liquido": 10.0}]
    referencia_produto = {"M": 1}

    linha = montar_linha_linx(1, "X|1", itens, referencia_produto, tipo="com")
    assert linha is not None

    colunas_sem_fonte = {
        "filial",
        "item",
        "pedido_cor_produto",
        "romaneio",
        "caixa",
        "pedido_produto",
        "packs",
        "entrega",
        "caixa_fechada",
        "representante",
        "ipi",
        "preco2",
        "preco3",
        "preco4",
        "desconto_item",
        "origem",
        "mata_saldo",
        "item_pedido",
        "ordem_producao",
        "licenciado_royalties",
        "percent_desconto",
        "caixa_virtual",
    }

    assert colunas_sem_fonte & set(linha.keys()) == set()


def test_montar_linha_linx_qtde_embalada_zero_nao_lanca_excecao():
    itens = [{"sg_tamanho": "M", "qt_liquida": 0, "vl_liquido": 0.0}]
    referencia_produto = {"M": 1}

    linha = montar_linha_linx(1, "X|1", itens, referencia_produto, tipo="com")

    assert linha is not None
    assert linha["preco1"] is None
