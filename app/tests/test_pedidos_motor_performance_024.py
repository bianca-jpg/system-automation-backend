"""Regressões de paridade e complexidade do índice de estoque do motor."""

import logging
from collections.abc import Iterator, Mapping

import pytest

from app.modules.pedidos.domain import motor_adequacao as motor
from app.modules.pedidos.domain.orcamento_pedido import OrcamentoPedido


def _item(
    nr_pedido: int,
    cd_prod_cor: str,
    sg_tamanho: str,
    qt_liquida: int,
    vl_liquido: float,
    *,
    canal: str = "Franquia",
) -> dict:
    return {
        "nr_pedido": nr_pedido,
        "cd_prod_cor": cd_prod_cor,
        "sg_tamanho": sg_tamanho,
        "ds_grupo": "Teste",
        "qt_liquida": qt_liquida,
        "vl_liquido": vl_liquido,
        "canal": canal,
        "status_credito": "Com Credito",
    }


def _indice_legado(
    estoque: dict[str, int], produtos: set[str]
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Referência da semântica anterior: uma varredura integral por produto."""
    por_produto: dict[str, dict[str, int]] = {}
    totais: dict[str, int] = {}
    for produto in produtos:
        prefixo = f"{produto}_"
        subset = {
            chave: quantidade
            for chave, quantidade in estoque.items()
            if chave.startswith(prefixo)
        }
        por_produto[produto] = subset
        totais[produto] = sum(subset.values())
    return por_produto, totais


def test_indice_otimizado_mantem_paridade_exata_com_varredura_legada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dados = [
        _item(10, "A", "M", 4, 400.0),
        _item(10, "A_B", "M", 3, 360.0),
        _item(20, "A", "M", 4, 600.0),
        _item(30, "C", "P", 2, 200.0, canal="Multimarca"),
    ]
    estoque = {
        "Franquia": {
            "A_M": 5,
            "A_G": 1,
            # Prefixos sobrepostos são deliberados: historicamente esta chave
            # conta tanto para A quanto para A_B no score de disponibilidade.
            "A_B_M": 3,
            "FORA_M": 999,
        },
        "Multimarca": {"C_P": 2, "C_M": 1},
    }

    otimizado = motor.processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.25
    )

    monkeypatch.setattr(motor, "_indexar_estoque_por_produto", _indice_legado)
    referencia = motor.processar_pedidos(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.25
    )

    assert otimizado == referencia


class _EstoqueContado(Mapping[str, int]):
    def __init__(self, valores: dict[str, int]) -> None:
        self._valores = valores
        self.chamadas_iter = 0
        self.itens_visitados = 0

    def __getitem__(self, chave: str) -> int:
        return self._valores[chave]

    def __iter__(self) -> Iterator[str]:
        self.chamadas_iter += 1
        for chave in self._valores:
            self.itens_visitados += 1
            yield chave

    def __len__(self) -> int:
        return len(self._valores)


@pytest.mark.parametrize("quantidade_produtos", [10, 100])
def test_indice_visita_estoque_uma_vez_e_copia_apenas_subset_do_produto(
    quantidade_produtos: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    tamanhos = ("P", "M", "G", "GG")
    dados = [
        _item(numero + 1, f"P{numero:04d}", "M", 1, float(numero + 1))
        for numero in range(quantidade_produtos)
    ]
    valores_estoque = {
        f"P{numero:04d}_{tamanho}": 2
        for numero in range(quantidade_produtos)
        for tamanho in tamanhos
    }
    valores_estoque.update(
        {f"SEM_PEDIDO_{numero:04d}_M": 10 for numero in range(quantidade_produtos)}
    )
    estoque = _EstoqueContado(valores_estoque)

    tamanhos_dos_subsets: list[int] = []
    adequar_original = motor.adequar_grade_produto

    def _adequar_observando_subset(
        itens: list,
        estoque_local: dict,
        cd_prod_cor: str,
        ledger: OrcamentoPedido,
        *,
        cancel_token: motor.CancellationToken | None = None,
    ) -> list:
        tamanhos_dos_subsets.append(len(estoque_local))
        return adequar_original(
            itens, estoque_local, cd_prod_cor, ledger, cancel_token=cancel_token
        )

    monkeypatch.setattr(motor, "adequar_grade_produto", _adequar_observando_subset)

    motor._processar_pedidos_canal(
        dados, estoque, ja_processados=set(), criterio="valor", tolerancia=0.05
    )

    # A curva é linear no tamanho do mapa: cada entrada é visitada uma única
    # vez, independentemente de haver 10 ou 100 produtos solicitados.
    assert estoque.chamadas_iter == 1
    assert estoque.itens_visitados == len(valores_estoque)
    # Cada processamento recebe somente as quatro grades do próprio produto;
    # as entradas dos outros produtos e as sem pedido nunca são copiadas.
    assert len(tamanhos_dos_subsets) == quantidade_produtos
    assert set(tamanhos_dos_subsets) == {len(tamanhos)}


def test_logs_do_motor_expoem_apenas_contagens_e_categorias(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pedido_bloqueado = 918273645
    pedido_sem_estoque = 817263544
    pedido_sem_canal = 716253443
    pedido_processado = 615243342
    codigo = "SKU_SIGILOSO_024"
    tamanho = "TAMANHO_SIGILOSO_024"
    canal_invalido = "CANAL_SIGILOSO_024"
    dados = [
        _item(pedido_bloqueado, codigo, tamanho, 1, 10.0),
        _item(pedido_sem_estoque, codigo, tamanho, 10, 100.0),
        _item(
            pedido_sem_canal,
            codigo,
            tamanho,
            1,
            10.0,
            canal=canal_invalido,
        ),
        _item(pedido_processado, codigo, tamanho, 1, 10.0),
    ]
    dados[0]["status_credito"] = "Sem Credito"

    with caplog.at_level(logging.INFO, logger=motor.__name__):
        motor.processar_pedidos(
            dados,
            {"Franquia": {}, "Multimarca": {}},
            ja_processados={(pedido_processado, codigo)},
        )

    mensagens = "\n".join(caplog.messages)
    for valor_sensivel in (
        pedido_bloqueado,
        pedido_sem_estoque,
        pedido_sem_canal,
        pedido_processado,
        codigo,
        tamanho,
        canal_invalido,
    ):
        assert str(valor_sensivel) not in mensagens

    assert "[Skip] 1 par(es)" in mensagens
    assert "[Credito] 1 pedido(s)" in mensagens
    assert "1 par(es) (pedido, produto) em stand-by" in mensagens
    assert "Sem estoque para gerar OR em 1 par(es)" in mensagens
