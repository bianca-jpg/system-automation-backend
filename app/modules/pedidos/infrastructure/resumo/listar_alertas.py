"""Projeção paginada de alertas operacionais (8 categorias)."""

from __future__ import annotations

from sqlalchemy import text

from app.modules.pedidos.domain.consultas import ConsultaAlertas, PaginaCursor


async def listar_alertas_sql(repo, consulta: ConsultaAlertas) -> PaginaCursor:
    """Calcula e retorna alertas operacionais prioritários desagrupados por pedido/item individual

    Categorias de negócio (`kind="negocio"`, default do schema):
    - linx: Pedidos individuais com OR gerada sem confirmação no ERP Linx.
    - payload: Pedidos individuais importados sem detalhes de itens (#ID).
    - performance: Jobs de processamento com retentativa ou atraso.
    - divergencia: Produtos com saldo em estoque zerado e pedidos ativos.
    - geracao_or: Jobs de OR individuais com falha no CD.
    - anomalia_credito: Pedidos bloqueados por limite financeiro.
    - anomalia_estoque: Pedidos com ruptura de estoque.
    - adequacao: Pedidos ativos em processo de adequação de grade.
    - fila_processamento: Tarefas pendentes na fila assíncrona.

    Categoria de integração (`kind="integracao"`, quick task 260909-ek7):
    - integracao_banco_de_dados / integracao_estoque: avisos de falha crítica
      de infraestrutura (Postgres/Databricks), lidos do estado em Redis
      (`app.modules.health.service.listar_linhas_de_alerta`). Ficam sempre no
      INÍCIO de `rows` (semeados antes das consultas SQL), participando de
      paginação/`total`/busca como qualquer outra linha.
    """

    # Avisos de integração crítica primeiro (D-03): garante que aparecem
    # sempre na primeira página e contam em total/has_more, sem tocar em
    # paginação/cursor. Import deferido para quebrar ciclo (mesmo padrão de
    # `repositorio_consultas.py:118`). Fachada best-effort — Redis fora do ar
    # devolve `[]` e a página de alertas continua respondendo.
    from app.modules.health.service import listar_linhas_de_alerta

    all_alerts: list[dict] = list(await listar_linhas_de_alerta())
    now_str = "Agora"

    # Helper seguro para execuções SQL rápidas
    async def _fetch_rows(sql_text: str, params: dict | None = None) -> list[dict]:
        try:
            res = await repo.db.execute(text(sql_text), params or {})
            return [dict(row) for row in res.mappings().all()]
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Falha silenciosa ao buscar alertas: %s", exc, exc_info=True)
            return []

    # 1. Envio para o Linx (Desagrupado por pedido)
    linx_rows = await _fetch_rows(
        "SELECT o.nr_pedido, o.nome_clifor, o.cd_prod_cor "
        "FROM ordens_reserva_linx o "
        "WHERE NOT EXISTS (SELECT 1 FROM pedidos_processados_erp pe WHERE pe.nr_pedido = o.nr_pedido) "
        "ORDER BY o.created_at DESC LIMIT 50"
    )
    for r in linx_rows:
        nr = r.get("nr_pedido")
        order_id = f"#{nr}"
        client = r.get("nome_clifor") or "Cliente"
        all_alerts.append(
            {
                "id": f"alert-linx-{nr}",
                "order_id": order_id,
                "category": "linx",
                "type": "error",
                "title": f"Envio pendente para Linx: Pedido {order_id}",
                "message": f"Pedido {order_id} ({client}) — OR gerada aguardando confirmação no ERP Linx.",
                "time": now_str,
                "affected_count": 1,
            }
        )

    # 2. Pedidos sem detalhes de itens / Payload (Desagrupado por pedido)
    empty_payload_rows = await _fetch_rows(
        "SELECT p.nr_pedido, p.client, p.canal "
        "FROM pedidos p "
        "WHERE NOT EXISTS (SELECT 1 FROM ordens_reserva o WHERE o.nr_pedido = p.nr_pedido) "
        "ORDER BY p.nr_pedido DESC LIMIT 50"
    )
    for r in empty_payload_rows:
        nr = r.get("nr_pedido")
        order_id = f"#{nr}"
        client = r.get("client") or "Cliente"
        all_alerts.append(
            {
                "id": f"alert-payload-{nr}",
                "order_id": order_id,
                "category": "payload",
                "type": "error",
                "title": f"Payload incompleto: Pedido {order_id}",
                "message": f"Pedido {order_id} ({client}) importado apenas com número (#ID), sem itens/produtos.",
                "time": now_str,
                "affected_count": 1,
            }
        )

    # 3. Falha na geração de ORs (Desagrupado por job)
    failed_job_rows = await _fetch_rows(
        "SELECT id, error_code, attempts, finished_at "
        "FROM durable_jobs "
        "WHERE status = 'failed' "
        "ORDER BY updated_at DESC LIMIT 20"
    )
    for r in failed_job_rows:
        job_id = str(r.get("id"))[:8]
        err = r.get("error_code") or "erro_desconhecido"
        all_alerts.append(
            {
                "id": f"alert-job-failed-{job_id}",
                "order_id": None,
                "category": "geracao_or",
                "type": "error",
                "title": f"Falha no Job de OR ({job_id})",
                "message": f"Job de processamento {job_id} falhou com código '{err}'. Impacto no envio ao CD.",
                "time": now_str,
                "affected_count": 1,
            }
        )

    # 5. Desempenho / Retentativas de Jobs
    retried_job_rows = await _fetch_rows(
        "SELECT id, attempts FROM durable_jobs "
        "WHERE attempts > 1 OR (status = 'running' AND updated_at < now() - interval '5 minutes') "
        "LIMIT 20"
    )
    for r in retried_job_rows:
        job_id = str(r.get("id"))[:8]
        att = r.get("attempts")
        all_alerts.append(
            {
                "id": f"alert-perf-{job_id}",
                "order_id": None,
                "category": "performance",
                "type": "warning",
                "title": f"Retentativa de Job ({job_id})",
                "message": f"Tarefa assíncrona {job_id} em execução com {att} retentativa(s).",
                "time": now_str,
                "affected_count": 1,
            }
        )

    # 6. Divergências de Estoque por Produto
    divergent_stock_rows = await _fetch_rows(
        "SELECT DISTINCT e.cd_prod_cor, e.canal FROM estoque e "
        "WHERE e.qt_disponivel <= 0 AND EXISTS (SELECT 1 FROM pedidos p WHERE p.cd_prod_cor = e.cd_prod_cor) "
        "LIMIT 30"
    )
    for r in divergent_stock_rows:
        prod = r.get("cd_prod_cor")
        canal = r.get("canal") or "Canal"
        all_alerts.append(
            {
                "id": f"alert-div-{prod}-{canal}",
                "order_id": None,
                "category": "divergencia",
                "type": "warning",
                "title": f"Estoque Zerado: {prod}",
                "message": f"Produto {prod} ({canal}) possui pedidos ativos porém saldo em estoque zerado/negativo.",
                "time": now_str,
                "affected_count": 1,
            }
        )

    # 7. Pedidos Bloqueados por Crédito (Desagrupado por pedido)
    credit_rows = await _fetch_rows(
        "SELECT p.nr_pedido, p.client, p.canal FROM pedidos p "
        "WHERE p.status_credito LIKE '%SEM%' OR p.status_credito LIKE '%BLOQUE%' "
        "LIMIT 50"
    )
    for r in credit_rows:
        nr = r.get("nr_pedido")
        order_id = f"#{nr}"
        client = r.get("client") or "Cliente"
        all_alerts.append(
            {
                "id": f"alert-credit-{nr}",
                "order_id": order_id,
                "category": "anomalia_credito",
                "type": "warning",
                "title": f"Bloqueio de Crédito: Pedido {order_id}",
                "message": f"Pedido {order_id} ({client}) retido por limite financeiro de crédito.",
                "time": now_str,
                "affected_count": 1,
            }
        )

    # 8. Pedidos em Adequação de Grade (Desagrupado por pedido)
    adequacao_rows = await _fetch_rows(
        "SELECT DISTINCT o.nr_pedido, o.cd_prod_cor FROM ordens_reserva o "
        "WHERE o.tipo = 'com' OR o.tipo = 'com_adequacao' "
        "LIMIT 50"
    )
    for r in adequacao_rows:
        nr = r.get("nr_pedido")
        order_id = f"#{nr}"
        prod = r.get("cd_prod_cor")
        all_alerts.append(
            {
                "id": f"alert-adequacao-{nr}-{prod}",
                "order_id": order_id,
                "category": "adequacao",
                "type": "info",
                "title": f"Adequação Ativa: Pedido {order_id}",
                "message": f"Pedido {order_id} ({prod}) em processo de adequação dinâmica de grade.",
                "time": now_str,
                "affected_count": 1,
            }
        )

    # 9. Fila de Processamento Ativa
    pending_job_rows = await _fetch_rows(
        "SELECT id, kind, status FROM durable_jobs "
        "WHERE status IN ('queued', 'running', 'retrying') "
        "LIMIT 20"
    )
    for r in pending_job_rows:
        job_id = str(r.get("id"))[:8]
        kind = r.get("kind")
        st = r.get("status")
        all_alerts.append(
            {
                "id": f"alert-queue-{job_id}",
                "order_id": None,
                "category": "fila_processamento",
                "type": "info",
                "title": f"Fila Assíncrona: Job {job_id}",
                "message": f"Tarefa {kind} ({job_id}) em estado '{st}' na fila de processamento.",
                "time": now_str,
                "affected_count": 1,
            }
        )

    # Filtragem por busca caso fornecida
    if consulta.busca:
        b = consulta.busca.lower()
        all_alerts = [
            a
            for a in all_alerts
            if b in a["title"].lower()
            or b in a["message"].lower()
            or b in (a.get("category") or "").lower()
            or b in (a.get("order_id") or "").lower()
        ]

    total = len(all_alerts)
    limit = consulta.limite
    has_more = total > limit
    rows = all_alerts[:limit]

    return PaginaCursor(rows=rows, total=total, has_more=has_more, next_key=None)
