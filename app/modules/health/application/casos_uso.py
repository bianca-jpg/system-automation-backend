"""Casos de uso dos avisos de integração crítica (D-01/D-02/D-05)."""

from __future__ import annotations

from datetime import datetime

from app.modules.health.application.ports import RepositorioAvisosIntegracaoPort
from app.modules.health.domain.avisos import (
    CATALOGO,
    AvisoIntegracao,
    FonteIntegracao,
    abrir_ou_atualizar,
    esta_aberto,
    resolver,
)


async def registrar_falha(
    repo: RepositorioAvisosIntegracaoPort,
    *,
    fonte: FonteIntegracao,
    agora: datetime,
) -> tuple[AvisoIntegracao, bool]:
    """Abre ou atualiza o aviso da fonte; devolve o aviso e se era novo.

    `era_novo` deixa o chamador (fachada) decidir o nível/texto do log —
    "aberto" na primeira falha, "recorrente" nas seguintes.
    """

    atual = await repo.obter(fonte)
    era_novo = not esta_aberto(atual)
    aviso = abrir_ou_atualizar(atual, fonte=fonte, agora=agora)
    await repo.salvar(aviso)
    return aviso, era_novo


async def registrar_sucesso(
    repo: RepositorioAvisosIntegracaoPort,
    *,
    fonte: FonteIntegracao,
    agora: datetime,
) -> AvisoIntegracao | None:
    """Resolve o aviso aberto da fonte, se houver; devolve `None` sem I/O extra
    quando não havia nada para resolver (D-05)."""

    atual = await repo.obter(fonte)
    if not esta_aberto(atual):
        return None

    assert atual is not None  # esta_aberto garante não-nulo aqui
    aviso = resolver(atual, agora=agora)
    await repo.salvar(aviso)
    return aviso


def para_linha_de_alerta(aviso: AvisoIntegracao) -> dict[str, object]:
    """Tradução pura para o contrato de leitura de `GET /api/v1/alertas`.

    Usa os NOMES DE CAMPO de `AlertaOut` (nunca os aliases camelCase): a
    serialização por alias é responsabilidade da camada HTTP. Nenhum I/O e
    nenhum logging aqui.
    """

    copy = CATALOGO[aviso.fonte]
    return {
        "id": f"aviso-integracao-{aviso.fonte.value}",
        "order_id": None,
        "category": copy.categoria,
        "type": copy.severidade,
        "title": copy.titulo,
        "message": copy.mensagem,
        "time": "Agora",
        "affected_count": aviso.ocorrencias,
        "kind": "integracao",
    }
