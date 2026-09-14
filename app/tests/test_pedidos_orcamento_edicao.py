"""Testes unitários puros da aritmética de rewind/cobrança de
`orcamento_edicao.py` (Phase 20, GRADE-03) — síncronos, sem fixture de banco,
sem `await`. Não retestam a regra do ledger em si (`OrcamentoPedido`, já
coberta por `test_pedidos_orcamento_pedido.py`), só a camada nova de
agregação por par e cobrança por estado final (PD-01/PD-02)."""

import pytest

from app.modules.pedidos.domain.orcamento_edicao import (
    ContribuicaoOrcamento,
    OrcamentoPedidoExcedidoError,
    OrcamentoPedidoSnapshot,
    calcular_contribuicao,
    validar_edicao_orcamento,
)

# ---------------------------------------------------------------------------
# calcular_contribuicao — medida pelo TOTAL do par (PD-01), não por tamanho
# ---------------------------------------------------------------------------


def test_calcular_contribuicao_par_igual_ao_baseline_nao_contribui():
    contribuicao = calcular_contribuicao(baseline_total=100, quantidade=100)
    assert contribuicao == ContribuicaoOrcamento(adicao=0, corte=0)


def test_calcular_contribuicao_par_acima_do_baseline_contribui_adicao():
    contribuicao = calcular_contribuicao(baseline_total=100, quantidade=104)
    assert contribuicao == ContribuicaoOrcamento(adicao=4, corte=0)


def test_calcular_contribuicao_par_abaixo_do_baseline_contribui_corte():
    contribuicao = calcular_contribuicao(baseline_total=100, quantidade=97)
    assert contribuicao == ContribuicaoOrcamento(adicao=0, corte=3)


def test_calcular_contribuicao_rejeita_baseline_negativo():
    with pytest.raises(ValueError, match="baseline_total"):
        calcular_contribuicao(baseline_total=-1, quantidade=10)


def test_calcular_contribuicao_rejeita_quantidade_negativa():
    with pytest.raises(ValueError, match="quantidade"):
        calcular_contribuicao(baseline_total=10, quantidade=-1)


# ---------------------------------------------------------------------------
# validar_edicao_orcamento — dentro do limite passa
# ---------------------------------------------------------------------------


def test_validar_edicao_orcamento_dentro_do_limite_de_adicao_passa():
    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=0, consumido_corte=0
    )
    # tolerância 0.05, total 100 → limite 5; contribuição nova de 5 cabe.
    validar_edicao_orcamento(
        snapshot=snapshot,
        tolerancia=0.05,
        contribuicao_atual=ContribuicaoOrcamento(adicao=0, corte=0),
        contribuicao_nova=ContribuicaoOrcamento(adicao=5, corte=0),
    )  # não levanta


# ---------------------------------------------------------------------------
# validar_edicao_orcamento — estouro de adição
# ---------------------------------------------------------------------------


def test_validar_edicao_orcamento_estoura_adicao_leva_restante_adicao_no_erro():
    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=0, consumido_corte=0
    )
    with pytest.raises(OrcamentoPedidoExcedidoError) as exc_info:
        validar_edicao_orcamento(
            snapshot=snapshot,
            tolerancia=0.05,
            contribuicao_atual=ContribuicaoOrcamento(adicao=0, corte=0),
            contribuicao_nova=ContribuicaoOrcamento(adicao=6, corte=0),
        )
    erro = exc_info.value
    assert erro.orcamento == "adicao"
    assert erro.restante_adicao == 5
    assert erro.nr_pedido == 1
    assert erro.adicao_solicitada == 6


# ---------------------------------------------------------------------------
# validar_edicao_orcamento — estouro de corte
# ---------------------------------------------------------------------------


def test_validar_edicao_orcamento_estoura_corte_leva_restante_corte_no_erro():
    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=0, consumido_corte=0
    )
    with pytest.raises(OrcamentoPedidoExcedidoError) as exc_info:
        validar_edicao_orcamento(
            snapshot=snapshot,
            tolerancia=0.05,
            contribuicao_atual=ContribuicaoOrcamento(adicao=0, corte=0),
            contribuicao_nova=ContribuicaoOrcamento(adicao=0, corte=6),
        )
    erro = exc_info.value
    assert erro.orcamento == "corte"
    assert erro.restante_corte == 5
    assert erro.corte_solicitado == 6


# ---------------------------------------------------------------------------
# rewind — a contribuição atual do PRÓPRIO par é devolvida antes de recobrar
# ---------------------------------------------------------------------------


def test_validar_edicao_orcamento_rewind_do_proprio_par_evita_dobrar_a_cobranca():
    """Par baseline 100, hoje em 104 (contribuindo adicao=4, já refletido no
    snapshot). Editar para 105 deve cobrar 5 no total, não 4+1 — sem o
    rewind, a cobrança cumulativa estouraria o limite de 5 incorretamente."""

    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=4, consumido_corte=0
    )
    validar_edicao_orcamento(
        snapshot=snapshot,
        tolerancia=0.05,
        contribuicao_atual=ContribuicaoOrcamento(adicao=4, corte=0),
        contribuicao_nova=ContribuicaoOrcamento(adicao=5, corte=0),
    )  # não levanta — cobrança final é 5, dentro do limite de 5


def test_validar_edicao_orcamento_rewind_com_terceiro_consumidor_reduz_orcamento_disponivel():
    """Consumido ao vivo de adicao=5, mas o próprio par só contribui 4 —
    a peça restante veio de OUTRO produto do mesmo pedido. Editar o par de
    104 para 105 estoura: 1 (outro produto) + 5 (este par) = 6 > 5."""

    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=5, consumido_corte=0
    )
    with pytest.raises(OrcamentoPedidoExcedidoError) as exc_info:
        validar_edicao_orcamento(
            snapshot=snapshot,
            tolerancia=0.05,
            contribuicao_atual=ContribuicaoOrcamento(adicao=4, corte=0),
            contribuicao_nova=ContribuicaoOrcamento(adicao=5, corte=0),
        )
    erro = exc_info.value
    # dos 5 de limite, 1 já pertence a outro produto → só 4 disponíveis para
    # este par antes da recobrança.
    assert erro.restante_adicao == 4


# ---------------------------------------------------------------------------
# idempotência — cobrar o mesmo estado final duas vezes dá o mesmo veredito
# ---------------------------------------------------------------------------


def test_validar_edicao_orcamento_e_idempotente_para_o_mesmo_estado_final():
    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=4, consumido_corte=0
    )
    contribuicao_atual = ContribuicaoOrcamento(adicao=4, corte=0)
    contribuicao_nova = ContribuicaoOrcamento(adicao=5, corte=0)

    # primeira "recobrança" do mesmo par para o mesmo estado final (105)
    validar_edicao_orcamento(
        snapshot=snapshot,
        tolerancia=0.05,
        contribuicao_atual=contribuicao_atual,
        contribuicao_nova=contribuicao_nova,
    )
    # re-salvar exatamente a mesma grade (mesma quantidade final) não deve
    # consumir orçamento cumulativamente — o snapshot já refletiria 5 de
    # consumido_adicao no mundo real, e a contribuição atual também seria 5;
    # simulamos isso diretamente para provar que o veredito não muda.
    snapshot_apos_salvar = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=5, consumido_corte=0
    )
    validar_edicao_orcamento(
        snapshot=snapshot_apos_salvar,
        tolerancia=0.05,
        contribuicao_atual=ContribuicaoOrcamento(adicao=5, corte=0),
        contribuicao_nova=ContribuicaoOrcamento(adicao=5, corte=0),
    )  # não levanta — mesmo estado final, mesmo veredito, sem consumo extra


# ---------------------------------------------------------------------------
# grandfathering — par legado JÁ ACIMA do limite (CR-01, 20-REVIEW)
# ---------------------------------------------------------------------------


def test_validar_edicao_orcamento_resave_sem_mudanca_de_par_ja_acima_do_limite_passa():
    """Reprodução direta do CR-01: pedido legado "sem adequação" cujo par já
    contribui MAIS do que o próprio limite do pedido (nunca medido antes
    desta fase, por causa do próprio bug de SQL que ela corrige). Um
    resave puro (mesma quantidade final) não introduz nenhum desvio novo e
    não pode ser bloqueado — só editar de novo o já-existente não é uma
    edição de fato."""

    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=8, consumido_corte=0
    )
    # limite_adicao = floor(100 * 0.05) = 5; contribuicao_atual (8) já
    # excede o limite — situação que o rewind+recobrança ingênuo travava.
    validar_edicao_orcamento(
        snapshot=snapshot,
        tolerancia=0.05,
        contribuicao_atual=ContribuicaoOrcamento(adicao=8, corte=0),
        contribuicao_nova=ContribuicaoOrcamento(adicao=8, corte=0),
    )  # não levanta — grandfathered


def test_validar_edicao_orcamento_reducao_de_par_ja_acima_do_limite_passa():
    """Mesma situação legada, mas a edição REDUZ a contribuição do par (ainda
    acima do baseline, mas menos do que hoje) — sempre permitido, mesmo sem
    voltar totalmente para dentro do ±5%."""

    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=8, consumido_corte=0
    )
    validar_edicao_orcamento(
        snapshot=snapshot,
        tolerancia=0.05,
        contribuicao_atual=ContribuicaoOrcamento(adicao=8, corte=0),
        contribuicao_nova=ContribuicaoOrcamento(adicao=6, corte=0),
    )  # não levanta — redução nunca introduz desvio novo


def test_validar_edicao_orcamento_aumento_de_par_ja_acima_do_limite_continua_estourando():
    """O grandfathering só cobre o que o par já detinha — aumentar AINDA MAIS
    a contribuição de um par já acima do limite continua sendo rejeitado,
    porque isso introduz desvio novo de verdade."""

    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=8, consumido_corte=0
    )
    with pytest.raises(OrcamentoPedidoExcedidoError) as exc_info:
        validar_edicao_orcamento(
            snapshot=snapshot,
            tolerancia=0.05,
            contribuicao_atual=ContribuicaoOrcamento(adicao=8, corte=0),
            contribuicao_nova=ContribuicaoOrcamento(adicao=9, corte=0),
        )
    assert exc_info.value.orcamento == "adicao"


# ---------------------------------------------------------------------------
# adição e corte são independentes — não se compensam (D-08)
# ---------------------------------------------------------------------------


def test_validar_edicao_orcamento_adicao_e_corte_nao_se_compensam():
    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=1, total_original=100, consumido_adicao=0, consumido_corte=5
    )
    # corte já esgotado (limite_corte=5, consumido_corte=5); orçamento de
    # adição continua cheio e independente.
    validar_edicao_orcamento(
        snapshot=snapshot,
        tolerancia=0.05,
        contribuicao_atual=ContribuicaoOrcamento(adicao=0, corte=0),
        contribuicao_nova=ContribuicaoOrcamento(adicao=5, corte=0),
    )  # não levanta — adição não foi afetada pelo corte esgotado

    with pytest.raises(OrcamentoPedidoExcedidoError) as exc_info:
        validar_edicao_orcamento(
            snapshot=snapshot,
            tolerancia=0.05,
            contribuicao_atual=ContribuicaoOrcamento(adicao=0, corte=0),
            contribuicao_nova=ContribuicaoOrcamento(adicao=0, corte=1),
        )
    assert exc_info.value.orcamento == "corte"


# ---------------------------------------------------------------------------
# mensagem de erro em português, em peças
# ---------------------------------------------------------------------------


def test_orcamento_pedido_excedido_error_mensagem_em_portugues_com_pecas():
    snapshot = OrcamentoPedidoSnapshot(
        nr_pedido=42, total_original=100, consumido_adicao=0, consumido_corte=0
    )
    with pytest.raises(OrcamentoPedidoExcedidoError) as exc_info:
        validar_edicao_orcamento(
            snapshot=snapshot,
            tolerancia=0.05,
            contribuicao_atual=ContribuicaoOrcamento(adicao=0, corte=0),
            contribuicao_nova=ContribuicaoOrcamento(adicao=6, corte=0),
        )
    mensagem = str(exc_info.value)
    assert "42" in mensagem
    assert "peça" in mensagem
    assert "orçamento" in mensagem.lower()
