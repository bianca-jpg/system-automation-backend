"""Registro das chaves que o motor de adequação honra literalmente.

Este módulo é a fonte única de verdade sobre quais chaves da tabela
`parametros` são de fato lidas em produção (hoje: `tolerancia_adequacao` e
`criterio_selecao`, ambas lidas em
`app/modules/pedidos/processing/infrastructure/adapters.py::load_adequation_config`),
com tipo, faixa/valores válidos e o ponto do código onde cada uma é
aplicada.

Serve a três consumidores: validação na escrita, o selo "ativo no motor"
devolvido por `GET /api/v1/parametros`, e documentação viva ao lado do
código que aplica a regra (`aplicado_em`).

Criar uma chave fora deste registro é permitido, mas inerte: o motor não vai
lê-la enquanto não existir leitura escrita explicitamente no código.
"""

from dataclasses import dataclass
from typing import Final

from app.modules.parametros.domain.coercao import _coerce
from app.modules.parametros.domain.exceptions import ValorDeParametroInvalidoError
from app.modules.parametros.infrastructure.models import ParametroTipo


@dataclass(frozen=True, slots=True)
class ParametroConhecido:
    """Descreve uma chave que o motor de adequação consome de fato.

    `aplicado_em` guarda o caminho `arquivo::função` do ponto que consome a
    chave — é o que transforma este registro em documentação viva.
    """

    chave: str
    tipo: ParametroTipo
    descricao: str
    aplicado_em: str
    minimo: float | None = None
    maximo: float | None = None
    valores_aceitos: frozenset[str] | None = None


PARAMETROS_CONHECIDOS: Final[dict[str, ParametroConhecido]] = {
    "tolerancia_adequacao": ParametroConhecido(
        chave="tolerancia_adequacao",
        tipo=ParametroTipo.FLOAT,
        descricao=(
            "Tolerância de adequação da grade, como FRAÇÃO — não percentual "
            "(0.05 = 5%). Gravar 10 querendo 10% grava 1000%."
        ),
        aplicado_em=(
            "app/modules/pedidos/domain/motor_adequacao.py::adequar_grade_produto "
            "— a tolerância vira `limite_falta` (ceil) e `orcamento_aumento` "
            "(floor) sobre o total da grade do pedido+produto."
        ),
        minimo=0.0,
        maximo=1.0,
    ),
    "criterio_selecao": ParametroConhecido(
        chave="criterio_selecao",
        tipo=ParametroTipo.STRING,
        descricao="Critério de priorização entre pedidos concorrentes pelo mesmo estoque.",
        aplicado_em=(
            "app/modules/pedidos/domain/motor_adequacao.py — 'quantidade' ordena "
            "por `qt_liquida`; qualquer outro valor cai em `vl_liquido`."
        ),
        valores_aceitos=frozenset({"valor", "quantidade"}),
    ),
}


def parametro_conhecido(chave: str) -> ParametroConhecido | None:
    """Lookup direto no registro, sem levantar erro para chave desconhecida."""
    return PARAMETROS_CONHECIDOS.get(chave)


def e_consumido_pelo_motor(chave: str) -> bool:
    """True se `chave` é uma das que o motor de adequação honra literalmente."""
    return parametro_conhecido(chave) is not None


# Validação de valor é do caminho de ESCRITA. `get_param_value` (leitura,
# em leitura_resiliente.py) não pode chamar esta função nem passar a
# levantar erro — o motor de adequação precisa continuar caindo no default
# de settings.py diante de parâmetro ausente ou corrompido.
def validar_valor_de_parametro(chave: str, valor: str, tipo: str) -> None:
    """Levanta `ValorDeParametroInvalidoError` se `valor` não é aceitável
    para `chave`, segundo o tipo e a faixa declarados no registro.

    Chave fora do registro é livre: retorna sem checar nada, porque este
    registro descreve o que o motor honra, não uma política geral de nomes.
    """
    conhecido = parametro_conhecido(chave)
    if conhecido is None:
        return

    if tipo != conhecido.tipo:
        raise ValorDeParametroInvalidoError(
            chave,
            f"tipo declarado é '{conhecido.tipo}', recebido '{tipo}'.",
        )

    try:
        coagido = _coerce(valor, tipo)
    except Exception as exc:
        raise ValorDeParametroInvalidoError(
            chave,
            f"valor '{valor}' não é conversível para o tipo '{tipo}'.",
        ) from exc

    tem_limite = conhecido.minimo is not None or conhecido.maximo is not None
    if tem_limite and not (conhecido.minimo <= coagido <= conhecido.maximo):
        raise ValorDeParametroInvalidoError(
            chave,
            f"valor deve estar entre {conhecido.minimo} e {conhecido.maximo} "
            f"(fração, não percentual — 0.05 = 5%); recebido {coagido}.",
        )

    if (
        conhecido.valores_aceitos is not None
        and coagido not in conhecido.valores_aceitos
    ):
        aceitos = ", ".join(sorted(conhecido.valores_aceitos))
        raise ValorDeParametroInvalidoError(
            chave,
            f"valor deve ser um de: {aceitos}; recebido '{coagido}'.",
        )


__all__ = [
    "ParametroConhecido",
    "PARAMETROS_CONHECIDOS",
    "parametro_conhecido",
    "e_consumido_pelo_motor",
    "validar_valor_de_parametro",
]
