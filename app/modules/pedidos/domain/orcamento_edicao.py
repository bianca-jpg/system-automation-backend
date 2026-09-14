"""Domínio puro da trava de orçamento ±5% na edição manual de OR "sem
adequação" (Phase 20, GRADE-03).

Este módulo NÃO recria a regra `floor(total × tolerancia)` nem
`consumir_adicao`/`consumir_corte` — isso já existe e está testado em
`OrcamentoPedido` (D-04). O que falta e este módulo entrega:

1. `OrcamentoPedidoSnapshot` — o formato de dado que atravessa a fronteira
   entre o repositório de leitura (Task 2) e a aplicação.
2. `calcular_contribuicao` — quanto um PAR (`nr_pedido`, `cd_prod_cor`)
   contribui para o consumo do pedido, medido pelo TOTAL do par (PD-01), não
   por tamanho — essa é a semântica correta para ORs "sem adequação"; a
   agregação por item já existe em `PEDIDO_BUDGET_SQL` para o ramo "com
   adequação" (D-01) e não é tocada por este módulo.
3. `validar_edicao_orcamento` — a regra de cobrança por ESTADO FINAL do par
   (PD-02: rewind + recobrança), não por delta da edição. Cobrar por delta
   cobraria adição ao desfazer um corte e não seria idempotente em
   re-salvamentos; cobrar por estado final resolve os dois problemas. A
   recobrança GRANDFATHERS o que o par já detém (`contribuicao_atual`): só o
   incremento acima dela é checado contra o orçamento restante, então um
   resave sem mudança (ou uma redução) nunca é bloqueado, mesmo que o par já
   esteja acima do próprio limite por dados legados (fix CR-01, 20-REVIEW).

Sem SQLAlchemy, sem I/O, sem `async` — testável sem banco.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.pedidos.domain.orcamento_pedido import OrcamentoPedido

__all__ = [
    "ContribuicaoOrcamento",
    "OrcamentoPedidoExcedidoError",
    "OrcamentoPedidoSnapshot",
    "calcular_contribuicao",
    "validar_edicao_orcamento",
]


@dataclass(frozen=True, slots=True)
class OrcamentoPedidoSnapshot:
    """Foto do orçamento ao vivo de um pedido, devolvida pelo repositório
    (Task 2). `consumido_adicao`/`consumido_corte` já refletem TODAS as
    edições salvas até agora, incluindo a contribuição atual do próprio par
    sendo editado — é por isso que `validar_edicao_orcamento` precisa
    rebobinar essa contribuição antes de recobrar (PD-02)."""

    nr_pedido: int
    total_original: int
    consumido_adicao: int
    consumido_corte: int


@dataclass(frozen=True, slots=True)
class ContribuicaoOrcamento:
    """Quanto um par (`nr_pedido`, `cd_prod_cor`) contribui para o consumo
    do pedido, num estado específico (atual ou proposto). Os dois campos são
    mutuamente exclusivos em valor — um par nunca contribui adição E corte
    ao mesmo tempo, porque ambos derivam do mesmo sinal de
    `quantidade - baseline_total`."""

    adicao: int
    corte: int


def calcular_contribuicao(
    *, baseline_total: int, quantidade: int
) -> ContribuicaoOrcamento:
    """Mede o desvio do par PELO TOTAL (ver PD-01 no cabeçalho do plano) —
    não por tamanho. Redistribuir tamanhos sem mudar o total do par (que é
    permitido hoje em ORs "sem adequação") preserva `quantidade ==
    baseline_total` e portanto contribui zero para os dois orçamentos."""

    if baseline_total < 0:
        raise ValueError(
            f"baseline_total não pode ser negativo: {baseline_total!r}"
        )
    if quantidade < 0:
        raise ValueError(f"quantidade não pode ser negativa: {quantidade!r}")
    return ContribuicaoOrcamento(
        adicao=max(quantidade - baseline_total, 0),
        corte=max(baseline_total - quantidade, 0),
    )


class OrcamentoPedidoExcedidoError(RuntimeError):
    """Erro de domínio levantado quando a edição proposta estoura o
    orçamento de adição ou de corte do pedido. Carrega atributos
    estruturados para quem monta a resposta HTTP (409, plano 20-03) e uma
    mensagem já pronta, em português e em peças, para exibir ao operador."""

    def __init__(
        self,
        *,
        nr_pedido: int,
        orcamento: str,
        restante_adicao: int,
        restante_corte: int,
        adicao_solicitada: int,
        corte_solicitado: int,
    ) -> None:
        self.nr_pedido = nr_pedido
        self.orcamento = orcamento
        self.restante_adicao = restante_adicao
        self.restante_corte = restante_corte
        self.adicao_solicitada = adicao_solicitada
        self.corte_solicitado = corte_solicitado
        if orcamento == "adicao":
            mensagem = (
                f"Pedido {nr_pedido}: orçamento de adição excedido — restam "
                f"{restante_adicao} peça(s) disponíveis para adicionar, mas "
                f"a edição pede {adicao_solicitada} peça(s)."
            )
        else:
            mensagem = (
                f"Pedido {nr_pedido}: orçamento de corte excedido — restam "
                f"{restante_corte} peça(s) disponíveis para cortar, mas a "
                f"edição pede {corte_solicitado} peça(s)."
            )
        super().__init__(mensagem)


def validar_edicao_orcamento(
    *,
    snapshot: OrcamentoPedidoSnapshot,
    tolerancia: float,
    contribuicao_atual: ContribuicaoOrcamento,
    contribuicao_nova: ContribuicaoOrcamento,
) -> None:
    """Valida a edição proposta pelo ESTADO FINAL do par (PD-02), não pelo
    delta, mas GRANDFATHERING o que o par já detém — nunca recobra de novo a
    contribuição atual, só o INCREMENTO acima dela (fix CR-01). Passos:

    1. Rewind: remove do consumido ao vivo a contribuição ATUAL do próprio
       par, sobrando só o que os OUTROS produtos do mesmo pedido consomem.
       Piso em zero por orçamento — nunca deixa o valor rebobinado ficar
       negativo, porque `OrcamentoPedido.__post_init__` levantaria
       `ValueError` para um snapshot temporariamente inconsistente, o que
       transformaria uma inconsistência de leitura numa falha opaca aqui.
    2. Constrói UM `OrcamentoPedido` (D-04) com os consumidos rebobinados —
       quem sabe o limite é o ledger, este módulo não recalcula
       `floor(total × tolerancia)`.
    3. Calcula o INCREMENTO de adição: quanto `contribuicao_nova` excede
       `contribuicao_atual`. Um resave sem mudança ou uma redução (incremento
       zero) é SEMPRE permitido, mesmo que o par já esteja, por dados
       legados, acima do próprio limite — o que ele já detém nunca é
       recobrado de novo. Só o incremento precisa caber no espaço que sobra
       para este par (`restante` do ledger rebobinado, descontada a própria
       `contribuicao_atual`, que já está implicitamente reservada para ele).
    4. Repete o mesmo raciocínio para o corte.
    """

    consumido_previo_adicao = max(
        snapshot.consumido_adicao - contribuicao_atual.adicao, 0
    )
    consumido_previo_corte = max(
        snapshot.consumido_corte - contribuicao_atual.corte, 0
    )

    ledger = OrcamentoPedido(
        nr_pedido=snapshot.nr_pedido,
        total_original=snapshot.total_original,
        consumido_previo_adicao=consumido_previo_adicao,
        consumido_previo_corte=consumido_previo_corte,
        tolerancia=tolerancia,
    )

    restante_adicao_antes = ledger.restante_adicao
    incremento_adicao = max(
        contribuicao_nova.adicao - contribuicao_atual.adicao, 0
    )
    if incremento_adicao > 0:
        espaco_incremento_adicao = max(
            restante_adicao_antes - contribuicao_atual.adicao, 0
        )
        if incremento_adicao > espaco_incremento_adicao:
            raise OrcamentoPedidoExcedidoError(
                nr_pedido=snapshot.nr_pedido,
                orcamento="adicao",
                restante_adicao=restante_adicao_antes,
                restante_corte=ledger.restante_corte,
                adicao_solicitada=contribuicao_nova.adicao,
                corte_solicitado=contribuicao_nova.corte,
            )

    restante_corte_antes = ledger.restante_corte
    incremento_corte = max(contribuicao_nova.corte - contribuicao_atual.corte, 0)
    if incremento_corte > 0:
        espaco_incremento_corte = max(
            restante_corte_antes - contribuicao_atual.corte, 0
        )
        if incremento_corte > espaco_incremento_corte:
            raise OrcamentoPedidoExcedidoError(
                nr_pedido=snapshot.nr_pedido,
                orcamento="corte",
                restante_adicao=ledger.restante_adicao,
                restante_corte=restante_corte_antes,
                adicao_solicitada=contribuicao_nova.adicao,
                corte_solicitado=contribuicao_nova.corte,
            )
