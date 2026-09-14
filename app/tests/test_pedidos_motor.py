"""Unit tests do motor de adequação (Core) — Etapa 0 do plano de extração DDD.
Todas as funções aqui são puras (sem I/O), então os testes não tocam o banco."""

import logging

from app.modules.pedidos.domain.orcamento_pedido import OrcamentoPedido
from app.modules.pedidos.domain.standby_motivo import (
    BLACKLIST,
    FURO_GRADE,
    MOTIVOS_VALIDOS,
    SEM_CREDITO,
    SEM_ESTOQUE,
)
from app.modules.pedidos.service import adequar_grade_produto as adequar
from app.modules.pedidos.service import (
    agrupar_por_produto,
    aplicar_tudo_ou_nada,
    canal_bucket,
    get_tamanho_idx,
    is_sem_credito,
    marcar_stand_by,
    processar_pedidos,
)
from app.tests.invariantes_motor_adequacao import (
    assert_estoque_nunca_excedido,
    assert_orcamento_nao_excedido,
)


def _item(sg_tamanho, qt_liquida, vl_liquido, **extra):
    base = {
        "nr_pedido": 1,
        "cd_prod_cor": "PROD1",
        "sg_tamanho": sg_tamanho,
        "ds_grupo": "Camisas",
        "qt_liquida": qt_liquida,
        "vl_liquido": vl_liquido,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# standby_motivo — vocabulário canônico
# ---------------------------------------------------------------------------


def test_standby_motivo_valores_canonicos_e_distintos():
    assert FURO_GRADE == "furo_grade"
    assert SEM_CREDITO == "sem_credito"
    assert SEM_ESTOQUE == "sem_estoque"
    assert BLACKLIST == "blacklist"
    assert len({FURO_GRADE, SEM_CREDITO, SEM_ESTOQUE, BLACKLIST}) == 4
    assert {FURO_GRADE, SEM_CREDITO, SEM_ESTOQUE, BLACKLIST} == MOTIVOS_VALIDOS


# ---------------------------------------------------------------------------
# canal_bucket / is_sem_credito / get_tamanho_idx
# ---------------------------------------------------------------------------


def test_canal_bucket():
    assert canal_bucket("Franquia") == "Franquia"
    assert canal_bucket("Multimarca") == "Multimarca"
    assert canal_bucket("Outro") == "Franquia"
    assert canal_bucket(None) == "Franquia"


def test_is_sem_credito():
    assert is_sem_credito("Sem Crédito") is True
    assert is_sem_credito("SEM CREDITO") is True
    assert is_sem_credito("sem credito") is True
    assert is_sem_credito("Com Crédito") is False
    assert is_sem_credito(None) is False
    assert is_sem_credito("") is False


def test_get_tamanho_idx():
    assert get_tamanho_idx("M") == 3  # ALFABETICO: XPP,PP,P,M,...
    assert get_tamanho_idx("42") == 42  # numérico puro
    assert get_tamanho_idx("XPTO") == 999  # desconhecido


# ---------------------------------------------------------------------------
# adequar_grade_produto
# ---------------------------------------------------------------------------


def test_adequar_grade_produto_estoque_suficiente_sem_ajuste():
    """Atualizado no plano 14-06: `adequar_grade_produto` recebe o `ledger`
    do pedido (ALOC-07) em vez de `tolerancia`, e não recalcula mais
    `vl_liquido`/`diff_valor` internamente — isso passou para
    `_recalcular_financeiro_produto` (FIX-02), aplicado pelo orquestrador
    depois das duas passadas. Este teste cobre só a decisão de quantidade
    da passada 1 quando o estoque já é suficiente (sem corte nenhum)."""
    itens = [_item("P", 5, 500.0), _item("M", 5, 500.0)]
    estoque = {"PROD1_P": 10, "PROD1_M": 10}
    ledger = OrcamentoPedido(
        nr_pedido=1,
        total_original=10,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )

    resultado = adequar(itens, estoque, "PROD1", ledger)

    for item in resultado:
        assert item["status_item"] == "Gerar OR"
        assert item["qt_liquida"] == 5
        assert item["qt_solicitada"] == 5


def test_processar_pedidos_orcamento_pedido_corte_atravessa_produtos_respeita_teto():
    """Substitui o teste antigo de tolerância `ceil` POR PRODUTO ISOLADO
    (regra que deixou de existir — ALOC-07/08) por um cenário ponta a ponta,
    via `processar_pedidos`, do orçamento ±5% do PEDIDO COMPLETO atravessando
    3 produtos.

    Pedido de 100 peças (34 + 33 + 33) em 3 produtos (A, B, C), cada um
    precisando de um corte de exatamente 2 peças. Orçamento de corte do
    pedido = floor(100 * 0,05) = 5. Ordem da passada 1 é por corte
    necessário crescente; como os 3 têm falta idêntica (2), o desempate é
    por cd_prod_cor ascendente — A e B consomem o orçamento (corte
    acumulado 2, depois 4) e C, processado por último, precisaria de mais 2
    mas só resta 1: vai para stand-by. Total efetivamente cortado fica em 4,
    nunca acima de 5."""
    dados = [
        _item("M", 34, 340.0, nr_pedido=1, cd_prod_cor="A", canal="Franquia"),
        _item("M", 33, 330.0, nr_pedido=1, cd_prod_cor="B", canal="Franquia"),
        _item("M", 33, 330.0, nr_pedido=1, cd_prod_cor="C", canal="Franquia"),
    ]
    estoque = {"Franquia": {"A_M": 32, "B_M": 31, "C_M": 31}}

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    assert resultado["selecionados"] == [(1, "A"), (1, "B")]
    assert resultado["preteridos"] == [(1, "C")]

    item_a = resultado["resultados"][(1, "A")][0]
    assert item_a["status_item"] == "Gerar OR"
    assert item_a["qt_liquida"] == 32  # 34 - 2 (corte)
    assert item_a["qt_solicitada"] == 34

    item_b = resultado["resultados"][(1, "B")][0]
    assert item_b["status_item"] == "Gerar OR"
    assert item_b["qt_liquida"] == 31  # 33 - 2 (corte)
    assert item_b["qt_solicitada"] == 33

    item_c = resultado["resultados"][(1, "C")][0]
    assert item_c["status_item"] == "Pedido em Stand By"
    assert item_c["qt_liquida"] == 33  # preservada, não reduzida
    assert (
        item_c["motivo_stand_by"]
        == "Orçamento do pedido: corte necessário excede o restante do orçamento de 5%"
    )

    total_cortado = (34 - item_a["qt_liquida"]) + (33 - item_b["qt_liquida"])
    assert total_cortado == 4
    assert total_cortado <= 5  # nunca acima de floor(100 * 0,05)

    assert_estoque_nunca_excedido(estoque["Franquia"], resultado)
    assert_orcamento_nao_excedido(
        total_original=100,
        adicionado_execucoes=[0],
        cortado_execucoes=[total_cortado],
        tolerancia=0.05,
    )


def test_processar_pedidos_orcamento_pedido_corte_e_adicao_nao_se_compensam():
    """ALOC-08 (14-VALIDATION.md, cenário nomeado "Não se compensam"): dentro
    do MESMO pedido, o orçamento de corte e o de adição são contadores
    independentes (ver `OrcamentoPedido`) — o produto A consome o orçamento
    de CORTE inteiro (5) e o produto B, no mesmo pedido, ainda recebe a
    ADIÇÃO inteira (5) do orçamento de adição. Se os dois orçamentos
    compartilhassem um único pool, B receberia 0 (o pool já teria sido
    esgotado pelo corte de A) — a asserção de `item_b` é o que prova que
    isso NÃO acontece."""
    dados = [
        _item("M", 60, 6000.0, nr_pedido=1, cd_prod_cor="A", canal="Franquia"),
        _item("M", 40, 4000.0, nr_pedido=1, cd_prod_cor="B", canal="Franquia"),
    ]
    # total do pedido = 60 + 40 = 100 -> limite_adicao == limite_corte == 5
    estoque = {"Franquia": {"A_M": 55, "B_M": 45}}

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    item_a = resultado["resultados"][(1, "A")][0]
    assert item_a["status_item"] == "Gerar OR"
    assert item_a["qt_liquida"] == 55  # corte de 5 -> orçamento de CORTE esgotado

    item_b = resultado["resultados"][(1, "B")][0]
    assert item_b["status_item"] == "Gerar OR"
    # +5 do orçamento de ADIÇÃO, intacto apesar do corte de A ter esgotado o
    # orçamento de CORTE do mesmo pedido — os dois não se compensam.
    assert item_b["qt_liquida"] == 45

    assert_estoque_nunca_excedido(estoque["Franquia"], resultado)
    assert_orcamento_nao_excedido(
        total_original=100,
        adicionado_execucoes=[5],
        cortado_execucoes=[5],
        tolerancia=0.05,
    )


def test_processar_pedidos_orcamento_acumulado_execucoes_nao_reseta():
    """ALOC-09 (14-VALIDATION.md, cenário nomeado "Double-spend"): o
    orçamento de corte consumido em uma execução anterior é threadado via
    `consumido_previo_corte_por_pedido` para a execução seguinte do MESMO
    pedido — a segunda chamada NUNCA enxerga o orçamento como se tivesse 5%
    novos disponíveis; o teto efetivo já nasce em 0 quando a execução
    anterior já esgotou o orçamento inteiro (ALOC-07/08/09)."""
    dados = [_item("M", 100, 10000.0, nr_pedido=1, cd_prod_cor="A", canal="Franquia")]

    # Execução 1: falta de 5 (100 - 95), exatamente o teto floor(100*0.05)=5.
    # Consome o orçamento de corte inteiro.
    estoque_execucao_1 = {"Franquia": {"A_M": 95}}
    resultado_1 = processar_pedidos(
        dados,
        estoque_execucao_1,
        ja_processados=set(),
        criterio="valor",
        tolerancia=0.05,
        total_original_por_pedido={1: 100},
        consumido_previo_adicao_por_pedido={1: 0},
        consumido_previo_corte_por_pedido={1: 0},
    )
    item_1 = resultado_1["resultados"][(1, "A")][0]
    assert item_1["status_item"] == "Gerar OR"
    assert item_1["qt_liquida"] == 95  # corte de 5, orçamento de corte esgotado

    # Execução 2: MESMO pedido, agora faltando ainda mais estoque (falta de
    # 10). Se o orçamento tivesse "resetado" para 5% novos, o corte ainda
    # seria concedido (o tudo-ou-nada exige só que a falta caiba no
    # restante). Mas o consumido da execução 1 (5) é passado como
    # `consumido_previo_corte_por_pedido`, então o teto efetivo desta
    # execução já nasce em 0 (floor(100*0.05) - 5 == 0) e o corte é recusado.
    estoque_execucao_2 = {"Franquia": {"A_M": 90}}
    resultado_2 = processar_pedidos(
        dados,
        estoque_execucao_2,
        ja_processados=set(),
        criterio="valor",
        tolerancia=0.05,
        total_original_por_pedido={1: 100},
        consumido_previo_adicao_por_pedido={1: 0},
        consumido_previo_corte_por_pedido={1: 5},
    )
    item_2 = resultado_2["resultados"][(1, "A")][0]
    assert item_2["status_item"] == "Pedido em Stand By"
    assert item_2["qt_liquida"] == 100  # preservada, corte recusado (orçamento zerado)
    assert (
        item_2["motivo_stand_by"]
        == "Orçamento do pedido: corte necessário excede o restante do orçamento de 5%"
    )

    assert_orcamento_nao_excedido(
        total_original=100,
        adicionado_execucoes=[0, 0],
        cortado_execucoes=[5, 0],
        tolerancia=0.05,
    )


def test_processar_pedidos_tolerancia_recebida_muda_o_teto_observavel():
    """Prova ponta a ponta de que `tolerancia_adequacao` deixou de ser
    parâmetro morto: o MESMO cenário (falta de 8 num pedido de 100 peças,
    produto de tamanho único — sem furo de grade) sai como stand-by com o
    teto de 5% e como OR com o teto de 10%."""
    dados = [_item("M", 100, 10000.0, nr_pedido=1, cd_prod_cor="A", canal="Franquia")]
    estoque = {"Franquia": {"A_M": 92}}

    resultado_005 = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )
    item_005 = resultado_005["resultados"][(1, "A")][0]
    assert item_005["status_item"] == "Pedido em Stand By"
    assert item_005["qt_liquida"] == 100

    resultado_010 = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.10
    )
    item_010 = resultado_010["resultados"][(1, "A")][0]
    assert item_010["status_item"] == "Gerar OR"
    assert item_010["qt_liquida"] == 92

    assert_orcamento_nao_excedido(
        total_original=100,
        adicionado_execucoes=[0],
        cortado_execucoes=[8],
        tolerancia=0.10,
    )


def test_processar_pedidos_motivo_de_stand_by_reflete_a_tolerancia_configurada():
    """Trava a FORMATAÇÃO da mensagem de stand-by por orçamento de corte:
    prova que o percentual interpolado muda com `tolerancia_adequacao` em vez
    de continuar hardcoded como "5%". O caso 0.10 é justamente o que pegaria
    o artefato de notação científica (`1E+1%`) se a implementação regredisse."""
    dados = [_item("M", 100, 10000.0, nr_pedido=1, cd_prod_cor="A", canal="Franquia")]
    estoque = {"Franquia": {"A_M": 85}}  # falta de 15, acima do teto de 5 e do de 10

    resultado_005 = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )
    item_005 = resultado_005["resultados"][(1, "A")][0]
    assert item_005["status_item"] == "Pedido em Stand By"
    assert item_005["qt_liquida"] == 100
    assert item_005["motivo_stand_by"] == (
        "Orçamento do pedido: corte necessário excede o restante do orçamento de 5%"
    )

    resultado_010 = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.10
    )
    item_010 = resultado_010["resultados"][(1, "A")][0]
    assert item_010["status_item"] == "Pedido em Stand By"
    assert item_010["qt_liquida"] == 100
    assert item_010["motivo_stand_by"] == (
        "Orçamento do pedido: corte necessário excede o restante do orçamento de 10%"
    )


def test_processar_pedidos_permutacao_de_produtos_produz_resultado_identico():
    """Invariante de permutação (14-VALIDATION.md): o MESMO pedido (mesmos
    produtos, mesmas quantidades) submetido com os itens em ordens de
    entrada diferentes produz exatamente o mesmo resultado por produto — a
    ordenação das duas passadas depende do valor computado (corte
    necessário, preço unitário), nunca da ordem de chegada."""

    def _montar_dados(ordem):
        base = {
            "A": _item("M", 34, 340.0, nr_pedido=1, cd_prod_cor="A", canal="Franquia"),
            "B": _item("M", 33, 330.0, nr_pedido=1, cd_prod_cor="B", canal="Franquia"),
            "C": _item("M", 33, 330.0, nr_pedido=1, cd_prod_cor="C", canal="Franquia"),
        }
        return [base[nome] for nome in ordem]

    estoque = {"Franquia": {"A_M": 32, "B_M": 31, "C_M": 31}}

    resultado_normal = processar_pedidos(
        _montar_dados(["A", "B", "C"]),
        estoque,
        ja_processados=set(),
        criterio="valor",
        tolerancia=0.05,
    )
    resultado_invertido = processar_pedidos(
        _montar_dados(["C", "B", "A"]),
        estoque,
        ja_processados=set(),
        criterio="valor",
        tolerancia=0.05,
    )

    assert resultado_normal["selecionados"] == resultado_invertido["selecionados"]
    assert resultado_normal["preteridos"] == resultado_invertido["preteridos"]

    for par in ((1, "A"), (1, "B"), (1, "C")):
        itens_normal = resultado_normal["resultados"][par]
        itens_invertido = resultado_invertido["resultados"][par]
        assert len(itens_normal) == len(itens_invertido)
        for item_normal, item_invertido in zip(
            itens_normal, itens_invertido, strict=True
        ):
            assert item_normal["status_item"] == item_invertido["status_item"]
            assert item_normal["qt_liquida"] == item_invertido["qt_liquida"]
            assert item_normal["vl_liquido"] == item_invertido["vl_liquido"]


# ---------------------------------------------------------------------------
# ALOC-09 — fallback ruidoso: logger.warning quando o placeholder é usado
# ---------------------------------------------------------------------------


def test_processar_pedidos_aloc09_alerta_quando_orcamento_nao_fornecido(caplog):
    dados = [_item("M", 10, 1000.0, nr_pedido=1, canal="Franquia")]
    estoque = {"Franquia": {"PROD1_M": 10}}

    with caplog.at_level(
        logging.WARNING, logger="app.modules.pedidos.domain.motor_adequacao"
    ):
        processar_pedidos(
            dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
        )

    mensagens = [registro.getMessage() for registro in caplog.records]
    assert any(
        "ALOC-09" in mensagem and "placeholder de execução única" in mensagem
        for mensagem in mensagens
    )


def test_processar_pedidos_aloc09_sem_alerta_quando_orcamento_fornecido(caplog):
    dados = [_item("M", 10, 1000.0, nr_pedido=1, canal="Franquia")]
    estoque = {"Franquia": {"PROD1_M": 10}}

    with caplog.at_level(
        logging.WARNING, logger="app.modules.pedidos.domain.motor_adequacao"
    ):
        processar_pedidos(
            dados,
            estoque,
            ja_processados=set(),
            criterio="valor",
            tolerancia=0.05,
            total_original_por_pedido={1: 10},
            consumido_previo_adicao_por_pedido={1: 0},
            consumido_previo_corte_por_pedido={1: 0},
        )

    mensagens = [registro.getMessage() for registro in caplog.records]
    assert not any("ALOC-09" in mensagem for mensagem in mensagens)


def test_marcar_stand_by_preserva_valores_e_remove_campos_transitorios():
    itens = [_item("P", 5, 500.0, is_ext=True, comentario="x", aceita_adequacao=True)]

    resultado = marcar_stand_by(itens)

    assert resultado[0]["status_item"] == "Pedido em Stand By"
    assert resultado[0]["qt_liquida"] == 5
    assert resultado[0]["vl_liquido"] == 500.0
    assert resultado[0]["diff_valor"] == 0.0
    assert (
        resultado[0]["motivo_stand_by"]
        == "Preterido: estoque insuficiente para todos os pedidos do produto"
    )
    assert "is_ext" not in resultado[0]
    assert "comentario" not in resultado[0]
    assert "aceita_adequacao" not in resultado[0]


# ---------------------------------------------------------------------------
# aplicar_tudo_ou_nada
# ---------------------------------------------------------------------------


def test_aplicar_tudo_ou_nada_reserva_grade_completa_quando_estoque_suficiente():
    itens = [_item("P", 5, 500.0), _item("M", 5, 500.0)]
    estoque = {"PROD1_P": 10, "PROD1_M": 10}

    resultado = aplicar_tudo_ou_nada(itens, estoque, "PROD1")

    for item in resultado:
        assert item["status_item"] == "Gerar OR"
        assert item["qt_liquida"] == 5
        assert item["qt_solicitada"] == 5  # ALOC-02: nunca difere do pedido
        assert item["vl_liquido"] == 500.0
        assert item["diff_valor"] == 0.0


def test_aplicar_tudo_ou_nada_falta_em_um_unico_tamanho_manda_grade_inteira_para_stand_by():
    itens = [_item("P", 5, 500.0), _item("M", 5, 500.0)]
    estoque = {"PROD1_P": 10, "PROD1_M": 3}  # só M falta

    resultado = aplicar_tudo_ou_nada(itens, estoque, "PROD1")

    # AMBOS vão a stand-by, inclusive P (que isoladamente teria sobra): a
    # decisão é da grade inteira, não tamanho a tamanho.
    for item in resultado:
        assert item["status_item"] == "Pedido em Stand By"
        assert item["qt_liquida"] == 5  # nunca reduzida
        assert item["motivo_stand_by"] == "Sem adequação: grade completa indisponível"


def test_aplicar_tudo_ou_nada_nao_decrementa_estoque_por_si_so():
    itens = [_item("P", 5, 500.0), _item("M", 5, 500.0)]
    estoque = {"PROD1_P": 10, "PROD1_M": 10}

    aplicar_tudo_ou_nada(itens, estoque, "PROD1")

    # A função nunca decrementa estoque; o commit é responsabilidade exclusiva
    # de quem chama (mesmo contrato de adequar_grade_produto).
    assert estoque == {"PROD1_P": 10, "PROD1_M": 10}


# ---------------------------------------------------------------------------
# agrupar_por_produto
# ---------------------------------------------------------------------------


def test_agrupar_por_produto_ignora_pares_ja_processados():
    dados = [
        {"nr_pedido": 1, "cd_prod_cor": "A"},
        {"nr_pedido": 2, "cd_prod_cor": "A"},
        {"nr_pedido": 3, "cd_prod_cor": "B"},
    ]
    resultado = agrupar_por_produto(dados, ja_processados={(2, "A")})

    assert set(resultado["A"].keys()) == {1}
    assert set(resultado["B"].keys()) == {3}


def test_agrupar_por_produto_pedido_processado_num_produto_segue_elegivel_no_outro():
    """O skip é por PAR, não por pedido: é o que faz o faturamento parcial
    funcionar. O pedido 1 já gerou OR do produto A e continua aberto para o B."""
    dados = [
        {"nr_pedido": 1, "cd_prod_cor": "A"},
        {"nr_pedido": 1, "cd_prod_cor": "B"},
    ]
    resultado = agrupar_por_produto(dados, ja_processados={(1, "A")})

    assert "A" not in resultado
    assert set(resultado["B"].keys()) == {1}


# ---------------------------------------------------------------------------
# processar_pedidos — cenário completo: prioridade por valor, crédito bloqueado,
# estoque parcial disputado entre dois pedidos do mesmo produto/canal.
# ---------------------------------------------------------------------------


def test_processar_pedidos_prioriza_maior_valor_e_isola_sem_credito():
    dados = [
        _item(
            "M", 10, 1000.0, nr_pedido=1, canal="Franquia", status_credito="Com Credito"
        ),
        _item(
            "M", 10, 500.0, nr_pedido=2, canal="Franquia", status_credito="Com Credito"
        ),
        _item(
            "M", 5, 300.0, nr_pedido=3, canal="Franquia", status_credito="Sem Credito"
        ),
    ]
    estoque = {"Franquia": {"PROD1_M": 15}}

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    # Grão de saída: o par (nr_pedido, cd_prod_cor). Crédito é do CLIENTE, então
    # bloqueados_credito segue por nr_pedido.
    assert resultado["selecionados"] == [(1, "PROD1")]
    assert resultado["preteridos"] == [(2, "PROD1")]
    assert resultado["bloqueados_credito"] == [3]
    assert resultado["pares_processados"] == {(1, "PROD1")}

    item1 = resultado["resultados"][(1, "PROD1")][0]
    assert item1["status_item"] == "Gerar OR"
    assert item1["qt_liquida"] == 10
    assert item1["vl_liquido"] == 1000.0
    assert item1["qt_solicitada"] == 10  # atendido integralmente

    item2 = resultado["resultados"][(2, "PROD1")][0]
    assert item2["status_item"] == "Pedido em Stand By"
    assert item2["qt_liquida"] == 10  # preservado, não reduzido
    # Atualizado no plano 14-06 (ALOC-02/ALOC-03/ALOC-07/ALOC-08): sob a
    # regra nova, o pedido 2 é um pedido de UM único produto (total do
    # pedido = 10), então seu orçamento de corte é floor(10 * 5%) == 0 — o
    # corte necessário (5, decorrente do estoque já consumido pelo pedido 1
    # de maior prioridade) nunca cabe nesse orçamento zerado, e
    # `adequar_grade_produto` delega o stand-by a `marcar_stand_by` (que
    # SEMPRE inclui `motivo_stand_by`, consistente com furo de grade e
    # crédito) em vez de uma ramificação inline sem esse campo, como era o
    # comportamento antes do ledger por pedido.
    assert item2["motivo_stand_by"] == (
        "Orçamento do pedido: corte necessário excede o restante do orçamento de 5%"
    )

    item3 = resultado["resultados"][(3, "PROD1")][0]
    assert item3["status_item"] == "Pedido em Stand By"
    assert item3["motivo_stand_by"] == "Aguardando liberação de crédito"


def test_processar_pedidos_particiona_por_canal_sem_misturar_estoque():
    dados = [
        _item(
            "M",
            10,
            1000.0,
            nr_pedido=10,
            cd_prod_cor="X",
            canal="Franquia",
            status_credito="Com Credito",
        ),
        _item(
            "M",
            10,
            1000.0,
            nr_pedido=20,
            cd_prod_cor="X",
            canal="Multimarca",
            status_credito="Com Credito",
        ),
    ]
    estoque = {"Franquia": {"X_M": 10}, "Multimarca": {}}

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    assert resultado["selecionados"] == [(10, "X")]
    assert resultado["preteridos"] == [(20, "X")]  # sem estoque no canal Multimarca


def test_processar_pedidos_sem_canal_vai_para_stand_by_sem_consumir_franquia():
    """Canal irreconhecível NÃO cai no fallback 'Franquia' no matching: adivinhar
    o canal alocaria peça do canal errado, que é o que o isolamento impede.
    O par vai para stand-by e não é marcado como processado, então reaparece se a
    origem corrigir o rótulo."""
    dados = [
        _item(
            "M",
            10,
            1000.0,
            nr_pedido=10,
            cd_prod_cor="X",
            canal="Franquia",
            status_credito="Com Credito",
        ),
        _item(
            "M",
            10,
            900.0,
            nr_pedido=20,
            cd_prod_cor="X",
            canal=None,
            status_credito="Com Credito",
        ),
        _item(
            "M",
            10,
            800.0,
            nr_pedido=30,
            cd_prod_cor="X",
            canal="CANAL_NOVO",
            status_credito="Com Credito",
        ),
    ]
    estoque = {"Franquia": {"X_M": 10}, "Multimarca": {}}

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    assert resultado["selecionados"] == [(10, "X")]
    assert resultado["preteridos"] == [(20, "X"), (30, "X")]
    # Nenhum par sem canal é marcado como processado.
    assert resultado["pares_processados"] == {(10, "X")}

    for nr in (20, 30):
        item = resultado["resultados"][(nr, "X")][0]
        assert item["status_item"] == "Pedido em Stand By"
        assert "Canal não identificado" in item["motivo_stand_by"]
        assert item["qt_liquida"] == 10  # preservado, não reduzido

    # O pedido de Franquia levou o estoque inteiro: os sem canal não o tocaram.
    assert resultado["resultados"][(10, "X")][0]["qt_liquida"] == 10


def test_processar_pedidos_sem_canal_grava_preteridos_motivo_fallback_defensivo():
    """Ramo defensivo `pares_sem_canal` (achado A1, 16-01-PLAN.md): código morto
    no caminho real de `build_processing_plan` (que já garante canal canônico
    antes do motor rodar), mas a invariante `len(preteridos) ==
    len(preteridos_motivo)` precisa valer SEMPRE, mesmo neste caminho morto —
    por isso o fallback `SEM_ESTOQUE` também é gravado aqui."""
    dados = [
        _item(
            "M",
            10,
            1000.0,
            nr_pedido=10,
            cd_prod_cor="X",
            canal="Franquia",
            status_credito="Com Credito",
        ),
        _item(
            "M",
            10,
            900.0,
            nr_pedido=20,
            cd_prod_cor="X",
            canal=None,
            status_credito="Com Credito",
        ),
        _item(
            "M",
            10,
            800.0,
            nr_pedido=30,
            cd_prod_cor="X",
            canal="CANAL_NOVO",
            status_credito="Com Credito",
        ),
    ]
    estoque = {"Franquia": {"X_M": 10}, "Multimarca": {}}

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    assert resultado["preteridos_motivo"][(20, "X")] == SEM_ESTOQUE
    assert resultado["preteridos_motivo"][(30, "X")] == SEM_ESTOQUE
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"]) == 2


def test_processar_pedidos_sem_canal_respeita_pares_ja_processados():
    dados = [
        _item(
            "M",
            5,
            500.0,
            nr_pedido=1,
            cd_prod_cor="X",
            canal=None,
            status_credito="Com Credito",
        ),
    ]

    resultado = processar_pedidos(
        dados, {}, ja_processados={(1, "X")}, criterio="valor", tolerancia=0.05
    )

    assert resultado["preteridos"] == []
    assert resultado["resultados"] == {}


def test_processar_pedidos_desempate_por_nr_pedido_crescente_independente_da_ordem_de_entrada():
    """I6 (14-VALIDATION.md): dois pedidos empatados em score (vl_liquido) e em
    quantidade de produtos disponíveis só podem ser desempatados de um jeito —
    o de MENOR nr_pedido vence. A ordem de entrada da lista `dados` (que reflete
    a ordem de chegada do snapshot, não garantida pela origem) não pode mudar
    o vencedor: é o que fecha a variação de plan_hash entre execuções
    "iguais" (ALOC-06)."""
    item_nr2 = _item(
        "M", 10, 1000.0, nr_pedido=2, canal="Franquia", status_credito="Com Credito"
    )
    item_nr5 = _item(
        "M", 10, 1000.0, nr_pedido=5, canal="Franquia", status_credito="Com Credito"
    )
    estoque = {"Franquia": {"PROD1_M": 10}}

    resultado_ordem_normal = processar_pedidos(
        [item_nr2, item_nr5],
        estoque,
        ja_processados=set(),
        criterio="valor",
        tolerancia=0.05,
    )
    resultado_ordem_invertida = processar_pedidos(
        [item_nr5, item_nr2],
        estoque,
        ja_processados=set(),
        criterio="valor",
        tolerancia=0.05,
    )

    for resultado in (resultado_ordem_normal, resultado_ordem_invertida):
        assert resultado["selecionados"] == [(2, "PROD1")]
        assert resultado["preteridos"] == [(5, "PROD1")]

    # Reprodutibilidade completa do plano de seleção, não só das listas de
    # chaves: o conteúdo item a item de cada par disputado também tem que
    # bater byte-a-byte entre as duas ordens de entrada.
    for par in ((2, "PROD1"), (5, "PROD1")):
        itens_normal = resultado_ordem_normal["resultados"][par]
        itens_invertida = resultado_ordem_invertida["resultados"][par]
        assert len(itens_normal) == len(itens_invertida)
        for item_normal, item_invertida in zip(
            itens_normal, itens_invertida, strict=True
        ):
            assert item_normal["status_item"] == item_invertida["status_item"]
            assert item_normal["qt_liquida"] == item_invertida["qt_liquida"]
            assert item_normal["vl_liquido"] == item_invertida["vl_liquido"]


def test_processar_pedidos_fatura_um_produto_e_deixa_o_outro_em_stand_by():
    """Faturamento PARCIAL, o caso que a granularidade por par existe para servir:
    o mesmo pedido gera OR do produto com estoque e fica em stand-by no outro."""
    dados = [
        _item(
            "M",
            5,
            500.0,
            nr_pedido=1,
            cd_prod_cor="COM_ESTOQUE",
            canal="Franquia",
            status_credito="Com Credito",
        ),
        _item(
            "M",
            5,
            500.0,
            nr_pedido=1,
            cd_prod_cor="SEM_ESTOQUE",
            canal="Franquia",
            status_credito="Com Credito",
        ),
    ]
    estoque = {"Franquia": {"COM_ESTOQUE_M": 5, "SEM_ESTOQUE_M": 0}}

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    assert resultado["selecionados"] == [(1, "COM_ESTOQUE")]
    assert resultado["preteridos"] == [(1, "SEM_ESTOQUE")]
    # Só o par com estoque é marcado como processado; o outro volta à listagem.
    assert resultado["pares_processados"] == {(1, "COM_ESTOQUE")}
    assert len(resultado["pares_processados"]) == 1


def test_processar_pedidos_preteridos_motivo_e_campo_aditivo_chaves_existentes_preservadas():
    """K3 (16-VALIDATION.md): `preteridos_motivo` é a 6ª chave, puramente
    aditiva — as 5 chaves antigas de retorno de `processar_pedidos` não
    mudam de forma."""
    dados = [
        _item(
            "M",
            5,
            500.0,
            nr_pedido=1,
            cd_prod_cor="COM_ESTOQUE",
            canal="Franquia",
            status_credito="Com Credito",
        ),
        _item(
            "M",
            5,
            500.0,
            nr_pedido=1,
            cd_prod_cor="SEM_ESTOQUE",
            canal="Franquia",
            status_credito="Com Credito",
        ),
    ]
    estoque = {"Franquia": {"COM_ESTOQUE_M": 5, "SEM_ESTOQUE_M": 0}}

    resultado = processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    assert set(resultado.keys()) == {
        "resultados",
        "selecionados",
        "preteridos",
        "bloqueados_credito",
        "pares_processados",
        "preteridos_motivo",
    }
    assert resultado["preteridos"] == [(1, "SEM_ESTOQUE")]
    assert resultado["preteridos_motivo"] == {(1, "SEM_ESTOQUE"): SEM_ESTOQUE}
    assert len(resultado["preteridos"]) == len(resultado["preteridos_motivo"])
