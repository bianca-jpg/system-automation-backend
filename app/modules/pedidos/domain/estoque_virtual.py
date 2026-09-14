"""PLACEBO TEMPORÁRIO — regra do estoque virtual (pura, sem I/O).

Ver o docstring de `EstoqueVirtual` em app/modules/pedidos/models.py para o porquê
do placebo, os limites aceitos e a condição de remoção.
"""

from collections.abc import Iterable
from datetime import date, datetime

from app.modules.pedidos.domain.value_objects import normalizar_canal

ChaveEstoque = tuple[str, str, str]  # (cd_prod_cor, sg_tamanho, canal)


def _data_criacao(created_at) -> date | None:
    """created_at da OR -> data local. O resto do projeto trata hora LOCAL como
    "hoje" (ver `_dt_foto_estoque` e `ler_faturamento_colecao` na ingestão), e a
    data da foto vem do Databricks já em data de negócio — comparar em UTC
    deslocaria a fronteira do dia."""
    if created_at is None:
        return None
    if isinstance(created_at, datetime):
        return (
            created_at.astimezone().date() if created_at.tzinfo else created_at.date()
        )
    if isinstance(created_at, date):
        return created_at
    return None


def calcular_estoque_virtual(
    foto: dict[ChaveEstoque, int],
    ordens: Iterable[tuple],
    dt_foto: date | None,
    pares_no_erp: set[tuple[int, str]],
) -> dict[ChaveEstoque, int]:
    """Disponível por (cd_prod_cor, sg_tamanho, canal) descontando as reservas que
    o app já gerou e que a foto de estoque ainda não reflete.

    `foto` é a foto crua (saída de `obter_foto_estoque`). `ordens` são tuplas
    (nr_pedido, cd_prod_cor, tipo, itens, created_at, aprovado_em) — a saída de
    `carregar_ordens_reserva`. OR de tipo 'sem' desconta igual: também é reserva.

    Desconta a OR quando as DUAS condições valem:

    - foi criada em/depois de `dt_foto`. A foto de hoje não reflete a OR de hoje
      (não há integração com o ERP), mas a virada do dia é o reset aceito do
      placebo: amanhã a peça reaparece como disponível.
    - o par (nr_pedido, cd_prod_cor) NÃO está em `pares_no_erp`. Se está, a reserva
      foi feita no Linx e a foto do Databricks já a reflete — descontar de novo
      tiraria a mesma peça duas vezes.

    `dt_foto` None (origem sem data válida) desconta TODAS as ORs: sem âncora, o
    conservador é assumir que nenhuma foi refletida.

    Item cujo canal não resolve para Franquia/Multimarca é ignorado, coerente com o
    motor: sem canal não há estoque contra o qual descontar. O `cd_prod_cor` usado
    é o da OR, não o do item — é ele que define a identidade da reserva.

    Piso em zero: a projeção nunca fica negativa.
    """
    virtual: dict[ChaveEstoque, int] = dict(foto)

    for nr_pedido, cd_prod_cor, _tipo, itens, created_at, _aprovado_em in ordens:
        if (nr_pedido, cd_prod_cor) in pares_no_erp:
            continue
        if dt_foto is not None:
            criada_em = _data_criacao(created_at)
            if criada_em is not None and criada_em < dt_foto:
                continue

        for item in itens or []:
            canal = normalizar_canal(item.get("canal"))
            sg_tamanho = str(item.get("sg_tamanho") or "").strip().upper()
            qt = int(item.get("qt_liquida") or 0)
            if canal is None or not sg_tamanho or qt <= 0:
                continue
            chave = (cd_prod_cor, sg_tamanho, canal)
            virtual[chave] = max(0, virtual.get(chave, 0) - qt)

    return virtual
