"""Persistência do estado atual do aviso de integração em Redis (PD-01).

Uma chave-hash por fonte, TTL de segurança (não é o mecanismo de resolução,
que é PD-03). `listar()` nunca usa `KEYS`/`SCAN`: o conjunto de fontes é
fechado e pequeno (`FonteIntegracao`).
"""

from __future__ import annotations

import logging
from datetime import datetime

from redis.asyncio import Redis

from app.modules.health.domain.avisos import AvisoIntegracao, FonteIntegracao

logger = logging.getLogger(__name__)

_PREFIXO = "automation:aviso-integracao"

# Rede de segurança contra chave imortal se um processo morrer — não é o
# mecanismo de resolução (esse é PD-03: os dois pollers periódicos já
# existentes resolvem o aviso quando a integração volta a funcionar).
_TTL_ABERTO_SEGUNDOS = 7 * 24 * 3600
# TTL curto do resolvido: deixa a resolução observável por um cliente que
# consulte logo depois e então some sozinho.
_TTL_RESOLVIDO_SEGUNDOS = 120

_CAMPOS_OBRIGATORIOS = (
    "fonte",
    "primeira_ocorrencia_em",
    "ultima_ocorrencia_em",
    "ocorrencias",
)


def _chave(fonte: FonteIntegracao) -> str:
    return f"{_PREFIXO}:{fonte.value}"


class RepositorioAvisosRedis:
    """Implementa `RepositorioAvisosIntegracaoPort` sobre um cliente Redis já
    conectado — a ligação com `get_redis()` é do composition root (service.py),
    não desta classe."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def obter(self, fonte: FonteIntegracao) -> AvisoIntegracao | None:
        bruto = await self._redis.hgetall(_chave(fonte))
        if not bruto:
            return None
        return self._desserializar(fonte, bruto)

    async def salvar(self, aviso: AvisoIntegracao) -> None:
        chave = _chave(aviso.fonte)
        mapping: dict[str, str] = {
            "fonte": aviso.fonte.value,
            "primeira_ocorrencia_em": aviso.primeira_ocorrencia_em.isoformat(),
            "ultima_ocorrencia_em": aviso.ultima_ocorrencia_em.isoformat(),
            "ocorrencias": str(aviso.ocorrencias),
        }
        if aviso.resolvido_em is not None:
            mapping["resolvido_em"] = aviso.resolvido_em.isoformat()

        ttl = (
            _TTL_RESOLVIDO_SEGUNDOS
            if aviso.resolvido_em is not None
            else _TTL_ABERTO_SEGUNDOS
        )

        pipeline = self._redis.pipeline()
        # DELETE antes do HSET: garante que um `resolvido_em` de uma rodada
        # anterior (aviso reaberto) não fique preso no hash (D-05).
        pipeline.delete(chave)
        pipeline.hset(chave, mapping=mapping)
        pipeline.expire(chave, ttl)
        await pipeline.execute()

    async def listar(self) -> list[AvisoIntegracao]:
        fontes = list(FonteIntegracao)
        pipeline = self._redis.pipeline()
        for fonte in fontes:
            pipeline.hgetall(_chave(fonte))
        resultados = await pipeline.execute()

        avisos: list[AvisoIntegracao] = []
        for fonte, bruto in zip(fontes, resultados, strict=True):
            if not bruto:
                continue
            aviso = self._desserializar(fonte, bruto)
            if aviso is not None:
                avisos.append(aviso)
        return avisos

    def _desserializar(
        self, fonte: FonteIntegracao, bruto: dict[str, str]
    ) -> AvisoIntegracao | None:
        if any(campo not in bruto for campo in _CAMPOS_OBRIGATORIOS):
            logger.warning(
                "Hash de aviso de integração incompleto para fonte=%s — tratando "
                "como ausente.",
                fonte.value,
            )
            return None
        try:
            return AvisoIntegracao(
                fonte=fonte,
                primeira_ocorrencia_em=datetime.fromisoformat(
                    bruto["primeira_ocorrencia_em"]
                ),
                ultima_ocorrencia_em=datetime.fromisoformat(
                    bruto["ultima_ocorrencia_em"]
                ),
                ocorrencias=int(bruto["ocorrencias"]),
                resolvido_em=(
                    datetime.fromisoformat(bruto["resolvido_em"])
                    if "resolvido_em" in bruto
                    else None
                ),
            )
        except (ValueError, TypeError):
            logger.warning(
                "Hash de aviso de integração corrompido para fonte=%s — tratando "
                "como ausente.",
                fonte.value,
            )
            return None
