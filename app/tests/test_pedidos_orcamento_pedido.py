"""Testes unitários do ledger de orçamento ±5% por pedido completo
(OrcamentoPedido) — ALOC-07/08/09, isolados de motor_adequacao.py."""

from app.modules.pedidos.domain.orcamento_pedido import OrcamentoPedido

# ---------------------------------------------------------------------------
# limite / floor inteiro, nunca ceil nem float
# ---------------------------------------------------------------------------


def test_orcamento_pedido_limite_e_floor_inteiro_nunca_ceil_nem_float():
    ledger = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )
    assert ledger.limite_adicao == 5
    assert ledger.limite_corte == 5
    assert isinstance(ledger.limite_adicao, int)
    assert isinstance(ledger.limite_corte, int)

    # floor(19 * 0.05) == floor(0.95) == 0 — nunca arredonda para cima (ceil
    # daria 1).
    ledger_pequeno = OrcamentoPedido(
        nr_pedido=2,
        total_original=19,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )
    assert ledger_pequeno.limite_adicao == 0
    assert ledger_pequeno.limite_corte == 0


def test_orcamento_pedido_restante_inicial_e_o_limite_menos_o_consumido_previo():
    ledger = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=2,
        consumido_previo_corte=1,
        tolerancia=0.05,
    )
    assert ledger.restante_adicao == 3
    assert ledger.restante_corte == 4


# ---------------------------------------------------------------------------
# dois orçamentos independentes — não se compensam (ALOC-08)
# ---------------------------------------------------------------------------


def test_orcamento_pedido_dois_orcamentos_nao_se_compensam():
    ledger = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=5,  # esgota o orçamento de corte (limite_corte == 5)
        tolerancia=0.05,
    )
    assert ledger.restante_corte == 0
    # o orçamento de adição continua intocado, cheio — os dois são estado
    # independente dentro do mesmo ledger.
    assert ledger.restante_adicao == 5


# ---------------------------------------------------------------------------
# consumir_corte — tudo ou nada
# ---------------------------------------------------------------------------


def test_orcamento_pedido_corte_e_tudo_ou_nada_recusa_sem_decrementar_quando_excede():
    ledger = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )
    assert ledger.restante_corte == 5

    concedido = ledger.consumir_corte(3)
    assert concedido is True
    assert ledger.restante_corte == 2

    # pedir mais do que resta: recusa e NÃO decrementa nada (nem parcial).
    concedido = ledger.consumir_corte(3)
    assert concedido is False
    assert ledger.restante_corte == 2

    # mas o que resta ainda cabe.
    concedido = ledger.consumir_corte(2)
    assert concedido is True
    assert ledger.restante_corte == 0


def test_orcamento_pedido_consumir_corte_negativo_levanta_value_error():
    ledger = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )
    try:
        ledger.consumir_corte(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("esperava ValueError para quantidade_necessaria negativa")


# ---------------------------------------------------------------------------
# consumir_adicao — parcial, satura no restante
# ---------------------------------------------------------------------------


def test_orcamento_pedido_adicao_e_parcial_satura_no_restante():
    ledger = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )
    assert ledger.restante_adicao == 5

    concedido = ledger.consumir_adicao(3)
    assert concedido == 3
    assert ledger.restante_adicao == 2

    # pede mais do que resta: nunca levanta, satura no que sobrou.
    concedido = ledger.consumir_adicao(10)
    assert concedido == 2
    assert ledger.restante_adicao == 0

    # já zerado: continua devolvendo 0, nunca negativo.
    concedido = ledger.consumir_adicao(1)
    assert concedido == 0
    assert ledger.restante_adicao == 0


def test_orcamento_pedido_consumir_adicao_negativo_levanta_value_error():
    ledger = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )
    try:
        ledger.consumir_adicao(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("esperava ValueError para quantidade_desejada negativa")


# ---------------------------------------------------------------------------
# acumulado entre execuções sucessivas — nunca reseta nem excede (ALOC-09)
# ---------------------------------------------------------------------------


def test_orcamento_pedido_acumulado_entre_execucoes_sucessivas_nunca_reseta_nem_excede_o_limite():
    total_original = 100  # limite_adicao == limite_corte == 5

    # Execução 1: consome tudo que existe (tenta consumir mais do que 5).
    ledger_1 = OrcamentoPedido(
        nr_pedido=1,
        total_original=total_original,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )
    adicionado_1 = ledger_1.consumir_adicao(10)  # só cabem 5
    cortado_1 = ledger_1.consumir_corte(10)  # excede: recusa tudo (tudo-ou-nada)
    assert adicionado_1 == 5
    assert cortado_1 is False
    # como o corte foi recusado tudo-ou-nada, nada foi consumido de corte
    # ainda — usa consumir_corte(5), que cabe exatamente, para avançar o
    # cenário de double-spend com um valor que os dois orçamentos aceitam.
    cortado_1 = ledger_1.consumir_corte(5)
    assert cortado_1 is True

    # Execução 2: recebe o consumido total da execução 1 como consumido_previo.
    ledger_2 = OrcamentoPedido(
        nr_pedido=1,
        total_original=total_original,
        consumido_previo_adicao=ledger_1.consumido_adicao_total,
        consumido_previo_corte=ledger_1.consumido_corte_total,
        tolerancia=0.05,
    )
    # o ledger 2 NASCE sem orçamento nenhum — a execução 1 já esgotou os
    # dois; nunca "reseta" a 5% cheios de novo.
    assert ledger_2.restante_adicao == 0
    assert ledger_2.restante_corte == 0
    adicionado_2 = ledger_2.consumir_adicao(10)  # tenta mais, satura em 0
    cortado_2 = ledger_2.consumir_corte(1)  # tenta mais, recusa (tudo-ou-nada)
    assert adicionado_2 == 0
    assert cortado_2 is False

    # Execução 3: mesma coisa, ainda sem crédito extra.
    ledger_3 = OrcamentoPedido(
        nr_pedido=1,
        total_original=total_original,
        consumido_previo_adicao=ledger_2.consumido_adicao_total,
        consumido_previo_corte=ledger_2.consumido_corte_total,
        tolerancia=0.05,
    )
    adicionado_3 = ledger_3.consumir_adicao(10)
    cortado_3 = ledger_3.consumir_corte(1)
    assert adicionado_3 == 0
    assert cortado_3 is False

    limite = (total_original * 5) // 100
    total_adicionado_3_execucoes = adicionado_1 + adicionado_2 + adicionado_3
    total_cortado_3_execucoes = (5 if cortado_1 else 0) + 0 + 0
    assert total_adicionado_3_execucoes == limite
    assert total_adicionado_3_execucoes <= limite
    assert total_cortado_3_execucoes == limite
    assert total_cortado_3_execucoes <= limite


# ---------------------------------------------------------------------------
# construtor — validação de entrada
# ---------------------------------------------------------------------------


def test_orcamento_pedido_construtor_recusa_valores_negativos():
    for kwargs in (
        {
            "nr_pedido": 1,
            "total_original": -1,
            "consumido_previo_adicao": 0,
            "consumido_previo_corte": 0,
            "tolerancia": 0.05,
        },
        {
            "nr_pedido": 1,
            "total_original": 100,
            "consumido_previo_adicao": -1,
            "consumido_previo_corte": 0,
            "tolerancia": 0.05,
        },
        {
            "nr_pedido": 1,
            "total_original": 100,
            "consumido_previo_adicao": 0,
            "consumido_previo_corte": -1,
            "tolerancia": 0.05,
        },
    ):
        try:
            OrcamentoPedido(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"esperava ValueError para kwargs={kwargs!r}")


def test_orcamento_pedido_construtor_exige_os_5_campos_obrigatorios():
    try:
        OrcamentoPedido(nr_pedido=1, total_original=100)  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        raise AssertionError(
            "esperava TypeError do próprio Python por campo obrigatório ausente"
        )


def test_orcamento_pedido_construtor_recusa_tolerancia_fora_da_faixa():
    for tolerancia in (-0.01, 1.01):
        try:
            OrcamentoPedido(
                nr_pedido=1,
                total_original=100,
                consumido_previo_adicao=0,
                consumido_previo_corte=0,
                tolerancia=tolerancia,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"esperava ValueError para tolerancia={tolerancia!r}")


# ---------------------------------------------------------------------------
# tolerancia — paridade com o comportamento antigo + efeito real do parâmetro
# ---------------------------------------------------------------------------


def test_orcamento_pedido_tolerancia_005_e_identica_ao_comportamento_antigo():
    # Restrição dura do usuário: com tolerancia=0.05 (default de settings.py),
    # o resultado numérico tem que ser idêntico a (total_original * 5) // 100
    # para TODO total_original — prova formal de que este plano é só wiring,
    # não mudança de regra de negócio.
    for total_original in range(0, 1001):
        ledger = OrcamentoPedido(
            nr_pedido=1,
            total_original=total_original,
            consumido_previo_adicao=0,
            consumido_previo_corte=0,
            tolerancia=0.05,
        )
        esperado = (total_original * 5) // 100
        assert ledger.limite_adicao == esperado, total_original
        assert ledger.limite_corte == esperado, total_original


def test_orcamento_pedido_tolerancia_viva_muda_o_limite():
    # tolerancia=0.10 com total_original=100 dá limite 10 — o parâmetro
    # deixou de ser morto.
    ledger_10 = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.10,
    )
    assert ledger_10.limite_adicao == 10
    assert ledger_10.limite_corte == 10

    ledger_zero = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.0,
    )
    assert ledger_zero.limite_adicao == 0
    assert ledger_zero.limite_corte == 0
    assert ledger_zero.restante_adicao == 0
    assert ledger_zero.restante_corte == 0

    ledger_total = OrcamentoPedido(
        nr_pedido=1,
        total_original=100,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=1.0,
    )
    assert ledger_total.limite_adicao == 100
    assert ledger_total.limite_corte == 100


def test_orcamento_pedido_tolerancia_diferente_de_005_continua_floor_nunca_ceil():
    ledger_19 = OrcamentoPedido(
        nr_pedido=1,
        total_original=19,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.05,
    )
    assert ledger_19.limite_adicao == 0
    assert ledger_19.limite_corte == 0
    assert isinstance(ledger_19.limite_adicao, int)

    ledger_99 = OrcamentoPedido(
        nr_pedido=1,
        total_original=99,
        consumido_previo_adicao=0,
        consumido_previo_corte=0,
        tolerancia=0.10,
    )
    assert ledger_99.limite_adicao == 9
    assert ledger_99.limite_corte == 9
    assert isinstance(ledger_99.limite_adicao, int)
