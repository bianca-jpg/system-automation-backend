"""Mede o pico de RSS (resource.getrusage) de `build_processing_plan` em modo
`SEM_ADEQUAR`, processando um canal inteiro a partir de um dataset sintético
grande gerado em memória — critério de sucesso 4 do ROADMAP da Fase 15
("o pico de memória medido ao processar um canal inteiro no modo sem
adequação permanece dentro do orçamento já reservado para o worker
dedicado").

Esta é a PRIMEIRA instrumentação de memória automatizada deste repositório:
nenhum teste em `app/tests/` usa `resource`/`psutil`/`tracemalloc` até este
arquivo. Os números de `docs/adequacao.md` (434 MiB) e do comentário em
`processing/domain.py` (linhas 34-41) são medições manuais antigas de uma
sessão de profiling do modo `ADEQUAR` — não são testes reprodutíveis, e não
cobrem `SEM_ADEQUAR`, cujo volume elegível pode ser estruturalmente maior por
não filtrar crédito antes do roteamento pelo caminho global (15-RESEARCH.md,
Assumption A2).

Limitação conhecida de `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`:
é o pico monotônico de RSS desde o início do PROCESSO Python, não um
snapshot pontual — uma vez que o processo atinge um pico alto, o número
nunca cai, mesmo que a memória seja liberada depois. Por isso a leitura mais
fiel deste teste vem de rodá-lo ISOLADO (não em meio à suíte completa, onde
outros testes já teriam empurrado o pico para cima antes deste chegar a
rodar); o comando de verificação abaixo roda só este arquivo por esse
motivo:

    docker compose -f .docker/docker-compose.yml exec -T api \
        uv run pytest app/tests/test_pedidos_processing_sem_adequar_memory_024.py -q -s

O teste é pulado fora de Linux (`sys.platform != "linux"`): `resource`
existe em qualquer POSIX (Linux e macOS), mas `ru_maxrss` reporta unidades
diferentes entre eles (KiB no Linux, bytes no macOS) — sem o guard, rodar
este arquivo fora do container Docker (Linux) compararia um número na
unidade errada contra o teto calibrado abaixo, um falso positivo ou falso
negativo silencioso.

Teto de calibração (`_MEMORY_CEILING_MIB` = 700.0 MiB): calibrado em
2026-08-20 a partir do pico REAL observado na primeira execução deste
teste, isolado via Docker, sobre o dataset sintético completo (45.000
pares / 112.500 itens) — `delta_total_mib` medido: **630.9 MiB**
(`delta_chamada_mib`, só a chamada a `build_processing_plan`: 540.7 MiB).
A asserção passou de primeira sobre o teto de 700.0 MiB (margem real de
~9.9%); o teto NÃO foi alterado, pois não houve necessidade de recalibrar
— ver `15-04-SUMMARY.md` para a decisão completa e o alerta sobre a margem
apertada frente à extrapolação de ~600 MiB do modo ADEQUAR.
"""

from __future__ import annotations

import gc
import resource
import sys

import pytest

from app.modules.pedidos.domain.value_objects import montar_chave_estoque
from app.modules.pedidos.processing.domain import (
    ProcessingChannel,
    ProcessingMode,
    ProcessingRequest,
    build_processing_plan,
)

_SYNTHETIC_PAIR_COUNT = 45_000
_ITEMS_PER_PAIR_CYCLE = (2, 3)
_MEMORY_CEILING_MIB = 700.0


def _construir_canal_sintetico(
    quantidade_pares: int,
) -> tuple[list[dict], dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Gera, em memória (sem I/O), um canal Franquia sintético com
    `quantidade_pares` pares (pedido, produto) distintos.

    Cada par tem um produto único (`cd_prod_cor` diferente por pedido) — isso
    estressa `_indexar_estoque_por_produto` com um produto distinto por
    pedido, o cenário de maior cardinalidade do índice. Alterna 2 e 3
    tamanhos por par (`_ITEMS_PER_PAIR_CYCLE`), produzindo 112.500 itens no
    total para 45.000 pares. Estoque abundante (10 unidades, sempre acima da
    `qt_liquida=2` pedida) e crédito liberado ("Com Crédito") garantem que
    TODOS os pares completem a grade e gerem OR via `aplicar_tudo_ou_nada` —
    o cenário de pico de payload materializado, não o cenário mais leniente
    de stand-by (que mediria menos memória que o pior caso real).
    """
    pending_items: list[dict] = []
    stock: dict[str, dict[str, int]] = {"Franquia": {}, "Multimarca": {}}
    reference: dict[str, dict[str, int]] = {}

    for i in range(quantidade_pares):
        nr_pedido = 20_000_000 + i
        cd_prod_cor = f"SKU{i:06d}"
        sizes = ("36", "37") if i % 2 == 0 else ("36", "37", "38")
        reference[cd_prod_cor] = {"36": 1, "37": 2, "38": 3}

        for tamanho in sizes:
            pending_items.append(
                {
                    "nr_pedido": nr_pedido,
                    "cd_prod_cor": cd_prod_cor,
                    "sg_tamanho": tamanho,
                    "ds_grupo": "GRUPO_SINTETICO",
                    "qt_liquida": 2,
                    "vl_liquido": 40.0,
                    "status_credito": "Com Crédito",
                    "client": f"Cliente Sintetico {i}",
                    "canal": "Franquia",
                    "ds_produto": f"Produto Sintetico {i}",
                    "data": "2026-08-17",
                }
            )
            stock["Franquia"][montar_chave_estoque(cd_prod_cor, tamanho)] = 10

    return pending_items, stock, reference


@pytest.mark.skipif(
    sys.platform != "linux",
    reason=(
        "resource.getrusage/ru_maxrss é específico de Linux (mesmo ambiente "
        "do container Docker onde a suíte roda); em outro POSIX (macOS) o "
        "módulo existe mas ru_maxrss reporta bytes, não KiB, invalidando a "
        "comparação contra o teto calibrado neste arquivo."
    ),
)
def test_sem_adequar_pico_de_memoria_do_canal_inteiro_fica_dentro_do_orcamento_do_worker() -> (  # noqa: E501
    None
):
    gc.collect()
    rss_antes_hidratacao = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    pending_items, stock, reference = _construir_canal_sintetico(_SYNTHETIC_PAIR_COUNT)
    # Piso do dataset ANTES de medir memória: falha alto e cedo se o gerador
    # sintético estiver errado, em vez de gastar tempo medindo um dataset
    # incompleto e produzir um número de memória enganoso.
    assert len(pending_items) >= 100_000

    rss_apos_hidratacao = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    draft = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        pending_items=pending_items,
        reference=reference,
        stock=stock,
        criterion="valor",
        tolerance=0.05,
    )

    rss_apos_build = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # ru_maxrss é reportado em KiB no Linux; delta convertido para MiB.
    delta_chamada_mib = (rss_apos_build - rss_apos_hidratacao) / 1024
    delta_total_mib = (rss_apos_build - rss_antes_hidratacao) / 1024

    print(
        f"[memoria-024] delta_chamada_mib={delta_chamada_mib:.1f} "
        f"delta_total_mib={delta_total_mib:.1f} "
        f"teto_mib={_MEMORY_CEILING_MIB:.1f}"
    )

    # Asserções de volume ANTES da asserção de memória: nunca mascarar um
    # dataset incompleto/parcial (menos pares do que o pedido) como "memória
    # OK" — um plano vazio ou parcial mediria um cenário mais leve que o
    # pior caso real que este teste existe para vigiar.
    assert draft.candidate_count == _SYNTHETIC_PAIR_COUNT
    assert len(draft.rows) == _SYNTHETIC_PAIR_COUNT
    assert draft.deferred_count == 0
    assert draft.blocked_credit_count == 0

    assert delta_total_mib < _MEMORY_CEILING_MIB, (
        f"pico de RSS incremental do canal inteiro ({delta_total_mib:.1f} MiB) "
        f"excedeu o teto calibrado ({_MEMORY_CEILING_MIB:.1f} MiB); delta só "
        f"da chamada a build_processing_plan: {delta_chamada_mib:.1f} MiB"
    )
