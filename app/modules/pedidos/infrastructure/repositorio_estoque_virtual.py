"""PLACEBO TEMPORÁRIO — projeção e leitura de `estoque_virtual`.

Ver o docstring de `EstoqueVirtual` em app/modules/pedidos/models.py para o porquê
do placebo, os limites aceitos e a condição de remoção.

Divisão deliberada entre PROJETAR (só leitura + cálculo) e PERSISTIR (escrita):
o caminho de LEITURA projeta em memória quando a projeção gravada está velha, sem
escrever nada — reinserir a foto inteira (~20 mil linhas) a cada carregamento de
dashboard, numa transação que ninguém vai commitar, seria trabalho jogado fora.
Quem grava é o caminho que gera OR, que é o momento em que a projeção muda de
verdade e que já tem transação com commit.

Consequência documentada: entre um sync que troca a data da foto e a próxima
geração de OR, a TABELA fica defasada (as colunas `dt_estoque` e `recalculado_em`
mostram isso). Os números devolvidos por `carregar_estoque` estão sempre certos —
é ele que o motor e as telas usam.
"""

import json
import logging
from datetime import date, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pedidos.domain.estoque_virtual import (
    ChaveEstoque,
    calcular_estoque_virtual,
)
from app.modules.pedidos.domain.value_objects import CANAIS, montar_chave_estoque
from app.modules.pedidos.infrastructure.models import EstoqueVirtual
from app.modules.pedidos.infrastructure.repositorio_ingestao_readmodel import (
    carregar_estado_foto_estoque,
    carregar_foto_estoque,
    carregar_pares_processados_erp,
)
from app.modules.pedidos.infrastructure.repositorio_ordens import (
    carregar_ordens_reserva,
    estado_ordens_reserva,
)

logger = logging.getLogger(__name__)

_MAX_ALVOS_ESTOQUE = 200

_ESTOQUE_ALVOS_CTE = """
alvos AS (
    SELECT DISTINCT
        trim(target.cd_prod_cor) AS cd_prod_cor,
        upper(trim(target.sg_tamanho)) AS sg_tamanho,
        target.canal
    FROM jsonb_to_recordset(CAST(:targets AS jsonb))
        AS target(cd_prod_cor text, sg_tamanho text, canal text)
),
foto AS (
    SELECT
        target.cd_prod_cor,
        target.sg_tamanho,
        target.canal,
        coalesce(stock.qt_disponivel, 0)::integer AS qt_disponivel,
        stock.dt_estoque
    FROM alvos target
    LEFT JOIN estoque stock
      ON stock.cd_prod_cor = target.cd_prod_cor
     AND upper(stock.sg_tamanho) = target.sg_tamanho
     AND stock.canal = target.canal
),
reservas AS (
    SELECT
        foto.cd_prod_cor,
        foto.sg_tamanho,
        foto.canal,
        sum(
            greatest(
                coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0),
                0
            )
        )::bigint AS qt_reservada
    FROM foto
    JOIN ordens_reserva ordem
      ON ordem.cd_prod_cor = foto.cd_prod_cor
    CROSS JOIN LATERAL jsonb_array_elements(coalesce(ordem.itens, '[]'::jsonb)) item
    WHERE upper(trim(item ->> 'sg_tamanho')) = foto.sg_tamanho
      AND coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
      AND CASE
            WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
              OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
            THEN 'Multimarca'
            WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
              OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
            THEN 'Franquia'
            ELSE NULL
          END = foto.canal
      AND NOT EXISTS (
          SELECT 1
          FROM pedido_produto_read erp
          WHERE erp.source = 'erp'
            AND erp.nr_pedido = ordem.nr_pedido
            AND erp.cd_prod_cor = ordem.cd_prod_cor
      )
      AND (
          foto.dt_estoque IS NULL
          OR (ordem.created_at AT TIME ZONE 'America/Sao_Paulo')::date
             >= foto.dt_estoque
      )
    GROUP BY 1, 2, 3
),
projecao AS (
    SELECT
        foto.cd_prod_cor,
        foto.sg_tamanho,
        foto.canal,
        greatest(foto.qt_disponivel - coalesce(reservas.qt_reservada, 0), 0)::integer
            AS qt_disponivel,
        foto.dt_estoque
    FROM foto
    LEFT JOIN reservas USING (cd_prod_cor, sg_tamanho, canal)
)
"""


def _serializar_alvos(
    targets: set[tuple[str, str, str]],
) -> str:
    """Valida e serializa um lote bounded de chaves de estoque."""
    if len(targets) > _MAX_ALVOS_ESTOQUE:
        raise ValueError(
            f"um lote de estoque suporta no máximo {_MAX_ALVOS_ESTOQUE} chaves"
        )
    rows = []
    for product, size, channel in sorted(targets):
        product = product.strip()
        size = size.strip().upper()
        if not 1 <= len(product) <= 64 or not 1 <= len(size) <= 16:
            raise ValueError("produto/tamanho fora dos limites do estoque")
        if channel not in CANAIS:
            raise ValueError("canal desconhecido no estoque")
        rows.append(
            {
                "cd_prod_cor": product,
                "sg_tamanho": size,
                "canal": channel,
            }
        )
    return json.dumps(rows, ensure_ascii=False)


async def carregar_estoque_disponivel_alvos(
    db: AsyncSession,
    targets: set[tuple[str, str, str]],
) -> dict[tuple[str, str, str], int]:
    """Calcula disponibilidade autoritativa somente para as chaves pedidas."""
    if not targets:
        return {}
    rows = (
        await db.execute(
            text(
                f"""
                WITH {_ESTOQUE_ALVOS_CTE}
                SELECT cd_prod_cor, sg_tamanho, canal, qt_disponivel
                FROM projecao
                ORDER BY cd_prod_cor, sg_tamanho, canal
                """
            ),
            {"targets": _serializar_alvos(targets)},
        )
    ).all()
    return {
        (str(product), str(size), str(channel)): int(qty)
        for product, size, channel, qty in rows
    }


async def recalcular_estoque_virtual_alvos(
    db: AsyncSession,
    targets: set[tuple[str, str, str]],
) -> dict[tuple[str, str, str], int]:
    """Atualiza incrementalmente a projeção apenas para até 200 chaves tocadas.

    Não faz commit. A alteração da OR e este upsert participam da mesma transação.
    """
    if not targets:
        return {}
    rows = (
        await db.execute(
            text(
                f"""
                WITH {_ESTOQUE_ALVOS_CTE}
                INSERT INTO estoque_virtual (
                    cd_prod_cor, sg_tamanho, canal, qt_disponivel,
                    dt_estoque, recalculado_em
                )
                SELECT
                    cd_prod_cor, sg_tamanho, canal, qt_disponivel,
                    dt_estoque, now()
                FROM projecao
                ON CONFLICT (cd_prod_cor, sg_tamanho, canal) DO UPDATE
                SET qt_disponivel = EXCLUDED.qt_disponivel,
                    dt_estoque = EXCLUDED.dt_estoque,
                    recalculado_em = EXCLUDED.recalculado_em
                RETURNING cd_prod_cor, sg_tamanho, canal, qt_disponivel
                """
            ),
            {"targets": _serializar_alvos(targets)},
        )
    ).all()
    return {
        (str(product), str(size), str(channel)): int(qty)
        for product, size, channel, qty in rows
    }


def _por_canal(projecao: dict[ChaveEstoque, int]) -> dict[str, dict[str, int]]:
    """Projeção -> formato que o motor consome: { canal -> { f'{cd}_{tam}': qt } }.

    Pré-semeia os dois canais, igual a `obter_estoque_por_canal`, para que
    `estoque.get(canal, {})` no motor se comporte de forma idêntica.
    """
    estoque: dict[str, dict[str, int]] = {canal: {} for canal in CANAIS}
    for (cd, tam, canal), qt in projecao.items():
        if canal in estoque:
            estoque[canal][montar_chave_estoque(cd, tam)] = qt
    return estoque


async def _estado_projecao(
    db: AsyncSession,
) -> tuple[date | None, int, datetime | None]:
    """(data da foto que originou a projeção, nº de linhas, quando foi calculada).

    As três colunas são gravadas juntas para todas as linhas, então `max` de cada
    uma descreve a projeção inteira.
    """
    row = (
        await db.execute(
            select(
                func.max(EstoqueVirtual.dt_estoque),
                func.count(),
                func.max(EstoqueVirtual.recalculado_em),
            ).select_from(EstoqueVirtual)
        )
    ).one()
    return row[0], row[1] or 0, row[2]


async def projetar_estoque_virtual(
    db: AsyncSession,
) -> tuple[dict[ChaveEstoque, int], date | None, int]:
    """Calcula a projeção a partir da foto + ORs. Só LÊ — não escreve nada.

    Devolve (projeção, data da foto, peças descontadas por OR do app).
    """
    foto, dt_foto = await carregar_foto_estoque(db)
    ordens = await carregar_ordens_reserva(db)
    pares_no_erp = await carregar_pares_processados_erp(db)

    projecao = calcular_estoque_virtual(foto, ordens, dt_foto, pares_no_erp)
    descontado = sum(max(0, foto.get(chave, 0) - qt) for chave, qt in projecao.items())
    return projecao, dt_foto, descontado


async def recalcular_estoque_virtual(db: AsyncSession) -> dict[str, dict[str, int]]:
    """Refaz a projeção do zero e GRAVA. Chamado pelos caminhos que geram OR.

    Sem commit próprio — quem commita é o caso de uso —, mas com flush: a projeção
    precisa estar visível para leituras posteriores na MESMA transação.
    """
    projecao, dt_foto, descontado = await projetar_estoque_virtual(db)

    await db.execute(delete(EstoqueVirtual))
    db.add_all(
        EstoqueVirtual(
            cd_prod_cor=cd,
            sg_tamanho=tam,
            canal=canal,
            qt_disponivel=qt,
            dt_estoque=dt_foto,
        )
        for (cd, tam, canal), qt in projecao.items()
    )
    await db.flush()

    logger.info(
        "Estoque virtual recalculado: %d chaves, foto de %s, %d peça(s) descontada(s) "
        "por OR gerada no app.",
        len(projecao),
        dt_foto or "data desconhecida",
        descontado,
    )
    return _por_canal(projecao)


async def ler_estoque_virtual_por_canal(db: AsyncSession) -> dict[str, dict[str, int]]:
    """Projeção gravada, no formato que o motor consome."""
    rows = (
        await db.execute(
            select(
                EstoqueVirtual.cd_prod_cor,
                EstoqueVirtual.sg_tamanho,
                EstoqueVirtual.canal,
                EstoqueVirtual.qt_disponivel,
            )
        )
    ).all()
    return _por_canal({(cd, tam, canal): qt for cd, tam, canal, qt in rows})


async def carregar_estoque(db: AsyncSession) -> dict[str, dict[str, int]]:
    """Estoque que o app usa para reservar E para exibir: o VIRTUAL.

    Substitui o antigo `carregar_estoque` (que lia a foto crua) em todos os
    consumidores — motor de adequação, campo `stock` dos cards, `/resumo` e
    dashboard passam a ver o mesmo número reservável. A foto crua continua
    acessível por `carregar_estoque_fisico`.

    Auto-cura: projeta na hora, em vez de devolver número velho, quando a projeção
    gravada não existe, é de outra foto (a `dt_estoque` avançou — é isto que
    implementa o reset diário sem a ingestão conhecer este módulo) ou é anterior a
    alguma OR.

    A checagem por OR mais nova existe porque a data da foto sozinha não bastava:
    uma OR criada depois do último recálculo ficava invisível para a leitura, que
    devolvia disponível já reservado. Os caminhos que geram OR recalculam na mesma
    transação (então `created_at == recalculado_em`, e a comparação estrita não
    dispara à toa), mas isto garante o número certo mesmo se um caminho futuro
    esquecer o gatilho.
    """
    dt_fisica, total_fisico = await carregar_estado_foto_estoque(db)
    dt_projetada, total_projetado, recalculado_em = await _estado_projecao(db)
    or_mais_recente, _total_ors = await estado_ordens_reserva(db)

    inexistente = total_projetado == 0 and total_fisico > 0
    outra_foto = dt_projetada != dt_fisica
    or_mais_nova = (
        or_mais_recente is not None
        and recalculado_em is not None
        and or_mais_recente > recalculado_em
    )

    if inexistente or outra_foto or or_mais_nova:
        projecao, dt_foto, descontado = await projetar_estoque_virtual(db)
        logger.info(
            "Estoque virtual projetado em memória (motivo: %s): %d chaves, foto de %s, "
            "%d peça(s) descontada(s).",
            "projeção ausente"
            if inexistente
            else ("foto trocada" if outra_foto else "OR mais nova que a projeção"),
            len(projecao),
            dt_foto or "data desconhecida",
            descontado,
        )
        return _por_canal(projecao)

    return await ler_estoque_virtual_por_canal(db)
