"""Leitura bruta do Databricks (SQL Statement Execution API) para a ingestão.

Cada função monta a consulta de uma view/tabela específica e devolve as
linhas cruas (sem parse/agregação) — isso é responsabilidade do domínio.
"""

from app.shared.config.settings import get_settings
from app.shared.infrastructure.databricks_client import (
    DatabricksError,
    executar_consulta,
)


async def ler_pedidos_em_aberto() -> list[dict]:
    settings = get_settings()
    tabela = settings.databricks_tabela_pedidos
    if not tabela:
        raise DatabricksError("DATABRICKS_TABELA_PEDIDOS não configurada no .env.")

    # A view `system_automation_pedidos_em_aberto` já é a lista AUTORITATIVA de pedidos
    # em aberto (o recorte de status/ciclo é feito na origem, no Databricks).
    # Trazemos tudo o que a view retornar, mantendo apenas a sanidade de
    # qt_entregar > 0 (item sem quantidade pendente não é "em aberto").
    statement = (
        "SELECT nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo, "
        "qt_entregar, vl_liquido, nm_cliente, ds_tp_canal, nm_prod, "
        "status_credito, dt_emissao, indica_blacklist "
        f"FROM {tabela} "
        "WHERE try_cast(qt_entregar AS DOUBLE) > 0"
    )
    # INLINE (rápido); se algum mês passar de 25 MiB o cliente cai p/ EXTERNAL_LINKS.
    return await executar_consulta(statement)


async def ler_estoque() -> list[dict]:
    """Foto de estoque do dia. A view `system_automation_estoque_filtrado` já filtra
    `dt_estoque = current_date()` na origem e entrega 1 linha por
    (canal, codigo_produto, tamanho) — nenhum recorte é feito aqui.

    Os nomes são os da view, sem alias para o vocabulário interno: aliasar
    esconderia o schema real e faria a próxima troca de schema ser difícil de
    achar. A tradução é responsabilidade de `agregar_estoque`.
    """
    settings = get_settings()
    tabela = settings.databricks_tabela_estoque
    if not tabela:
        raise DatabricksError("DATABRICKS_TABELA_ESTOQUE não configurada no .env.")

    statement = (
        "SELECT dt_estoque, canal, codigo_produto, tamanho, quantidade_disponivel "
        f"FROM {tabela}"
    )
    return await executar_consulta(statement)


async def ler_pedidos_processados_erp() -> list[dict]:
    """Pedidos JÁ processados no ERP (reserva feita / embalado). Fonte de
    verdade externa: só entram linhas efetivamente processadas
    (indica_reserva OR indica_embalado = true) e do ANO configurado
    (`INGESTAO_ANO_HISTORICO`, default 2026)."""
    settings = get_settings()
    tabela = settings.databricks_tabela_pedidos_processados
    if not tabela:
        raise DatabricksError(
            "DATABRICKS_TABELA_PEDIDOS_PROCESSADOS não configurada no .env."
        )

    ano = settings.ingestao_ano_historico
    statement = (
        "SELECT nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo, nm_prod, "
        "nm_cliente, ds_tp_canal, qt_entregar, qt_distribuida, vl_liquido, "
        "indica_reserva, indica_embalado, dt_emissao "
        f"FROM {tabela} "
        f"WHERE year(try_cast(dt_emissao AS DATE)) = {ano} "
        "AND (indica_reserva = true OR indica_embalado = true)"
    )
    return await executar_consulta(statement)


async def ler_referencia_tamanhos() -> list[dict]:
    """Referência tamanho -> posição da grade Linx (5ª fonte, ING-01)."""
    settings = get_settings()
    tabela = settings.databricks_tabela_tamanho_ref
    if not tabela:
        raise DatabricksError("DATABRICKS_TABELA_TAMANHO_REF não configurada no .env.")

    statement = f"SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM {tabela}"
    return await executar_consulta(statement)


async def ler_faturamento_colecao() -> list[dict]:
    """Série de faturamento planejado × distribuído das 3 coleções mais recentes,
    por canal. Calcula o agregado direto no Databricks (GROUP BY colecao × canal)
    para trazer um payload mínimo (~6 linhas)."""
    from datetime import UTC, date, datetime

    settings = get_settings()
    tabela = settings.databricks_tabela_pedidos_processados
    if not tabela:
        raise DatabricksError(
            "DATABRICKS_TABELA_PEDIDOS_PROCESSADOS não configurada no .env."
        )

    # Regra de coleção project: muda a cada 6 meses (semestre civil). Âncora: 1º
    # semestre de 2026 = coleção 117. A coleção vem da `dt_emissao` do pedido,
    # não do código de coleção da origem (que não é confiável para o recorte
    # por semestre e, por isso, deixou de ser ingerido na revisão 028).
    _COLECAO_ANCORA = 117
    _ANO_ANCORA = 2026

    def _colecao_vigente(hoje: date) -> int:
        semestre = 0 if hoje.month <= 6 else 1
        return _COLECAO_ANCORA + (hoje.year - _ANO_ANCORA) * 2 + semestre

    def _colecao_periodo(colecao: int, hoje: date) -> tuple[date, date]:
        abs_sem = _ANO_ANCORA * 2 + (colecao - _COLECAO_ANCORA)
        ano, semestre = divmod(abs_sem, 2)
        if semestre == 0:
            inicio, fim = date(ano, 1, 1), date(ano, 6, 30)
        else:
            inicio, fim = date(ano, 7, 1), date(ano, 12, 31)
        return inicio, min(fim, hoje)

    hoje = datetime.now(UTC).date()
    vigente = _colecao_vigente(hoje)
    colecoes = [vigente - 2, vigente - 1, vigente]
    periodos = {c: _colecao_periodo(c, hoje) for c in colecoes}
    case_expr = " ".join(
        f"WHEN dt_emissao BETWEEN '{ini.isoformat()}' AND '{fim.isoformat()}' THEN {c}"
        for c, (ini, fim) in periodos.items()
    )
    menor = min(ini for ini, _ in periodos.values()).isoformat()
    maior = max(fim for _, fim in periodos.values()).isoformat()

    statement = (
        "WITH dedup AS ("
        "  SELECT nr_pedido, sg_tamanho, MAX(ds_tp_canal) AS canal, "
        "         MAX(dt_emissao) AS dt_emissao, "
        "         MAX(vl_planejado) AS vp, MAX(vl_distribuido) AS vd "
        f"  FROM {tabela} "
        f"  WHERE dt_emissao BETWEEN '{menor}' AND '{maior}' "
        "  AND (indica_reserva = true OR indica_embalado = true) "
        "  GROUP BY nr_pedido, sg_tamanho"
        ") "
        f"SELECT CASE {case_expr} END AS colecao, canal, "
        "       ROUND(SUM(vp),2) vl_planejado, ROUND(SUM(vd),2) vl_distribuido "
        "FROM dedup WHERE canal IS NOT NULL GROUP BY 1, canal ORDER BY colecao"
    )
    return await executar_consulta(statement)
