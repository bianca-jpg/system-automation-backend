"""Política de quantidade por modo (ALOC-02): contrato do modo de adequação.

Módulo-folha do domínio (mesmo padrão de `rateio.py`, plano 14-01): só stdlib,
sem import de outros módulos de `pedidos/domain/` nem de `pedidos/processing/`.

`ADEQUAR` permite reduzir/ampliar a grade pedida dentro de um orçamento de
tolerância (comportamento hoje implementado por `adequar_grade_produto`).
`SEM_ADEQUAR` exige a grade exatamente como pedida em todos os tamanhos, ou o
par inteiro fica em stand-by — nunca quantidade parcial (implementado por
`aplicar_tudo_ou_nada`, plano 14-05).

Este enum é PRÓPRIO do domínio e não importa `ProcessingMode` de
`app.modules.pedidos.processing.domain`: a direção de dependência DDD deste
repositório é `processing/` (consumidor, orquestração durável) -> `domain/`
(núcleo puro), nunca o inverso. Os valores literais são mantidos idênticos
aos de `ProcessingMode` DE PROPÓSITO (espelhados, não importados); a
paridade entre os dois enums é garantida por teste
(`test_modo_adequacao_valores_espelham_processing_mode`), não por import
compartilhado.
"""

from enum import StrEnum


class ModoAdequacao(StrEnum):
    ADEQUAR = "adequar"
    SEM_ADEQUAR = "sem_adequar"


__all__ = ["ModoAdequacao"]
