"""Vocabulário canônico CURTO de motivo de stand-by (STANDBY-01/04).

Estas strings atravessam a fronteira do motor de adequação até a tabela
`pedido_standby_motivo` (Phase 16, plano 16-02) e dali até a UI (Phase 17,
STANDBY-04). Contraste explícito com `motivo_stand_by`
(`motor_adequacao.marcar_stand_by`), que carrega uma frase longa e dinâmica
(ex. com o tamanho específico do furo) para log/depuração — os dois
propósitos são deliberadamente distintos: a frase pode mudar livremente sem
quebrar nada a jusante, estas constantes não podem (viram `CHECK constraint`
em banco). Um único lugar para estas strings evita que motor, `processing/domain.py`
(plano 16-03) e a migration (plano 16-02) divirjam entre si — nunca redigitar
os literais soltos em outro arquivo.

`BLACKLIST` (quick task 260825-jhv, migration 033): motivo canônico para
pedidos de cliente `indica_blacklist="SIM"` preteridos pela exceção
tudo-ou-nada forçada no motor — nunca reaproveita `FURO_GRADE`/`SEM_ESTOQUE`,
mesmo quando a causa técnica seria idêntica.
"""

SEM_CREDITO = "sem_credito"
SEM_ESTOQUE = "sem_estoque"
FURO_GRADE = "furo_grade"
BLACKLIST = "blacklist"

MOTIVOS_VALIDOS = frozenset({SEM_CREDITO, SEM_ESTOQUE, FURO_GRADE, BLACKLIST})

__all__ = [
    "SEM_CREDITO",
    "SEM_ESTOQUE",
    "FURO_GRADE",
    "BLACKLIST",
    "MOTIVOS_VALIDOS",
]
