"""Objetos de valor do lado de leitura de Adequação & Reserva.

Eles não conhecem FastAPI, SQLAlchemy ou o formato físico das tabelas. A camada
HTTP valida os enums/limites e a aplicação entrega estes objetos ao port de
consulta.
"""

from dataclasses import dataclass
from typing import Literal

Canal = Literal["Todos", "Franquia", "Multimarca"]
EstagioProduto = Literal["aguardando", "edicao", "historico"]
OrdenacaoProduto = Literal[
    "lastOrderAt",
    "code",
    "name",
    "totalQty",
    "ordersCount",
    "totalValue",
    "remainingWindow",
]
DirecaoOrdenacao = Literal["asc", "desc"]
StatusHistorico = Literal[
    "Todos",
    "Com Adequação",
    "Sem Adequação",
    "Bloqueado Estoque",
    "Bloqueado Crédito",
]


@dataclass(frozen=True, slots=True)
class ConsultaAlertas:
    canal: Canal
    busca: str
    limite: int
    cursor: tuple[str, int, str] | None = None


@dataclass(frozen=True, slots=True)
class ConsultaProdutos:
    estagio: EstagioProduto
    canal: Canal
    busca: str
    status: StatusHistorico
    ordenacao: OrdenacaoProduto
    direcao: DirecaoOrdenacao
    limite: int
    cursor: tuple[object, str, str] | None = None
    # Paginacao numerada. Exclusiva com `cursor`: keyset responde "o que vem
    # depois desta linha" e nao sabe saltar para uma pagina arbitraria, entao a
    # tela com numeros clicaveis pede offset. Ja resolvido em
    # (pagina - 1) * limite pela camada de aplicacao.
    offset: int | None = None


@dataclass(frozen=True, slots=True)
class ConsultaClientesProduto:
    codigo_produto: str
    estagio: EstagioProduto
    canal: Canal
    status: StatusHistorico
    limite: int
    cursor: tuple[str, int] | None = None


@dataclass(frozen=True, slots=True)
class ConsultaLookupPedidos:
    busca: str
    limite: int
    cursor: tuple[str, int] | None = None


@dataclass(frozen=True, slots=True)
class PaginaCursor:
    rows: list[dict]
    total: int
    has_more: bool
    next_key: tuple | None
