"""API de leitura pública do contexto Integração de Dados — superfície
explícita para outros bounded contexts (hoje: Adequação & Reserva / pedidos)
consumirem em vez de importar os models de `ingestao` diretamente.

A troca já foi concluída: `pedidos` não importa mais `ingestao.models`. O
consumo passa por `pedidos/infrastructure/repositorio_ingestao_readmodel.py`,
que reexporta estas funções com os nomes usados dentro daquele bounded context
— com duas exceções: `obter_faturamento_colecao`, importada direto por
`pedidos/service.py`, e `carregar_pedidos_itens`, implementada no próprio
readmodel porque compõe esta foto com as tabelas de processamento de Pedidos.
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestao.infrastructure.models import (
    Estoque,
    FaturamentoColecao,
    PedidoProcessadoErp,
    ProdutoTamanhoPosicao,
)

CANAIS = ("Franquia", "Multimarca")

# Margem confortável sob o limite de 65.535 parâmetros por statement do
# protocolo Postgres — a referência tem 569.726 linhas reais (Fase 2), então
# a leitura é SEMPRE filtrada por produtos da rodada, nunca a tabela inteira.
_TAMANHO_CHUNK = 500


def _canal_bucket(canal: str | None) -> str:
    """Mesma regra de fallback de app.modules.pedidos.service.canal_bucket: o
    canal já vem normalizado da ingestão; qualquer valor fora dos dois
    esperados (inclusive None) cai em 'Franquia'."""
    return canal if canal in CANAIS else "Franquia"


async def obter_estoque_por_canal(db: AsyncSession) -> dict[str, dict[str, int]]:
    """Porte fiel de pedidos/service.py:carregar_estoque. Estoque isolado por
    canal: { canal -> { f'{cd}_{tam}': qt } }."""
    rows = (
        await db.execute(
            select(
                Estoque.canal,
                Estoque.cd_prod_cor,
                Estoque.sg_tamanho,
                Estoque.qt_disponivel,
            )
        )
    ).all()
    estoque: dict[str, dict[str, int]] = {canal: {} for canal in CANAIS}
    for canal, cd, tam, qt in rows:
        estoque[_canal_bucket(canal)][f"{cd}_{tam}"] = qt
    return estoque


async def obter_foto_estoque(
    db: AsyncSession,
) -> tuple[dict[tuple[str, str, str], int], date | None]:
    """Foto crua de estoque por (cd_prod_cor, sg_tamanho, canal), com a data dela.

    Diferente de `obter_estoque_por_canal`, que devolve o dict plano por canal no
    formato que o motor consome: aqui a chave é a tupla completa. É o que o cálculo
    do estoque virtual precisa para descontar reserva sem ter que desmontar a
    chave textual `f'{cd}_{tam}'` (o `_` como separador não é parseável com
    segurança de volta).

    A data é a mesma em todas as linhas — o full refresh grava uma foto por vez —,
    então devolvemos a maior encontrada.
    """
    rows = (
        await db.execute(
            select(
                Estoque.cd_prod_cor,
                Estoque.sg_tamanho,
                Estoque.canal,
                Estoque.qt_disponivel,
                Estoque.dt_estoque,
            )
        )
    ).all()
    foto = {(cd, tam, canal): qt for cd, tam, canal, qt, _ in rows}
    datas = [dt for *_, dt in rows if dt is not None]
    return foto, (max(datas) if datas else None)


async def obter_estado_foto_estoque(db: AsyncSession) -> tuple[date | None, int]:
    """(data da foto, nº de linhas) — versão barata de `obter_foto_estoque`, para
    detectar que uma projeção derivada ficou velha sem carregar a foto inteira."""
    row = (
        await db.execute(
            select(func.max(Estoque.dt_estoque), func.count()).select_from(Estoque)
        )
    ).one()
    return row[0], row[1] or 0


async def obter_pedidos_processados_erp(db: AsyncSession) -> dict[int, dict]:
    """Porte fiel de pedidos/service.py:carregar_processados_erp. Retorna, por
    nr_pedido: {"client", "canal", "data", "items": [...], "indica_reserva",
    "indica_embalado"}."""
    stmt = select(
        PedidoProcessadoErp.nr_pedido,
        PedidoProcessadoErp.client,
        PedidoProcessadoErp.canal,
        PedidoProcessadoErp.data,
        PedidoProcessadoErp.cd_prod_cor,
        PedidoProcessadoErp.sg_tamanho,
        PedidoProcessadoErp.ds_grupo,
        PedidoProcessadoErp.ds_produto,
        PedidoProcessadoErp.qt,
        PedidoProcessadoErp.vl_liquido,
        PedidoProcessadoErp.indica_reserva,
        PedidoProcessadoErp.indica_embalado,
    )
    rows = (await db.execute(stmt)).all()
    agrupado: dict[int, dict] = {}
    for r in rows:
        info = agrupado.setdefault(
            r.nr_pedido,
            {
                "client": r.client,
                "canal": r.canal,
                "data": r.data,
                "items": [],
                "indica_reserva": False,
                "indica_embalado": False,
            },
        )
        info["items"].append(
            {
                "nr_pedido": r.nr_pedido,
                "cd_prod_cor": r.cd_prod_cor,
                "sg_tamanho": r.sg_tamanho,
                "ds_grupo": r.ds_grupo or "",
                "qt_liquida": r.qt,
                "vl_liquido": float(r.vl_liquido),
                "ds_produto": r.ds_produto,
                "client": r.client,
                "canal": r.canal,
            }
        )
        info["indica_reserva"] = info["indica_reserva"] or r.indica_reserva
        info["indica_embalado"] = info["indica_embalado"] or r.indica_embalado
    return agrupado


async def obter_pares_processados_erp(
    db: AsyncSession, *, canal: str = "Todos"
) -> set[tuple[int, str]]:
    """Chaves ERP diretas, sem materializar 675 mil itens em cards intermediários."""
    stmt = select(
        PedidoProcessadoErp.nr_pedido,
        PedidoProcessadoErp.cd_prod_cor,
    ).distinct()
    if canal != "Todos":
        stmt = stmt.where(PedidoProcessadoErp.canal == canal)
    rows = (await db.execute(stmt)).all()
    return {(int(nr), str(cd)) for nr, cd in rows}


async def obter_faturamento_colecao(db: AsyncSession) -> list[dict]:
    """Série de faturamento planejado × distribuído das 3 coleções mais recentes,
    por canal. Lê do Postgres (pré-calculado na ingestão), sem query ao Databricks."""
    stmt = select(
        FaturamentoColecao.colecao,
        FaturamentoColecao.canal,
        FaturamentoColecao.vl_planejado,
        FaturamentoColecao.vl_distribuido,
    ).order_by(FaturamentoColecao.colecao)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "colecao": r.colecao,
            "canal": r.canal,
            "planejado": round(float(r.vl_planejado), 2),
            "distribuido": round(float(r.vl_distribuido), 2),
        }
        for r in rows
    ]


async def obter_referencia_posicoes_por_produtos(
    db: AsyncSession, cd_prod_cors: set[str]
) -> dict[str, dict[str, int]]:
    """Referência tamanho -> posição, filtrada pelos produtos da rodada atual.

    NUNCA carrega `produto_tamanho_posicao` inteira (569.726 linhas reais,
    medidas na Fase 2): sempre `WHERE cd_prod_cor IN (...)`, em fatias de
    `_TAMANHO_CHUNK` para não estourar o limite de parâmetros por statement.

    Devolve `{cd_prod_cor: {sg_tamanho: nr_posicao}}`. Um `cd_prod_cor` sem
    nenhuma posição cadastrada simplesmente não aparece no dict (sem
    `KeyError`) — quem chama decide o que fazer com a ausência (D-01, Plano
    03-03).
    """
    if not cd_prod_cors:
        return {}

    referencia: dict[str, dict[str, int]] = {}
    lista = list(cd_prod_cors)
    for inicio in range(0, len(lista), _TAMANHO_CHUNK):
        chunk = lista[inicio : inicio + _TAMANHO_CHUNK]
        rows = (
            await db.execute(
                select(
                    ProdutoTamanhoPosicao.cd_prod_cor,
                    ProdutoTamanhoPosicao.sg_tamanho,
                    ProdutoTamanhoPosicao.nr_posicao,
                ).where(ProdutoTamanhoPosicao.cd_prod_cor.in_(chunk))
            )
        ).all()
        for cd, tam, pos in rows:
            referencia.setdefault(cd, {})[tam] = pos
    return referencia
